import hashlib
import json
from typing import Any
from uuid import UUID

from app.api.errors import ApiError
from app.schemas.plans import BrollRequest
from app.schemas.visual import SceneSegment, SceneType, VideoManifest

CRITICAL_FACE_SCORE_THRESHOLD = 0.55
FACE_PADDING_MS = 220
RETURN_TO_SUBJECT_GAP_MS = 700
CONSECUTIVE_GAP_MS = 350
MAX_CONSECUTIVE_BROLL_CUTS = 3


def latest_visual_manifest_for_broll(
    *,
    repository: Any,
    project_id: UUID,
    workspace_id: UUID,
) -> VideoManifest | None:
    plan_repository = getattr(repository, "plan_repository", None)
    visual_repository = getattr(plan_repository, "visual_repository", None)
    if plan_repository is None or visual_repository is None:
        return None
    try:
        upload = plan_repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        )
    except ApiError:
        return None
    record = visual_repository.latest_ready_for_upload(
        uploaded_video_id=upload.id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    return record.manifest if record and record.manifest else None


def visual_manifest_cache_signature(visual_manifest: VideoManifest | None) -> str:
    if visual_manifest is None:
        return "none"
    scenes = [
        {
            "index": scene.index,
            "start_ms": scene.start_ms,
            "end_ms": scene.end_ms,
            "caption": scene.caption,
            "scene_type": scene.scene_type.value,
            "importance": round(scene.visual_importance_score, 3),
            "objects": [obj.label for obj in scene.objects[:6]],
            "faces": len(scene.faces),
            "embedding_hash": _embedding_hash(scene.embedding),
        }
        for scene in visual_manifest.scenes[:24]
    ]
    payload = {
        "duration_ms": visual_manifest.duration_ms,
        "provider": visual_manifest.provider,
        "frame_count": visual_manifest.frame_count,
        "scene_count": len(visual_manifest.scenes),
        "scenes": scenes,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def apply_stage4_broll_rules(
    *,
    requests: list[BrollRequest],
    visual_manifest: VideoManifest | None,
    duration_ms: int,
) -> list[BrollRequest]:
    if not requests:
        return []
    duration_ms = max(duration_ms, max(request.end_ms for request in requests))
    scenes = (
        sorted(visual_manifest.scenes, key=lambda scene: scene.start_ms) if visual_manifest else []
    )
    critical_scenes = _critical_face_scenes(scenes)
    enriched = []
    for request in sorted(requests, key=lambda item: item.start_ms):
        scene = _best_scene_for_request(request=request, scenes=scenes)
        candidate = _enrich_request_from_scene(request=request, scene=scene)
        placed = _avoid_critical_face_overlap(
            request=candidate,
            critical_scenes=critical_scenes,
            duration_ms=duration_ms,
        )
        if placed is not None:
            placed = _reanchor_after_move(
                request=placed,
                original_scene=scene,
                scenes=scenes,
            )
            enriched.append(placed)
    return _enforce_consecutive_limit(
        requests=enriched,
        critical_scenes=critical_scenes,
        duration_ms=duration_ms,
    )


def broll_slot_metadata(
    *,
    request: BrollRequest,
    query: str,
    candidate_count: int,
    selected_shot_type: str | None,
    selected_semantic_score: float | None,
    provider_errors: list[str],
    visual_manifest: VideoManifest | None,
) -> dict[str, object]:
    return {
        "stage": 4,
        "semantic_search": bool(request.semantic_query or request.semantic_embedding),
        "semantic_query": request.semantic_query or query,
        "semantic_embedding": request.semantic_embedding,
        "semantic_embedding_dimensions": len(request.semantic_embedding),
        "source_scene_index": request.source_scene_index,
        "source_scene_type": request.source_scene_type,
        "shot_type": request.shot_type,
        "selected_shot_type": selected_shot_type,
        "placement_rule": request.placement_rule,
        "avoid_face_overlap": request.avoid_face_overlap,
        "max_consecutive_broll_cuts": MAX_CONSECUTIVE_BROLL_CUTS,
        "candidate_count": candidate_count,
        "provider_errors": provider_errors,
        "semantic_score": selected_semantic_score,
        "visual_manifest_signature": visual_manifest_cache_signature(visual_manifest),
        "selection_rules": [
            "clip_embedding_similarity",
            "shot_type_diversity",
            "critical_face_avoidance",
            "max_three_consecutive_broll",
            "vertical_quality",
            "duration_fit",
        ],
    }


def _enrich_request_from_scene(
    *, request: BrollRequest, scene: SceneSegment | None
) -> BrollRequest:
    if scene is None:
        fallback_query = " ".join(request.keywords[:2])[:180]
        return request.model_copy(
            update={
                "semantic_query": request.semantic_query or fallback_query or None,
                "shot_type": request.shot_type or "cutaway",
                "placement_rule": request.placement_rule or "semantic_anchor",
                "avoid_face_overlap": request.avoid_face_overlap,
            }
        )
    semantic_query = request.semantic_query or _semantic_query_for_scene(
        request=request,
        scene=scene,
    )
    return request.model_copy(
        update={
            "semantic_query": semantic_query,
            "semantic_embedding": request.semantic_embedding or scene.embedding,
            "source_scene_index": (
                request.source_scene_index
                if request.source_scene_index is not None
                else scene.index
            ),
            "source_scene_type": request.source_scene_type or scene.scene_type.value,
            "shot_type": (
                request.shot_type if request.shot_type != "cutaway" else _shot_type_for_scene(scene)
            ),
            "placement_rule": request.placement_rule or "semantic_anchor",
            "avoid_face_overlap": request.avoid_face_overlap,
        }
    )


def _reanchor_after_move(
    *,
    request: BrollRequest,
    original_scene: SceneSegment | None,
    scenes: list[SceneSegment],
) -> BrollRequest:
    if not request.placement_rule.startswith("avoid_face_overlap"):
        return request
    scene = _best_scene_for_request(request=request, scenes=scenes)
    if scene is None or (original_scene is not None and scene.index == original_scene.index):
        return request
    return request.model_copy(
        update={
            "semantic_query": _semantic_query_for_scene(request=request, scene=scene),
            "semantic_embedding": scene.embedding,
            "source_scene_index": scene.index,
            "source_scene_type": scene.scene_type.value,
            "shot_type": _shot_type_for_scene(scene),
        }
    )


def _avoid_critical_face_overlap(
    *,
    request: BrollRequest,
    critical_scenes: list[SceneSegment],
    duration_ms: int,
) -> BrollRequest | None:
    if not request.avoid_face_overlap or not _overlaps_any_critical(request, critical_scenes):
        return request
    slot_duration = request.end_ms - request.start_ms
    gap = _closest_face_safe_gap(
        preferred_start=request.start_ms,
        slot_duration=slot_duration,
        critical_scenes=critical_scenes,
        duration_ms=duration_ms,
    )
    if gap is None:
        return None
    start_ms, rule = gap
    return request.model_copy(
        update={
            "start_ms": start_ms,
            "end_ms": start_ms + slot_duration,
            "placement_rule": rule,
        }
    )


def _enforce_consecutive_limit(
    *,
    requests: list[BrollRequest],
    critical_scenes: list[SceneSegment],
    duration_ms: int,
) -> list[BrollRequest]:
    output: list[BrollRequest] = []
    consecutive_count = 0
    previous_end_ms: int | None = None
    for request in sorted(requests, key=lambda item: item.start_ms):
        candidate = request
        if previous_end_ms is not None and candidate.start_ms < previous_end_ms:
            shifted = _shift_to_minimum_start(
                request=candidate,
                minimum_start=previous_end_ms + 120,
                critical_scenes=critical_scenes,
                duration_ms=duration_ms,
            )
            if shifted is None:
                continue
            candidate = shifted
        if previous_end_ms is None or candidate.start_ms - previous_end_ms > CONSECUTIVE_GAP_MS:
            consecutive_count = 1
        elif consecutive_count >= MAX_CONSECUTIVE_BROLL_CUTS:
            shifted = _shift_after_return_gap(
                request=candidate,
                previous_end_ms=previous_end_ms,
                critical_scenes=critical_scenes,
                duration_ms=duration_ms,
            )
            if shifted is None:
                continue
            candidate = shifted
            consecutive_count = 1
        else:
            consecutive_count += 1
        output.append(candidate)
        previous_end_ms = candidate.end_ms
    return output


def _shift_to_minimum_start(
    *,
    request: BrollRequest,
    minimum_start: int,
    critical_scenes: list[SceneSegment],
    duration_ms: int,
) -> BrollRequest | None:
    slot_duration = request.end_ms - request.start_ms
    if minimum_start + slot_duration > duration_ms:
        remaining_duration = duration_ms - minimum_start
        if remaining_duration < 1200:
            return None
        slot_duration = remaining_duration
    gap = _closest_face_safe_gap(
        preferred_start=minimum_start,
        slot_duration=slot_duration,
        critical_scenes=critical_scenes,
        duration_ms=duration_ms,
    )
    if gap is None:
        return None
    start_ms, _rule = gap
    if start_ms < minimum_start:
        return None
    return request.model_copy(
        update={
            "start_ms": start_ms,
            "end_ms": start_ms + slot_duration,
            "placement_rule": "avoid_broll_overlap",
        }
    )


def _shift_after_return_gap(
    *,
    request: BrollRequest,
    previous_end_ms: int,
    critical_scenes: list[SceneSegment],
    duration_ms: int,
) -> BrollRequest | None:
    slot_duration = request.end_ms - request.start_ms
    preferred_start = previous_end_ms + RETURN_TO_SUBJECT_GAP_MS
    gap = _closest_face_safe_gap(
        preferred_start=preferred_start,
        slot_duration=slot_duration,
        critical_scenes=critical_scenes,
        duration_ms=duration_ms,
    )
    if gap is None:
        return None
    start_ms, _rule = gap
    if start_ms - previous_end_ms < RETURN_TO_SUBJECT_GAP_MS:
        return None
    return request.model_copy(
        update={
            "start_ms": start_ms,
            "end_ms": start_ms + slot_duration,
            "placement_rule": "max_three_consecutive_return_to_subject",
        }
    )


def _closest_face_safe_gap(
    *,
    preferred_start: int,
    slot_duration: int,
    critical_scenes: list[SceneSegment],
    duration_ms: int,
) -> tuple[int, str] | None:
    safe_ranges = _safe_ranges(critical_scenes=critical_scenes, duration_ms=duration_ms)
    viable: list[tuple[int, int, str]] = []
    for start, end in safe_ranges:
        if end - start < slot_duration:
            continue
        if start <= preferred_start and preferred_start + slot_duration <= end:
            viable.append(
                (preferred_start, abs(preferred_start - start), "face_safe_semantic_anchor")
            )
        else:
            clamped_start = min(max(preferred_start, start), end - slot_duration)
            rule = (
                "avoid_face_overlap_after_scene"
                if clamped_start >= preferred_start
                else "avoid_face_overlap_before_scene"
            )
            viable.append((clamped_start, abs(clamped_start - preferred_start), rule))
    if not viable:
        return None
    best = min(viable, key=lambda item: item[1])
    return best[0], best[2]


def _safe_ranges(*, critical_scenes: list[SceneSegment], duration_ms: int) -> list[tuple[int, int]]:
    if not critical_scenes:
        return [(0, duration_ms)]
    blocked = sorted(
        (
            max(0, scene.start_ms - FACE_PADDING_MS),
            min(duration_ms, scene.end_ms + FACE_PADDING_MS),
        )
        for scene in critical_scenes
    )
    merged: list[tuple[int, int]] = []
    for start, end in blocked:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
    safe: list[tuple[int, int]] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            safe.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration_ms:
        safe.append((cursor, duration_ms))
    return safe


def _overlaps_any_critical(request: BrollRequest, critical_scenes: list[SceneSegment]) -> bool:
    return any(
        request.start_ms < scene.end_ms + FACE_PADDING_MS
        and request.end_ms > scene.start_ms - FACE_PADDING_MS
        for scene in critical_scenes
    )


def _critical_face_scenes(scenes: list[SceneSegment]) -> list[SceneSegment]:
    return [
        scene
        for scene in scenes
        if (
            (scene.faces or scene.scene_type == SceneType.TALKING_HEAD)
            and scene.visual_importance_score >= CRITICAL_FACE_SCORE_THRESHOLD
        )
    ]


def _best_scene_for_request(
    *, request: BrollRequest, scenes: list[SceneSegment]
) -> SceneSegment | None:
    if not scenes:
        return None
    return max(
        scenes,
        key=lambda scene: (
            _overlap_ms(request.start_ms, request.end_ms, scene.start_ms, scene.end_ms),
            scene.visual_importance_score,
        ),
    )


def _overlap_ms(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def _semantic_query_for_scene(*, request: BrollRequest, scene: SceneSegment) -> str:
    parts = [
        scene.caption,
        *[obj.label for obj in scene.objects[:4]],
        scene.scene_type.value.replace("_", " "),
        *request.keywords[:2],
    ]
    text = " ".join(part.strip().lower() for part in parts if part and part.strip())
    return " ".join(dict.fromkeys(text.split()))[:180] or "brand moment"


def _shot_type_for_scene(scene: SceneSegment) -> str:
    if scene.scene_type == SceneType.PRODUCT:
        return "product_detail"
    if scene.scene_type == SceneType.LANDSCAPE:
        return "establishing"
    if scene.scene_type == SceneType.SCREEN_RECORDING:
        return "screen_insert"
    if scene.scene_type == SceneType.TEXT_HEAVY:
        return "graphic_insert"
    if scene.scene_type == SceneType.CROWD:
        return "people_context"
    if scene.scene_type == SceneType.TALKING_HEAD:
        return "context_cutaway"
    labels = {obj.label.lower() for obj in scene.objects}
    if labels & {"watch", "shoe", "bag", "phone", "laptop", "product"}:
        return "product_detail"
    return "cutaway"


def _embedding_hash(values: list[float]) -> str:
    if not values:
        return "none"
    rounded = [round(value, 6) for value in values[:128]]
    return hashlib.sha256(json.dumps(rounded, separators=(",", ":")).encode("utf-8")).hexdigest()
