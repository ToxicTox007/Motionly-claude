from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer

NORMALIZER_VERSION = "editorial-delta/v1"

_ALLOWED_FIELDS: dict[AIEditLayer, set[str]] = {
    AIEditLayer.CUTS: {"start_ms", "end_ms", "duration_ms", "count", "pace"},
    AIEditLayer.BROLL: {"slot_id", "asset_id", "start_ms", "end_ms", "disabled", "density"},
    AIEditLayer.MUSIC: {"music_track_id", "music_volume", "sfx_intensity", "mood"},
    AIEditLayer.EFFECTS: {"color_grade", "preset", "enabled", "intensity"},
    AIEditLayer.CAPTIONS: {"segment_id", "style", "enabled", "highlighted"},
    AIEditLayer.RENDER: {"width", "height", "fps", "format", "watermark"},
}


def _allow_list(payload: dict[str, Any], allowed: set[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in payload.items():
        if key not in allowed:
            continue
        if isinstance(value, bool | int | float) or value is None:
            result[key] = value
        elif (
            isinstance(value, str)
            and len(value) <= 80
            and not value.startswith(("http://", "https://"))
        ):
            result[key] = value
        elif (
            isinstance(value, list)
            and len(value) <= 32
            and all(isinstance(item, bool | int | float) or item is None for item in value)
        ):
            result[key] = value
    return result


def normalize_editorial_delta(
    *,
    layer: AIEditLayer,
    event_type: AIEditFeedbackEventType,
    ai_payload: dict[str, Any] | None,
    user_payload: dict[str, Any] | None,
) -> tuple[dict[str, object], bool]:
    """Create a compact, allow-listed delta; never retain source text, URLs, or prompts."""
    allowed = _ALLOWED_FIELDS.get(layer, set())
    before = _allow_list(ai_payload or {}, allowed)
    after = _allow_list(user_payload or {}, allowed)
    if event_type == AIEditFeedbackEventType.KEPT:
        after = before
    payload = {"layer": layer.value, "outcome": event_type.value, "before": before, "after": after}
    trainable = bool(after) and event_type in {
        AIEditFeedbackEventType.CHANGED,
        AIEditFeedbackEventType.KEPT,
        AIEditFeedbackEventType.REJECTED,
    }
    return payload, trainable


def capture_consented_feedback(
    *,
    repository,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID,
    source_event_id: UUID | None,
    layer: AIEditLayer,
    event_type: AIEditFeedbackEventType,
    ai_payload: dict[str, Any] | None,
    user_payload: dict[str, Any] | None,
) -> object | None:
    delta, trainable = normalize_editorial_delta(
        layer=layer, event_type=event_type, ai_payload=ai_payload, user_payload=user_payload
    )
    return repository.capture_delta(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        source_event_id=source_event_id,
        layer=layer.value,
        delta=delta,
        normalizer_version=NORMALIZER_VERSION,
        memory_eligible=bool(delta["after"]),
        training_eligible=trainable,
    )


def delta_fingerprint(delta: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(delta, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
