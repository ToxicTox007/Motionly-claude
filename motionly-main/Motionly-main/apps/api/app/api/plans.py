from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.dependencies import (
    Actor,
    get_actor,
    get_edit_planner_provider,
    get_plan_queue,
    get_plan_repository,
    get_proposal_repository,
)
from app.api.errors import ApiError
from app.core.logging import get_logger
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.ai_proposals import ProposalState
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer
from app.schemas.captions import TranscriptStatus
from app.schemas.plans import (
    EDIT_PLAN_PROMPT_VERSION,
    BrollDensity,
    EditPlanStyle,
    PlanCaptionHighlightsUpdateRequest,
    PlanCaptionHighlightsUpdateResponse,
    PlanCreateRequest,
    PlanCreateResponse,
    PlanCutsUpdateRequest,
    PlanCutsUpdateResponse,
    PlanGetResponse,
    PlanJobStatusResponse,
    PlanListResponse,
    PlanManualElementsUpdateRequest,
    PlanManualElementsUpdateResponse,
    PlanSnapshotCreateRequest,
    PlanSnapshotCreateResponse,
    PlanSnapshotListResponse,
    PlanSnapshotRestoreResponse,
    TimelineUpdateResponse,
)
from app.schemas.scale import ScaleResourceStatus
from app.schemas.visual import VideoManifest
from app.services.ai_feedback import record_ai_feedback
from app.services.ai_proposals import (
    build_multimodal_context,
    request_identity,
)
from app.services.edit_planner import (
    EditPlannerProvider,
    summarize_plan,
)
from app.services.layer_job_enqueue import prepare_heavy_job_enqueue
from app.services.offload import run_blocking
from app.services.queue import PlanQueue
from app.services.timeline_compiler import (
    compile_timeline,
    project_timeline_to_legacy_plan,
)
from app.services.transcript_quality import is_transcript_usable_for_planning

router = APIRouter(prefix="/projects/{project_id}/plans", tags=["AI plans"])

ActorDep = Annotated[Actor, Depends(get_actor)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]
ProposalRepositoryDep = Annotated[object, Depends(get_proposal_repository)]
EditPlannerProviderDep = Annotated[EditPlannerProvider, Depends(get_edit_planner_provider)]
PlanQueueDep = Annotated[PlanQueue, Depends(get_plan_queue)]

logger = get_logger(__name__)


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


async def _get_project_for_workspace(repository, *, project_id: UUID, workspace_id: UUID) -> None:
    await _repository_call(
        repository,
        "get_project_for_workspace_async",
        "get_project_for_workspace",
        project_id=project_id,
        workspace_id=workspace_id,
    )


async def _get_active_upload(repository, *, project_id: UUID, workspace_id: UUID):
    return await _repository_call(
        repository,
        "get_active_upload_async",
        "get_active_upload",
        project_id=project_id,
        workspace_id=workspace_id,
    )


async def _get_plan(repository, *, project_id: UUID, workspace_id: UUID, plan_id: UUID):
    return await _repository_call(
        repository,
        "get_plan_async",
        "get_plan",
        project_id=project_id,
        workspace_id=workspace_id,
        plan_id=plan_id,
    )


async def _update_plan_cuts(
    repository,
    *,
    project_id: UUID,
    workspace_id: UUID,
    plan_id: UUID,
    plan,
    summary,
    target_duration_s: int,
):
    return await _repository_call(
        repository,
        "update_plan_cuts_async",
        "update_plan_cuts",
        project_id=project_id,
        workspace_id=workspace_id,
        plan_id=plan_id,
        plan=plan,
        summary=summary,
        target_duration_s=target_duration_s,
    )


