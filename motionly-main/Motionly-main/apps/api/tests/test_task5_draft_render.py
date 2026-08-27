from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.exceptions import ApplicationError

from app.api.errors import ApiError, api_error_handler
from app.api.internal_workflows import router as internal_workflows_router
from app.api.workflows import _detail
from app.core.middleware import request_id_middleware
from app.orchestration.activities import BullMQActivities
from app.orchestration.contracts import EnqueueStepRequest, EnsureDraftRenderRequest
from app.orchestration.render_activities import WorkflowRenderActivities
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.plans import EditPlanRecord
from app.repositories.renders import InMemoryRenderRepository
from app.repositories.workflows import WorkflowArtifactType, WorkflowRunState
from app.schemas.plans import (
    BrollDensity,
    EditPlan,
    EditPlanCut,
    EditPlanStatus,
    EditPlanStyle,
    PlanSummary,
)
from app.schemas.renders import RenderJobStatus
from app.services.queue import QueueRegistry, RenderQueue
from tests.test_task5_proposal_gate import HASH, _state


def _approved_render_state(*, gpu: bool = False):
    state = _state()
    captions = state.plan_repository.caption_repository
    upload = captions.get_active_upload(
        project_id=state.project_id,
        workspace_id=state.workspace_id,
    )
    upload.metadata.width = 1920
    upload.metadata.height = 1080
    upload.metadata.fps = 30
    upload.metadata.codec = "h264"
    upload.metadata.orientation = "landscape"
    transcript = captions.latest_transcript(
        project_id=state.project_id,
        workspace_id=state.workspace_id,
    )
    assert transcript is not None
    edit_plan = EditPlan(
        style=EditPlanStyle.CLEAN_MINIMAL,
        target_duration_s=12,
        cuts=[EditPlanCut(start_ms=0, end_ms=12_000, reason="approved source")],
        music_mood="neutral",
        color_grade="natural",
    )
    plan = EditPlanRecord(
        id=uuid4(),
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        transcript_id=transcript.id,
        version=1,
        schema_version=edit_plan.schema_version,
        status=EditPlanStatus.READY,
        is_active=True,
        style=edit_plan.style,
        target_duration_s=edit_plan.target_duration_s,
        broll_density=BrollDensity.NONE,
        prompt="approved workflow plan",
        feedback=None,
        provider="fixture",
        model="fixture-plan-v1",
        cache_key="workflow-draft-render-plan",
        schema_hash="c" * 64,
        summary=PlanSummary(
            duration_s=12,
            cut_count=1,
            broll_count=0,
            zoom_count=0,
            music_mood="neutral",
            effects=[],
            color_grade="natural",
        ),
        plan=edit_plan,
    )
    state.plan_repository.plans[plan.id] = plan
    state.run.active_plan_id = plan.id
    state.run.active_plan_revision = plan.revision
    state.run.state = WorkflowRunState.ASSEMBLING
    decision_id = uuid4()
    timeline_id = uuid4()
    state.workflow_repository.link_artifact(
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        workflow_run_id=state.run.id,
        artifact_type=WorkflowArtifactType.DECISION,
        artifact_id=decision_id,
    )
    state.workflow_repository.link_artifact(
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        workflow_run_id=state.run.id,
        artifact_type=WorkflowArtifactType.TIMELINE_REVISION,
        artifact_id=timeline_id,
        artifact_revision=plan.revision,
    )
    state.render_repository = InMemoryRenderRepository(captions)
    state.billing_repository = InMemoryBillingRepository()
    state.render_queue = RenderQueue(QueueRegistry())
    state.broll_repository = None
    state.settings = SimpleNamespace(
        worker_api_token="worker-secret",
        app_env="test",
        billing_enabled=False,
        remotion_enabled=False,
        ffmpeg_gpu_enabled=gpu,
        ffmpeg_gpu_encoder="h264_nvenc",
        allow_fixture_providers=True,
    )
    return state


