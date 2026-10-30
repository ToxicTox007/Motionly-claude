import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.billing import enforce_render_entitlement
from app.api.dependencies import (
    Actor,
    get_actor,
    get_billing_repository,
    get_broll_repository,
    get_plan_repository,
    get_render_queue,
    get_render_repository,
    get_storage_signer,
)
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.renders import InMemoryRenderRepository, RenderJobRecord
from app.schemas.broll import BrollSlotStatus
from app.schemas.captions import CaptionPresetKey, CaptionSegment, TranscriptWord
from app.schemas.plans import EditPlan
from app.schemas.renders import (
    CutSegment,
    DurationPreset,
    ExportDownloadUrlResponse,
    ExportListResponse,
    ExportRestoreResponse,
    RenderAdjustmentAction,
    RenderBulkCreateRequest,
    RenderBulkCreateResponse,
    RenderCreateRequest,
    RenderCreateResponse,
    RenderEngine,
    RenderJobResponse,
    RenderJobStatus,
    RenderListResponse,
)
from app.schemas.scale import ScaleResourceStatus
from app.schemas.uploads import UploadStatus
from app.services.ass_captions import caption_track_to_ass
from app.services.caption_presets import get_caption_preset
from app.services.cut_planning import PLANNER_VERSION, build_crop_warnings, plan_keep_segments
from app.services.edit_plan_validation import (
    EditPlanValidationContext,
    EditPlanValidationFailure,
    validate_edit_plan_for_render,
)
from app.services.export_download import export_download_filename
from app.services.matte_jobs import matte_availability
from app.services.media_url_policy import MediaUrlPolicyError, validate_render_media_manifest
from app.services.offload import run_blocking
from app.services.queue import RenderQueue
from app.services.render_templates import (
    build_props_hash,
    cap_keep_segments_to_duration,
    effect_compatibility,
    export_settings,
    render_specs_for_options,
    select_template,
)
from app.services.storage import StorageSigner
from app.services.timeline_compiler import compile_timeline
from app.services.upload_validation import assert_video_render_ready
from app.services.visual_tracking import subject_track_from_manifest

router = APIRouter(tags=["render and exports"])

ActorDep = Annotated[Actor, Depends(get_actor)]
RenderRepositoryDep = Annotated[InMemoryRenderRepository, Depends(get_render_repository)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]
BrollRepositoryDep = Annotated[InMemoryBrollRepository, Depends(get_broll_repository)]
RenderQueueDep = Annotated[RenderQueue, Depends(get_render_queue)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]

_RENDERER_VERSIONS: dict[RenderEngine, str] = {
    RenderEngine.FFMPEG_BASIC: "ffmpeg-timeline-v3",
    RenderEngine.REMOTION_PREMIUM: "remotion-timeline-v2",
}


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


async def _enqueue_render_or_fail(
    repository,
    job: RenderJobRecord,
    *,
    render_queue: RenderQueue,
    project_id: UUID,
    workspace_id: UUID,
    priority: int,
    render_engine: str,
    compute_tier: str,
):
    """Queue a committed render job, or fail it so it never sits QUEUED forever.

    The render row (and its billing slot) is written before the BullMQ enqueue.
    If the enqueue fails (bridge timeout, Redis outage, backpressure), the row is
    moved to FAILED, which also releases the slot and lets the user retry it,
    instead of an orphaned QUEUED render that duplicate detection keeps
    returning (audit WJ-13).
    """
    try:
        return await run_blocking(
            render_queue.enqueue_render,
            render_job_id=job.id,
            project_id=project_id,
            workspace_id=workspace_id,
            priority=priority,
            render_engine=render_engine,
            compute_tier=compute_tier,
        )
    except Exception as exc:
        code = exc.code if isinstance(exc, ApiError) else "queue_enqueue_failed"
        try:
            await _repository_call(
                repository,
                "transition_job_async",
                "transition_job",
                job=job,
                status=RenderJobStatus.FAILED,
                progress_pct=job.progress_pct,
                message="Render could not be queued.",
                event_type="failed",
                error_code=code,
                error_message="The render could not be queued. Retry it to try again.",
                require_active=True,
            )
        except ApiError:
            # A concurrent transition (cancel) already moved it out of QUEUED.
            pass
        if isinstance(exc, ApiError):
            raise
        raise ApiError(
            503,
            "render_queue_unavailable",
            "The render could not be queued. Retry it to try again.",
        ) from exc


async def _events_for_job(repository, render_job_id: UUID, *, limit: int = 100):
    async_method = getattr(repository, "events_for_job_async", None)
    if async_method is not None:
        return await async_method(render_job_id, limit=limit)
    return repository.events_for_job(render_job_id, limit=limit)


async def _get_render_job(
    repository,
    *,
    project_id: UUID,
    render_job_id: UUID,
    workspace_id: UUID,
):
    return await _repository_call(
        repository,
        "get_render_job_async",
        "get_render_job",
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=workspace_id,
    )


def _select_render_engine(
    *,
    settings,
    billing_plan_key: str,
    has_active_broll: bool,
    has_music: bool,
    has_effects: bool,
    caption_animation: str = "none",
    manual_element_types: set[str] | None = None,
    has_template_motion: bool = False,
) -> RenderEngine:
    """
    Promotes to REMOTION_PREMIUM when:
    - REMOTION_ENABLED=true in settings
    - user is on a paid plan (or billing is disabled / personal mode)
    - at least one feature that needs the premium renderer is active

    Falls back to FFMPEG_BASIC in all other cases (zero regression risk).
    """
    if not getattr(settings, "remotion_enabled", False):
        return RenderEngine.FFMPEG_BASIC
    paid_plans = {"creator", "pro", "agency"}
    billing_disabled = not getattr(settings, "billing_enabled", False)
    is_eligible = billing_plan_key in paid_plans or billing_disabled
    features = requested_renderer_features(
        has_active_broll=has_active_broll,
        has_music=has_music,
        has_effects=has_effects,
        caption_animation=caption_animation,
        manual_element_types=manual_element_types or set(),
        has_template_motion=has_template_motion,
    )
    has_premium_feature = bool(features)
    if is_eligible and has_premium_feature:
        return RenderEngine.REMOTION_PREMIUM
    return RenderEngine.FFMPEG_BASIC


def requested_renderer_features(
    *,
    has_active_broll: bool,
    has_music: bool,
    has_effects: bool,
    caption_animation: str,
    manual_element_types: set[str],
    has_template_motion: bool,
) -> set[str]:
    features: set[str] = set()
    if has_active_broll:
        features.add("broll")
    if has_music:
        features.add("catalog_music")
    if has_effects:
        features.add("visual_effects")
    if caption_animation not in {"", "none", "static"}:
        features.add(f"animated_captions:{caption_animation}")
    if has_template_motion:
        features.add("template_intro_outro")
    features.update(
        f"manual_{element_type}"
        for element_type in manual_element_types
        if element_type in {"transition", "music", "sfx", "broll", "graphic"}
    )
    return features


def renderer_feature_compatibility(
    *, engine: RenderEngine, requested_features: set[str]
) -> dict[str, object]:
    ffmpeg_unsupported = {
        "template_intro_outro",
        "manual_transition",
        "manual_music",
        "manual_graphic",
    }
    unsupported = {
        feature
        for feature in requested_features
        if feature in ffmpeg_unsupported or feature.startswith("animated_captions:")
    }
    if engine == RenderEngine.REMOTION_PREMIUM:
        unsupported = set()
    return {
        "matrix_version": 1,
        "requested": sorted(requested_features),
        "unsupported": sorted(unsupported),
        "downgraded": sorted(unsupported),
        "matrix_document": "docs/renderer-feature-matrix.md",
    }


