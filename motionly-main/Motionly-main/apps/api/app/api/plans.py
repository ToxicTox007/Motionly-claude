from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    Actor,
    get_actor,
    get_edit_planner_provider,
    get_plan_queue,
    get_plan_repository,
)
from app.api.errors import ApiError
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.captions import TranscriptStatus
from app.schemas.plans import (
    EDIT_PLAN_SCHEMA_VERSION,
    EditPlanStatus,
    PlanActivateResponse,
    PlanCaptionHighlightsUpdateRequest,
    PlanCaptionHighlightsUpdateResponse,
    PlanCreateRequest,
    PlanCreateResponse,
    PlanCutsUpdateRequest,
    PlanCutsUpdateResponse,
    PlanListResponse,
)
from app.services.edit_planner import (
    PLANNER_SCHEMA_HASH,
    EditPlannerProvider,
    build_plan_cache_key,
    normalize_plan,
    summarize_plan,
)
from app.services.queue import PlanQueue

router = APIRouter(prefix="/projects/{project_id}/plans", tags=["AI plans"])

ActorDep = Annotated[Actor, Depends(get_actor)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]
EditPlannerProviderDep = Annotated[EditPlannerProvider, Depends(get_edit_planner_provider)]
PlanQueueDep = Annotated[PlanQueue, Depends(get_plan_queue)]


@router.post("", response_model=PlanCreateResponse, status_code=201)
async def create_plan(
    project_id: UUID,
    payload: PlanCreateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    planner_provider: EditPlannerProviderDep,
    plan_queue: PlanQueueDep,
) -> PlanCreateResponse:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    transcript = repository.latest_transcript(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    if not transcript:
        raise ApiError(404, "transcript_not_found", "Generate a transcript before planning edits.")
    if transcript.status != TranscriptStatus.READY:
        raise ApiError(
            409,
            str(transcript.status),
            transcript.error_message or "Transcript is not ready for edit planning.",
        )

    upload = repository.get_active_upload(project_id=project_id, workspace_id=actor.workspace_id)
    video_duration_ms = upload.metadata.duration_ms or transcript.duration_ms or 0
    cache_key = build_plan_cache_key(transcript=transcript, payload=payload)
    job = plan_queue.enqueue_plan(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        transcript_id=transcript.id,
    )
    if not payload.force:
        cached = repository.find_cached_plan(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            cache_key=cache_key,
        )
        if cached:
            return PlanCreateResponse(plan=cached.to_response(), job_id=job.id, cached=True)

    result = planner_provider.create_plan(
        transcript=transcript,
        payload=payload,
        video_duration_ms=video_duration_ms,
    )
    plan = normalize_plan(
        plan=result.plan,
        words=transcript.words,
        duration_ms=video_duration_ms or result.plan.target_duration_s * 1000,
    )
    record = repository.create_plan(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        transcript_id=transcript.id,
        schema_version=EDIT_PLAN_SCHEMA_VERSION,
        status=EditPlanStatus.READY,
        style=payload.style,
        target_duration_s=payload.target_duration_s,
        broll_density=payload.broll_density,
        prompt=payload.prompt.strip(),
        feedback=payload.feedback.strip() if payload.feedback else None,
        provider=result.provider,
        model=result.model,
        cache_key=cache_key,
        schema_hash=PLANNER_SCHEMA_HASH,
        latency_ms=result.latency_ms,
        cost_estimate_usd=result.cost_estimate_usd,
        validation_errors=result.validation_errors,
        summary=summarize_plan(plan),
        plan=plan,
    )
    return PlanCreateResponse(plan=record.to_response(), job_id=job.id)


@router.get("", response_model=PlanListResponse)
async def list_plans(
    project_id: UUID,
    actor: ActorDep,
    repository: PlanRepositoryDep,
) -> PlanListResponse:
    plans = repository.list_plans(project_id=project_id, workspace_id=actor.workspace_id)
    active = next((plan for plan in plans if plan.is_active), None)
    return PlanListResponse(
        plans=[plan.to_response() for plan in plans],
        active_plan_id=active.id if active else None,
    )


@router.post("/{plan_id}/activate", response_model=PlanActivateResponse)
async def activate_plan(
    project_id: UUID,
    plan_id: UUID,
    actor: ActorDep,
    repository: PlanRepositoryDep,
) -> PlanActivateResponse:
    plan = repository.activate_plan(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
    )
    return PlanActivateResponse(plan=plan.to_response())


@router.patch("/{plan_id}/cuts", response_model=PlanCutsUpdateResponse)
async def update_plan_cuts(
    project_id: UUID,
    plan_id: UUID,
    payload: PlanCutsUpdateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
) -> PlanCutsUpdateResponse:
    record = repository.get_plan(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
    )
    target_duration_s = max(
        10,
        round(sum(cut.end_ms - cut.start_ms for cut in payload.cuts) / 1000),
    )
    plan = record.plan.model_copy(
        update={
            "cuts": payload.cuts,
            "target_duration_s": target_duration_s,
        }
    )
    updated = repository.update_plan_cuts(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
        plan=plan,
        summary=summarize_plan(plan),
        target_duration_s=target_duration_s,
    )
    return PlanCutsUpdateResponse(plan=updated.to_response())


@router.patch("/{plan_id}/caption-highlights", response_model=PlanCaptionHighlightsUpdateResponse)
async def update_plan_caption_highlights(
    project_id: UUID,
    plan_id: UUID,
    payload: PlanCaptionHighlightsUpdateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
) -> PlanCaptionHighlightsUpdateResponse:
    record = repository.get_plan(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
    )
    plan = record.plan.model_copy(
        update={"caption_highlights": payload.caption_highlights},
    )
    updated = repository.update_plan_cuts(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
        plan=plan,
        summary=summarize_plan(plan),
        target_duration_s=record.target_duration_s,
    )
    return PlanCaptionHighlightsUpdateResponse(plan=updated.to_response())
