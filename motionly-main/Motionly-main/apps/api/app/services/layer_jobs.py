import json
import time
from typing import Any
from uuid import UUID

from app.api.billing import build_billing_status
from app.api.errors import ApiError
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.effects import InMemoryEffectsRepository
from app.repositories.music import InMemoryMusicRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.regeneration import InMemoryRegenerationRepository
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer
from app.schemas.broll import BROLL_SCHEMA_VERSION, BrollGenerateRequest, BrollSlotStatus, StockOrientation
from app.schemas.captions import CaptionGenerateRequest, TranscriptStatus
from app.schemas.effects import EffectsConfigRequest, EffectsStatus
from app.schemas.music import MusicSelectRequest, MusicSelectionStatus
from app.schemas.regeneration import REGENERATION_SCHEMA_VERSION, RegenerateRequest
from app.services.ai_feedback import record_ai_feedback
from app.services.broll_intelligence import (
    apply_stage4_broll_rules,
    broll_slot_metadata,
    latest_visual_manifest_for_broll,
    visual_manifest_cache_signature,
)
from app.services.caption_chunking import chunk_caption_words
from app.services.caption_presets import CAPTION_SCHEMA_VERSION, get_caption_preset
from app.services.effects import (
    DEFAULT_AFTER_EFFECTS_PRESETS,
    after_effects_handoff_urls,
    build_effects_cache_key,
    defaults_for_preset,
    effect_count_limit_per_30s,
    normalize_motion_and_gradient,
    preview_urls,
    render_time_impact,
)
from app.services.regeneration import (
    affected_layers_for_request,
    build_regeneration_cache_key,
    preserved_layers,
    settings_summary,
)
from app.services.stock_video import (
    StockVideoProvider,
    build_broll_cache_key,
    candidate_shot_type,
    desired_requests,
    ensure_owned_stock_asset,
    keyword_query,
    latency_ms_since,
    score_candidate,
    semantic_match_score,
)
from app.services.storage import StorageSigner
from app.schemas.music import MusicRegion, MusicTrackCandidate