def source_timeline_for_keep_segments(keep_segments: list[CutSegment]) -> dict[str, object]:
    """Build the immutable source-to-output mapping shared by both renderers."""
    clips: list[dict[str, object]] = []
    output_cursor = 0
    for index, segment in enumerate(keep_segments):
        duration_ms = segment.end_ms - segment.start_ms
        if duration_ms <= 0:
            continue
        clips.append(
            {
                "id": f"cut-{index}",
                "source_start_ms": segment.start_ms,
                "source_end_ms": segment.end_ms,
                "output_start_ms": output_cursor,
                "output_end_ms": output_cursor + duration_ms,
                "reason": segment.reason,
            }
        )
        output_cursor += duration_ms
    return {
        "schema_version": 1,
        "duration_ms": output_cursor,
        "clips": clips,
    }


def beat_sync_summary(
    *,
    keep_segments: list,
    beat_map: list[float],
    tolerance_ms: int,
) -> dict[str, object]:
    if not beat_map:
        return {
            "enabled": False,
            "beat_count": 0,
            "aligned_cut_count": 0,
            "tolerance_ms": tolerance_ms,
            "aligned_cuts": [],
        }
    aligned = []
    for segment in keep_segments:
        nearest = min(beat_map, key=lambda beat: abs(float(beat) - segment.start_ms))
        offset = round(float(nearest) - segment.start_ms)
        if abs(offset) <= tolerance_ms:
            aligned.append(
                {
                    "cut_start_ms": segment.start_ms,
                    "beat_ms": round(float(nearest)),
                    "offset_ms": offset,
                }
            )
    return {
        "enabled": True,
        "beat_count": len(beat_map),
        "aligned_cut_count": len(aligned),
        "tolerance_ms": tolerance_ms,
        "aligned_cuts": aligned[:12],
        "strategy": "alignment report only; speech cuts are not retimed",
    }


