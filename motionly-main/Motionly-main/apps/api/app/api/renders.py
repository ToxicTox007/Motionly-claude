import asyncio
import json
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.billing import enforce_render_entitlement, record_render_usage
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
from app.repositories.renders import InMemoryRenderRepository
from app.schemas.billing import BillingPlanKey
from app.schemas.broll import BrollSlotStatus
from app.schemas.captions import CaptionSegment, TranscriptWord
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
from app.schemas.uploads import UploadStatus
from app.services.ai_feedback import record_render_kept_signals
from app.services.ass_captions import caption_track_to_ass
from app.services.billing import PLAN_DEFINITIONS
from app.services.cut_planning import PLANNER_VERSION, build_crop_warnings, plan_keep_segments
from app.services.queue import RenderQueue
from app.services.render_jobs import complete_render_job
from app.services.render_templates import (
    build_props_hash,
    cap_keep_segments_to_duration,
    effect_compatibility,
    export_settings,
    render_specs_for_options,
    select_template,
)
from app.services.storage import StorageSigner
from app.services.upload_validation import assert_video_render_ready

router = APIRouter(tags=["render and exports"])

ActorDep = Annotated[Actor, Depends(get_actor)]
RenderRepositoryDep = Annotated[InMemoryRenderRepository, Depends(get_render_repository)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]
BrollRepositoryDep = Annotated[InMemoryBrollRepository, Depends(get_broll_repository)]
RenderQueueDep = Annotated[RenderQueue, Depends(get_render_queue)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]

_RENDERER_VERSIONS: dict[RenderEngine, str] = {
    RenderEngine.FFMPEG_BASIC: "ffmpeg-caption-broll-v2",
    RenderEngine.REMOTION_PREMIUM: "remotion-premium-v1",
}


def _select_render_engine(
    *,
    settings,
    billing_plan_key: str,
    has_active_broll: bool,
    has_music: bool,
    has_effects: bool,
) -> RenderEngine:
    """
    Promotes to REMOTION_PREMIUM when:
    - REMOTION_ENABLED=true in settings
    - user is on a paid plan (or billing is disabled / personal mode)
    - at least one premium feature is active (B-roll, music, or visual effects)

    Falls back to FFMPEG_BASIC in all other cases (zero regression risk).
    """
    if not getattr(settings, "remotion_enabled", False):
        return RenderEngine.FFMPEG_BASIC
    paid_plans = {"creator", "pro", "agency"}
    billing_disabled = not getattr(settings, "billing_enabled", False)
    is_eligible = billing_plan_key in paid_plans or billing_disabled
    has_premium_feature = has_active_broll or has_music or has_effects
    if is_eligible and has_premium_feature:
        return RenderEngine.REMOTION_PREMIUM
    return RenderEngine.FFMPEG_BASIC


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
        "strategy": "report alignment; speech cuts are not moved by the FFmpeg fallback",
    }