def _render_app(state) -> FastAPI:
    app = FastAPI()
    for name, value in vars(state).items():
        setattr(app.state, name, value)
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(internal_workflows_router)
    return app


def _ensure_body(state) -> dict[str, str]:
    return {
        "workflow_run_id": str(state.run.id),
        "workspace_id": str(state.workspace_id),
        "project_id": str(state.project_id),
        "request_hash": HASH,
    }


def _ensure(state) -> dict[str, object]:
    with TestClient(_render_app(state)) as client:
        response = client.post(
            "/internal/workflows/draft-renders/ensure",
            json=_ensure_body(state),
            headers={"x-motionly-worker-token": "worker-secret"},
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_draft_render_activity_is_exact_and_confines_token_to_header() -> None:
    request = EnsureDraftRenderRequest(
        workflow_run_id=str(uuid4()),
        workspace_id=str(uuid4()),
        project_id=str(uuid4()),
        request_hash=HASH,
    )
    render_id = uuid4()
    seen: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(http_request.headers)
        seen["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "workflow_run_id": request.workflow_run_id,
                "render_job_id": str(render_id),
                "props_hash": "b" * 64,
                "render_engine": "ffmpeg_basic",
                "render_queue": "render_gpu",
                "source_plan_revision": 2,
                "source_media_revision": 3,
            },
        )

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            activity = WorkflowRenderActivities(
                api_base_url="https://api.motionly.test",
                worker_token="transport-secret",
                client=client,
            )
            result = await activity.ensure_workflow_draft_render(request)
            assert result.render_job_id == str(render_id)
            assert set(seen["body"]) == {
                "workflow_run_id",
                "workspace_id",
                "project_id",
                "request_hash",
            }
            assert seen["headers"]["x-motionly-worker-token"] == "transport-secret"
            assert "transport-secret" not in repr(result)
            assert "transport-secret" not in json.dumps(seen["body"])
        finally:
            await client.aclose()

        invalid = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "workflow_run_id": request.workflow_run_id,
                        "render_job_id": str(render_id),
                        "props_hash": "b" * 64,
                        "render_engine": "ffmpeg_basic",
                        "render_queue": "render",
                        "source_plan_revision": 2,
                        "source_media_revision": 3,
                        "manifest": {"secret": "not history safe"},
                    },
                )
            )
        )
        try:
            with pytest.raises(ApplicationError) as exc_info:
                await WorkflowRenderActivities(
                    api_base_url="https://api.motionly.test",
                    worker_token="transport-secret",
                    client=invalid,
                ).ensure_workflow_draft_render(request)
            assert exc_info.value.type == "workflow_render_response_invalid"
        finally:
            await invalid.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("gpu", "expected_queue"),
    [(False, "render"), (True, "render_gpu")],
)
def test_internal_ensure_is_one_target_retry_safe_and_routes_persisted_tier(
    gpu: bool,
    expected_queue: str,
) -> None:
    state = _approved_render_state(gpu=gpu)
    with TestClient(_render_app(state)) as client:
        denied = client.post("/internal/workflows/draft-renders/ensure", json=_ensure_body(state))
        first = client.post(
            "/internal/workflows/draft-renders/ensure",
            json=_ensure_body(state),
            headers={"x-motionly-worker-token": "worker-secret"},
        )
        replay = client.post(
            "/internal/workflows/draft-renders/ensure",
            json=_ensure_body(state),
            headers={"x-motionly-worker-token": "worker-secret"},
        )
        unknown = client.post(
            "/internal/workflows/draft-renders/ensure",
            json={**_ensure_body(state), "source_url": "https://storage.invalid/source.mp4"},
            headers={"x-motionly-worker-token": "worker-secret"},
        )

    assert denied.status_code == 403
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert set(first.json()) == {
        "workflow_run_id",
        "render_job_id",
        "props_hash",
        "render_engine",
        "render_queue",
        "source_plan_revision",
        "source_media_revision",
    }
    assert first.json()["render_queue"] == expected_queue
    assert unknown.status_code == 422
    assert len(state.render_repository.render_jobs) == 1
    assert state.render_queue.registry.jobs == {}
    assert state.run.state == WorkflowRunState.DRAFT_RENDERING


