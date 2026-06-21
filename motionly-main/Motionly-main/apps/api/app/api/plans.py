from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    Actor,
    get_actor,
    get_edit_planner_provider,
    get_plan_queue,
    get_plan_repository,
)
from app.api.errors import ApiError
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer
from app.schemas.captions import TranscriptStatus
from app.schemas.plans import (
    EDIT_PLAN_SCHEMA_VERSION,
    BrollDensity,
    EditPlanStatus,
    EditPlanStyle,
    PlanActivateResponse,
    PlanCaptionHighlightsUpdateRequest,
    PlanCaptionHighlightsUpdateResponse,
    PlanCreateRequest,
    PlanCreateResponse,
    PlanCutsUpdateRequest,
    PlanCutsUpdateResponse,
    PlanListResponse,
    PlanManualElementsUpdateRequest,
    PlanManualElementsUpdateResponse,
    PlanSnapshotCreateRequest,
    PlanSnapshotCreateResponse,
    PlanSnapshotListResponse,
    PlanSnapshotRestoreResponse,
)
from app.schemas.scale import ScaleResourceStatus
from app.schemas.visual import VideoManifest
from app.services.ai_feedback import record_ai_feedback, record_plan_suggestions
from app.services.edit_planner import (
    PLANNER_SCHEMA_HASH,
    EditPlannerProvider,
    build_plan_cache_key,
    normalize_plan,
    summarize_plan,
)
from app.services.queue import PlanQueue
from app.services.style_classifier import classify_edit_style

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
    request: Request = None,  # type: ignore[assignment]
) -> PlanCreateResponse:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    payload = _apply_phase5_learning(
        payload=payload,
        request=request,
        workspace_id=actor.workspace_id,
    )
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
    visual_manifest = _latest_visual_manifest(
        repository=repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        uploaded_video_id=upload.id,
    )
    style_classification = classify_edit_style(
        transcript=transcript,
        visual_manifest=visual_manifest,
        requested_style=payload.style,
        prompt=payload.prompt,
        feedback=payload.feedback,
    )
    effective_payload = payload.model_copy(
        update={"style": style_classification.effective_style},
    )
    cache_key = build_plan_cache_key(
        transcript=transcript,
        payload=effective_payload,
        visual_manifest=visual_manifest,
        style_classification=style_classification,
    )
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
        payload=effective_payload,
        video_duration_ms=video_duration_ms,
        visual_manifest=visual_manifest,
        style_classification=style_classification,
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
        style=style_classification.effective_style,
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
        summary=summarize_plan(plan, style_classification=style_classification),
        plan=plan,
    )
    record_plan_suggestions(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        plan=record,
    )
    return PlanCreateResponse(plan=record.to_response(), job_id=job.id)


def _apply_phase5_learning(
    *,
    payload: PlanCreateRequest,
    request: Request | None,
    workspace_id: UUID,
) -> PlanCreateRequest:
    state = getattr(getattr(request, "app", None), "state", None)
    scale_repository = getattr(state, "scale_repository", None)
    if scale_repository is None:
        return payload
    updates: dict[str, object] = {}
    direction_parts = [payload.prompt.strip()] if payload.prompt.strip() else []
    if payload.saved_template_id:
        template = scale_repository.get(
            resource_id=payload.saved_template_id,
            workspace_id=workspace_id,
        )
        if template.kind != "saved_template":
            raise ApiError(409, "saved_template_required", "Selected resource is not a template.")
        settings = template.payload.get("settings") or {}
        if isinstance(settings, dict):
            if not payload.style and settings.get("style"):
                updates["style"] = EditPlanStyle(str(settings["style"]))
            if settings.get("target_duration_s"):
                updates["target_duration_s"] = int(settings["target_duration_s"])
            if settings.get("broll_density"):
                updates["broll_density"] = BrollDensity(str(settings["broll_density"]))
            if settings.get("direction"):
                direction_parts.append(str(settings["direction"]))
    if payload.style_profile_id:
        profile = scale_repository.get(
            resource_id=payload.style_profile_id,
            workspace_id=workspace_id,
        )
        if profile.kind != "style_profile" or profile.status != ScaleResourceStatus.READY:
            raise ApiError(409, "style_profile_not_ready", "Train the custom style first.")
        learned = profile.result.get("learned_settings") or {}
        if isinstance(learned, dict):
            if learned.get("style"):
                updates["style"] = EditPlanStyle(str(learned["style"]))
            if learned.get("target_duration_s"):
                updates["target_duration_s"] = max(10, min(90, int(learned["target_duration_s"])))
            if learned.get("broll_density"):
                updates["broll_density"] = BrollDensity(str(learned["broll_density"]))
            direction_parts.append(
                "Custom style profile: " + str(learned.get("caption_direction") or learned)
            )
    adapter = scale_repository.latest(
        workspace_id=workspace_id,
        kind="model_training",
        status=ScaleResourceStatus.READY,
    )
    if adapter and adapter.result.get("preferences"):
        direction_parts.append(
            "Workspace learned preferences: " + str(adapter.result["preferences"])
        )
    if direction_parts:
        updates["prompt"] = "\n".join(direction_parts)[:1200]
    return payload.model_copy(update=updates)


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