def apply_motion_design_export_settings(
    *,
    export_settings: dict[str, object],
    source_metadata,
    effects_config,
) -> tuple[dict[str, object], dict[str, object]]:
    motion = effects_config.motion
    motion_design: dict[str, object] = {
        "schema_version": 1,
        "keyframe_count": len(motion.keyframes),
        "ken_burns": motion.ken_burns.model_dump(mode="json"),
        "speed_ramp": motion.speed_ramp.model_dump(mode="json"),
        "beat_impact": motion.beat_impact.model_dump(mode="json"),
        "auto_reframe": {
            **motion.auto_reframe.model_dump(mode="json"),
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
    crop["x"] = round(motion.auto_reframe.subject_x_pct / 100, 4)
    crop["y"] = round(motion.auto_reframe.subject_y_pct / 100, 4)
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
    }
    return updated, motion_design


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
    if request.app.state.settings.billing_enabled:
        billing_status = enforce_render_entitlement(
            workspace_id=actor.workspace_id,
            repository=billing_repository,
        )
    else:
        personal_plan = PLAN_DEFINITIONS[BillingPlanKey.AGENCY]
        billing_status = SimpleNamespace(
            current_plan=personal_plan,
            entitlements=personal_plan.entitlements,
            can_render=True,
        )
    upload = repository.get_active_upload(project_id=project_id, workspace_id=actor.workspace_id)
    if upload.status != UploadStatus.READY:
        raise ApiError(
            409,
            "source_video_not_ready",
            "Finish upload preprocessing before rendering an export.",
        )
    readiness = assert_video_render_ready(upload.metadata)
    upload.metadata.warnings = readiness.warning_codes

    track = (
        repository.get_caption_track(
            project_id=project_id,
            caption_track_id=payload.caption_track_id,
            workspace_id=actor.workspace_id,
        )
        if payload.caption_track_id
        else repository.latest_caption_track(project_id=project_id, workspace_id=actor.workspace_id)
    )
    if not track.segments:
        raise ApiError(422, "caption_segments_empty", "Generate captions before rendering.")
    caption_words = [word for segment in track.segments for word in segment.words]

    edit_plan_record = None
    if payload.edit_plan_id:
        edit_plan_record = plan_repository.get_plan(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            plan_id=payload.edit_plan_id,
        )
    else:
        edit_plan_record = next(
            (
                plan
                for plan in plan_repository.list_plans(
                    project_id=project_id,
                    workspace_id=actor.workspace_id,
                )
                if plan.is_active
            ),
            None,
        )

    effective_duration_preset = (
        payload.duration_preset
        if payload.adjustment_action != RenderAdjustmentAction.MAKE_PUNCHIER
        else DurationPreset.SHORT
    )
    keep_segments = cap_keep_segments_to_duration(
        segments=plan_keep_segments(
            words=caption_words,
            caption_segments=track.segments,
            metadata=upload.metadata,
        ),
        duration_preset=effective_duration_preset,
    )
    retimed_caption_segments = retime_caption_segments_for_keep_segments(
        segments=track.segments,
        keep_segments=keep_segments,
    )
    caption_segments_for_render = retimed_caption_segments if payload.include_captions else []
    caption_words_for_render = [
        word for segment in retimed_caption_segments for word in segment.words
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
    if effective_duration_preset != payload.duration_preset:
        render_export_settings["requested_duration_preset"] = payload.duration_preset.value
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
        broll_repository.list_slots(project_id=project_id, workspace_id=actor.workspace_id)
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
        asset = broll_repository.get_asset(
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
            asset_source = request.app.state.storage_signer.create_signed_download(
                bucket=str(storage_bucket),
                path=str(storage_path),
                expires_in_seconds=3600,
            ).url
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
        music_selection = music_repository.latest_selection(
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    if music_selection and edit_plan_record and music_selection.edit_plan_id != edit_plan_record.id:
        music_selection = None
    if music_selection:
        music_props = {
            "selection_id": str(music_selection.id),
            "music_track_id": (
                str(music_selection.music_track_id) if music_selection.music_track_id else None
            ),
            "music_volume": music_selection.music_volume,
            "sfx_intensity": music_selection.sfx_intensity,
            "regions": [region.model_dump(mode="json") for region in music_selection.regions],
        }
        if music_selection.music_track_id and music_repository:
            music_track = music_repository.get_track(
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
            music_props["beat_sync"] = beat_sync_summary(
                keep_segments=keep_segments,
                beat_map=music_track.beat_map,
                tolerance_ms=140,
            )
    effects_repository = getattr(request.app.state, "effects_repository", None)
    effects_config = None
    effects_props = None
    motion_design_manifest = None
    if effects_repository:
        effects_config = effects_repository.latest_config(
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
    if effects_config and edit_plan_record and effects_config.edit_plan_id != edit_plan_record.id:
        effects_config = None
    if effects_config:
        effects_props = effects_config.to_response().model_dump(mode="json")
        render_export_settings, motion_design_manifest = apply_motion_design_export_settings(
            export_settings=render_export_settings,
            source_metadata=upload.metadata,
            effects_config=effects_config,
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
    render_props = {
        "sourceVideo": {
            "src": f"supabase://{upload.storage_bucket}/{upload.storage_path}",
            "width": upload.metadata.width or specs.width,
            "height": upload.metadata.height or specs.height,
            "duration_ms": upload.metadata.duration_ms,
        },
        "editPlan": render_edit_plan,
        "captions": [segment.model_dump(mode="json") for segment in caption_segments_for_render],
        "words": [word.model_dump(mode="json") for word in caption_words_for_render],
        "captionStyle": track.style.model_dump(mode="json"),
        "assets": active_broll_assets,
        "templateId": template.id.value,
        "exportSettings": render_props_export_settings,
        "watermark": billing_status.current_plan.entitlements.watermark,
        "musicSelection": music_props,
        "effectsConfig": effects_props,
        "sourceStems": _source_stems_for_render(
            request=request,
            project_id=project_id,
            workspace_id=actor.workspace_id,
        ),
    }
    props_hash = build_props_hash(render_props)
    selected_engine = _select_render_engine(
        settings=request.app.state.settings,
        billing_plan_key=billing_status.current_plan.key.value,
        has_active_broll=bool(active_broll_assets),
        has_music=bool(music_selection and music_selection.music_track_id),
        has_effects=bool(effects_config),
    )
    compute_tier = (
        "gpu"
        if selected_engine == RenderEngine.FFMPEG_BASIC
        and getattr(request.app.state.settings, "ffmpeg_gpu_enabled", False)
        else "cpu"
    )
    manifest = {
        "schema_version": 1,
        "source_video_id": str(upload.id),
        "caption_track_id": str(track.id),
        "include_captions": payload.include_captions,
        "include_broll": payload.include_broll,
        "adjustment_action": payload.adjustment_action.value if payload.adjustment_action else None,
        "edit_plan_id": str(edit_plan_record.id) if edit_plan_record else None,
        "music_selection_id": str(music_selection.id) if music_selection else None,
        "effects_config_id": str(effects_config.id) if effects_config else None,
        "motion_design": motion_design_manifest,
        "after_effects_handoff": (
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
        "export_settings": render_export_settings,
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
            "bucket": upload.storage_bucket,
            "path": upload.storage_path,
        },
        "caption_ass": (
            caption_track_to_ass(segments=caption_segments_for_render, style=track.style)
            if payload.include_captions
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
                if payload.include_captions
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
    job = repository.create_render_job(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        caption_track_id=track.id,
        status=RenderJobStatus.QUEUED,
        planner_version=PLANNER_VERSION,
        manifest=manifest,
        keep_segments=keep_segments,
        specs=specs,
        template_id=template.id,
        render_engine=selected_engine,
        renderer_version=_RENDERER_VERSIONS[selected_engine],
        props_hash=props_hash,
        logs=[
            "render manifest prepared",
            f"template={template.id.value}",
            f"include_captions={payload.include_captions}",
            f"include_broll={payload.include_broll}",
            f"broll_assets={len(active_broll_assets)}",
            f"compute_tier={compute_tier}",
            f"adjustment_action={payload.adjustment_action.value}"
            if payload.adjustment_action
            else "adjustment_action=none",
        ],
        progress_pct=5,
    )
    record_render_kept_signals(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=edit_plan_record.id if edit_plan_record else None,
        render_job_id=job.id,
        render_props=render_props,
    )
    if request.app.state.settings.billing_enabled:
        record_render_usage(
            workspace_id=actor.workspace_id,
            render_job_id=job.id,
            repository=billing_repository,
            plan=billing_status.current_plan.key,
        )
    queue_job = render_queue.enqueue_render(
        render_job_id=job.id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        priority=10 if billing_status.current_plan.entitlements.priority_queue else 0,
        render_engine=selected_engine.value,
        compute_tier=compute_tier,
    )

    if request.app.state.settings.should_complete_renders_synchronously:
        job = complete_render_job(
            job=job,
            repository=repository,
            export_bucket=request.app.state.settings.export_storage_bucket,
            storage_root=request.app.state.settings.media_work_dir,
            supabase_url=(
                str(request.app.state.settings.supabase_url)
                if getattr(request.app.state.settings, "supabase_url", None)
                else None
            ),
            service_role_key=getattr(
                request.app.state.settings,
                "supabase_service_role_key",
                None,
            ),
            execute_ffmpeg=request.app.state.settings.render_execute_ffmpeg,
            allow_fixture_media=(
                getattr(request.app.state.settings, "app_env", "test") != "production"
                or getattr(request.app.state.settings, "allow_fixture_providers", False)
            ),
        )
    return RenderCreateResponse(
        job=job.to_response(repository.events_for_job(job.id)),
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
    responses: list[RenderCreateResponse] = []
    for export_options in payload.export_targets:
        response = await create_render(
            project_id=project_id,
            payload=RenderCreateRequest(
                caption_track_id=payload.caption_track_id,
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
            5,
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
        first_segment_for_keep = True
        for segment in segments:
            overlap_start = max(segment.start_ms, keep.start_ms)
            overlap_end = min(segment.end_ms, keep.end_ms)
            if overlap_end <= overlap_start:
                continue
            next_start = (
                output_start
                if first_segment_for_keep
                else output_start + (overlap_start - keep.start_ms)
            )
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
            first_segment_for_keep = False
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
) -> RenderListResponse:
    return RenderListResponse(
        jobs=[
            job.to_response(repository.events_for_job(job.id))
            for job in repository.list_render_jobs(
                project_id=project_id,
                workspace_id=actor.workspace_id,
            )
        ],
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
    job = repository.get_render_job(
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=actor.workspace_id,
    )
    return job.to_response(repository.events_for_job(job.id))


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
        while True:
            if await request.is_disconnected():
                break
            job = repository.get_render_job(
                project_id=project_id,
                render_job_id=render_job_id,
                workspace_id=actor.workspace_id,
            )
            payload = job.to_response(repository.events_for_job(job.id)).model_dump(
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
            if job.status in terminal:
                break
            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/projects/{project_id}/renders/{render_job_id}/cancel",
    response_model=RenderJobResponse,
)
async def cancel_render(
    project_id: UUID,
    render_job_id: UUID,
    actor: ActorDep,
    repository: RenderRepositoryDep,
) -> RenderJobResponse:
    job = repository.get_render_job(
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
    job = repository.transition_job(
        job=job,
        status=RenderJobStatus.CANCELED,
        progress_pct=job.progress_pct,
        message="Render canceled.",
    )
    return job.to_response(repository.events_for_job(job.id))


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
) -> RenderCreateResponse:
    job = repository.retry_project_render_job(
        project_id=project_id,
        render_job_id=render_job_id,
        workspace_id=actor.workspace_id,
    )
    queue_job = render_queue.enqueue_render(
        render_job_id=job.id,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        render_engine=(job.render_engine or RenderEngine.FFMPEG_BASIC).value,
        compute_tier=str(job.manifest.get("compute_tier") or "cpu"),
    )
    return RenderCreateResponse(
        job=job.to_response(repository.events_for_job(job.id)),
        job_id=job.id,
        queue_job_id=queue_job.id,
        status=job.status,
    )


@router.get("/projects/{project_id}/exports", response_model=ExportListResponse)
async def list_exports(
    project_id: UUID,
    actor: ActorDep,
    repository: RenderRepositoryDep,
) -> ExportListResponse:
    return ExportListResponse(
        exports=[
            export.to_response()
            for export in repository.list_exports(
                project_id=project_id,
                workspace_id=actor.workspace_id,
            )
        ],
    )


@router.get("/exports/{export_id}/download-url", response_model=ExportDownloadUrlResponse)
async def get_export_download_url(
    export_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: RenderRepositoryDep,
    storage_signer: StorageSignerDep,
) -> ExportDownloadUrlResponse:
    export = repository.get_export(export_id=export_id, workspace_id=actor.workspace_id)
    ttl = request.app.state.settings.export_signed_url_ttl_seconds
    signed = storage_signer.create_signed_download(
        bucket=export.storage_bucket,
        path=export.storage_path,
        expires_in_seconds=ttl,
    )
    return ExportDownloadUrlResponse(
        export_id=export.id,
        signed_download_url=signed.url,
        expires_in_seconds=ttl,
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


def _source_stems_for_render(
    *, request: Request, project_id: UUID, workspace_id: UUID
) -> dict[str, object] | None:
    scale_repository = getattr(request.app.state, "scale_repository", None)
    if scale_repository is None:
        return None
    resource = scale_repository.latest(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="stem_separation",
    )
    if resource is None or resource.status.value != "ready":
        return None
    return resource.result