def test_render_draft_completion_requires_export_and_projects_safe_detail() -> None:
    state = _approved_render_state()
    ensured = _ensure(state)
    render_id = next(iter(state.render_repository.render_jobs))
    enqueue_request = EnqueueStepRequest(
        workflow_run_id=str(state.run.id),
        workflow_step_id=None,
        workspace_id=str(state.workspace_id),
        project_id=str(state.project_id),
        step_key="render-first-draft-v2",
        step_kind="render_draft",
        job_kind="render",
        queue_name=str(ensured["render_queue"]),
        attempt_number=1,
        attempt_key="render-attempt-0001",
        input_hash=str(ensured["props_hash"]),
        next_run_state="draft_ready",
        resource_id=str(render_id),
        max_attempts=3,
    )
    enqueued = asyncio.run(
        BullMQActivities(
            state.render_queue.registry,
            workflow_repository=state.workflow_repository,
        ).enqueue_bullmq_step(enqueue_request)
    )
    prepared = state.workflow_repository.list_steps(
        workflow_run_id=state.run.id,
        workspace_id=state.workspace_id,
        project_id=state.project_id,
    )[0]
    assert prepared.resource_id == render_id
    assert prepared.bullmq_job_id == enqueued.job_id
    begun = state.workflow_repository.begin_step(
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        workflow_run_id=state.run.id,
        step_key="render-first-draft-v2",
        step_kind="render_draft",
        attempt_key="render-attempt-0001",
        lease_owner="worker-test",
        lease_seconds=60,
        input_hash=str(ensured["props_hash"]),
    )
    assert begun.attempt is not None
    render = state.render_repository.render_jobs[render_id]
    with pytest.raises(ApiError, match="canonical export"):
        state.render_repository.transition_job(
            job=render,
            status=RenderJobStatus.COMPLETE,
            progress_pct=100,
            message="complete",
        )
    exported = state.render_repository.create_export(
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        render_job_id=render_id,
        storage_bucket="exports",
        storage_path="workflow/base.mp4",
        format="mp4",
        width=1080,
        height=1920,
        fps=30,
        duration_ms=12_000,
        file_size_bytes=1024,
        settings_summary={},
    )
    state.render_repository.transition_job(
        job=render,
        status=RenderJobStatus.COMPLETE,
        progress_pct=100,
        message="complete",
    )
    output = {
        "render_job_id": str(render_id),
        "render_status": "complete",
        "failed": False,
    }
    with pytest.raises(ApiError, match="claim expired"):
        state.workflow_repository.complete_step(
            workspace_id=state.workspace_id,
            project_id=state.project_id,
            workflow_run_id=state.run.id,
            step_key="render-first-draft-v2",
            claim_token=begun.attempt.claim_token,
            output_hash="d" * 64,
            output=output,
            next_run_state=WorkflowRunState.DRAFT_READY,
            now=begun.attempt.lease_expires_at + timedelta(seconds=1),
        )
    assert not any(
        artifact.artifact_type == WorkflowArtifactType.DRAFT_EXPORT
        for artifact in state.workflow_repository.list_artifacts(
            workflow_run_id=state.run.id,
            workspace_id=state.workspace_id,
            project_id=state.project_id,
        )
    )
    run, step, replay = state.workflow_repository.complete_step(
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        workflow_run_id=state.run.id,
        step_key="render-first-draft-v2",
        claim_token=begun.attempt.claim_token,
        output_hash="d" * 64,
        output=output,
        next_run_state=WorkflowRunState.DRAFT_READY,
        now=begun.attempt.lease_expires_at - timedelta(seconds=1),
    )
    assert not replay
    assert run.state == WorkflowRunState.DRAFT_READY
    assert step.output == output
    detail = asyncio.run(_detail(state.workflow_repository, state.render_repository, run=state.run))
    assert detail.draft_render is not None
    assert detail.draft_render.id == render_id
    assert detail.draft_render.status == "complete"
    assert detail.draft_render.progress_pct == 100
    assert detail.draft_export is not None
    assert detail.draft_export.id == exported.id
    serialized = detail.model_dump(mode="json")
    assert "manifest" not in serialized["draft_render"]
    assert "storage_path" not in serialized["draft_export"]
    assert "thumbnail_url" not in serialized["draft_export"]


