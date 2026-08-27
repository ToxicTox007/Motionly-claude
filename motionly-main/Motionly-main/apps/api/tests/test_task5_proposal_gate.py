from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from app.api.errors import ApiError, api_error_handler
from app.api.internal_workflows import router as internal_workflows_router
from app.api.workflows import _detail
from app.core.middleware import request_id_middleware
from app.orchestration.contracts import EnsureProposalRequest
from app.orchestration.proposal_activities import WorkflowProposalActivities
from app.repositories.ai_proposals import InMemoryAIProposalRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.projects import InMemoryProjectRepository, ProjectApiRecord
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.repositories.workflows import (
    InMemoryWorkflowRepository,
    WorkflowArtifactType,
    WorkflowRunState,
)
from app.schemas.captions import LanguageHint
from app.schemas.uploads import UploadStatus
from app.schemas.visual import VideoManifest, VisualManifestStatus
from app.schemas.workflow_proposals import WorkflowProposalEnsureRequest
from app.schemas.workflows import WorkflowAllowedAction, WorkflowSummaryResponse
from app.services.transcription_provider import FixtureTranscriptionProvider

HASH = "a" * 64


def _state(*, with_visual: bool = True):
    workspace_id, project_id, user_id = uuid4(), uuid4(), uuid4()
    uploads = InMemoryUploadRepository()
    uploads.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = uploads.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="workflow/source.mp4",
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=2048,
    )
    upload.status = UploadStatus.READY
    upload.workflow_version = "first-draft-v2"
    upload.source_object_reference_id = uuid4()
    upload.verified_size_bytes = 2048
    upload.verified_sha256 = "b" * 64
    upload.metadata.duration_ms = 12_000
    upload.metadata.has_audio = True
    uploads.attach_active_video(project_id=project_id, video_id=upload.id)

    captions = InMemoryCaptionRepository(uploads)
    transcription = FixtureTranscriptionProvider().transcribe(
        audio_path=None,
        language_hint=LanguageHint.AUTO,
    )
    captions.create_transcript(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status="ready",
        language="en",
        language_hint="auto",
        provider="fixture",
        model="fixture-whisper",
        confidence=0.95,
        duration_ms=12_000,
        raw_payload={},
        words=transcription.words,
    )
    plans = InMemoryPlanRepository(captions)
    visual = InMemoryVisualRepository(uploads)
    if with_visual:
        visual.create_manifest(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=upload.id,
            status=VisualManifestStatus.READY,
            provider="fixture_visual",
            model="fixture-visual-v1",
            manifest=VideoManifest(
                provider="fixture_visual",
                duration_ms=12_000,
                summary={"scene_count": 1},
            ),
            cache_key="visual-cache",
        )
    plans.visual_repository = visual
    proposals = InMemoryAIProposalRepository(plan_repository=plans)
    workflows = InMemoryWorkflowRepository()
    run, _ = workflows.start_workflow(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        source_media_revision=upload.media_revision,
        source_object_reference_id=upload.source_object_reference_id,
        workflow_type="first_draft",
        workflow_version="first-draft-v2",
        idempotency_key="proposal-gate-test-run",
        request_hash=HASH,
        started_by=user_id,
        input={},
    )
    run.state = WorkflowRunState.PROPOSAL_READY
    projects = InMemoryProjectRepository()
    projects.projects[project_id] = ProjectApiRecord(
        id=project_id,
        workspace_id=workspace_id,
        created_by=user_id,
        title="Workflow proposal",
    )
    return SimpleNamespace(
        settings=SimpleNamespace(worker_api_token="worker-secret", app_env="test"),
        workflow_repository=workflows,
        proposal_repository=proposals,
        plan_repository=plans,
        project_repository=projects,
        run=run,
        workspace_id=workspace_id,
        project_id=project_id,
    )


def _app(state) -> FastAPI:
    app = FastAPI()
    app.state.settings = state.settings
    app.state.workflow_repository = state.workflow_repository
    app.state.proposal_repository = state.proposal_repository
    app.state.plan_repository = state.plan_repository
    app.state.project_repository = state.project_repository
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(internal_workflows_router)
    return app