@router.get("/snapshots", response_model=PlanSnapshotListResponse)
async def list_plan_snapshots(
    project_id: UUID,
    actor: ActorDep,
    repository: PlanRepositoryDep,
) -> PlanSnapshotListResponse:
    snapshots = repository.list_plan_snapshots(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    return PlanSnapshotListResponse(
        snapshots=[snapshot.to_response() for snapshot in snapshots],
    )


@router.post("/snapshots", response_model=PlanSnapshotCreateResponse, status_code=201)
async def create_plan_snapshot(
    project_id: UUID,
    payload: PlanSnapshotCreateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
) -> PlanSnapshotCreateResponse:
    source_plan = (
        repository.get_plan(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=payload.plan_id,
        )
        if payload.plan_id
        else repository.active_plan(project_id=project_id, workspace_id=actor.workspace_id)
    )
    snapshot = repository.create_plan_snapshot(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        source_plan=source_plan,
        name=payload.name,
        notes=payload.notes,
        created_by_user_id=actor.user_id,
    )
    return PlanSnapshotCreateResponse(snapshot=snapshot.to_response())


@router.post(
    "/snapshots/{snapshot_id}/restore",
    response_model=PlanSnapshotRestoreResponse,
    status_code=201,
)
async def restore_plan_snapshot(
    project_id: UUID,
    snapshot_id: UUID,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> PlanSnapshotRestoreResponse:
    plan = repository.restore_plan_snapshot(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        snapshot_id=snapshot_id,
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=plan.id,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.CUTS,
        source="version_history",
        decision_id=f"{plan.id}:snapshot_restore",
        action="restore_snapshot",
        user_payload={
            "snapshot_id": str(snapshot_id),
            "cuts": [cut.model_dump(mode="json") for cut in plan.plan.cuts],
        },
        metadata={"plan_version": plan.version},
    )
    return PlanSnapshotRestoreResponse(
        plan=plan.to_response(),
        snapshot_id=snapshot_id,
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
    request: Request = None,  # type: ignore[assignment]
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
        summary=summarize_plan(
            plan,
            style_classification=record.summary.style_classification,
        ),
        target_duration_s=target_duration_s,
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=plan_id,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.CUTS,
        source="manual_editor",
        decision_id=f"{plan_id}:cuts",
        action="update_cuts",
        ai_payload={"cuts": [cut.model_dump(mode="json") for cut in record.plan.cuts]},
        user_payload={"cuts": [cut.model_dump(mode="json") for cut in payload.cuts]},
        metadata={"previous_version": record.version, "target_duration_s": target_duration_s},
    )
    return PlanCutsUpdateResponse(plan=updated.to_response())


@router.patch("/{plan_id}/caption-highlights", response_model=PlanCaptionHighlightsUpdateResponse)
async def update_plan_caption_highlights(
    project_id: UUID,
    plan_id: UUID,
    payload: PlanCaptionHighlightsUpdateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
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
        summary=summarize_plan(
            plan,
            style_classification=record.summary.style_classification,
        ),
        target_duration_s=record.target_duration_s,
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=plan_id,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.CAPTIONS,
        source="manual_editor",
        decision_id=f"{plan_id}:caption_highlights",
        action="update_caption_highlights",
        ai_payload={
            "caption_highlights": [
                highlight.model_dump(mode="json") for highlight in record.plan.caption_highlights
            ],
        },
        user_payload={
            "caption_highlights": [
                highlight.model_dump(mode="json") for highlight in payload.caption_highlights
            ],
        },
        metadata={"previous_version": record.version},
    )
    return PlanCaptionHighlightsUpdateResponse(plan=updated.to_response())


@router.patch("/{plan_id}/manual-elements", response_model=PlanManualElementsUpdateResponse)
async def update_plan_manual_elements(
    project_id: UUID,
    plan_id: UUID,
    payload: PlanManualElementsUpdateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> PlanManualElementsUpdateResponse:
    record = repository.get_plan(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
    )
    plan = record.plan.model_copy(update={"manual_elements": payload.manual_elements})
    updated = repository.update_plan_cuts(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
        plan=plan,
        summary=summarize_plan(
            plan,
            style_classification=record.summary.style_classification,
        ),
        target_duration_s=record.target_duration_s,
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=plan_id,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.EFFECTS,
        source="manual_editor",
        decision_id=f"{plan_id}:manual_elements",
        action="update_manual_elements",
        ai_payload={
            "manual_elements": [
                item.model_dump(mode="json") for item in record.plan.manual_elements
            ]
        },
        user_payload={
            "manual_elements": [item.model_dump(mode="json") for item in payload.manual_elements]
        },
        metadata={"previous_version": record.version},
    )
    return PlanManualElementsUpdateResponse(plan=updated.to_response())


def _latest_visual_manifest(
    *,
    repository: InMemoryPlanRepository,
    project_id: UUID,
    workspace_id: UUID,
    uploaded_video_id: UUID,
) -> VideoManifest | None:
    visual_repository = getattr(repository, "visual_repository", None)
    if visual_repository is None:
        return None
    record = visual_repository.latest_ready_for_upload(
        uploaded_video_id=uploaded_video_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    return record.manifest if record and record.manifest else None
