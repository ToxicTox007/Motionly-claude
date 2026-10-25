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
from app.schemas.broll import (
    BROLL_SCHEMA_VERSION,
    BrollConfidenceLabel,
    BrollGenerateRequest,
    BrollSlotStatus,
    StockOrientation,
)
from app.schemas.captions import CaptionGenerateRequest, TranscriptStatus
from app.schemas.effects import EffectsConfigRequest, EffectsStatus
from app.schemas.music import MusicRegion, MusicSelectionStatus, MusicSelectRequest
from app.schemas.regeneration import REGENERATION_SCHEMA_VERSION, RegenerateRequest
from app.services.ai_feedback import record_ai_feedback
from app.services.broll_intelligence import (
    apply_stage4_broll_rules,
    broll_slot_metadata,
    latest_visual_manifest_for_broll,
    manifest_embeddings_match,
    visual_manifest_cache_signature,
    without_scene_embeddings,
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
from app.services.matte_jobs import enqueue_matte_for_upload
from app.services.model_gateway import FeatureQuotaError
from app.services.music import MusicTrackCandidate, catalog_rights_snapshot, rights_fingerprint
from app.services.regeneration import (
    affected_layers_for_request,
    build_regeneration_cache_key,
    preserved_layers,
    settings_summary,
)
from app.services.stock_video import (
    DEFAULT_EMBEDDING_MODEL_NAME,
    StockVideoCandidate,
    StockVideoProvider,
    build_broll_cache_key,
    build_stock_asset_raw_metadata,
    candidate_shot_type,
    desired_requests,
    enqueue_stock_asset_enrichment,
    ensure_owned_stock_asset,
    keyword_query,
    latency_ms_since,
    query_embedding,
    score_candidate_details,
    semantic_match_score,
)
from app.services.storage import StorageSigner
from app.services.transcript_quality import is_transcript_usable_for_planning


def execute_broll_generation(
    *,
    repository: InMemoryBrollRepository,
    billing_repository,
    providers: list[StockVideoProvider],
    storage_signer: StorageSigner,
    stock_asset_enrichment_queue,
    settings,
    embedding_provider=None,
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
    if not manifest_embeddings_match(
        visual_manifest, getattr(embedding_provider, "preferred_model_revision", None)
    ):
        # The scene vectors (and any the planner copied from them) were made by a
        # different embedding model; embed each slot's query with the active one.
        visual_manifest = without_scene_embeddings(visual_manifest)
        source_requests = [
            request.model_copy(update={"semantic_embedding": []}) for request in source_requests
        ]
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

    preferred_embedding_revision = getattr(
        embedding_provider,
        "preferred_model_revision",
        None,
    )
    embedding_cache_version = getattr(
        embedding_provider,
        "cache_version",
        getattr(settings, "visual_clip_model", DEFAULT_EMBEDDING_MODEL_NAME),
    )
    cache_key = build_broll_cache_key(
        edit_plan_id=plan.id,
        density=payload.density,
        schema_version=BROLL_SCHEMA_VERSION,
        semantic_context={
            "visual": visual_manifest_cache_signature(visual_manifest),
            "embedding_routes": embedding_cache_version,
        },
    )
    if not payload.force:
        cached = repository.find_cached_slots(
            project_id=project_id,
            workspace_id=workspace_id,
            cache_key=cache_key,
        )
        if cached and any(bool(slot.metadata.get("embedding_fallback_used")) for slot in cached):
            cached = []
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
    adjacent_embeddings: list[list[float]] = []
    clip_model_name = getattr(settings, "visual_clip_model", DEFAULT_EMBEDDING_MODEL_NAME)
    slots: list[dict[str, object]] = []
    for broll_request in requests:
        query = keyword_query(broll_request, plan.style.value)
        used_gateway_embedding = not bool(broll_request.semantic_embedding)
        embedding_quota_refused = False
        if broll_request.semantic_embedding:
            slot_embedding = broll_request.semantic_embedding
        else:
            try:
                slot_embedding = query_embedding(
                    semantic_query=broll_request.semantic_query or query,
                    clip_model_name=clip_model_name,
                    embedding_provider=embedding_provider,
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
            except FeatureQuotaError:
                # The plan's embeddings quota is used up: rank this slot lexically
                # and keep the slot set out of the cache so it is redone later.
                slot_embedding = []
                embedding_quota_refused = True
        selected_embedding_revision = getattr(
            embedding_provider,
            "model_version",
            clip_model_name,
        )
        embedding_fallback_used = bool(
            embedding_quota_refused
            or (
                used_gateway_embedding
                and preferred_embedding_revision
                and selected_embedding_revision != preferred_embedding_revision
            )
        )
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
                score_details = score_candidate_details(
                    candidate=candidate,
                    slot_duration_ms=slot_duration_ms,
                    duplicate_provider_asset_ids=used_provider_asset_ids,
                    semantic_query=broll_request.semantic_query or query,
                    semantic_embedding=slot_embedding,
                    target_shot_type=broll_request.shot_type,
                    used_shot_types=used_shot_types,
                    adjacent_embeddings=adjacent_embeddings,
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
                    score=score_details.total,
                    raw_metadata={
                        **build_stock_asset_raw_metadata(
                            candidate=candidate,
                            query=query,
                            semantic_query=broll_request.semantic_query or query,
                            target_shot_type=broll_request.shot_type,
                        ),
                        "latency_ms": latency_ms_since(started),
                        "confidence_label": score_details.confidence_label.value,
                        "score_breakdown": {
                            "semantic": round(score_details.semantic_score, 4),
                            "embedding": round(score_details.embedding_score, 4),
                            "lexical": round(score_details.lexical_score, 4),
                            "orientation": round(score_details.orientation_score, 4),
                            "duration": round(score_details.duration_score, 4),
                            "diversity": round(score_details.diversity_score, 4),
                            "provider_license": round(score_details.provider_license_score, 4),
                        },
                    },
                )
                enqueue_stock_asset_enrichment(
                    queue=stock_asset_enrichment_queue,
                    asset=asset,
                    clip_model_name=clip_model_name,
                )
                candidate = StockVideoCandidate(
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
                    relevance_rank=candidate.relevance_rank,
                    provider_url=candidate.provider_url,
                    source_url=candidate.source_url,
                    license_metadata=candidate.license_metadata,
                    attribution_metadata=candidate.attribution_metadata,
                    raw_metadata=asset.raw_metadata,
                )
                score_details = score_candidate_details(
                    candidate=candidate,
                    slot_duration_ms=slot_duration_ms,
                    duplicate_provider_asset_ids=used_provider_asset_ids,
                    semantic_query=broll_request.semantic_query or query,
                    semantic_embedding=slot_embedding,
                    target_shot_type=broll_request.shot_type,
                    used_shot_types=used_shot_types,
                    adjacent_embeddings=adjacent_embeddings,
                )
                asset = repository.update_asset_metadata(
                    asset=asset,
                    score=score_details.total,
                    raw_metadata={
                        "confidence_label": score_details.confidence_label.value,
                        "score_breakdown": {
                            "semantic": round(score_details.semantic_score, 4),
                            "embedding": round(score_details.embedding_score, 4),
                            "lexical": round(score_details.lexical_score, 4),
                            "orientation": round(score_details.orientation_score, 4),
                            "duration": round(score_details.duration_score, 4),
                            "diversity": round(score_details.diversity_score, 4),
                            "provider_license": round(score_details.provider_license_score, 4),
                        },
                    },
                )
                ranked_assets.append((score_details.total, asset, candidate, score_details))
            if ranked_assets:
                break

        ranked_assets.sort(key=lambda item: item[0], reverse=True)
        selected = ranked_assets[0][1] if ranked_assets else None
        selected_candidate = ranked_assets[0][2] if ranked_assets else None
        selected_score = ranked_assets[0][3] if ranked_assets else None
        selected_shot_type = candidate_shot_type(selected_candidate) if selected_candidate else None
        selected_semantic_score = (
            semantic_match_score(
                candidate=selected_candidate,
                semantic_query=broll_request.semantic_query or query,
                semantic_embedding=slot_embedding,
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
            selected_embedding = selected.raw_metadata.get("embedding")
            if isinstance(selected_embedding, list):
                adjacent_embeddings.append(
                    [float(value) for value in selected_embedding if isinstance(value, int | float)]
                )
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
                "candidate_asset_ids": [
                    asset.id for _, asset, _candidate, _details in ranked_assets[:5]
                ],
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
                )
                | {
                    "confidence_label": (
                        selected_score.confidence_label.value
                        if selected_score
                        else BrollConfidenceLabel.LOW.value
                    ),
                    "ranking_mode": (
                        "embedding"
                        if selected_score and selected_score.embedding_score > 0
                        else "lexical_fallback"
                    ),
                    "validation_errors": (
                        selected_score.reasons if selected_score else ["no_candidate"]
                    ),
                    "embedding_model_revision": selected_embedding_revision,
                    "embedding_fallback_used": embedding_fallback_used,
                }
                | ({"embedding_unavailable": "quota_exceeded"} if embedding_quota_refused else {}),
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
    from app.api.music import build_music_cache_key, candidates_for_change

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

    if not payload.include_music:
        rights_snapshot: dict[str, object] = {
            "status": "not_applicable",
            "verified": True,
            "verified_at": None,
        }
    elif track is not None:
        rights_snapshot = catalog_rights_snapshot(
            track,
            acknowledged=payload.rights_decision,
        )
        settings = getattr(getattr(request, "app", None), "state", None)
        app_env = getattr(getattr(settings, "settings", None), "app_env", "test")
        if app_env == "production" and not rights_snapshot["verified"]:
            raise ApiError(
                409,
                "music_rights_unverified",
                (
                    "Select a currently cleared licensed catalog track and "
                    "acknowledge its usage terms."
                ),
            )
    else:
        rights_snapshot = {"status": "unverified", "verified": False}

    cache_key = (
        build_music_cache_key(
            edit_plan_id=plan.id,
            mood=mood,
            music_volume=music_volume,
            sfx_intensity=sfx_intensity,
            track_id=payload.music_track_id,
        )
        + f":rights:{rights_fingerprint(rights_snapshot)}"
    )
    if not payload.force:
        cached = repository.find_cached_selection(
            project_id=project_id,
            workspace_id=workspace_id,
            cache_key=cache_key,
        )
        if cached and bool(cached.rights_snapshot.get("verified")):
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
        rights_confirmed=False,
        rights_snapshot=rights_snapshot,
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
    matte_job_id = None
    if background_removal.enabled and request is not None:
        visual_repository = getattr(request.app.state, "visual_repository", None)
        matte_queue = getattr(request.app.state, "matte_queue", None)
        if visual_repository is not None and matte_queue is not None:
            upload = plan_repository.get_active_upload(
                project_id=project_id,
                workspace_id=workspace_id,
            )
            _matte, matte_job = enqueue_matte_for_upload(
                repository=visual_repository,
                matte_queue=matte_queue,
                workspace_id=workspace_id,
                project_id=project_id,
                upload_id=upload.id,
                background_removal=background_removal,
                force=payload.force,
                runtime=getattr(request.app.state, "inference_runtime", None),
            )
            matte_job_id = matte_job.id if matte_job else None
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
    return {"config_id": str(config.id), "cached": False, "matte_job_id": matte_job_id}


def execute_regeneration(
    *,
    repository: InMemoryRegenerationRepository,
    workspace_id: UUID,
    project_id: UUID,
    payload: RegenerateRequest,
    control_settings_id: UUID | None = None,
) -> dict[str, Any]:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
    affected_layers = affected_layers_for_request(payload)
    summary = settings_summary(payload)
    cache_key = build_regeneration_cache_key(payload)

    controls = None
    if control_settings_id is not None:
        controls = repository.get_control_settings(control_settings_id)

    if controls is not None:
        artifacts = repository.get_artifact_versions_by_cache_key(
            project_id=project_id,
            cache_key=cache_key,
        )
    else:
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
            cache_key=cache_key,
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
    model_gateway=None,
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
    if not transcript.words or not is_transcript_usable_for_planning(transcript.quality_status):
        reason = (
            transcript.quality_reasons[0]
            if transcript.quality_reasons
            else "transcript_quality_blocked"
        )
        raise ApiError(
            409,
            reason,
            transcript.error_message or "Transcript quality must be recovered before captions.",
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
        model_gateway=model_gateway,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if not segments:
        raise ApiError(422, "caption_segments_empty", "No captionable words were found.")

    output_language = (payload.output_language or "original").strip().lower()
    target_language = (
        transcript.language if output_language in {"", "original", "auto"} else output_language
    )
    track = repository.create_caption_track(
        workspace_id=workspace_id,
        project_id=project_id,
        transcript_id=transcript.id,
        preset=payload.preset,
        schema_version=CAPTION_SCHEMA_VERSION,
        style=preset.style,
        hide_filler_words=payload.hide_filler_words,
        language=target_language,
        review_status=("approved" if target_language == transcript.language else "needs_review"),
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