def apply_motion_design_export_settings(
    *,
    export_settings: dict[str, object],
    source_metadata,
    effects_config,
    subject_track: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    motion = effects_config.motion
    track = subject_track or []
    motion_design: dict[str, object] = {
        "schema_version": 1,
        "keyframe_count": len(motion.keyframes),
        "ken_burns": motion.ken_burns.model_dump(mode="json"),
        "speed_ramp": motion.speed_ramp.model_dump(mode="json"),
        "beat_impact": motion.beat_impact.model_dump(mode="json"),
        "auto_reframe": {
            **motion.auto_reframe.model_dump(mode="json"),
            "subject_track": track,
            "applied": False,
            "crop": None,
        },
    }
    if not motion.auto_reframe.enabled:
        return export_settings, motion_design

    updated = dict(export_settings)
    framing = dict(updated.get("framing") or {})
    output_aspect = str(framing.get("output_aspect") or "9:16")
    if output_aspect != motion.auto_reframe.target_aspect:
        return updated, motion_design

    crop = dict(framing.get("crop") or {})
    crop["x"] = round(float(track[0]["x"]) if track else motion.auto_reframe.subject_x_pct / 100, 4)
    crop["y"] = round(float(track[0]["y"]) if track else motion.auto_reframe.subject_y_pct / 100, 4)
    crop["zoom"] = max(1, min(3, float(crop.get("zoom") or 1)))
    framing["fit_mode"] = "custom"
    framing["crop"] = crop
    updated["framing"] = framing

    source_width = getattr(source_metadata, "width", None)
    source_height = getattr(source_metadata, "height", None)
    motion_design["auto_reframe"] = {
        **motion.auto_reframe.model_dump(mode="json"),
        "applied": True,
        "source_width": source_width,
        "source_height": source_height,
        "crop": crop,
        "tracked": bool(track),
        "track_point_count": len(track),
    }
    return updated, motion_design


async def _inflight_duplicate(
    repository: InMemoryRenderRepository,
    *,
    workspace_id: UUID,
    project_id: UUID,
    props_hash: str,
) -> RenderJobRecord | None:
    """The oldest queued or running render of exactly these props, if any."""
    jobs = await _repository_call(
        repository,
        "list_inflight_duplicate_jobs_async",
        "list_inflight_duplicate_jobs",
        workspace_id=workspace_id,
        project_id=project_id,
        props_hash=props_hash,
        limit=1,
    )
    return jobs[0] if jobs else None


async def _cancel_duplicate(repository: InMemoryRenderRepository, job: RenderJobRecord) -> None:
    try:
        await _repository_call(
            repository,
            "transition_job_async",
            "transition_job",
            job=job,
            status=RenderJobStatus.CANCELED,
            progress_pct=job.progress_pct,
            message="Render not started: an identical render is already in progress.",
            event_type="cancelled",
            error_code="render_duplicate",
            error_message="An identical render is already in progress.",
            require_active=True,
        )
    except ApiError as exc:
        if exc.code != "render_not_cancelable":
            raise


async def _render_target_result(
    repository: InMemoryRenderRepository,
    job: RenderJobRecord,
    *,
    project_id: UUID,
    publish: bool,
) -> RenderCreateResponse | RenderJobRecord:
    """The create response for an already-queued render (nothing is re-queued)."""
    if not publish:
        return job
    return RenderCreateResponse(
        job=job.to_response(await _events_for_job(repository, job.id)),
        job_id=job.id,
        queue_job_id=RenderQueue.job_id_for_render(
            render_job_id=job.id,
            project_id=project_id,
            workspace_id=job.workspace_id,
            priority=_render_queue_priority(job),
        ),
        status=job.status,
    )


async def _confirm_render_slot(
    *,
    job: RenderJobRecord,
    actor: object,
    repository: InMemoryRenderRepository,
    billing_repository: object,
    billing_enabled: bool,
) -> None:
    """Re-check the render limit now that ``job`` exists, before it is queued.

    Two requests can both pass the up-front check and then both create a
    render. Once created, each render is visible to every request, and renders
    are ranked oldest first, so exactly the renders that fit keep their slot.
    A render that lost the race is canceled (never queued) and the request
    gets the same 402 as the up-front check.
    """
    try:
        await enforce_render_entitlement(
            workspace_id=actor.workspace_id,
            repository=billing_repository,
            render_repository=repository,
            billing_enabled=billing_enabled,
            render_job_id=job.id,
        )
    except ApiError as exc:
        if exc.code != "render_limit_exceeded":
            raise
        await _repository_call(
            repository,
            "transition_job_async",
            "transition_job",
            job=job,
            status=RenderJobStatus.CANCELED,
            progress_pct=job.progress_pct,
            message="Render not started: the monthly render limit was reached.",
            event_type="cancelled",
            error_code="render_limit_exceeded",
            error_message=exc.message,
            require_active=True,
        )
        raise


@router.post(
    "/projects/{project_id}/renders",
    response_model=RenderCreateResponse,
    status_code=202,
)
async def create_render(
    project_id: UUID,
    payload: RenderCreateRequest,
    request: Request,
    actor: ActorDep,
    repository: RenderRepositoryDep,
    billing_repository: BillingRepositoryDep,
    plan_repository: PlanRepositoryDep,
    render_queue: RenderQueueDep,
    broll_repository: BrollRepositoryDep = None,  # type: ignore[assignment]
) -> RenderCreateResponse:
    result = await _create_render_target(
        project_id=project_id,
        payload=payload,
        request=request,
        actor=actor,
        repository=repository,
        billing_repository=billing_repository,
        plan_repository=plan_repository,
        render_queue=render_queue,
        broll_repository=broll_repository,
    )
    assert isinstance(result, RenderCreateResponse)
    return result


async def _create_render_target(
    *,
    project_id: UUID,
    payload: RenderCreateRequest,
    request: Request,
    actor: object,
    repository: InMemoryRenderRepository,
    billing_repository: object,
    plan_repository: InMemoryPlanRepository,
    render_queue: RenderQueue,
    broll_repository: InMemoryBrollRepository | None = None,
    workflow_repository: object | None = None,
    workflow_run_id: UUID | None = None,
    timeline_revision_id: UUID | None = None,
    publish: bool = True,
) -> RenderCreateResponse | RenderJobRecord:
    billing_enabled = bool(getattr(request.app.state.settings, "billing_enabled", True))
    billing_status = await enforce_render_entitlement(
        workspace_id=actor.workspace_id,
        repository=billing_repository,
        render_repository=repository,
        billing_enabled=billing_enabled,
    )
    upload = await run_blocking(
        repository.get_active_upload, project_id=project_id, workspace_id=actor.workspace_id
    )
    if upload.source_object_reference_id is None:
        reference_service = getattr(
            request.app.state,
            "object_reference_service",
            None,
        )
        if reference_service is not None:
            source_reference = await reference_service.create_reference(
                workspace_id=actor.workspace_id,
                project_id=project_id,
                storage_bucket=upload.storage_bucket,
                storage_path=upload.storage_path,
                owner_type="uploaded_video",
                owner_id=upload.id,
                object_type="source_video",
                mime_type=upload.mime_type,
                size_bytes=upload.verified_size_bytes or upload.file_size_bytes,
                sha256=upload.verified_sha256,
            )
            upload_repository = getattr(request.app.state, "upload_repository", None)
            if upload_repository is not None:
                upload = await _repository_call(
                    upload_repository,
                    "attach_source_reference_async",
                    "attach_source_reference",
                    upload=upload,
                    source_object_reference_id=source_reference.id,
                )
            else:
                upload.source_object_reference_id = source_reference.id
    if upload.source_object_reference_id is None:
        raise ApiError(
            503,
            "durable_media_reference_missing",
            "Render source media requires a durable object reference.",
        )
    if upload.status != UploadStatus.READY:
        raise ApiError(
            409,
            "source_video_not_ready",
            "Finish upload preprocessing before rendering an export.",
        )
    readiness = assert_video_render_ready(upload.metadata)
    upload.metadata.warnings = readiness.warning_codes

    effective_include_captions = (
        payload.export_options.include_captions
        if payload.export_options.include_captions is not None
        else payload.include_captions
    )
    requested_duration_preset = payload.export_options.duration_preset or payload.duration_preset
    hook_text = (payload.export_options.hook_text or "").strip() or None

    track = None
    if payload.caption_track_id:
        track = await run_blocking(
            repository.get_caption_track,
            project_id=project_id,
            caption_track_id=payload.caption_track_id,
            workspace_id=actor.workspace_id,
        )
    elif effective_include_captions:
        track = await run_blocking(
            repository.latest_caption_track,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    localized_package = None
    if payload.localized_publish_package_id:
        localized_repository = getattr(request.app.state, "localized_publishing_repository", None)
        if localized_repository is None:
            raise ApiError(
                503, "localized_publishing_unavailable", "Localized publishing is unavailable."
            )
        localized_package = await run_blocking(
            localized_repository.get_package,
            package_id=payload.localized_publish_package_id,
            workspace_id=actor.workspace_id,
            project_id=project_id,
        )
        if (
            getattr(localized_package.review_status, "value", localized_package.review_status)
            != "approved"
        ):
            raise ApiError(
                409,
                "localized_publish_review_required",
                "Review and approve the localized publish package before rendering.",
            )
        track = await run_blocking(
            repository.get_caption_track,
            project_id=project_id,
            caption_track_id=localized_package.caption_track_id,
            workspace_id=actor.workspace_id,
        )
    if effective_include_captions and (track is None or not track.segments):
        raise ApiError(422, "caption_segments_empty", "Generate captions before rendering.")

    caption_segments = track.segments if track else []
    caption_style = (
        track.style if track else get_caption_preset(CaptionPresetKey.BOLD_INFLUENCER).style
    )
    caption_words = [word for segment in caption_segments for word in segment.words]
    if not caption_words:
        transcript = await run_blocking(
            repository.caption_repository.latest_transcript,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        caption_words = transcript.words if transcript else []

    edit_plan_record = None
    if payload.edit_plan_id:
        edit_plan_record = await run_blocking(
            plan_repository.get_plan,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=payload.edit_plan_id,
        )
    else:
        plans = await run_blocking(
            plan_repository.list_plans,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        edit_plan_record = next((plan for plan in plans if plan.is_active), None)

    effective_duration_preset = (
        requested_duration_preset
        if payload.adjustment_action != RenderAdjustmentAction.MAKE_PUNCHIER
        else DurationPreset.SHORT
    )
    planned_segments = (
        [
            CutSegment(
                start_ms=cut.start_ms,
                end_ms=cut.end_ms,
                reason=cut.reason,
            )
            for cut in edit_plan_record.plan.cuts
        ]
        if edit_plan_record and edit_plan_record.plan.cuts
        else plan_keep_segments(
            words=caption_words,
            caption_segments=caption_segments,
            metadata=upload.metadata,
        )
    )
    keep_segments = cap_keep_segments_to_duration(
        segments=planned_segments,
        duration_preset=effective_duration_preset,
    )
    retimed_caption_segments = retime_caption_segments_for_keep_segments(
        segments=caption_segments,
        keep_segments=keep_segments,
    )
    caption_segments_for_render = retimed_caption_segments if effective_include_captions else []
    caption_words_for_render = [
        retimed_word
        for output_start, keep in _keep_segments_with_output_starts(keep_segments)
        for word in caption_words
        for retimed_word in [
            retime_word_for_overlap(
                word=word,
                keep=keep,
                output_start_ms=output_start,
            )
        ]
        if retimed_word is not None
    ]
    specs, applied_resolution = render_specs_for_options(
        options=payload.export_options,
        max_resolution=billing_status.current_plan.entitlements.max_resolution,
        source_metadata=upload.metadata,
    )
    render_export_settings = export_settings(
        specs=specs,
        options=payload.export_options,
        applied_resolution=applied_resolution,
        duration_preset=effective_duration_preset,
    )
    render_export_settings["watermark"] = billing_status.current_plan.entitlements.watermark
    if localized_package:
        render_export_settings["localized_publish"] = {
            "package_id": str(localized_package.id),
            "language": getattr(localized_package.language, "value", localized_package.language),
            "title": localized_package.selected_title,
            "description": localized_package.selected_description,
        }
    if effective_duration_preset != requested_duration_preset:
        render_export_settings["requested_duration_preset"] = requested_duration_preset.value
    render_props_export_settings = {
        **specs.model_dump(mode="json"),
        **render_export_settings,
    }
    template = select_template(
        plan=edit_plan_record.plan if edit_plan_record else None,
        requested_template_id=payload.template_id,
    )
    applied_effects, unsupported_effects, downgraded_effects = effect_compatibility(
        template=template,
        effects=edit_plan_record.plan.effects if edit_plan_record else [],
    )
    broll_slots = (
        await run_blocking(
            broll_repository.list_slots, project_id=project_id, workspace_id=actor.workspace_id
        )
        if broll_repository
        else []
    )
    if edit_plan_record:
        broll_slots = [slot for slot in broll_slots if slot.edit_plan_id == edit_plan_record.id]
    active_broll_assets = []
    for slot in broll_slots if payload.include_broll else []:
        if (
            slot.status != BrollSlotStatus.READY
            or not slot.selected_asset_id
            or not broll_repository
        ):
            continue
        asset = await run_blocking(
            broll_repository.get_asset,
            project_id=project_id,
            workspace_id=actor.workspace_id,
            asset_id=slot.selected_asset_id,
        )
        retimed_range = retime_range_for_keep_segments(
            start_ms=slot.start_ms,
            end_ms=slot.end_ms,
            keep_segments=keep_segments,
        )
        if retimed_range is None:
            continue
        asset_source = asset.download_url or asset.preview_video_url or ""
        storage_path = asset.raw_metadata.get("storage_path")
        storage_bucket = asset.raw_metadata.get("storage_bucket")
        if storage_path and storage_bucket:
            asset_source = f"supabase://{storage_bucket}/{storage_path}"
        active_broll_assets.append(
            {
                "id": str(asset.id),
                "kind": "video",
                "src": asset_source,
                "start_ms": retimed_range[0],
                "end_ms": retimed_range[1],
                "source_start_ms": slot.start_ms,
                "source_end_ms": slot.end_ms,
                "slot_id": str(slot.id),
                "provider": asset.provider.value,
                "license": asset.license,
                "crossfade_ms": slot.crossfade_ms,
                "score": slot.score,
            }
        )
    if edit_plan_record and payload.include_broll:
        for element in edit_plan_record.plan.manual_elements:
            if element.type != "broll":
                continue
            src = element.properties.get("assetUrl") or element.properties.get("src")
            if not isinstance(src, str) or not src:
                continue
            retimed_range = retime_range_for_keep_segments(
                start_ms=element.start_ms,
                end_ms=element.end_ms,
                keep_segments=keep_segments,
            )
            if retimed_range is None:
                continue
            active_broll_assets.append(
                {
                    "id": element.id,
                    "kind": "video",
                    "src": src,
                    "start_ms": retimed_range[0],
                    "end_ms": retimed_range[1],
                    "source_start_ms": element.start_ms,
                    "source_end_ms": element.end_ms,
                    "provider": str(element.properties.get("source") or "manual"),
                    "license": str(element.properties.get("license") or "user_owned"),
                    "crossfade_ms": 220,
                    "score": 1,
                }
            )
    music_repository = getattr(request.app.state, "music_repository", None)
    music_selection = None
    music_props = None
    if music_repository:
        music_selection = await run_blocking(
            music_repository.latest_selection,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    if music_selection and edit_plan_record and music_selection.edit_plan_id != edit_plan_record.id:
        music_selection = None
    if (
        music_selection
        and music_selection.music_track_id
        and getattr(request.app.state.settings, "app_env", "test") == "production"
        and not bool(getattr(music_selection, "rights_snapshot", {}).get("verified"))
    ):
        raise ApiError(
            409,
            "music_rights_unverified",
            "A verified licensed music selection is required before rendering.",
        )
    if music_selection:
        music_props = {
            "selection_id": str(music_selection.id),
            "music_track_id": (
                str(music_selection.music_track_id) if music_selection.music_track_id else None
            ),
            "music_volume": music_selection.music_volume,
            "sfx_intensity": music_selection.sfx_intensity,
            "regions": [region.model_dump(mode="json") for region in music_selection.regions],
            "rights": getattr(music_selection, "rights_snapshot", {}),
        }
        if music_selection.music_track_id and music_repository:
            music_track = await run_blocking(
                music_repository.get_track,
                project_id=project_id,
                workspace_id=actor.workspace_id,
                track_id=music_selection.music_track_id,
            )
            music_props["track"] = music_track.to_response().model_dump(mode="json")
            music_props["mix"] = {
                "ducking": {
                    "enabled": True,
                    "speech_attenuation_db": -12,
                    "attack_ms": 180,
                    "release_ms": 420,
                },
                "loudness_target_lufs": -14,
                "beat_snap_tolerance_ms": 140,
            }
            music_props["beat_alignment_report"] = beat_sync_summary(
                keep_segments=keep_segments,
                beat_map=music_track.beat_map,
                tolerance_ms=140,
            )
    effects_repository = getattr(request.app.state, "effects_repository", None)
    effects_config = None
    effects_props = None
    motion_design_manifest = None
    visual_record = None
    if effects_repository:
        effects_config = await run_blocking(
            effects_repository.latest_config,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    if effects_config and edit_plan_record and effects_config.edit_plan_id != edit_plan_record.id:
        effects_config = None
    if effects_config:
        visual_repository = getattr(request.app.state, "visual_repository", None)
        visual_record = (
            await run_blocking(
                visual_repository.latest_ready_for_upload,
                uploaded_video_id=upload.id,
                project_id=project_id,
                workspace_id=actor.workspace_id,
            )
            if visual_repository
            else None
        )
        subject_track = subject_track_from_manifest(
            visual_record.manifest if visual_record else None
        )
        effects_props = effects_config.to_response().model_dump(mode="json")
        effects_props["motion"]["auto_reframe"]["subject_track"] = subject_track
        render_export_settings, motion_design_manifest = apply_motion_design_export_settings(
            export_settings=render_export_settings,
            source_metadata=upload.metadata,
            effects_config=effects_config,
            subject_track=subject_track,
        )
        render_props_export_settings = {
            **specs.model_dump(mode="json"),
            **render_export_settings,
        }
    render_edit_plan = (
        edit_plan_record.plan.model_dump(mode="json")
        if edit_plan_record
        else {
            "schema_version": 1,
            "style": template.style.value,
            "target_duration_s": max(
                10,
                min(90, round((upload.metadata.duration_ms or 30_000) / 1000)),
            ),
            "cuts": [segment.model_dump(mode="json") for segment in keep_segments],
            "caption_highlights": [],
            "broll_requests": [],
            "zoom_moments": [],
            "music_mood": "ambient creator bed",
            "effects": [],
            "color_grade": template.default_color_grade,
            "risk_flags": [],
            "manual_elements": [],
        }
    )
    render_edit_plan = apply_render_adjustment(
        edit_plan=render_edit_plan,
        action=payload.adjustment_action,
        keep_segments=keep_segments,
        template_default_color_grade=template.default_color_grade,
    )
    if localized_package:
        translations = {
            item.source_element_id: item.content for item in localized_package.overlay_translations
        }
        for element in render_edit_plan.get("manual_elements") or []:
            if not isinstance(element, dict) or str(element.get("id")) not in translations:
                continue
            properties = dict(element.get("properties") or {})
            properties["content"] = translations[str(element["id"])]
            element["properties"] = properties
    if edit_plan_record:
        render_source_duration_ms = (
            upload.metadata.duration_ms
            or max((word.end_ms for word in caption_words_for_render), default=0)
            or max((segment.end_ms for segment in keep_segments), default=0)
            or 1
        )
        try:
            validated_render_plan = validate_edit_plan_for_render(
                plan=EditPlan.model_validate(render_edit_plan),
                context=EditPlanValidationContext(
                    source_duration_ms=render_source_duration_ms,
                    transcript_words=caption_words,
                    project_id=project_id,
                    plan_id=edit_plan_record.id,
                ),
            )
        except EditPlanValidationFailure as exc:
            raise ApiError(
                409,
                "invalid_edit_plan_for_render",
                "Edit plan must be regenerated before rendering.",
            ) from exc
        render_edit_plan = validated_render_plan.model_dump(mode="json")
    render_edit_plan["manual_elements"] = retime_manual_elements_for_keep_segments(
        elements=list(render_edit_plan.get("manual_elements") or []),
        keep_segments=keep_segments,
        include_broll=False,
    )
    if hook_text:
        render_edit_plan["manual_elements"].append(
            {
                "id": f"repurpose-hook-{payload.export_options.platform.value}",
                "type": "text",
                "name": "Platform opening hook",
                "zone": "canvas",
                "start_ms": 0,
                "end_ms": min(
                    3_000,
                    max(1, sum(segment.end_ms - segment.start_ms for segment in keep_segments)),
                ),
                "x_pct": 50,
                "y_pct": 18,
                "width_pct": 86,
                "height_pct": 14,
                "rotation_deg": 0,
                "opacity": 1,
                "properties": {
                    "content": hook_text,
                    "color": "#ffffff",
                    "fontSize": 46,
                    "fontWeight": 800,
                },
                "ai_reason": "platform-specific opening hook",
            }
        )
    source_stems = await _source_stems_for_render(
        request=request,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    compiled_timeline = compile_timeline(
        plan=EditPlan.model_validate(render_edit_plan),
        source_video_id=upload.id,
        source_duration_ms=upload.metadata.duration_ms or 1,
        captions=caption_segments if effective_include_captions else [],
        caption_style=caption_style.model_dump(mode="json"),
        broll=active_broll_assets,
        music=music_props,
        source_stems=source_stems,
        effects_config=effects_props,
        preserve_layers_from=(edit_plan_record.plan.timeline if edit_plan_record else None),
    )
    source_timeline = compiled_timeline.model_dump(mode="json")
    render_edit_plan["timeline"] = source_timeline
    segmentation_matte = None
    matte_status = {"status": "skipped", "reason": "matte_not_requested"}
    if effects_props and effects_props.get("background_removal", {}).get("enabled"):
        visual_repository = getattr(request.app.state, "visual_repository", None)
        matte_record = (
            await run_blocking(
                visual_repository.latest_matte_for_upload,
                workspace_id=actor.workspace_id,
                project_id=project_id,
                uploaded_video_id=upload.id,
            )
            if visual_repository is not None
            else None
        )
        availability = matte_availability(matte_record)
        matte_status = {
            "status": availability["status"],
            "reason": availability["reason"],
        }
        matte = availability["matte"]
        if matte is not None:
            segmentation_matte = {
                "src": f"supabase://{matte.bucket}/{matte.path}",
                "model": matte.model,
                "bucket": matte.bucket,
                "path": matte.path,
                "cache_key": matte.cache_key,
                "status": matte.status.value,
            }
    render_props = {
        "sourceVideo": {
            "src": f"supabase://{upload.storage_bucket}/{upload.storage_path}",
            "referenceId": (
                str(upload.source_object_reference_id)
                if upload.source_object_reference_id
                else None
            ),
            "width": upload.metadata.width or specs.width,
            "height": upload.metadata.height or specs.height,
            "duration_ms": upload.metadata.duration_ms,
        },
        "editPlan": render_edit_plan,
        "captions": [segment.model_dump(mode="json") for segment in caption_segments_for_render],
        "words": [word.model_dump(mode="json") for word in caption_words_for_render],
        "captionStyle": caption_style.model_dump(mode="json"),
        "assets": active_broll_assets,
        "templateId": template.id.value,
        "exportSettings": render_props_export_settings,
        "watermark": billing_status.current_plan.entitlements.watermark,
        "musicSelection": music_props,
        "effectsConfig": effects_props,
        "sourceStems": source_stems,
        "timeline": source_timeline,
        "segmentationMatte": segmentation_matte,
        "segmentationMatteStatus": matte_status,
    }
    settings = request.app.state.settings
    allow_fixture_media = bool(
        getattr(settings, "allow_fixture_providers", False)
        or getattr(settings, "app_env", "local") != "production"
    )
    try:
        # Resolves DNS for every external media URL: keep it off the event loop.
        await run_blocking(
            validate_render_media_manifest,
            render_props,
            allow_fixture=allow_fixture_media,
            resolve_dns=True,
            require_dns=False,
        )
    except MediaUrlPolicyError as exc:
        raise ApiError(
            422,
            "unsafe_media_url",
            "One or more media references are not allowed for rendering.",
        ) from exc
    props_hash = build_props_hash(render_props)
    manual_element_types = {
        element.type
        for element in (edit_plan_record.plan.manual_elements if edit_plan_record else [])
    }
    if hook_text:
        manual_element_types.add("text")
    requested_features = requested_renderer_features(
        has_active_broll=bool(active_broll_assets),
        has_music=bool(music_selection and music_selection.music_track_id),
        has_effects=bool(effects_config),
        caption_animation=template.caption_animation,
        manual_element_types=manual_element_types,
        has_template_motion=bool(template.intro or template.outro),
    )
    selected_engine = _select_render_engine(
        settings=request.app.state.settings,
        billing_plan_key=billing_status.current_plan.key.value,
        has_active_broll=bool(active_broll_assets),
        has_music=bool(music_selection and music_selection.music_track_id),
        has_effects=bool(effects_config),
        caption_animation=template.caption_animation,
        manual_element_types=manual_element_types,
        has_template_motion=bool(template.intro or template.outro),
    )
    compute_tier = (
        "gpu"
        if selected_engine == RenderEngine.FFMPEG_BASIC
        and getattr(request.app.state.settings, "ffmpeg_gpu_enabled", False)
        else "cpu"
    )
    manifest = {
        "schema_version": 1,
        "creator_user_id": str(actor.user_id),
        "billing_plan": billing_status.current_plan.key.value,
        "source_video_id": str(upload.id),
        "caption_track_id": str(track.id) if track else None,
        "include_captions": effective_include_captions,
        "include_broll": payload.include_broll,
        "adjustment_action": payload.adjustment_action.value if payload.adjustment_action else None,
        "edit_plan_id": str(edit_plan_record.id) if edit_plan_record else None,
        "workflow_run_id": str(workflow_run_id) if workflow_run_id else None,
        "timeline_revision_id": str(timeline_revision_id) if timeline_revision_id else None,
        "source_plan_revision": edit_plan_record.revision if edit_plan_record else None,
        "source_media_revision": getattr(upload, "media_revision", None),
        "music_selection_id": str(music_selection.id) if music_selection else None,
        "effects_config_id": str(effects_config.id) if effects_config else None,
        "motion_design": motion_design_manifest,
        "external_finishing_metadata": (
            {
                "project_file_url": effects_config.after_effects_project_url,
                "correction_manifest_url": effects_config.correction_manifest_url,
                "presets": [
                    preset.model_dump(mode="json")
                    for preset in effects_config.after_effects_presets
                ],
            }
            if effects_config
            else None
        ),
        "template_id": template.id.value,
        "broll_selection_version": max([slot.version for slot in broll_slots], default=None),
        "render_engine": selected_engine.value,
        "compute_tier": compute_tier,
        "video_encoder": (
            request.app.state.settings.ffmpeg_gpu_encoder if compute_tier == "gpu" else "libx264"
        ),
        "renderer_version": _RENDERER_VERSIONS[selected_engine],
        "props_hash": props_hash,
        "timeline": source_timeline,
        "renderer_compatibility": renderer_feature_compatibility(
            engine=selected_engine,
            requested_features=requested_features,
        ),
        "export_settings": render_export_settings,
        "repurpose": {
            "hook_text": hook_text,
            "caption_mode": "burned" if effective_include_captions else "none",
        },
        "billing": {
            "plan": billing_status.current_plan.key.value,
            "watermark": billing_status.current_plan.entitlements.watermark,
            "max_resolution": billing_status.current_plan.entitlements.max_resolution,
            "priority_queue": billing_status.current_plan.entitlements.priority_queue,
        },
        "template": {
            "name": template.name,
            "caption_animation": template.caption_animation,
            "intro": template.intro,
            "outro": template.outro,
            "applied_effects": applied_effects,
            "unsupported_effects": unsupported_effects,
            "downgraded_effects": downgraded_effects,
        },
        "source": {
            "uploaded_video_id": str(upload.id),
            "reference_id": (
                str(upload.source_object_reference_id)
                if upload.source_object_reference_id
                else None
            ),
            "bucket": upload.storage_bucket,
            "path": upload.storage_path,
            "verified_size_bytes": upload.verified_size_bytes,
            "verified_sha256": upload.verified_sha256,
        },
        "caption_ass": (
            caption_track_to_ass(segments=caption_segments_for_render, style=caption_style)
            if effective_include_captions and track
            else None
        ),
        "render_props": render_props,
        "broll": {
            "slot_count": len(broll_slots),
            "active_asset_count": len(active_broll_assets),
            "included": payload.include_broll,
            "disabled_slot_count": len(
                [slot for slot in broll_slots if slot.status == BrollSlotStatus.DISABLED]
            ),
        },
        "audio": {
            "music_selected": bool(music_selection and music_selection.music_track_id),
            "music_volume": music_selection.music_volume if music_selection else 0,
            "sfx_intensity": music_selection.sfx_intensity if music_selection else 0,
            "source_audio_preserved": True,
            "limiter": "alimiter=0.95",
        },
        "warnings": build_crop_warnings(upload.metadata),
        "steps": [
            "build immutable render manifest",
            "stage B-roll, music, and effects selections for the premium renderer",
            (
                "render captioned vertical MP4 with FFmpeg fallback engine"
                if effective_include_captions
                else "render vertical MP4 without burned-in captions"
            ),
            (
                "overlay selected transcript-aligned B-roll clips"
                if active_broll_assets
                else "render without B-roll overlays"
            ),
            "apply account entitlement watermark and resolution caps",
        ],
    }
    job_values = {
        "workspace_id": actor.workspace_id,
        "project_id": project_id,
        "uploaded_video_id": upload.id,
        "caption_track_id": track.id if track else None,
        "workflow_run_id": workflow_run_id,
        "edit_plan_id": edit_plan_record.id if edit_plan_record else None,
        "source_plan_revision": edit_plan_record.revision if edit_plan_record else None,
        "timeline_revision_id": timeline_revision_id,
        "source_media_revision": getattr(upload, "media_revision", None),
        "status": RenderJobStatus.QUEUED,
        "planner_version": PLANNER_VERSION,
        "manifest": manifest,
        "keep_segments": keep_segments,
        "specs": specs,
        "template_id": template.id,
        "render_engine": selected_engine,
        "renderer_version": _RENDERER_VERSIONS[selected_engine],
        "props_hash": props_hash,
        "logs": [
            "render manifest prepared",
            f"template={template.id.value}",
            f"include_captions={effective_include_captions}",
            f"include_broll={payload.include_broll}",
            f"broll_assets={len(active_broll_assets)}",
            f"compute_tier={compute_tier}",
            f"adjustment_action={payload.adjustment_action.value}"
            if payload.adjustment_action
            else "adjustment_action=none",
        ],
        "progress_pct": 5,
    }
    if workflow_run_id is not None:
        if workflow_repository is None or timeline_revision_id is None:
            raise ApiError(
                500,
                "workflow_render_lineage_missing",
                "Workflow render persistence requires immutable approval lineage.",
            )
        job = await run_blocking(
            repository.ensure_workflow_draft_render,
            workflow_repository=workflow_repository,
            **job_values,
        )
    else:
        # An identical render that is still queued or running is returned
        # instead of a second one (client retries, double clicks), unless the
        # caller explicitly asks for a fresh render with force=true.
        dedupe = not payload.force
        existing = (
            await _inflight_duplicate(
                repository,
                workspace_id=actor.workspace_id,
                project_id=project_id,
                props_hash=props_hash,
            )
            if dedupe
            else None
        )
        if existing is not None:
            return await _render_target_result(
                repository, existing, project_id=project_id, publish=publish
            )
        job = await run_blocking(repository.create_render_job, **job_values)
        if dedupe:
            # A concurrent identical request may have created its render
            # first; the oldest one is kept and this one is never queued.
            winner = await _inflight_duplicate(
                repository,
                workspace_id=actor.workspace_id,
                project_id=project_id,
                props_hash=props_hash,
            )
            if winner is not None and winner.id != job.id:
                await _cancel_duplicate(repository, job)
                return await _render_target_result(
                    repository, winner, project_id=project_id, publish=publish
                )
        await _confirm_render_slot(
            job=job,
            actor=actor,
            repository=repository,
            billing_repository=billing_repository,
            billing_enabled=billing_enabled,
        )
    if not publish:
        return job
    # The BullMQ bridge is a blocking subprocess round-trip (up to its timeout).
    queue_job = await _enqueue_render_or_fail(
        repository,
        job,
        render_queue=render_queue,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        priority=10 if billing_status.current_plan.entitlements.priority_queue else 0,
        render_engine=selected_engine.value,
        compute_tier=compute_tier,
    )

    return RenderCreateResponse(
        job=job.to_response(await run_blocking(repository.events_for_job, job.id)),
        job_id=job.id,
        queue_job_id=queue_job.id,
        status=job.status,
    )


@router.post(
    "/projects/{project_id}/renders/bulk",
    response_model=RenderBulkCreateResponse,
    status_code=202,
)
async def create_bulk_render(
    project_id: UUID,
    payload: RenderBulkCreateRequest,
    request: Request,
    actor: ActorDep,
    repository: RenderRepositoryDep,
    billing_repository: BillingRepositoryDep,
    plan_repository: PlanRepositoryDep,
    render_queue: RenderQueueDep,
    broll_repository: BrollRepositoryDep = None,  # type: ignore[assignment]
) -> RenderBulkCreateResponse:
    # Refuse the whole batch up front if it cannot fit, rather than creating
    # the first few targets and failing part-way. Each target is then checked
    # (and confirmed after creation) again by create_render.
    await enforce_render_entitlement(
        workspace_id=actor.workspace_id,
        repository=billing_repository,
        render_repository=repository,
        billing_enabled=bool(getattr(request.app.state.settings, "billing_enabled", True)),
        requested=len(payload.export_targets),
    )
    responses: list[RenderCreateResponse] = []
    for export_options in payload.export_targets:
        response = await create_render(
            project_id=project_id,
            payload=RenderCreateRequest(
                caption_track_id=payload.caption_track_id,
                localized_publish_package_id=payload.localized_publish_package_id,
                include_captions=payload.include_captions,
                include_broll=payload.include_broll,
                edit_plan_id=payload.edit_plan_id,
                template_id=payload.template_id,
                adjustment_action=payload.adjustment_action,
                duration_preset=payload.duration_preset,
                export_options=export_options,
                force=payload.force,
            ),
            request=request,
            actor=actor,
            repository=repository,
            billing_repository=billing_repository,
            plan_repository=plan_repository,
            render_queue=render_queue,
            broll_repository=broll_repository,
        )
        responses.append(response)
    return RenderBulkCreateResponse(
        jobs=[response.job for response in responses],
        job_ids=[response.job_id for response in responses],
        queue_job_ids=[response.queue_job_id for response in responses],
    )


def apply_render_adjustment(
    *,
    edit_plan: dict[str, object],
    action: RenderAdjustmentAction | None,
    keep_segments: list,
    template_default_color_grade: str,
) -> dict[str, object]:
    adjusted = {
        **edit_plan,
        "cuts": [segment.model_dump(mode="json") for segment in keep_segments],
        "effects": list(edit_plan.get("effects") or []),
        "caption_highlights": list(edit_plan.get("caption_highlights") or []),
        "zoom_moments": list(edit_plan.get("zoom_moments") or []),
        "risk_flags": list(edit_plan.get("risk_flags") or []),
    }
    if not action:
        return adjusted

    risk_flags = list(adjusted["risk_flags"])
    risk_flags.append(f"user_adjustment:{action.value}")
    adjusted["risk_flags"] = risk_flags

    if action == RenderAdjustmentAction.MAKE_PUNCHIER:
        adjusted["target_duration_s"] = max(
            10,
            round(sum(segment.end_ms - segment.start_ms for segment in keep_segments) / 1000),
        )
        effects = list(adjusted["effects"])
        for effect in ("emphasis_pulse", "speed_ramp", "zoom_punch"):
            if effect not in effects:
                effects.append(effect)
        adjusted["effects"] = effects
        return adjusted

    if action == RenderAdjustmentAction.MORE_CINEMATIC:
        effects = list(adjusted["effects"])
        for effect in ("ken_burns", "soft_flash", "warm_grade"):
            if effect not in effects:
                effects.append(effect)
        adjusted["effects"] = effects
        adjusted["color_grade"] = "cinematic_hotel"
        adjusted["music_mood"] = "cinematic ambient build"
        if not adjusted["zoom_moments"] and keep_segments:
            first = keep_segments[0]
            adjusted["zoom_moments"] = [
                {
                    "timestamp_ms": first.start_ms,
                    "intensity": 1.08,
                    "reason": "cinematic opening emphasis",
                }
            ]
        return adjusted

    adjusted["color_grade"] = adjusted.get("color_grade") or template_default_color_grade
    return adjusted


def retime_caption_segments_for_keep_segments(
    *,
    segments: list[CaptionSegment],
    keep_segments: list[CutSegment],
) -> list[CaptionSegment]:
    retimed: list[CaptionSegment] = []
    for output_start, keep in _keep_segments_with_output_starts(keep_segments):
        for segment in segments:
            overlap_start = max(segment.start_ms, keep.start_ms)
            overlap_end = min(segment.end_ms, keep.end_ms)
            if overlap_end <= overlap_start:
                continue
            # Same mapping as the segment's words (retime_word_for_overlap) and
            # timeline_compiler._caption_layers: a caption keeps its offset
            # within the kept section, so it never appears before its words.
            next_start = output_start + (overlap_start - keep.start_ms)
            next_end = output_start + (overlap_end - keep.start_ms)
            words = [
                retimed_word
                for word in segment.words
                for retimed_word in [
                    retime_word_for_overlap(
                        word=word,
                        keep=keep,
                        output_start_ms=output_start,
                    )
                ]
                if retimed_word is not None
            ]
            retimed.append(
                segment.model_copy(
                    update={
                        "start_ms": next_start,
                        "end_ms": next_end,
                        "index": len(retimed),
                        "words": words,
                        "text": " ".join(word.text for word in words) or segment.text,
                    }
                )
            )
    return retimed


def retime_word_for_overlap(
    *,
    word: TranscriptWord,
    keep: CutSegment,
    output_start_ms: int,
) -> TranscriptWord | None:
    overlap_start = max(word.start_ms, keep.start_ms)
    overlap_end = min(word.end_ms, keep.end_ms)
    if overlap_end <= overlap_start:
        return None
    return word.model_copy(
        update={
            "start_ms": output_start_ms + (overlap_start - keep.start_ms),
            "end_ms": output_start_ms + (overlap_end - keep.start_ms),
        }
    )


def retime_range_for_keep_segments(
    *,
    start_ms: int,
    end_ms: int,
    keep_segments: list[CutSegment],
) -> tuple[int, int] | None:
    for output_start, keep in _keep_segments_with_output_starts(keep_segments):
        overlap_start = max(start_ms, keep.start_ms)
        overlap_end = min(end_ms, keep.end_ms)
        if overlap_end <= overlap_start:
            continue
        return (
            output_start + (overlap_start - keep.start_ms),
            output_start + (overlap_end - keep.start_ms),
        )
    return None


def retime_manual_elements_for_keep_segments(
    *,
    elements: list[object],
    keep_segments: list[CutSegment],
    include_broll: bool = True,
) -> list[dict[str, object]]:
    """Project source-timed manual elements onto the assembled output timeline."""
    retimed: list[dict[str, object]] = []
    for raw_element in elements:
        if hasattr(raw_element, "model_dump"):
            element = raw_element.model_dump(mode="json")
        elif isinstance(raw_element, dict):
            element = dict(raw_element)
        else:
            continue
        if not include_broll and element.get("type") == "broll":
            continue
        start_ms = int(element.get("start_ms") or 0)
        end_ms = int(element.get("end_ms") or 0)
        for clip_index, (output_start, keep) in enumerate(
            _keep_segments_with_output_starts(keep_segments)
        ):
            overlap_start = max(start_ms, keep.start_ms)
            overlap_end = min(end_ms, keep.end_ms)
            if overlap_end <= overlap_start:
                continue
            next_element = {
                **element,
                "start_ms": output_start + (overlap_start - keep.start_ms),
                "end_ms": output_start + (overlap_end - keep.start_ms),
            }
            if overlap_start != start_ms or overlap_end != end_ms:
                next_element["id"] = f"{element.get('id', 'element')}-cut-{clip_index}"
            retimed.append(next_element)
    return retimed


def _keep_segments_with_output_starts(
    keep_segments: list[CutSegment],
) -> list[tuple[int, CutSegment]]:
    output_cursor = 0
    mapped: list[tuple[int, CutSegment]] = []
    for segment in keep_segments:
        if segment.end_ms <= segment.start_ms:
            continue
        mapped.append((output_cursor, segment))
        output_cursor += segment.end_ms - segment.start_ms
    return mapped


@router.get("/projects/{project_id}/renders", response_model=RenderListResponse)
async def list_renders(
    project_id: UUID,
    actor: ActorDep,
    repository: RenderRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> RenderListResponse:
    jobs = await _repository_call(
        repository,
        "list_render_jobs_async",
        "list_render_jobs",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return RenderListResponse(
        jobs=[job.to_response(await _events_for_job(repository, job.id)) for job in jobs],
    )


@router.get(
    "/projects/{project_id}/renders/{render_job_id}",
    response_model=RenderJobResponse,
)
async def get_render(
    project_id: UUID,
    render_job_id: UUID,
    actor: ActorDep,
    repository: RenderRepositoryDep,
) -> RenderJobResponse:
    job = await _get_render_job(
        repository,
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=actor.workspace_id,
    )
    return job.to_response(await _events_for_job(repository, job.id))


# Keep the render event stream alive with an SSE comment at least this often.
# `render_stream_now` / `render_stream_sleep` are module-level hooks so tests
# can drive the loop with a fake clock instead of real wall-clock time.
RENDER_STREAM_KEEPALIVE_SECONDS = 15


def render_stream_now() -> float:
    import time

    return time.monotonic()


async def render_stream_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@router.get("/projects/{project_id}/renders/{render_job_id}/events")
async def stream_render_events(
    project_id: UUID,
    render_job_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: RenderRepositoryDep,
) -> StreamingResponse:
    async def event_stream():
        last_signature = ""
        terminal = {
            RenderJobStatus.COMPLETE,
            RenderJobStatus.FAILED,
            RenderJobStatus.CANCELED,
        }
        # Emit an SSE comment keep-alive at least every 15 seconds so idle
        # renders keep the connection (and any intermediary buffers) alive while
        # the worker is busy. Injectable clock/sleep hooks let tests drive this
        # with a fake clock instead of real time.
        last_emit = render_stream_now()
        while True:
            if await request.is_disconnected():
                break
            job = await _get_render_job(
                repository,
                project_id=project_id,
                render_job_id=render_job_id,
                workspace_id=actor.workspace_id,
            )
            payload = job.to_response(await _events_for_job(repository, job.id)).model_dump(
                mode="json",
            )
            signature = (
                f"{payload['status']}:{payload['progress_pct']}:"
                f"{payload.get('updated_at')}:{len(payload.get('events') or [])}"
            )
            if signature != last_signature:
                yield "event: render\n"
                yield f"data: {json.dumps(payload)}\n\n"
                last_signature = signature
                last_emit = render_stream_now()
            elif render_stream_now() - last_emit >= RENDER_STREAM_KEEPALIVE_SECONDS:
                yield ": keep-alive\n\n"
                last_emit = render_stream_now()
            if job.status in terminal:
                break
            await render_stream_sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _render_queue_priority(job) -> int:
    manifest = getattr(job, "manifest", None)
    if not isinstance(manifest, dict):
        return 0
    billing = manifest.get("billing")
    if isinstance(billing, dict) and billing.get("priority_queue") is True:
        return 10
    return 0


@router.post(
    "/projects/{project_id}/renders/{render_job_id}/cancel",
    response_model=RenderJobResponse,
)
async def cancel_render(
    project_id: UUID,
    render_job_id: UUID,
    actor: ActorDep,
    repository: RenderRepositoryDep,
    render_queue: RenderQueueDep,
) -> RenderJobResponse:
    job = await _get_render_job(
        repository,
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=actor.workspace_id,
    )
    if job.status in {
        RenderJobStatus.COMPLETE,
        RenderJobStatus.FAILED,
        RenderJobStatus.CANCELED,
    }:
        raise ApiError(409, "render_not_cancelable", "This render can no longer be canceled.")
    try:
        await run_blocking(
            render_queue.cancel,
            RenderQueue.job_id_for_render(
                render_job_id=job.id,
                project_id=project_id,
                workspace_id=actor.workspace_id,
                priority=_render_queue_priority(job),
            ),
        )
    except Exception:
        # Best effort: queued jobs are removed from the queue when possible, and running jobs
        # still observe the persisted canceled status.
        pass
    job = await _repository_call(
        repository,
        "transition_job_async",
        "transition_job",
        job=job,
        status=RenderJobStatus.CANCELED,
        progress_pct=job.progress_pct,
        message="Render canceled.",
        event_type="cancelled",
        error_code="render_cancelled",
        error_message="Render canceled.",
        # The worker may finish (or fail) the render between the status check
        # above and this write; only cancel a render that is still active, so a
        # completed export is never overwritten (409 instead).
        require_active=True,
    )
    return job.to_response(await _events_for_job(repository, job.id))


@router.post(
    "/projects/{project_id}/renders/{render_job_id}/retry",
    response_model=RenderCreateResponse,
    status_code=202,
)
async def retry_render(
    project_id: UUID,
    render_job_id: UUID,
    actor: ActorDep,
    repository: RenderRepositoryDep,
    render_queue: RenderQueueDep,
    request: Request = None,  # type: ignore[assignment]
    billing_repository: BillingRepositoryDep = None,  # type: ignore[assignment]
) -> RenderCreateResponse:
    if billing_repository is not None and request is not None:
        # A failed or canceled render gave its slot back; retrying takes one
        # again, so it is subject to the same monthly limit as a new render.
        await enforce_render_entitlement(
            workspace_id=actor.workspace_id,
            repository=billing_repository,
            render_repository=repository,
            billing_enabled=bool(getattr(request.app.state.settings, "billing_enabled", True)),
        )
    job = await run_blocking(
        repository.retry_project_render_job,
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=actor.workspace_id,
    )
    queue_job = await _enqueue_render_or_fail(
        repository,
        job,
        render_queue=render_queue,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        render_engine=(job.render_engine or RenderEngine.FFMPEG_BASIC).value,
        compute_tier=str(job.manifest.get("compute_tier") or "cpu"),
        priority=_render_queue_priority(job),
    )
    return RenderCreateResponse(
        job=job.to_response(await run_blocking(repository.events_for_job, job.id)),
        job_id=job.id,
        queue_job_id=queue_job.id,
        status=job.status,
    )


@router.get("/projects/{project_id}/exports", response_model=ExportListResponse)
async def list_exports(
    project_id: UUID,
    actor: ActorDep,
    repository: RenderRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ExportListResponse:
    exports = await _repository_call(
        repository,
        "list_exports_async",
        "list_exports",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    return ExportListResponse(
        exports=[export.to_response() for export in exports],
    )


@router.get("/exports/{export_id}/download-url", response_model=ExportDownloadUrlResponse)
async def get_export_download_url(
    export_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: RenderRepositoryDep,
    storage_signer: StorageSignerDep,
) -> ExportDownloadUrlResponse:
    export = await _repository_call(
        repository,
        "get_export_async",
        "get_export",
        export_id=export_id,
        workspace_id=actor.workspace_id,
    )
    operations = getattr(request.app.state, "operations_repository", None)
    if operations is not None:
        operations.approve_export(
            workspace_id=actor.workspace_id,
            render_job_id=export.render_job_id,
            export_id=export.id,
        )
        operations.record_cost(
            workspace_id=actor.workspace_id,
            project_id=export.project_id,
            render_job_id=export.render_job_id,
            export_id=export.id,
            provider="supabase_storage",
            category="egress",
            quantity=float(export.file_size_bytes or 0) / 1_000_000_000,
            unit="gb",
            unit_cost_usd=0.09,
            approved_at=datetime.now(UTC),
            metadata={"outcome": "download"},
        )
    ttl = min(
        request.app.state.settings.export_signed_url_ttl_seconds,
        StorageSigner.MAX_DOWNLOAD_TTL_SECONDS,
    )
    download_filename = _export_download_filename(request=request, export=export)
    signed = storage_signer.create_signed_download(
        bucket=export.storage_bucket,
        path=export.storage_path,
        expires_in_seconds=ttl,
        download=download_filename,
    )
    return ExportDownloadUrlResponse(
        export_id=export.id,
        signed_download_url=signed.url,
        expires_in_seconds=ttl,
        download_filename=download_filename,
    )


@router.post(
    "/projects/{project_id}/exports/{export_id}/restore",
    response_model=ExportRestoreResponse,
    status_code=201,
)
async def restore_export_version(
    project_id: UUID,
    export_id: UUID,
    actor: ActorDep,
    repository: RenderRepositoryDep,
) -> ExportRestoreResponse:
    export = repository.restore_export(
        project_id=project_id,
        export_id=export_id,
        workspace_id=actor.workspace_id,
    )
    return ExportRestoreResponse(
        export=export.to_response(),
        restored_from_export_id=export_id,
    )


def _export_download_filename(*, request: Request, export) -> str:
    """Build a sanitized ``<project title>-vN.<ext>`` download filename.

    The title falls back to a neutral label when it is unavailable, so the
    Content-Disposition header the signed link produces is always well-formed.
    """
    title: str | None = None
    render_repository = getattr(request.app.state, "render_repository", None)
    caption_repository = getattr(render_repository, "caption_repository", None)
    getter = getattr(caption_repository, "get_project_for_workspace", None)
    if callable(getter):
        try:
            project = getter(
                project_id=export.project_id,
                workspace_id=export.workspace_id,
            )
            candidate = getattr(project, "title", None)
            if isinstance(candidate, str) and candidate.strip():
                title = candidate
        except Exception:
            title = None
    return export_download_filename(
        title=title,
        version=export.version,
        extension=getattr(export.format, "value", str(export.format)),
    )


async def _source_stems_for_render(
    *, request: Request, project_id: UUID, workspace_id: UUID
) -> dict[str, object] | None:
    scale_repository = getattr(request.app.state, "scale_repository", None)
    if scale_repository is None:
        return None
    result = await _repository_call(
        scale_repository,
        "latest_async",
        "latest",
        workspace_id=workspace_id,
        project_id=project_id,
        kind="stem_separation",
        status=ScaleResourceStatus.READY,
    )
    if result is None:
        return None
    raw = result.result
    if not isinstance(raw, dict):
        raise ApiError(
            409,
            "stem_artifact_unavailable",
            "The selected source stem is unavailable for rendering.",
        )
    active = raw.get("active", "vocals")
    if active not in {"vocals", "no_vocals"}:
        raise ApiError(
            409,
            "stem_artifact_unavailable",
            "The selected source stem is unavailable for rendering.",
        )
    selected = raw.get(active)
    expected_path = (
        f"workspaces/{workspace_id}/projects/{project_id}/stems/{result.id}/{active}.wav"
    )
    if (
        not isinstance(selected, dict)
        or not isinstance(selected.get("bucket"), str)
        or not selected["bucket"]
        or not isinstance(selected.get("path"), str)
        or selected["path"] != expected_path
    ):
        raise ApiError(
            409,
            "stem_artifact_unavailable",
            "The selected source stem is unavailable for rendering.",
        )
    object_storage = getattr(request.app.state, "object_storage", None)
    if object_storage is None or not callable(getattr(object_storage, "inspect_object", None)):
        raise ApiError(
            503,
            "stem_artifact_verification_unavailable",
            "The selected source stem cannot be verified for rendering.",
        )
    try:
        await run_blocking(
            object_storage.inspect_object,
            bucket=selected["bucket"],
            path=selected["path"],
        )
    except ApiError as exc:
        if exc.code in {"storage_object_missing", "storage_path_invalid"}:
            raise ApiError(
                409,
                "stem_artifact_unavailable",
                "The selected source stem is unavailable for rendering.",
            ) from exc
        raise ApiError(
            502,
            "stem_artifact_verification_failed",
            "The selected source stem could not be verified for rendering.",
        ) from exc
    return {**raw, "active": active}