def test_internal_proposal_endpoint_is_exact_retry_safe_and_exposes_artifact() -> None:
    state = _state()
    body = {
        "workflow_run_id": str(state.run.id),
        "workspace_id": str(state.workspace_id),
        "project_id": str(state.project_id),
        "request_hash": HASH,
    }
    with TestClient(_app(state)) as client:
        denied = client.post("/internal/workflows/proposals/ensure", json=body)
        assert denied.status_code == 403
        first = client.post(
            "/internal/workflows/proposals/ensure",
            json=body,
            headers={"x-motionly-worker-token": "worker-secret"},
        )
        replay = client.post(
            "/internal/workflows/proposals/ensure",
            json=body,
            headers={"x-motionly-worker-token": "worker-secret"},
        )
        unknown = client.post(
            "/internal/workflows/proposals/ensure",
            json={**body, "source_url": "https://storage.example/source.mp4"},
            headers={"x-motionly-worker-token": "worker-secret"},
        )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert set(first.json()) == {
        "workflow_run_id",
        "proposal_id",
        "proposal_revision",
        "request_id",
        "context_digest",
        "proposal_state",
    }
    assert unknown.status_code == 422
    assert len(state.proposal_repository.proposals) == 1
    proposal_artifacts = [
        item
        for item in state.workflow_repository.list_artifacts(
            workflow_run_id=state.run.id,
            workspace_id=state.workspace_id,
            project_id=state.project_id,
        )
        if item.artifact_type == WorkflowArtifactType.PROPOSAL
    ]
    assert [str(item.artifact_id) for item in proposal_artifacts] == [first.json()["proposal_id"]]

    state.run.state = WorkflowRunState.AWAITING_APPROVAL
    summary = WorkflowSummaryResponse.from_record(state.run)
    assert WorkflowAllowedAction.APPROVE_PROPOSAL in summary.allowed_actions
    assert WorkflowAllowedAction.REJECT_PROPOSAL in summary.allowed_actions
    detail = asyncio.run(_detail(state.workflow_repository, run=state.run))
    assert [
        artifact.id
        for artifact in detail.artifacts
        if artifact.type == WorkflowArtifactType.PROPOSAL
    ] == [UUID(first.json()["proposal_id"])]
    assert WorkflowAllowedAction.APPROVE_PROPOSAL in detail.allowed_actions
    assert WorkflowAllowedAction.REJECT_PROPOSAL in detail.allowed_actions


def test_internal_proposal_endpoint_fails_closed_without_ready_visual() -> None:
    state = _state(with_visual=False)
    with TestClient(_app(state)) as client:
        response = client.post(
            "/internal/workflows/proposals/ensure",
            json={
                "workflow_run_id": str(state.run.id),
                "workspace_id": str(state.workspace_id),
                "project_id": str(state.project_id),
                "request_hash": HASH,
            },
            headers={"x-motionly-worker-token": "worker-secret"},
        )
    assert response.status_code == 409
    assert state.proposal_repository.proposals == {}


def test_internal_schema_rejects_unknown_context_and_transport_fields() -> None:
    base = {
        "workflow_run_id": str(uuid4()),
        "workspace_id": str(uuid4()),
        "project_id": str(uuid4()),
        "request_hash": HASH,
    }
    for extra in (
        {"token": "secret"},
        {"url": "https://storage.example/source.mp4"},
        {"context": {"transcript": "not allowed"}},
        {"plan": {"cuts": []}},
    ):
        with pytest.raises(ValidationError):
            WorkflowProposalEnsureRequest.model_validate({**base, **extra})


def test_temporal_activity_uses_token_only_in_transport_and_validates_exact_response() -> None:
    request = EnsureProposalRequest(
        workflow_run_id=str(uuid4()),
        workspace_id=str(uuid4()),
        project_id=str(uuid4()),
        request_hash=HASH,
    )
    proposal_id = uuid4()
    seen = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(http_request.headers)
        seen["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "workflow_run_id": request.workflow_run_id,
                "proposal_id": str(proposal_id),
                "proposal_revision": 1,
                "request_id": "c" * 64,
                "context_digest": "d" * 64,
                "proposal_state": "queued",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    activity = WorkflowProposalActivities(
        api_base_url="https://api.motionly.test",
        worker_token="transport-secret",
        client=client,
    )
    result = asyncio.run(activity.ensure_workflow_proposal(request))
    asyncio.run(client.aclose())

    assert result.proposal_id == str(proposal_id)
    assert seen["body"] == {
        "workflow_run_id": request.workflow_run_id,
        "workspace_id": request.workspace_id,
        "project_id": request.project_id,
        "request_hash": HASH,
    }
    assert seen["headers"]["x-motionly-worker-token"] == "transport-secret"
    assert "transport-secret" not in repr(result)
    assert "transport-secret" not in json.dumps(seen["body"])

    async def invalid_response() -> None:
        invalid_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "workflow_run_id": request.workflow_run_id,
                        "proposal_id": str(proposal_id),
                        "proposal_revision": 1,
                        "request_id": "c" * 64,
                        "context_digest": "d" * 64,
                        "proposal_state": "queued",
                        "signed_url": "https://storage.example/source.mp4?token=secret",
                    },
                )
            )
        )
        try:
            with pytest.raises(ApplicationError) as exc:
                await WorkflowProposalActivities(
                    api_base_url="https://api.motionly.test",
                    worker_token="transport-secret",
                    client=invalid_client,
                ).ensure_workflow_proposal(request)
            assert exc.value.type == "workflow_proposal_response_invalid"
        finally:
            await invalid_client.aclose()

    asyncio.run(invalid_response())