async def _update_plan_with_revision(
    repository,
    *,
    plan_id: UUID,
    expected_revision: int,
    new_plan,
    project_id: UUID,
    workspace_id: UUID,
    summary,
    target_duration_s: int,
    change_type: str = "manual_edit",
    request: Request | None = None,
):
    """Revision-enforced plan update. Raises RevisionConflictError on mismatch.

    On success, best-effort broadcasts ``plan_revision_updated`` on the project's
    private live channel (Task 26) so other editors reload only the plan panel
    and advance their tracked revision. The message carries ids/revision only.
    """
    updated = await _repository_call(
        repository,
        "update_plan_with_revision_async",
        "update_plan_with_revision",
        plan_id=plan_id,
        expected_revision=expected_revision,
        new_plan=new_plan,
        project_id=project_id,
        workspace_id=workspace_id,
        summary=summary,
        target_duration_s=target_duration_s,
        change_type=change_type,
    )
    _notify_plan_revision(
        request,
        project_id=project_id,
        workspace_id=workspace_id,
        plan_id=plan_id,
        revision=getattr(updated, "revision", None),
    )
    return updated


def _notify_plan_revision(
    request: Request | None,
    *,
    project_id: UUID,
    workspace_id: UUID,
    plan_id: UUID,
    revision: int | None,
) -> None:
    """Emit the collaboration broadcast if a notifier is wired and we have a
    revision. Never raises: a missed broadcast must not fail the save."""
    if request is None or revision is None:
        return
    notifier = getattr(request.app.state, "collaboration_notifier", None)
    if notifier is None:
        return
    notifier.notify_plan_revision_updated(
        project_id=project_id,
        workspace_id=workspace_id,
        plan_id=plan_id,
        revision=revision,
    )


