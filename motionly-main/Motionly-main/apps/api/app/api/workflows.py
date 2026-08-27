from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import (
    Actor,
    get_actor,
    get_render_repository,
    get_workflow_repository,
)
from app.repositories.renders import InMemoryRenderRepository, SupabaseRenderRepository
from app.repositories.workflows import (
    InMemoryWorkflowRepository,
    SupabaseWorkflowRepository,
    WorkflowArtifactType,
    WorkflowRunRecord,
    WorkflowRunState,
)
from app.schemas.workflows import (
    WorkflowArtifactResponse,
    WorkflowCommandRequest,
    WorkflowCommandResponse,
    WorkflowDetailResponse,
    WorkflowDraftExportResponse,
    WorkflowDraftRenderResponse,
    WorkflowEventResponse,
    WorkflowListResponse,
    WorkflowStepResponse,
    WorkflowSummaryResponse,
)
from app.services.offload import run_blocking

router = APIRouter(prefix="/projects/{project_id}/workflows", tags=["workflows"])

ActorDep = Annotated[Actor, Depends(get_actor)]
WorkflowRepositoryDep = Annotated[
    InMemoryWorkflowRepository | SupabaseWorkflowRepository,
    Depends(get_workflow_repository),
]
RenderRepositoryDep = Annotated[
    InMemoryRenderRepository | SupabaseRenderRepository,
    Depends(get_render_repository),
]


async def _repository_call(repository: Any, async_name: str, sync_name: str, **kwargs: Any):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return await run_blocking(getattr(repository, sync_name), **kwargs)


async def _detail(
    repository: InMemoryWorkflowRepository | SupabaseWorkflowRepository,
    render_repository: InMemoryRenderRepository | SupabaseRenderRepository | None = None,
    *,
    run: WorkflowRunRecord,
) -> WorkflowDetailResponse:
    scope = {
        "workflow_run_id": run.id,
        "workspace_id": run.workspace_id,
        "project_id": run.project_id,
    }
    steps = await _repository_call(repository, "list_steps_async", "list_steps", **scope)
    events = await _repository_call(repository, "list_events_async", "list_events", **scope)
    artifacts = await _repository_call(
        repository, "list_artifacts_async", "list_artifacts", **scope
    )
    artifact_by_type = {artifact.artifact_type: artifact for artifact in artifacts}
    draft_render = None
    render_artifact = artifact_by_type.get(WorkflowArtifactType.DRAFT_RENDER_JOB)
    if render_artifact is not None and render_repository is not None:
        record = await _repository_call(
            render_repository,
            "get_render_job_async",
            "get_render_job",
            project_id=run.project_id,
            render_job_id=render_artifact.artifact_id,
            workspace_id=run.workspace_id,
        )
        if record.workflow_run_id == run.id:
            draft_render = WorkflowDraftRenderResponse.from_record(record)
    draft_export = None
    export_artifact = artifact_by_type.get(WorkflowArtifactType.DRAFT_EXPORT)
    if export_artifact is not None and render_repository is not None:
        record = await _repository_call(
            render_repository,
            "get_export_async",
            "get_export",
            export_id=export_artifact.artifact_id,
            workspace_id=run.workspace_id,
        )
        if record.project_id == run.project_id and record.workflow_run_id == run.id:
            draft_export = WorkflowDraftExportResponse.from_record(record)
    return WorkflowDetailResponse(
        **WorkflowSummaryResponse.from_record(run).model_dump(),
        steps=[WorkflowStepResponse.from_record(step) for step in steps],
        events=[WorkflowEventResponse.from_record(event) for event in events],
        artifacts=[WorkflowArtifactResponse.from_record(artifact) for artifact in artifacts],
        draft_render=draft_render,
        draft_export=draft_export,
    )


@router.get("", response_model=WorkflowListResponse)
async def list_workflows(
    project_id: UUID,
    actor: ActorDep,
    repository: WorkflowRepositoryDep,
    limit: int = Query(default=50, ge=1, le=100),
    state: Annotated[list[WorkflowRunState] | None, Query()] = None,
) -> WorkflowListResponse:
    runs = await _repository_call(
        repository,
        "list_workflows_async",
        "list_workflows",
        workspace_id=actor.workspace_id,
        project_id=project_id,
        limit=limit,
        states=set(state) if state else None,
    )
    return WorkflowListResponse(
        workflows=[WorkflowSummaryResponse.from_record(run) for run in runs]
    )


@router.get("/{workflow_run_id}", response_model=WorkflowDetailResponse)
async def get_workflow(
    project_id: UUID,
    workflow_run_id: UUID,
    actor: ActorDep,
    repository: WorkflowRepositoryDep,
    render_repository: RenderRepositoryDep,
) -> WorkflowDetailResponse:
    run = await _repository_call(
        repository,
        "get_workflow_async",
        "get_workflow",
        workflow_run_id=workflow_run_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    return await _detail(repository, render_repository, run=run)


@router.post("/{workflow_run_id}/cancel", response_model=WorkflowCommandResponse)
async def cancel_workflow(
    project_id: UUID,
    workflow_run_id: UUID,
    payload: WorkflowCommandRequest,
    actor: ActorDep,
    repository: WorkflowRepositoryDep,
    render_repository: RenderRepositoryDep,
) -> WorkflowCommandResponse:
    run, replay = await _repository_call(
        repository,
        "cancel_workflow_async",
        "cancel_workflow",
        workflow_run_id=workflow_run_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        expected_revision=payload.expected_revision,
        idempotency_key=payload.idempotency_key,
        requested_by=actor.user_id,
    )
    return WorkflowCommandResponse(
        workflow=await _detail(repository, render_repository, run=run),
        idempotent_replay=replay,
    )


@router.post(
    "/{workflow_run_id}/retry",
    response_model=WorkflowCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def retry_workflow(
    project_id: UUID,
    workflow_run_id: UUID,
    payload: WorkflowCommandRequest,
    actor: ActorDep,
    repository: WorkflowRepositoryDep,
    render_repository: RenderRepositoryDep,
) -> WorkflowCommandResponse:
    run, replay = await _repository_call(
        repository,
        "retry_workflow_async",
        "retry_workflow",
        workflow_run_id=workflow_run_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        expected_revision=payload.expected_revision,
        idempotency_key=payload.idempotency_key,
        requested_by=actor.user_id,
    )
    return WorkflowCommandResponse(
        workflow=await _detail(repository, render_repository, run=run),
        idempotent_replay=replay,
    )