def test_cancellation_blocks_late_workflow_export_and_render_success() -> None:
    state = _approved_render_state()
    _ensure(state)
    render = next(iter(state.render_repository.render_jobs.values()))
    state.workflow_repository.cancel_workflow(
        workflow_run_id=state.run.id,
        workspace_id=state.workspace_id,
        project_id=state.project_id,
        expected_revision=state.run.revision,
        idempotency_key="cancel-rendering-workflow",
        requested_by=state.run.started_by,
    )
    assert state.run.state == WorkflowRunState.CANCELLED
    with pytest.raises(ApiError, match="no longer active"):
        state.render_repository.create_export(
            workspace_id=state.workspace_id,
            project_id=state.project_id,
            render_job_id=render.id,
            storage_bucket="exports",
            storage_path="workflow/late.mp4",
            format="mp4",
            width=1080,
            height=1920,
            fps=30,
            duration_ms=12_000,
            file_size_bytes=1024,
            settings_summary={},
        )
    with pytest.raises(ApiError, match="no longer active"):
        state.render_repository.transition_job(
            job=render,
            status=RenderJobStatus.COMPLETE,
            progress_pct=100,
            message="late completion",
        )
    assert render.status == RenderJobStatus.QUEUED


def test_temporal_worker_wires_postgres_step_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.orchestration import worker as worker_module

    captured: dict[str, object] = {}

    async def connect(_config):
        return object()

    class Registry:
        closed = False

        def __init__(self, **values: object) -> None:
            captured["registry_values"] = values

        def close(self) -> None:
            self.closed = True

    class Repository:
        def __init__(self, **values: object) -> None:
            captured["repository_values"] = values

    class Worker:
        async def run(self) -> None:
            captured["ran"] = True

    def create(**values: object) -> Worker:
        captured["worker_values"] = values
        return Worker()

    monkeypatch.setattr(worker_module, "connect_temporal_client", connect)
    monkeypatch.setattr(worker_module, "BullMQQueueRegistry", Registry)
    monkeypatch.setattr(worker_module, "SupabaseWorkflowRepository", Repository)
    monkeypatch.setattr(worker_module, "create_worker", create)

    settings = Settings(
        _env_file=None,
        APP_ENV="local",
        PROCESS_ROLE="temporal-worker",
        REDIS_URL="redis://redis.test:6379",
        SUPABASE_URL="https://supabase.test",
        SUPABASE_SERVICE_ROLE_KEY="service-role-test",
        TEMPORAL_ADDRESS="temporal.test:7233",
        TEMPORAL_NAMESPACE="default",
        TEMPORAL_TASK_QUEUE="motionly-test",
        WORKER_API_BASE_URL="https://api.motionly.test",
        WORKER_API_TOKEN="worker-test",
    )
    asyncio.run(worker_module.run_worker(settings))

    worker_values = captured["worker_values"]
    assert isinstance(worker_values, dict)
    assert isinstance(worker_values["workflow_repository"], Repository)
    assert captured["repository_values"] == {
        "supabase_url": "https://supabase.test/",
        "service_role_key": "service-role-test",
    }
    assert captured["ran"] is True