@router.post("", response_model=PlanCreateResponse, status_code=201)
async def create_plan(
    project_id: UUID,
    payload: PlanCreateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    planner_provider: EditPlannerProviderDep,
    plan_queue: PlanQueueDep,
    proposal_repository: ProposalRepositoryDep = None,  # type: ignore[assignment]
    response: Response = None,  # type: ignore[assignment]
    request: Request = None,  # type: ignore[assignment]
) -> PlanCreateResponse:
    await _get_project_for_workspace(
        repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    payload = _apply_phase5_learning(
        payload=payload,
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
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
    if not transcript.words or not is_transcript_usable_for_planning(transcript.quality_status):
        reason = (
            transcript.quality_reasons[0]
            if transcript.quality_reasons
            else "transcript_quality_blocked"
        )
        raise ApiError(
            409,
            reason,
            transcript.error_message
            or "Transcript quality must be recovered before edit planning.",
        )

    if proposal_repository is None:
        state = getattr(getattr(request, "app", None), "state", None)
        proposal_repository = getattr(state, "proposal_repository", None)
    if proposal_repository is None:
        from app.repositories.ai_proposals import InMemoryAIProposalRepository

        proposal_repository = getattr(repository, "_proposal_repository", None)
        if proposal_repository is None:
            proposal_repository = InMemoryAIProposalRepository(plan_repository=repository)
            repository._proposal_repository = proposal_repository

    context = build_multimodal_context(
        repository=repository,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        payload=payload,
    )
    caller_key = payload.idempotency_key
    if payload.force and caller_key is None:
        caller_key = f"force-{uuid4()}"
    request_id = request_identity(context=context, idempotency_key=caller_key)
    proposal, deduplicated = proposal_repository.create_queued(
        context=context,
        request_id=request_id,
        created_by=actor.user_id,
        prompt_version=EDIT_PLAN_PROMPT_VERSION,
    )

    import json

    serialized_params = json.dumps(
        {
            "proposal_id": str(proposal.id),
            "request_id": proposal.request_id,
            "context_digest": proposal.context_digest,
            "user_id": str(actor.user_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    prepare_heavy_job_enqueue(request=request)
    job = await run_blocking(
        plan_queue.enqueue_plan,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        transcript_id=transcript.id,
        proposal_id=proposal.id,
        request_id=proposal.request_id,
        context_digest=proposal.context_digest,
        render_engine=serialized_params,
    )

    if response is not None:
        response.status_code = status.HTTP_202_ACCEPTED
    status_url = f"/projects/{project_id}/plans/jobs/{job.id}"
    proposal_url = f"/projects/{project_id}/proposals/{proposal.id}"
    return PlanCreateResponse(
        proposal_id=proposal.id,
        proposal_revision=proposal.revision,
        job_id=job.id,
        status=proposal.state.value,
        cached=deduplicated,
        project_id=project_id,
        status_url=status_url,
        proposal_url=proposal_url,
    )


@router.get("/jobs/{job_id}", response_model=PlanJobStatusResponse)
async def get_plan_job_status(
    project_id: UUID,
    job_id: str,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    request: Request,
) -> PlanJobStatusResponse:
    await _get_project_for_workspace(
        repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    queue_registry = request.app.state.queue_registry
    job = await run_blocking(queue_registry.get_job, job_id, queue_name="planning")
    if job is not None and (
        job.kind != "edit_plan"
        or job.workspace_id != actor.workspace_id
        or job.project_id != project_id
    ):
        job = None

    proposal_repository = getattr(request.app.state, "proposal_repository", None) or getattr(
        repository, "_proposal_repository", None
    )
    if job is not None and (job.resource_id is None or proposal_repository is None):
        from app.services.queue import QueueJobStatus

        if job.status == QueueJobStatus.RUNNING:
            return PlanJobStatusResponse(status="running", progress=50)
        if job.status == QueueJobStatus.QUEUED:
            return PlanJobStatusResponse(status="queued", progress=0)
        if job.status == QueueJobStatus.SUCCEEDED:
            return PlanJobStatusResponse(status="succeeded", progress=100)
        return PlanJobStatusResponse(
            status="failed", progress=0, failure_reason="Job failed in queue worker"
        )

    proposal_id = job.resource_id if job is not None else None
    if proposal_id is None:
        try:
            parts = job_id.split("--")
            if len(parts) >= 3 and parts[0] == "plan":
                proposal_id = UUID(parts[2])
        except (ValueError, TypeError):
            proposal_id = None
    if proposal_id is None:
        return PlanJobStatusResponse(status="failed", progress=0, failure_reason="Job not found")
    try:
        proposal = proposal_repository.get(
            proposal_id=proposal_id,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    except ApiError as exc:
        if exc.code == "proposal_not_found":
            return PlanJobStatusResponse(
                status="failed", progress=0, failure_reason="Job not found"
            )
        raise

    from app.services.queue import QueueJobStatus

    if proposal.state == ProposalState.QUEUED and job is not None:
        if job.status == QueueJobStatus.RUNNING:
            return PlanJobStatusResponse(
                status="running",
                progress=50,
                proposal_id=proposal.id,
                proposal_revision=proposal.revision,
            )
        if job.status not in {QueueJobStatus.QUEUED, QueueJobStatus.RUNNING}:
            return PlanJobStatusResponse(
                status="failed",
                progress=0,
                proposal_id=proposal.id,
                proposal_revision=proposal.revision,
                failure_reason=job.error_code or "Job failed in queue worker",
            )

    if proposal.state == ProposalState.AWAITING_APPROVAL:
        return PlanJobStatusResponse(
            status=proposal.state.value,
            progress=100,
            proposal_id=proposal.id,
            proposal_revision=proposal.revision,
        )
    if proposal.state in {
        ProposalState.APPROVED,
        ProposalState.PARTIALLY_APPROVED,
        ProposalState.REJECTED,
    }:
        return PlanJobStatusResponse(
            status=proposal.state.value,
            progress=100,
            proposal_id=proposal.id,
            proposal_revision=proposal.revision,
        )
    if proposal.state == ProposalState.FAILED:
        return PlanJobStatusResponse(
            status="failed",
            progress=0,
            proposal_id=proposal.id,
            proposal_revision=proposal.revision,
            failure_reason=proposal.failure_code or "Proposal generation failed",
        )
    return PlanJobStatusResponse(
        status=proposal.state.value,
        progress=50 if proposal.state == ProposalState.GENERATING else 0,
        proposal_id=proposal.id,
        proposal_revision=proposal.revision,
    )


def _apply_phase5_learning(
    *,
    payload: PlanCreateRequest,
    request: Request | None,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID | None = None,
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
            raise ApiError(409, "style_profile_not_ready", "Analyze the reference style first.")
        learned = profile.result.get("learned_settings") or {}
        if isinstance(learned, dict):
            if learned.get("style"):
                updates["style"] = EditPlanStyle(str(learned["style"]))
            if learned.get("target_duration_s"):
                updates["target_duration_s"] = max(10, min(90, int(learned["target_duration_s"])))
            if learned.get("broll_density"):
                updates["broll_density"] = BrollDensity(str(learned["broll_density"]))
            direction_parts.append(
                "Reference style profile: " + str(learned.get("caption_direction") or learned)
            )
    learning_repository = getattr(state, "editorial_learning_repository", None)
    settings = getattr(state, "settings", None)
    if (
        user_id is not None
        and learning_repository is not None
        and getattr(settings, "editorial_learning_enabled", False)
    ):
        # Only a per-user, explicitly promoted deployment may influence a new
        # plan. Missing policy, revoked data, or repository failures fail closed.
        try:
            preferences = learning_repository.resolve_active_preferences(
                workspace_id=workspace_id,
                project_id=project_id,
                user_id=user_id,
            )
        except Exception:
            preferences = None
        if preferences is not None:
            from app.services.editorial_memory import preferences_direction_text

            direction_parts.append(preferences_direction_text(preferences))
    if direction_parts:
        updates["prompt"] = "\n".join(direction_parts)[:1200]
    return payload.model_copy(update=updates)


@router.get("", response_model=PlanListResponse)
async def list_plans(
    project_id: UUID,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> PlanListResponse:
    plans = await _repository_call(
        repository,
        "list_plans_async",
        "list_plans",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
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
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> PlanSnapshotListResponse:
    snapshots = await _repository_call(
        repository,
        "list_plan_snapshots_async",
        "list_plan_snapshots",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return PlanSnapshotListResponse(
        snapshots=[snapshot.to_response() for snapshot in snapshots],
    )


@router.get("/{plan_id}", response_model=PlanGetResponse)
async def get_plan(
    project_id: UUID,
    plan_id: UUID,
    actor: ActorDep,
    repository: PlanRepositoryDep,
) -> PlanGetResponse:
    plan = await _get_plan(
        repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
    )
    return PlanGetResponse(plan=plan.to_response())


@router.post("/snapshots", response_model=PlanSnapshotCreateResponse, status_code=201)
async def create_plan_snapshot(
    project_id: UUID,
    payload: PlanSnapshotCreateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
) -> PlanSnapshotCreateResponse:
    source_plan = (
        await _get_plan(
            repository,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=payload.plan_id,
        )
        if payload.plan_id
        else await _repository_call(
            repository,
            "active_plan_async",
            "active_plan",
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    )
    snapshot = await _repository_call(
        repository,
        "create_plan_snapshot_async",
        "create_plan_snapshot",
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
    plan = await _repository_call(
        repository,
        "restore_plan_snapshot_async",
        "restore_plan_snapshot",
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


@router.patch("/{plan_id}/cuts", response_model=PlanCutsUpdateResponse)
async def update_plan_cuts(
    project_id: UUID,
    plan_id: UUID,
    payload: PlanCutsUpdateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> PlanCutsUpdateResponse:
    record = await _get_plan(
        repository,
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
    plan = project_timeline_to_legacy_plan(
        plan=plan,
        timeline=compile_timeline(
            plan=plan,
            source_video_id=(
                await _get_active_upload(
                    repository,
                    project_id=project_id,
                    workspace_id=actor.workspace_id,
                )
            ).id,
            source_duration_ms=(
                (
                    await _get_active_upload(
                        repository,
                        project_id=project_id,
                        workspace_id=actor.workspace_id,
                    )
                ).metadata.duration_ms
                or target_duration_s * 1000
            ),
            preserve_layers_from=record.plan.timeline,
        ),
    )
    summary = summarize_plan(
        plan,
        style_classification=record.summary.style_classification,
    )
    if payload.expected_revision is not None:
        updated = await _update_plan_with_revision(
            repository,
            plan_id=plan_id,
            expected_revision=payload.expected_revision,
            new_plan=plan,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            summary=summary,
            target_duration_s=target_duration_s,
            change_type="cuts_update",
            request=request,
        )
    else:
        logger.warning(
            "plan_mutation_without_revision",
            extra={"plan_id": str(plan_id), "endpoint": "cuts", "project_id": str(project_id)},
        )
        updated = await _update_plan_cuts(
            repository,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=plan_id,
            plan=plan,
            summary=summary,
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
    return PlanCutsUpdateResponse(plan=updated.to_response(), revision=updated.revision)


@router.patch("/{plan_id}/caption-highlights", response_model=PlanCaptionHighlightsUpdateResponse)
async def update_plan_caption_highlights(
    project_id: UUID,
    plan_id: UUID,
    payload: PlanCaptionHighlightsUpdateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> PlanCaptionHighlightsUpdateResponse:
    record = await _get_plan(
        repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
    )
    plan = record.plan.model_copy(
        update={"caption_highlights": payload.caption_highlights},
    )
    summary = summarize_plan(
        plan,
        style_classification=record.summary.style_classification,
    )
    if payload.expected_revision is not None:
        updated = await _update_plan_with_revision(
            repository,
            plan_id=plan_id,
            expected_revision=payload.expected_revision,
            new_plan=plan,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            summary=summary,
            target_duration_s=record.target_duration_s,
            change_type="caption_highlights_update",
            request=request,
        )
    else:
        logger.warning(
            "plan_mutation_without_revision",
            extra={
                "plan_id": str(plan_id),
                "endpoint": "caption_highlights",
                "project_id": str(project_id),
            },
        )
        updated = await _update_plan_cuts(
            repository,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=plan_id,
            plan=plan,
            summary=summary,
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
    return PlanCaptionHighlightsUpdateResponse(
        plan=updated.to_response(), revision=updated.revision
    )


@router.patch("/{plan_id}/manual-elements", response_model=PlanManualElementsUpdateResponse)
async def update_plan_manual_elements(
    project_id: UUID,
    plan_id: UUID,
    payload: PlanManualElementsUpdateRequest,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> PlanManualElementsUpdateResponse:
    record = await _get_plan(
        repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
    )
    plan = record.plan.model_copy(update={"manual_elements": payload.manual_elements})
    upload = await _get_active_upload(
        repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    plan = project_timeline_to_legacy_plan(
        plan=plan,
        timeline=compile_timeline(
            plan=plan,
            source_video_id=upload.id,
            source_duration_ms=upload.metadata.duration_ms or record.target_duration_s * 1000,
            preserve_layers_from=record.plan.timeline,
        ),
    )
    summary = summarize_plan(
        plan,
        style_classification=record.summary.style_classification,
    )
    if payload.expected_revision is not None:
        updated = await _update_plan_with_revision(
            repository,
            plan_id=plan_id,
            expected_revision=payload.expected_revision,
            new_plan=plan,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            summary=summary,
            target_duration_s=record.target_duration_s,
            change_type="manual_elements_update",
            request=request,
        )
    else:
        logger.warning(
            "plan_mutation_without_revision",
            extra={
                "plan_id": str(plan_id),
                "endpoint": "manual_elements",
                "project_id": str(project_id),
            },
        )
        updated = await _update_plan_cuts(
            repository,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=plan_id,
            plan=plan,
            summary=summary,
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
                element.model_dump(mode="json") for element in record.plan.manual_elements
            ],
        },
        user_payload={
            "manual_elements": [
                element.model_dump(mode="json") for element in payload.manual_elements
            ],
        },
        metadata={"previous_version": record.version},
    )
    return PlanManualElementsUpdateResponse(plan=updated.to_response(), revision=updated.revision)


@router.post("/{plan_id}/compile-timeline", response_model=TimelineUpdateResponse)
async def compile_project_timeline(
    project_id: UUID,
    plan_id: UUID,
    actor: ActorDep,
    repository: PlanRepositoryDep,
    request: Request,
    expected_revision: Annotated[int | None, Query()] = None,
) -> TimelineUpdateResponse:
    record = await _get_plan(
        repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=plan_id,
    )
    upload = await _get_active_upload(
        repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    caption_tracks = repository.caption_repository.list_caption_tracks(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    caption_track = caption_tracks[0] if caption_tracks else None
    broll_layers: list[dict[str, object]] = []
    broll_repository = getattr(request.app.state, "broll_repository", None)
    if broll_repository is not None:
        for slot in broll_repository.list_slots(
            project_id=project_id,
            workspace_id=actor.workspace_id,
        ):
            if str(slot.status) == "disabled" or not slot.selected_asset_id:
                continue
            asset = broll_repository.get_asset(
                project_id=project_id,
                workspace_id=actor.workspace_id,
                asset_id=slot.selected_asset_id,
            )
            broll_layers.append(
                {
                    "id": str(slot.id),
                    "start_ms": slot.start_ms,
                    "end_ms": slot.end_ms,
                    "src": asset.download_url or asset.preview_video_url or "",
                    "selected_asset_id": str(asset.id),
                    "keywords": slot.keywords,
                }
            )
    music = None
    music_repository = getattr(request.app.state, "music_repository", None)
    if music_repository is not None:
        selection = music_repository.latest_selection(
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        if selection is not None:
            music = selection.to_response().model_dump(mode="json")
    effects = None
    effects_repository = getattr(request.app.state, "effects_repository", None)
    if effects_repository is not None:
        config = effects_repository.latest_config(
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        if config is not None:
            effects = config.to_response().model_dump(mode="json")
    timeline = compile_timeline(
        plan=record.plan,
        source_video_id=upload.id,
        source_duration_ms=upload.metadata.duration_ms or record.target_duration_s * 1000,
        captions=caption_track.segments if caption_track else [],
        caption_style=(caption_track.style.model_dump(mode="json") if caption_track else {}),
        broll=broll_layers,
        music=music,
        effects_config=effects,
        preserve_layers_from=record.plan.timeline,
    )
    plan = project_timeline_to_legacy_plan(plan=record.plan, timeline=timeline)
    summary = summarize_plan(
        plan,
        style_classification=record.summary.style_classification,
    )
    if expected_revision is not None:
        updated = await _update_plan_with_revision(
            repository,
            plan_id=plan_id,
            expected_revision=expected_revision,
            new_plan=plan,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            summary=summary,
            target_duration_s=plan.target_duration_s,
            change_type="compile_timeline",
            request=request,
        )
    else:
        logger.warning(
            "plan_mutation_without_revision",
            extra={
                "plan_id": str(plan_id),
                "endpoint": "compile_timeline",
                "project_id": str(project_id),
            },
        )
        updated = await _update_plan_cuts(
            repository,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=plan_id,
            plan=plan,
            summary=summary,
            target_duration_s=plan.target_duration_s,
        )
    return TimelineUpdateResponse(plan=updated.to_response(), revision=updated.revision)


def _visual_context(
    *,
    repository: InMemoryPlanRepository,
    project_id: UUID,
    workspace_id: UUID,
    uploaded_video_id: UUID,
) -> tuple[VideoManifest | None, str | None]:
    visual_repository = getattr(repository, "visual_repository", None)
    if visual_repository is None:
        return None, "visual_context_unavailable"
    latest = visual_repository.latest_for_upload(
        uploaded_video_id=uploaded_video_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    record = visual_repository.latest_ready_for_upload(
        uploaded_video_id=uploaded_video_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    if record and record.manifest:
        return record.manifest, None
    status = latest.status.value if latest else "unavailable"
    return None, f"visual_context_{status}"
