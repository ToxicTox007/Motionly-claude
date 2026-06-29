from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from app.schemas.captions import TranscriptWord
from app.schemas.plans import (
    EDIT_PLAN_PROMPT_VERSION,
    EDIT_PLAN_SCHEMA_VERSION,
    SUPPORTED_EDIT_PLAN_ASPECT_RATIOS,
    SUPPORTED_EDIT_PLAN_PRESET_KEYS,
    EditPlan,
)

logger = logging.getLogger(__name__)

REQUIRED_GENERATED_PLAN_FIELDS = {
    "schema_version",
    "prompt_version",
    "style",
    "aspect_ratio",
    "render_dimensions",
    "layout",
    "target_duration_s",
    "cuts",
    "caption_highlights",
    "broll_requests",
    "zoom_moments",
    "music_mood",
    "effects",
    "color_grade",
    "risk_flags",
}


@dataclass(frozen=True)
class EditPlanValidationContext:
    source_duration_ms: int
    transcript_words: list[TranscriptWord]
    project_id: UUID | None = None
    plan_id: UUID | None = None
    job_id: str | None = None


class EditPlanValidationFailure(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("edit plan validation failed")
        self.errors = compact_validation_errors(errors)


def validate_generated_plan_payload(
    *,
    payload: object,
    context: EditPlanValidationContext,
) -> EditPlan:
    raw = _coerce_raw_object(payload)
    missing = sorted(field for field in REQUIRED_GENERATED_PLAN_FIELDS if field not in raw)
    if missing:
        raise EditPlanValidationFailure(
            [_safe_error("missing_required_field", path=field) for field in missing]
        )
    return validate_edit_plan(plan=raw, context=context)


def validate_edit_plan(
    *,
    plan: EditPlan | dict[str, object],
    context: EditPlanValidationContext,
) -> EditPlan:
    try:
        parsed = plan if isinstance(plan, EditPlan) else EditPlan.model_validate(plan)
    except ValidationError as exc:
        raise EditPlanValidationFailure(_pydantic_errors(exc)) from exc

    errors: list[str] = []
    if parsed.schema_version != EDIT_PLAN_SCHEMA_VERSION:
        errors.append(_safe_error("stale_schema_version", path="schema_version"))
    if parsed.prompt_version != EDIT_PLAN_PROMPT_VERSION:
        errors.append(_safe_error("stale_prompt_version", path="prompt_version"))
    if parsed.aspect_ratio not in SUPPORTED_EDIT_PLAN_ASPECT_RATIOS:
        errors.append(_safe_error("unsupported_aspect_ratio", path="aspect_ratio"))
    if parsed.layout.preset_key not in SUPPORTED_EDIT_PLAN_PRESET_KEYS:
        errors.append(_safe_error("unsupported_preset_key", path="layout.preset_key"))

    source_duration_ms = max(1, context.source_duration_ms)
    output_duration_ms = 0
    cut_ranges: list[tuple[int, int, int]] = []
    for index, cut in enumerate(parsed.cuts):
        path = f"cuts.{index}"
        if cut.start_ms < 0 or cut.end_ms < 0:
            errors.append(_safe_error("negative_timestamp", path=path))
        if cut.end_ms > source_duration_ms:
            errors.append(_safe_error("cut_outside_source_duration", path=f"{path}.end_ms"))
        cut_ranges.append((cut.start_ms, cut.end_ms, index))
        output_duration_ms += max(0, cut.end_ms - cut.start_ms)
    previous_end_ms = -1
    for start_ms, end_ms, index in sorted(cut_ranges, key=lambda item: item[0]):
        if start_ms < previous_end_ms:
            errors.append(_safe_error("invalid_cut_overlap", path=f"cuts.{index}"))
        previous_end_ms = max(previous_end_ms, end_ms)

    transcript_start_ms = min((word.start_ms for word in context.transcript_words), default=0)
    transcript_end_ms = max(
        (word.end_ms for word in context.transcript_words),
        default=source_duration_ms,
    )
    for index, highlight in enumerate(parsed.caption_highlights):
        if not transcript_start_ms <= highlight.timestamp_ms <= transcript_end_ms:
            errors.append(
                _safe_error(
                    "caption_outside_transcript_range",
                    path=f"caption_highlights.{index}.timestamp_ms",
                )
            )

    output_duration_ms = max(
        1,
        parsed.timeline.duration_ms if parsed.timeline is not None else output_duration_ms,
    )
    for index, request in enumerate(parsed.broll_requests):
        if request.start_ms < 0 or request.end_ms < 0:
            errors.append(_safe_error("negative_timestamp", path=f"broll_requests.{index}"))
        if request.end_ms > output_duration_ms:
            errors.append(
                _safe_error("broll_outside_output_timeline", path=f"broll_requests.{index}.end_ms")
            )

    for index, zoom in enumerate(parsed.zoom_moments):
        if zoom.timestamp_ms > output_duration_ms:
            errors.append(
                _safe_error(
                    "zoom_outside_output_timeline",
                    path=f"zoom_moments.{index}.timestamp_ms",
                )
            )

    if parsed.timeline is not None:
        for index, clip in enumerate(parsed.timeline.clips):
            if clip.source_end_ms > source_duration_ms:
                errors.append(
                    _safe_error("timeline_clip_outside_source", path=f"timeline.clips.{index}")
                )
            if clip.output_end_ms > output_duration_ms:
                errors.append(
                    _safe_error("timeline_clip_outside_output", path=f"timeline.clips.{index}")
                )

    if errors:
        raise EditPlanValidationFailure(errors)

    return parsed


def validate_edit_plan_for_render(
    *,
    plan: EditPlan,
    context: EditPlanValidationContext,
) -> EditPlan:
    try:
        return validate_edit_plan(plan=plan, context=context)
    except EditPlanValidationFailure as exc:
        logger.warning(
            "edit_plan_render_validation_failed",
            extra={
                "project_id": str(context.project_id) if context.project_id else None,
                "plan_id": str(context.plan_id) if context.plan_id else None,
                "validation_errors": exc.errors,
            },
        )
        raise


def compact_validation_errors(errors: list[str], *, limit: int = 12) -> list[str]:
    return [str(error)[:240] for error in errors[:limit]]


def _coerce_raw_object(payload: object) -> dict[str, object]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise EditPlanValidationFailure(
                [_safe_error("malformed_json", path="root", detail=exc.msg)]
            ) from exc
    if not isinstance(payload, dict):
        raise EditPlanValidationFailure([_safe_error("plan_not_object", path="root")])
    return payload


def _pydantic_errors(error: ValidationError) -> list[str]:
    safe: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "root"
        code = str(item.get("type") or "invalid_value")
        safe.append(_safe_error(code, path=location))
    return safe


def _safe_error(code: str, *, path: str, detail: str | None = None) -> str:
    payload = {"code": code, "path": path}
    if detail:
        payload["detail"] = detail[:80]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
