import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.internal_jobs import _execute_job, run_queue_job
from app.api.internal_renders import fail_render_job, update_render_progress
from app.api.renders import create_render
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.renders import RenderCreateRequest, RenderJobStatus
from app.schemas.uploads import UploadStatus, VideoMetadata
from app.services.local_storage import LocalObjectStorage
from app.services.operations import MetricsRegistry
from app.services.queue import EnqueuedJob, PreprocessQueue, QueueRegistry, RenderQueue
from app.services.render_jobs import complete_remotion_render_job
from tests.test_renders import make_render_repository


def test_production_api_refuses_media_job_execution() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(app_env="production", worker_api_token="worker-secret"),
                queue_registry=QueueRegistry(),
            )
        )
    )
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            run_queue_job(
                queue_job_id="any-job",
                request=request,
                x_motionly_worker_token="worker-secret",
            )
        )
    assert exc_info.value.status_code == 410
    assert exc_info.value.code == "direct_worker_execution_required"


def test_worker_runs_upload_preprocessing_job(tmp_path, monkeypatch) -> None:
    repository = InMemoryUploadRepository()
    registry = QueueRegistry()
    project_id = uuid4()
    workspace_id = uuid4()
    repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="workspaces/test/projects/test/raw/video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=100,
    )
    upload.status = UploadStatus.PROCESSING
    LocalObjectStorage(root=tmp_path).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"fake mp4",
    )

    class FakePreprocessor:
        def __init__(self, **_kwargs):
            pass

        def process(self, **kwargs):
            return (
                VideoMetadata(
                    duration_ms=3000,
                    width=1080,
                    height=1920,
                    has_audio=True,
                    orientation="vertical",
                ),
                kwargs["thumbnail_path"],
            )

    monkeypatch.setattr("app.services.upload_jobs.VideoPreprocessor", FakePreprocessor)
    queue_job = PreprocessQueue(registry).enqueue_video_preprocess(
        video_id=upload.id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    worker_api_token="worker-secret",
                    media_work_dir=str(tmp_path),
                    supabase_url=None,
                    supabase_service_role_key=None,
                ),
                queue_registry=registry,
                upload_repository=repository,
            ),
        ),
    )

    response = asyncio.run(
        run_queue_job(
            queue_job_id=queue_job.id,
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    processed = repository.get_upload_for_project(
        project_id=project_id,
        video_id=upload.id,
        workspace_id=workspace_id,
    )
    assert response.status == "succeeded"
    assert processed.status == "ready"
    assert processed.metadata.duration_ms == 3000
    assert processed.thumbnail_path == (
        f"workspaces/{workspace_id}/projects/{project_id}/thumbnails/{upload.id}.jpg"
    )


@pytest.mark.parametrize(
    ("workflow_run_id", "expected_fanout"),
    [(None, 1), (uuid4(), 0)],
    ids=["ordinary-preprocess-fans-out", "workflow-preprocess-does-not-fan-out"],
)
def test_preprocess_fanout_is_owned_by_exactly_one_orchestrator(
    monkeypatch,
    workflow_run_id,
    expected_fanout,
) -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    upload_id = uuid4()
    upload = SimpleNamespace(
        id=upload_id,
        workspace_id=workspace_id,
        project_id=project_id,
        status=UploadStatus.READY,
    )
    calls = {"transcription": 0, "visual": 0}

    monkeypatch.setattr(
        "app.api.internal_jobs.complete_upload_preprocessing_by_id",
        lambda **_kwargs: upload,
    )

    def queue_transcription(**_kwargs):
        calls["transcription"] += 1
        return SimpleNamespace(id=uuid4(), status=SimpleNamespace(value="queued"))

    def queue_visual(**_kwargs):
        calls["visual"] += 1
        return SimpleNamespace(
            manifest=SimpleNamespace(id=uuid4(), status=SimpleNamespace(value="queued")),
            job=SimpleNamespace(id="visual-job"),
        )

    monkeypatch.setattr("app.api.internal_jobs.queue_transcription_for_upload", queue_transcription)
    monkeypatch.setattr(
        "app.api.internal_jobs.visual_provider_identity",
        lambda _settings: ("p", "m"),
    )
    monkeypatch.setattr("app.api.internal_jobs.build_visual_cache_key", lambda **_kwargs: "v1")
    monkeypatch.setattr("app.api.internal_jobs.queue_visual_analysis_for_upload", queue_visual)

    state = SimpleNamespace(
        settings=SimpleNamespace(
            media_work_dir="/tmp/motionly-test",
            supabase_url=None,
            supabase_service_role_key=None,
            visual_sample_fps=2.0,
            visual_scene_threshold=0.42,
        ),
        upload_repository=object(),
        caption_repository=object(),
        transcription_queue=object(),
        transcription_provider=object(),
        visual_repository=object(),
        visual_queue=object(),
    )
    result = _execute_job(
        job=EnqueuedJob(
            id="preprocess-test",
            kind="video_preprocess",
            queue_name="upload_preprocess",
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=upload_id,
            workflow_run_id=workflow_run_id,
        ),
        request=SimpleNamespace(app=SimpleNamespace(state=state)),
    )

    assert calls == {"transcription": expected_fanout, "visual": expected_fanout}
    assert result["upload_id"] == str(upload_id)
    assert ("transcript_id" in result) is bool(expected_fanout)
    assert ("visual_manifest_id" in result) is bool(expected_fanout)


def test_domain_worker_reconstructs_identifier_only_workflow_context(monkeypatch) -> None:
    from app.workers.domain_job import _execute

    captured = {}

    def execute_job(*, job, request):
        captured["job"] = job
        assert request.app is not None
        return {"status": "ready"}

    monkeypatch.setattr("app.api.internal_jobs._execute_job", execute_job)
    identifiers = {
        "workflow_run_id": str(uuid4()),
        "workflow_step_id": str(uuid4()),
        "step_key": "preprocess-source-v1",
        "step_kind": "preprocess_source",
        "attempt_key": "temporal-attempt:preprocess:0002",
        "workflow_attempt_number": 2,
        "workflow_max_attempts": 3,
        "input_hash": "a" * 64,
        "next_run_state": "analyzing",
    }
    result = _execute(
        {
            "job_id": "workflow-preprocess-job",
            "kind": "video_preprocess",
            "queue_name": "upload_preprocess",
            "workspace_id": str(uuid4()),
            "project_id": str(uuid4()),
            "resource_id": str(uuid4()),
            **identifiers,
        }
    )

    job = captured["job"]
    assert result == {"status": "ready"}
    assert str(job.workflow_run_id) == identifiers["workflow_run_id"]
    assert str(job.workflow_step_id) == identifiers["workflow_step_id"]
    assert job.step_key == identifiers["step_key"]
    assert job.step_kind == identifiers["step_kind"]
    assert job.attempt_key == identifiers["attempt_key"]
    assert job.workflow_attempt_number == 2
    assert job.workflow_max_attempts == 3
    assert job.input_hash == identifiers["input_hash"]
    assert job.next_run_state == identifiers["next_run_state"]


def test_worker_runs_render_job_to_export() -> None:
    repository, project_id, workspace_id, user_id = make_render_repository()
    registry = QueueRegistry()
    metrics_registry = MetricsRegistry()
    settings = SimpleNamespace(
        worker_api_token="worker-secret",
        export_storage_bucket="exports",
        export_signed_url_ttl_seconds=300,
        should_complete_renders_synchronously=False,
        render_execute_ffmpeg=False,
        media_work_dir=".motionly/test-storage",
        billing_enabled=False,
        supabase_url=None,
        supabase_service_role_key=None,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                queue_registry=registry,
                metrics_registry=metrics_registry,
                render_repository=repository,
            ),
        ),
    )
    render_response = asyncio.run(
        create_render(
            project_id=project_id,
            payload=RenderCreateRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=InMemoryPlanRepository(repository.caption_repository),
            render_queue=RenderQueue(registry),
        )
    )

    worker_response = asyncio.run(
        run_queue_job(
            queue_job_id=render_response.queue_job_id,
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    job = repository.get_render_job(
        project_id=project_id,
        render_job_id=render_response.job_id,
        workspace_id=workspace_id,
    )
    assert worker_response.status == "succeeded"
    assert job.status == RenderJobStatus.COMPLETE
    assert repository.list_exports(project_id=project_id, workspace_id=workspace_id)
    summary = metrics_registry.summary()
    assert summary["render"]["p95_latency_ms"] == 0
    assert summary["export"]["p95_latency_ms"] == 0


def test_worker_progress_callback_updates_render_job() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    render = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=RenderJobStatus.QUEUED,
        planner_version="test",
        manifest={},
        keep_segments=[],
        progress_pct=5,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(worker_api_token="worker-secret"),
                render_repository=repository,
            ),
        ),
    )

    response = asyncio.run(
        update_render_progress(
            render_job_id=render.id,
            payload=SimpleNamespace(
                workspace_id=workspace_id,
                project_id=project_id,
                status=RenderJobStatus.RENDERING,
                progress_pct=45,
                message="Rendering premium video composition.",
                event_type="remotion_rendering",
                metadata={"phase": "render"},
            ),
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    assert response.status == RenderJobStatus.RENDERING
    assert response.progress_pct == 45
    assert repository.events_for_job(render.id)[-1].event_type == "remotion_rendering"


def test_worker_failure_callback_marks_terminal_failure() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    render = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=RenderJobStatus.RENDERING,
        planner_version="test",
        manifest={},
        keep_segments=[],
        progress_pct=45,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(worker_api_token="worker-secret"),
                render_repository=repository,
            ),
        ),
    )

    response = asyncio.run(
        fail_render_job(
            render_job_id=render.id,
            payload=SimpleNamespace(
                workspace_id=workspace_id,
                project_id=project_id,
                error_code="render_worker_failed",
                error_message="render-cli.ts exited with code 1",
                retrying=False,
                metadata={"attempts": 3},
            ),
            request=request,
            x_motionly_worker_token="worker-secret",
        )
    )

    assert response.status == RenderJobStatus.FAILED
    assert render.error_code == "render_worker_failed"
    assert "render-cli.ts exited with code 1" in render.logs[-1]