def execute_broll_generation(
    *,
    repository: InMemoryBrollRepository,
    billing_repository,
    providers: list[StockVideoProvider],
    storage_signer: StorageSigner,
    settings,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID | None,
    payload: BrollGenerateRequest,
) -> dict[str, Any]:
    if (
        settings.billing_enabled
        and not build_billing_status(workspace_id, billing_repository).entitlements.broll
    ):
        raise ApiError(
            402,
            "broll_plan_required",
            "Upgrade to Creator or higher to generate B-roll.",
        )
    if not providers:
        raise ApiError(
            503,
            "stock_provider_unavailable",
            "No stock video provider is configured for B-roll generation.",
        )

    started = time.perf_counter()
    plan = repository.active_plan(project_id=project_id, workspace_id=workspace_id)
    visual_manifest = latest_visual_manifest_for_broll(
        repository=repository,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    source_requests = desired_requests(plan.plan, payload.density)
    if not source_requests:
        raise ApiError(422, "broll_requests_empty", "The active edit plan has no B-roll requests.")
    duration_ms = (
        visual_manifest.duration_ms
        if visual_manifest and visual_manifest.duration_ms
        else max(plan.plan.target_duration_s * 1000, max(item.end_ms for item in source_requests))
    )
    requests = apply_stage4_broll_rules(
        requests=source_requests,
        visual_manifest=visual_manifest,
        duration_ms=duration_ms,
    )
    if not requests:
        raise ApiError(
            422,
            "broll_requests_empty",
            "The active edit plan has no B-roll that can be placed safely.",
        )

    cache_key = build_broll_cache_key(
        edit_plan_id=plan.id,
        density=payload.density,
        schema_version=BROLL_SCHEMA_VERSION,
        semantic_context=visual_manifest_cache_signature(visual_manifest),
    )
    if not payload.force:
        cached = repository.find_cached_slots(
            project_id=project_id,
            workspace_id=workspace_id,
            cache_key=cache_key,
        )
        if cached:
            for slot in cached:
                if slot.selected_asset_id:
                    asset = repository.get_asset(
                        project_id=project_id,
                        workspace_id=workspace_id,
                        asset_id=slot.selected_asset_id,
                    )
                    ensure_owned_stock_asset(
                        asset=asset,
                        repository=repository,
                        settings=settings,
                        storage_signer=storage_signer,
                    )
            return {
                "slot_ids": [str(slot.id) for slot in cached],
                "cached": True,
            }

    used_provider_asset_ids: set[str] = set()
    used_shot_types: set[str] = set()
    slots: list[dict[str, object]] = []
    for broll_request in requests:
        query = keyword_query(broll_request, plan.style.value)
        slot_duration_ms = broll_request.end_ms - broll_request.start_ms
        ranked_assets = []
        errors: list[str] = []
        for provider in providers:
            try:
                candidates = provider.search(
                    query=query,
                    orientation=StockOrientation.PORTRAIT,
                    min_width=720,
                    min_height=1280,
                )
            except ApiError as exc:
                errors.append(exc.code)
                continue
            for candidate in candidates:
                score = score_candidate(
                    candidate=candidate,
                    slot_duration_ms=slot_duration_ms,
                    duplicate_provider_asset_ids=used_provider_asset_ids,
                    semantic_query=broll_request.semantic_query or query,
                    semantic_embedding=broll_request.semantic_embedding,
                    target_shot_type=broll_request.shot_type,
                    used_shot_types=used_shot_types,
                )
                if candidate.duration_ms < 1500:
                    continue
                asset = repository.upsert_asset(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    provider=candidate.provider,
                    provider_asset_id=candidate.provider_asset_id,
                    title=candidate.title,
                    thumbnail_url=candidate.thumbnail_url,
                    preview_video_url=candidate.preview_video_url,
                    download_url=candidate.download_url,
                    license=candidate.license,
                    width=candidate.width,
                    height=candidate.height,
                    duration_ms=candidate.duration_ms,
                    orientation=candidate.orientation,
                    score=score,
                    raw_metadata={
                        **candidate.raw_metadata,
                        "query": query,
                        "semantic_query": broll_request.semantic_query or query,
                        "target_shot_type": broll_request.shot_type,
                        "latency_ms": latency_ms_since(started),
                    },
                )
                ranked_assets.append((score, asset, candidate))
            if ranked_assets:
                break

        ranked_assets.sort(key=lambda item: item[0], reverse=True)
        selected = ranked_assets[0][1] if ranked_assets else None
        selected_candidate = ranked_assets[0][2] if ranked_assets else None
        selected_shot_type = candidate_shot_type(selected_candidate) if selected_candidate else None
        selected_semantic_score = (
            semantic_match_score(
                candidate=selected_candidate,
                semantic_query=broll_request.semantic_query or query,
                semantic_embedding=broll_request.semantic_embedding,
            )
            if selected_candidate
            else None
        )
        if selected:
            selected = ensure_owned_stock_asset(
                asset=selected,
                repository=repository,
                settings=settings,
                storage_signer=storage_signer,
            )
            used_provider_asset_ids.add(selected.provider_asset_id)
        if selected_shot_type:
            used_shot_types.add(selected_shot_type)
        status = BrollSlotStatus.READY if selected else BrollSlotStatus.FAILED
        slots.append(
            {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "edit_plan_id": plan.id,
                "status": status,
                "start_ms": broll_request.start_ms,
                "end_ms": broll_request.end_ms,
                "keywords": [query, *broll_request.keywords][:8],
                "intent": f"Support transcript moment with {query}",
                "density": (
                    payload.density if payload.density != "from_plan" else plan.broll_density.value
                ),
                "priority": broll_request.priority,
                "selected_asset_id": selected.id if selected else None,
                "candidate_asset_ids": [asset.id for _, asset, _candidate in ranked_assets[:5]],
                "crossfade_ms": 260,
                "provider": selected.provider if selected else providers[0].provider_key,
                "score": ranked_assets[0][0] if ranked_assets else 0,
                "error_code": (
                    None if selected else (errors[0] if errors else "stock_asset_not_found")
                ),
                "error_message": None if selected else "No usable vertical stock clip was found.",
                "metadata": broll_slot_metadata(
                    request=broll_request,
                    query=query,
                    candidate_count=len(ranked_assets),
                    selected_shot_type=selected_shot_type,
                    selected_semantic_score=selected_semantic_score,
                    provider_errors=errors,
                    visual_manifest=visual_manifest,
                ),
            }
        )

    records = repository.create_slot_set(
        workspace_id=workspace_id,
        project_id=project_id,
        cache_key=cache_key,
        slots=slots,
    )
    return {
        "slot_ids": [str(slot.id) for slot in records],
        "cached": False,
        "failed": any(slot.status == BrollSlotStatus.FAILED for slot in records),
    }


def execute_music_selection(
    *,
    repository: InMemoryMusicRepository,
    plan_repository: InMemoryPlanRepository,
    providers,
    request,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID | None,
    payload: MusicSelectRequest,
) -> dict[str, Any]:
    from app.api.music import build_music_cache_key
    from app.services.music import candidates_for_change

    plan = plan_repository.active_plan(project_id=project_id, workspace_id=workspace_id)
    mood = payload.mood or plan.plan.music_mood
    track = None
    music_volume = payload.music_volume if payload.include_music else 0
    sfx_intensity = payload.sfx_intensity if payload.include_music else 0

    if not payload.include_music:
        mood = "none"
    elif payload.music_track_id:
        track = repository.get_track(
            project_id=project_id,
            workspace_id=workspace_id,
            track_id=payload.music_track_id,
        )
    else:
        if not providers:
            raise ApiError(
                503,
                "music_provider_not_configured",
                "Music selection is not configured for production.",
            )
        candidates: list[MusicTrackCandidate] = []
        for provider in providers:
            try:
                candidates = provider.search(mood=mood)
            except ApiError:
                continue
            if candidates:
                break
        if not candidates:
            raise ApiError(
                404,
                "music_track_not_found",
                "No music track was available for the requested mood.",
            )
        if payload.force:
            candidates = candidates_for_change(
                candidates=candidates,
                repository=repository,
                project_id=project_id,
                workspace_id=workspace_id,
                providers=providers,
            )
        candidate = candidates[0]
        track = repository.upsert_track(
            workspace_id=workspace_id,
            project_id=project_id,
            provider=candidate.provider,
            provider_track_id=candidate.provider_track_id,
            title=candidate.title,
            license=candidate.license,
            duration_ms=candidate.duration_ms,
            mood_tags=candidate.mood_tags,
            bpm=candidate.bpm,
            energy=candidate.energy,
            vocals=candidate.vocals,
            loopable=candidate.loopable,
            beat_map=candidate.beat_map,
            raw_metadata={**candidate.raw_metadata, "mood": mood},
        )

    cache_key = build_music_cache_key(
        edit_plan_id=plan.id,
        mood=mood,
        music_volume=music_volume,
        sfx_intensity=sfx_intensity,
        track_id=track.id if track else None,
    )
    if not payload.force:
        cached = repository.find_cached_selection(
            project_id=project_id,
            workspace_id=workspace_id,
            cache_key=cache_key,
        )
        if cached:
            return {"selection_id": str(cached.id), "cached": True}

    plan_duration_ms = (
        max((cut.end_ms for cut in plan.plan.cuts), default=0) or plan.plan.target_duration_s * 1000
    )
    regions = (
        [MusicRegion(id="music-0", start_ms=0, end_ms=plan_duration_ms)]
        if track and plan_duration_ms > 0
        else []
    )
    selection = repository.create_selection(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=plan.id,
        status=MusicSelectionStatus.READY,
        music_track_id=track.id if track else None,
        music_volume=music_volume,
        sfx_intensity=sfx_intensity,
        rights_confirmed=payload.rights_confirmed,
        cache_key=cache_key,
        regions=regions,
    )
    record_ai_feedback(
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        edit_plan_id=plan.id,
        event_type=(
            AIEditFeedbackEventType.REJECTED
            if not payload.include_music
            else AIEditFeedbackEventType.CHANGED
        ),
        layer=AIEditLayer.MUSIC,
        source="music_editor",
        decision_id=f"{plan.id}:music",
        action="select_music",
        ai_payload={"music_mood": plan.plan.music_mood},
        user_payload=repository.response_for_selection(selection).model_dump(mode="json"),
        metadata={"mood": mood, "force": payload.force},
    )
    return {"selection_id": str(selection.id), "cached": False}


def execute_effects_update(
    *,
    repository: InMemoryEffectsRepository,
    billing_repository,
    plan_repository: InMemoryPlanRepository,
    request,
    settings,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID | None,
    payload: EffectsConfigRequest,
) -> dict[str, Any]:
    premium_requested = (
        bool(payload.motion_effects)
        or bool(payload.motion.keyframes)
        or bool(payload.motion.clip_effects)
        or payload.motion.ken_burns.enabled
        or payload.motion.speed_ramp.enabled
        or payload.motion.beat_impact.enabled
        or payload.motion.auto_reframe.enabled
        or payload.background_removal.enabled
        or payload.gradient_ramp.enabled
        or bool(payload.after_effects_presets)
    )
    if (
        settings.billing_enabled
        and not build_billing_status(workspace_id, billing_repository).entitlements.premium_effects
        and premium_requested
    ):
        raise ApiError(
            402,
            "premium_effects_plan_required",
            "Upgrade to Pro to use premium effects.",
        )
    active_plan = next(
        (
            plan
            for plan in plan_repository.list_plans(
                project_id=project_id,
                workspace_id=workspace_id,
            )
            if plan.is_active
        ),
        None,
    )
    preset_defaults = defaults_for_preset(payload.preset)
    lut_intensity = (
        float(preset_defaults["lut_intensity"])
        if payload.reset_to_defaults
        else payload.lut_intensity
    )
    texture = preset_defaults["texture"] if payload.reset_to_defaults else payload.texture
    motion_effects = (
        preset_defaults["motion_effects"] if payload.reset_to_defaults else payload.motion_effects
    )
    motion = preset_defaults["motion"] if payload.reset_to_defaults else payload.motion
    background_removal = (
        preset_defaults["background_removal"]
        if payload.reset_to_defaults
        else payload.background_removal
    )
    gradient_ramp = (
        preset_defaults["gradient_ramp"] if payload.reset_to_defaults else payload.gradient_ramp
    )
    motion_effects, motion, gradient_ramp = normalize_motion_and_gradient(
        motion_effects=motion_effects,
        motion=motion,
        gradient_ramp=gradient_ramp,
    )
    after_effects_presets = (
        preset_defaults["after_effects_presets"]
        if payload.reset_to_defaults
        else payload.after_effects_presets or DEFAULT_AFTER_EFFECTS_PRESETS
    )
    cache_key = build_effects_cache_key(
        edit_plan_id=active_plan.id if active_plan else None,
        preset=payload.preset,
        lut_intensity=lut_intensity,
        texture=texture,
        motion_effects=motion_effects,
        motion=motion,
        background_removal=background_removal,
        gradient_ramp=gradient_ramp,
        after_effects_presets=after_effects_presets,
    )
    if not payload.force:
        cached = repository.find_cached_config(
            project_id=project_id,
            workspace_id=workspace_id,
            cache_key=cache_key,
        )
        if cached:
            return {"config_id": str(cached.id), "cached": True}

    preview_before_url, preview_after_url = preview_urls(project_id, payload.preset)
    next_version = _next_effects_version(
        repository=repository,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    after_effects_project_url, correction_manifest_url = after_effects_handoff_urls(
        project_id,
        next_version,
    )
    config = repository.create_config(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=active_plan.id if active_plan else None,
        status=EffectsStatus.READY,
        preset=payload.preset,
        lut_intensity=lut_intensity,
        texture=texture,
        motion_effects=motion_effects,
        motion=motion,
        background_removal=background_removal,
        gradient_ramp=gradient_ramp,
        after_effects_presets=after_effects_presets,
        after_effects_project_url=after_effects_project_url,
        correction_manifest_url=correction_manifest_url,
        effect_count_limit_per_30s=effect_count_limit_per_30s(motion_effects, motion),
        render_time_impact=render_time_impact(
            texture=texture,
            motion_effects=motion_effects,
            motion=motion,
            background_removal=background_removal,
            gradient_ramp=gradient_ramp,
            after_effects_presets=after_effects_presets,
        ),
        preview_before_url=preview_before_url,
        preview_after_url=preview_after_url,
        cache_key=cache_key,
    )
    record_ai_feedback(
        request=request,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        edit_plan_id=active_plan.id if active_plan else None,
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.EFFECTS,
        source="look_editor",
        decision_id=f"{active_plan.id if active_plan else project_id}:effects",
        action="update_effects",
        ai_payload=(
            {
                "color_grade": active_plan.plan.color_grade,
                "effects": active_plan.plan.effects,
                "zoom_moments": [
                    moment.model_dump(mode="json") for moment in active_plan.plan.zoom_moments
                ],
            }
            if active_plan
            else {}
        ),
        user_payload=repository.response_for_config(config).model_dump(mode="json"),
        metadata={"reset_to_defaults": payload.reset_to_defaults},
    )
    return {"config_id": str(config.id), "cached": False}


def execute_regeneration(
    *,
    repository: InMemoryRegenerationRepository,
    workspace_id: UUID,
    project_id: UUID,
    payload: RegenerateRequest,
) -> dict[str, Any]:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
    affected_layers = affected_layers_for_request(payload)
    summary = settings_summary(payload)
    controls = repository.create_control_settings(
        workspace_id=workspace_id,
        project_id=project_id,
        schema_version=REGENERATION_SCHEMA_VERSION,
        controls=payload.controls,
        action=payload.action,
        export_options=payload.export_options,
        settings_summary=summary,
    )
    artifacts = repository.create_artifact_versions(
        workspace_id=workspace_id,
        project_id=project_id,
        layers=affected_layers,
        cache_key=build_regeneration_cache_key(payload),
        settings_summary=summary,
    )
    return {
        "controls_id": str(controls.id),
        "artifact_version_ids": [str(artifact.id) for artifact in artifacts],
        "affected_layers": [layer.value for layer in affected_layers],
        "preserved_layers": [layer.value for layer in preserved_layers(affected_layers)],
    }


def execute_caption_generation(
    *,
    repository: InMemoryCaptionRepository,
    settings,
    workspace_id: UUID,
    project_id: UUID,
    payload: CaptionGenerateRequest,
) -> dict[str, Any]:
    transcript = (
        repository.get_transcript(
            transcript_id=payload.transcript_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        if payload.transcript_id
        else repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    )
    if not transcript:
        raise ApiError(404, "transcript_not_found", "Generate a transcript before captions.")
    if transcript.status != TranscriptStatus.READY:
        raise ApiError(
            409,
            str(transcript.status),
            transcript.error_message or "Transcript is not ready.",
        )

    from app.api.captions import translate_segments_if_requested

    preset = get_caption_preset(payload.preset)
    segments = chunk_caption_words(
        transcript.words,
        hide_filler_words=payload.hide_filler_words,
        style=preset.style,
    )
    segments = translate_segments_if_requested(
        segments=segments,
        source_language=transcript.language,
        output_language=payload.output_language,
        settings=settings,
    )
    if not segments:
        raise ApiError(422, "caption_segments_empty", "No captionable words were found.")

    track = repository.create_caption_track(
        workspace_id=workspace_id,
        project_id=project_id,
        transcript_id=transcript.id,
        preset=payload.preset,
        schema_version=CAPTION_SCHEMA_VERSION,
        style=preset.style,
        hide_filler_words=payload.hide_filler_words,
        segments=segments,
    )
    return {"track_id": str(track.id)}


def parse_layer_job_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ApiError(422, "queue_job_payload_invalid", "Queue job payload must be a JSON object.")
    return data


def _next_effects_version(
    *,
    repository: InMemoryEffectsRepository,
    project_id: UUID,
    workspace_id: UUID,
) -> int:
    next_version = getattr(repository, "next_version", None)
    if callable(next_version):
        return next_version(project_id=project_id, workspace_id=workspace_id)
    return 1