def test_remotion_completion_records_render_and_export_metrics() -> None:
    repository, project_id, workspace_id, _user_id = make_render_repository()
    job = repository.create_render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        caption_track_id=repository.latest_caption_track(
            project_id=project_id,
            workspace_id=workspace_id,
        ).id,
        status=RenderJobStatus.RENDERING,
        planner_version="test",
        manifest={},
        keep_segments=[],
        progress_pct=90,
    )
    metrics_registry = MetricsRegistry()

    complete_remotion_render_job(
        job=job,
        repository=repository,
        metrics_registry=metrics_registry,
        export_bucket="exports",
        export_storage_path=f"workspaces/{workspace_id}/projects/{project_id}/exports/{job.id}.mp4",
        benchmark_ms=3210,
        file_size_bytes=2048,
        width=1080,
        height=1920,
        fps=30,
        duration_ms=6000,
    )

    summary = metrics_registry.summary()
    assert summary["render"]["p95_latency_ms"] == 3210
    assert summary["export"]["p95_latency_ms"] == 3210


@pytest.mark.parametrize("linked", [True, False], ids=["canonical-link", "foreign-link"])
def test_workflow_edit_plan_completion_is_fenced_by_canonical_proposal(
    monkeypatch, linked: bool
) -> None:
    from app.schemas.ai_proposals import ProposalState

    workspace_id, project_id, proposal_id, workflow_run_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    canonical = SimpleNamespace(
        id=proposal_id,
        state=ProposalState.AWAITING_APPROVAL,
        workflow_run_id=workflow_run_id if linked else uuid4(),
    )
    proposal_repository = SimpleNamespace(get=lambda **_kwargs: canonical)
    monkeypatch.setattr(
        "app.services.proposal_generation.execute_proposal_generation",
        lambda **_kwargs: {
            "proposal_id": str(proposal_id),
            "status": "awaiting_approval",
            "result_hash": "a" * 64,
            "failed": False,
            "error_code": None,
            "error_message": None,
        },
    )
    state = SimpleNamespace(
        settings=SimpleNamespace(max_paid_provider_attempts=1),
        plan_repository=object(),
        proposal_repository=proposal_repository,
        edit_planner_provider=object(),
    )
    result = _execute_job(
        job=EnqueuedJob(
            id="workflow-edit-plan",
            kind="edit_plan",
            queue_name="planning",
            workspace_id=workspace_id,
            project_id=project_id,
            resource_id=proposal_id,
            workflow_run_id=workflow_run_id,
            attempts=0,
        ),
        request=SimpleNamespace(app=SimpleNamespace(state=state)),
    )

    assert result["status"] == "awaiting_approval"
    assert result["failed"] is (not linked)
    assert result.get("error_code") == (
        None if linked else "workflow_proposal_not_approval_ready"
    )
