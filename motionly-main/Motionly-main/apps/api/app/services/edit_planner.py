import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import ValidationError

from app.api.errors import ApiError
from app.repositories.captions import TranscriptRecord
from app.schemas.captions import TranscriptWord
from app.schemas.plans import (
    BrollDensity,
    BrollRequest,
    CaptionHighlight,
    EditPlan,
    EditPlanCut,
    EditPlanStyle,
    PlanCreateRequest,
    PlannerProviderResult,
    PlanSummary,
    ZoomMoment,
)

logger = logging.getLogger(__name__)

PLANNER_SCHEMA_HASH = hashlib.sha256(
    json.dumps(EditPlan.model_json_schema(), sort_keys=True).encode("utf-8")
).hexdigest()

SYSTEM_PROMPT = (
    "You are an expert short-form video editor for Instagram Reels, TikTok, and YouTube Shorts. "
    "Return exactly one JSON object and no Markdown, prose, comments, or code fences. "
    "The object must match the provided JSON schema exactly. Required top-level keys are "
    "schema_version, style, target_duration_s, cuts, caption_highlights, broll_requests, "
    "zoom_moments, music_mood, effects, color_grade, and risk_flags. Use only enum values from "
    "the schema. Use integer millisecond timestamps inside the source duration. Keep every cut "
    "and B-roll range with end_ms greater than start_ms. Preserve meaning. Do not overuse effects."
)


class EditPlannerProvider(Protocol):
    provider_name: str
    model: str

    def create_plan(
        self,
        *,
        transcript: TranscriptRecord,
        payload: PlanCreateRequest,
        video_duration_ms: int,
    ) -> PlannerProviderResult: ...


@dataclass(frozen=True)
class FixtureEditPlannerProvider:
    model: str = "fixture-edit-planner-v1"
    provider_name: str = "fixture"

    def create_plan(
        self,
        *,
        transcript: TranscriptRecord,
        payload: PlanCreateRequest,
        video_duration_ms: int,
    ) -> PlannerProviderResult:
        started = time.perf_counter()
        words = transcript.words
        if not words:
            raise ApiError(
                422,
                "planner_transcript_empty",
                "Generate a word-level transcript before creating an edit plan.",
            )

        duration_ms = max(1000, video_duration_ms or transcript.duration_ms or words[-1].end_ms)
        target_ms = min(payload.target_duration_s * 1000, duration_ms)
        requested_text = _combined_direction_text(payload)
        cuts = _build_cuts(
            words=words,
            target_ms=target_ms,
            duration_ms=duration_ms,
            direction_text=requested_text,
            style=payload.style,
        )
        highlights = _build_highlights(words=words, duration_ms=duration_ms)
        broll = _build_broll_requests(
            words=words,
            cuts=cuts,
            style=payload.style,
            density=payload.broll_density,
            duration_ms=duration_ms,
            direction_text=requested_text,
        )
        zoom_limit = _zoom_limit(direction_text=requested_text, default=4)
        zoom_intensity = _zoom_intensity(style=payload.style, direction_text=requested_text)
        zooms = [
            ZoomMoment(
                timestamp_ms=highlight.timestamp_ms,
                intensity=zoom_intensity,
                reason=_bounded_reason(
                    f"Emphasize {highlight.word} for {payload.style.value} pacing"
                ),
            )
            for highlight in highlights[:zoom_limit]
        ]
        plan = EditPlan(
            style=payload.style,
            target_duration_s=max(10, round(sum(cut.end_ms - cut.start_ms for cut in cuts) / 1000)),
            cuts=cuts,
            caption_highlights=highlights,
            broll_requests=broll,
            zoom_moments=zooms,
            music_mood=_music_mood(payload.style, requested_text),
            effects=_effects(payload.style, requested_text),
            color_grade=_color_grade(payload.style),
            risk_flags=_risk_flags(payload=payload, duration_ms=duration_ms, cuts=cuts),
        )
        return PlannerProviderResult(
            plan=plan,
            provider=self.provider_name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_estimate_usd=0,
            raw_payload={"mode": "deterministic_fixture"},
        )


@dataclass(frozen=True)
class HttpEditPlannerProvider:
    endpoint: str
    api_key: str
    model: str
    provider_name: str = "http_chat"

    def create_plan(
        self,
        *,
        transcript: TranscriptRecord,
        payload: PlanCreateRequest,
        video_duration_ms: int,
    ) -> PlannerProviderResult:
        started = time.perf_counter()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "schema": EditPlan.model_json_schema(),
                            "style": payload.style.value,
                            "target_duration_s": payload.target_duration_s,
                            "broll_density": payload.broll_density.value,
                            "prompt": payload.prompt,
                            "feedback": payload.feedback,
                            "duration_ms": video_duration_ms,
                            "words": [word.model_dump(mode="json") for word in transcript.words],
                        },
                        separators=(",", ":"),
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        response = httpx.post(
            self.endpoint,
            headers={"authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json=body,
            timeout=45,
        )
        if response.status_code >= 400:
            raise ApiError(502, "planner_provider_failed", "The edit planner provider failed.")
        data = response.json()
        content = _extract_content(data)
        validation_errors: list[str] = []
        try:
            raw_plan = json.loads(_extract_json_object(content))
        except json.JSONDecodeError as exc:
            validation_errors = [f"json_decode: {exc.msg} at line {exc.lineno} column {exc.colno}"]
            logger.warning(
                "edit_planner_json_decode_failed",
                extra={"validation_errors": validation_errors, "provider": self.provider_name},
            )
            plan = _fallback_plan(
                transcript=transcript,
                payload=payload,
                video_duration_ms=video_duration_ms,
            )
        else:
            plan, validation_errors = _validate_or_repair_plan(
                raw_plan=raw_plan,
                transcript=transcript,
                payload=payload,
                video_duration_ms=video_duration_ms,
                provider_name=self.provider_name,
            )
        plan = normalize_plan(plan=plan, words=transcript.words, duration_ms=video_duration_ms)
        usage = data.get("usage") if isinstance(data, dict) else None
        tokens = int(usage.get("total_tokens") or 0) if isinstance(usage, dict) else 0
        is_free_model = self.model == "openrouter/free" or self.model.endswith(":free")
        cost_estimate_usd = 0 if is_free_model else round(tokens * 0.000002, 6) if tokens else None
        return PlannerProviderResult(
            plan=plan,
            provider=self.provider_name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_estimate_usd=cost_estimate_usd,
            validation_errors=validation_errors,
            raw_payload={
                "usage": usage or {},
                "validation_errors": validation_errors,
                "fallback_used": bool(validation_errors),
            },
        )


def normalize_plan(*, plan: EditPlan, words: list[TranscriptWord], duration_ms: int) -> EditPlan:
    if not words:
        return plan
    cuts = [
        EditPlanCut(
            start_ms=_snap_to_word_boundary(cut.start_ms, words, "start"),
            end_ms=_snap_to_word_boundary(min(cut.end_ms, duration_ms), words, "end"),
            reason=cut.reason,
        )
        for cut in plan.cuts
    ]
    cuts = [cut for cut in cuts if cut.end_ms > cut.start_ms]
    if not cuts:
        cuts = _build_cuts(
            words=words,
            target_ms=min(plan.target_duration_s * 1000, duration_ms),
            duration_ms=duration_ms,
        )
    return plan.model_copy(
        update={
            "cuts": cuts,
            "caption_highlights": [
                item.model_copy(update={"timestamp_ms": min(item.timestamp_ms, duration_ms)})
                for item in plan.caption_highlights
            ],
            "broll_requests": [
                item.model_copy(
                    update={
                        "start_ms": min(item.start_ms, duration_ms),
                        "end_ms": min(max(item.end_ms, item.start_ms + 500), duration_ms),
                    }
                )
                for item in plan.broll_requests
                if item.start_ms < duration_ms
            ],
            "zoom_moments": [
                item.model_copy(update={"timestamp_ms": min(item.timestamp_ms, duration_ms)})
                for item in plan.zoom_moments
            ],
        }
    )


def summarize_plan(plan: EditPlan) -> PlanSummary:
    return PlanSummary(
        duration_s=round(sum(cut.end_ms - cut.start_ms for cut in plan.cuts) / 1000),
        cut_count=len(plan.cuts),
        broll_count=len(plan.broll_requests),
        zoom_count=len(plan.zoom_moments),
        music_mood=plan.music_mood,
        effects=plan.effects,
        color_grade=plan.color_grade,
        risk_flags=plan.risk_flags,
        source_topic=_source_topic(plan),
        direction_notes=_direction_notes(plan),
    )


def build_plan_cache_key(*, transcript: TranscriptRecord, payload: PlanCreateRequest) -> str:
    words_payload = [
        {"t": word.text, "s": word.start_ms, "e": word.end_ms} for word in transcript.words
    ]
    digest_payload = {
        "schema_hash": PLANNER_SCHEMA_HASH,
        "transcript_id": str(transcript.id),
        "duration_ms": transcript.duration_ms,
        "words": words_payload,
        "prompt": payload.prompt.strip(),
        "feedback": payload.feedback or "",
        "style": payload.style.value,
        "target_duration_s": payload.target_duration_s,
        "broll_density": payload.broll_density.value,
    }
    return hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _build_cuts(
    *,
    words: list[TranscriptWord],
    target_ms: int,
    duration_ms: int,
    direction_text: str = "",
    style: EditPlanStyle | None = None,
) -> list[EditPlanCut]:
    start_ms = max(0, words[0].start_ms)
    keep_until = min(duration_ms, max(start_ms + 1000, start_ms + target_ms))
    selected = [word for word in words if word.start_ms < keep_until]
    if not selected:
        selected = words
    chunks: list[EditPlanCut] = []
    chunk_start = selected[0].start_ms
    previous = selected[0]
    for word in selected[1:]:
        if word.start_ms - previous.end_ms >= 700:
            chunks.append(
                EditPlanCut(
                    start_ms=chunk_start,
                    end_ms=previous.end_ms,
                    reason=_cut_reason(
                        base="remove_pause",
                        style=style,
                        direction_text=direction_text,
                    ),
                )
            )
            chunk_start = word.start_ms
        previous = word
    chunks.append(
        EditPlanCut(
            start_ms=chunk_start,
            end_ms=min(previous.end_ms, duration_ms),
            reason=_cut_reason(
                base="strong_story",
                style=style,
                direction_text=direction_text,
            ),
        )
    )
    return chunks[:8]


def _build_highlights(*, words: list[TranscriptWord], duration_ms: int) -> list[CaptionHighlight]:
    candidates = [
        word
        for word in words
        if len(word.text.strip(".,!?").lower()) >= 5 and word.start_ms <= duration_ms
    ]
    sampled = candidates[0 :: max(1, len(candidates) // 4)][:4] or words[:2]
    return [
        CaptionHighlight(
            timestamp_ms=word.start_ms,
            word=word.text.strip(".,!?"),
            effect="pulse" if index == 0 else "color_pop",
        )
        for index, word in enumerate(sampled)
    ]


def _build_broll_requests(
    *,
    words: list[TranscriptWord],
    cuts: list[EditPlanCut],
    style: EditPlanStyle,
    density: BrollDensity,
    duration_ms: int,
    direction_text: str = "",
) -> list[BrollRequest]:
    count_by_density = {
        BrollDensity.NONE: 0,
        BrollDensity.LOW: 1,
        BrollDensity.MEDIUM: 2,
        BrollDensity.HIGH: 4,
    }
    count = count_by_density[density]
    if _contains_any(
        direction_text,
        ("more b-roll", "more broll", "premium b-roll", "premium broll"),
    ):
        count += 1
    if _contains_any(direction_text, ("less b-roll", "less broll", "fewer b-roll", "fewer broll")):
        count -= 1
    count = min(max(count, 0), max(0, min(4, len(cuts) + 3)))
    if count == 0:
        return []
    phrases = _keyword_phrases(words)
    requests = []
    for index in range(count):
        anchor = cuts[min(index, len(cuts) - 1)]
        start_ms = min(anchor.start_ms + 600, max(0, duration_ms - 1000))
        end_ms = min(duration_ms, max(start_ms + 1200, anchor.end_ms))
        requests.append(
            BrollRequest(
                start_ms=start_ms,
                end_ms=end_ms,
                keywords=[f"{style.value} {phrases[index % len(phrases)]}"],
                priority=max(0.35, 0.9 - index * 0.12),
            )
        )
    return requests


def _keyword_phrases(words: list[TranscriptWord]) -> list[str]:
    cleaned = [word.text.strip(".,!?").lower() for word in words if len(word.text) > 3]
    if not cleaned:
        return ["brand moment"]
    phrases = [" ".join(cleaned[index : index + 2]) for index in range(0, min(len(cleaned), 8), 2)]
    return phrases or ["key idea"]


def _music_mood(style: EditPlanStyle, direction_text: str = "") -> str:
    mood = {
        EditPlanStyle.LUXURY: "uplifting ambient luxury",
        EditPlanStyle.FOUNDER: "clean confident minimal",
        EditPlanStyle.TRAVEL: "bright energetic travel",
        EditPlanStyle.FASHION: "editorial high contrast",
        EditPlanStyle.PODCAST: "subtle conversational bed",
    }[style]
    if _contains_any(direction_text, ("sharper", "punchier", "founder")):
        return f"{mood} with crisp momentum"
    if _contains_any(direction_text, ("preserve", "educational", "teaching")):
        return f"{mood} under clear educational pacing"
    return mood


def _effects(style: EditPlanStyle, direction_text: str = "") -> list[str]:
    effects = {
        EditPlanStyle.LUXURY: ["warm_grade", "soft_flash"],
        EditPlanStyle.FOUNDER: ["subtle_push_in"],
        EditPlanStyle.TRAVEL: ["speed_ramp", "light_leak"],
        EditPlanStyle.FASHION: ["flash_cut", "film_grain"],
        EditPlanStyle.PODCAST: ["quote_card"],
    }[style]
    if _contains_any(direction_text, ("sharper", "punchier")) and "tight_jump_cuts" not in effects:
        effects = [*effects, "tight_jump_cuts"]
    if (
        _contains_any(direction_text, ("preserve", "educational", "teaching"))
        and "meaning_preserved_captions" not in effects
    ):
        effects = [*effects, "meaning_preserved_captions"]
    if _contains_any(direction_text, ("fewer zoom", "less zoom")):
        effects = [effect for effect in effects if effect != "subtle_push_in"]
    return effects[:8]


def _color_grade(style: EditPlanStyle) -> str:
    return {
        EditPlanStyle.LUXURY: "champagne_gold",
        EditPlanStyle.FOUNDER: "neutral_clean",
        EditPlanStyle.TRAVEL: "vibrant_daylight",
        EditPlanStyle.FASHION: "editorial_contrast",
        EditPlanStyle.PODCAST: "natural_skin_tones",
    }[style]


def _risk_flags(
    *,
    payload: PlanCreateRequest,
    duration_ms: int,
    cuts: list[EditPlanCut],
) -> list[str]:
    flags = []
    planned_ms = sum(cut.end_ms - cut.start_ms for cut in cuts)
    if payload.target_duration_s * 1000 < duration_ms * 0.35:
        flags.append("aggressive_trim_may_remove_context")
    if payload.broll_density == BrollDensity.HIGH:
        flags.append("high_broll_density_can_distract_from_speaker")
    if _contains_any(_combined_direction_text(payload), ("preserve", "educational")):
        flags.append("meaning_preservation_requested")
    if planned_ms < 10_000:
        flags.append("very_short_plan")
    return flags


def _combined_direction_text(payload: PlanCreateRequest) -> str:
    return " ".join(
        part.strip().lower()
        for part in (payload.prompt, payload.feedback or "")
        if part and part.strip()
    )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _cut_reason(
    *,
    base: str,
    style: EditPlanStyle | None,
    direction_text: str,
) -> str:
    style_prefix = f"{style.value}_" if style else ""
    if _contains_any(direction_text, ("sharper", "punchier")):
        return _bounded_reason(f"{style_prefix}{base}_sharper_pacing")
    if _contains_any(direction_text, ("preserve", "educational", "teaching")):
        return _bounded_reason(f"{style_prefix}{base}_preserve_educational_point")
    return _bounded_reason(f"{style_prefix}{base}" if style else base)


def _zoom_limit(*, direction_text: str, default: int) -> int:
    if _contains_any(direction_text, ("fewer zoom", "less zoom", "use fewer zoom")):
        return 1
    if _contains_any(direction_text, ("more zoom", "punchier", "sharper")):
        return 5
    return default


def _zoom_intensity(*, style: EditPlanStyle, direction_text: str) -> float:
    if _contains_any(direction_text, ("fewer zoom", "less zoom")):
        return 1.05
    if _contains_any(direction_text, ("punchier", "sharper")):
        return 1.28
    return 1.22 if style == EditPlanStyle.FASHION else 1.12


def _source_topic(plan: EditPlan) -> str:
    highlight_words = [item.word for item in plan.caption_highlights[:3] if item.word]
    return " / ".join(highlight_words) if highlight_words else f"{plan.style.value} edit"


def _direction_notes(plan: EditPlan) -> list[str]:
    notes = [
        f"{plan.style.value} style",
        f"{plan.target_duration_s}s target",
        f"{len(plan.broll_requests)} B-roll slots",
    ]
    if plan.zoom_moments:
        notes.append(f"{len(plan.zoom_moments)} zoom moments")
    if plan.risk_flags:
        notes.append(plan.risk_flags[0])
    return notes[:6]


def _bounded_reason(value: str) -> str:
    return value[:120]


def _snap_to_word_boundary(timestamp_ms: int, words: list[TranscriptWord], edge: str) -> int:
    key = "start_ms" if edge == "start" else "end_ms"
    return int(getattr(min(words, key=lambda word: abs(getattr(word, key) - timestamp_ms)), key))


def _extract_content(data: dict[str, object]) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
    raise ApiError(502, "planner_provider_invalid_response", "The edit planner response was empty.")


def _extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _validate_or_repair_plan(
    *,
    raw_plan: object,
    transcript: TranscriptRecord,
    payload: PlanCreateRequest,
    video_duration_ms: int,
    provider_name: str,
) -> tuple[EditPlan, list[str]]:
    try:
        return EditPlan.model_validate(raw_plan), []
    except ValidationError as first_error:
        first_errors = _validation_error_messages(first_error)

    repaired = _repair_plan_payload(
        raw_plan=raw_plan,
        transcript=transcript,
        payload=payload,
        video_duration_ms=video_duration_ms,
    )
    try:
        plan = EditPlan.model_validate(repaired)
    except ValidationError as second_error:
        validation_errors = [
            *first_errors,
            *[f"after_repair: {message}" for message in _validation_error_messages(second_error)],
            "fallback_used: deterministic_fixture_plan",
        ]
        logger.warning(
            "edit_planner_validation_failed",
            extra={"validation_errors": validation_errors, "provider": provider_name},
        )
        return (
            _fallback_plan(
                transcript=transcript,
                payload=payload,
                video_duration_ms=video_duration_ms,
            ),
            validation_errors,
        )

    validation_errors = [
        *first_errors,
        "repair_used: coerced_provider_plan",
    ]
    logger.warning(
        "edit_planner_validation_repaired",
        extra={"validation_errors": validation_errors, "provider": provider_name},
    )
    return plan, validation_errors


def _repair_plan_payload(
    *,
    raw_plan: object,
    transcript: TranscriptRecord,
    payload: PlanCreateRequest,
    video_duration_ms: int,
) -> dict[str, object]:
    source = raw_plan if isinstance(raw_plan, dict) else {}
    nested_plan = source.get("plan")
    if isinstance(nested_plan, dict):
        source = nested_plan

    words = transcript.words
    duration_ms = max(1000, video_duration_ms or transcript.duration_ms or _last_word_end(words))
    fallback = _fallback_plan(
        transcript=transcript,
        payload=payload,
        video_duration_ms=duration_ms,
    )
    fallback_payload = fallback.model_dump(mode="json")

    repaired: dict[str, object] = {
        "schema_version": 1,
        "style": _coerce_choice(source.get("style"), {item.value for item in EditPlanStyle}, payload.style.value),
        "target_duration_s": _coerce_int(
            source.get("target_duration_s") or source.get("duration_s"),
            default=max(10, min(90, payload.target_duration_s)),
            minimum=10,
            maximum=90,
        ),
        "cuts": _repair_cuts(source.get("cuts"), fallback_payload["cuts"], duration_ms),
        "caption_highlights": _repair_highlights(
            source.get("caption_highlights") or source.get("highlights"),
            fallback_payload["caption_highlights"],
            duration_ms,
        ),
        "broll_requests": _repair_broll_requests(
            source.get("broll_requests") or source.get("b_roll_requests"),
            fallback_payload["broll_requests"],
            duration_ms,
        ),
        "zoom_moments": _repair_zoom_moments(
            source.get("zoom_moments") or source.get("zooms"),
            fallback_payload["zoom_moments"],
            duration_ms,
        ),
        "music_mood": _coerce_text(source.get("music_mood"), fallback_payload["music_mood"]),
        "effects": _repair_string_list(source.get("effects"), fallback_payload["effects"], limit=8),
        "color_grade": _coerce_text(source.get("color_grade"), fallback_payload["color_grade"]),
        "risk_flags": _repair_string_list(source.get("risk_flags"), fallback_payload["risk_flags"], limit=10),
    }
    return repaired


def _fallback_plan(
    *,
    transcript: TranscriptRecord,
    payload: PlanCreateRequest,
    video_duration_ms: int,
) -> EditPlan:
    return FixtureEditPlannerProvider().create_plan(
        transcript=transcript,
        payload=payload,
        video_duration_ms=video_duration_ms,
    ).plan


def _repair_cuts(value: object, fallback: object, duration_ms: int) -> list[dict[str, object]]:
    items = value if isinstance(value, list) else fallback
    repaired = []
    for index, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        start_ms = _coerce_int(item.get("start_ms"), default=0, minimum=0, maximum=duration_ms)
        end_ms = _coerce_int(
            item.get("end_ms"),
            default=min(duration_ms, start_ms + 1200),
            minimum=start_ms + 1,
            maximum=duration_ms,
        )
        if end_ms <= start_ms:
            continue
        repaired.append(
            {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "reason": _coerce_text(item.get("reason"), f"keep_segment_{index + 1}")[:120],
            }
        )
    return repaired or list(fallback if isinstance(fallback, list) else [])


def _repair_highlights(value: object, fallback: object, duration_ms: int) -> list[dict[str, object]]:
    effect_choices = {"pulse", "underline", "color_pop", "scale", "none"}
    effect_aliases = {"color": "color_pop", "highlight": "color_pop", "pop": "pulse"}
    items = value if isinstance(value, list) else fallback
    repaired = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        raw_effect = str(item.get("effect") or "pulse").lower()
        effect = effect_aliases.get(raw_effect, raw_effect)
        repaired.append(
            {
                "timestamp_ms": _coerce_int(
                    item.get("timestamp_ms") or item.get("start_ms"),
                    default=0,
                    minimum=0,
                    maximum=duration_ms,
                ),
                "word": _coerce_text(item.get("word") or item.get("text"), "moment")[:80],
                "effect": effect if effect in effect_choices else "pulse",
            }
        )
    return repaired[:12] or list(fallback if isinstance(fallback, list) else [])


def _repair_broll_requests(value: object, fallback: object, duration_ms: int) -> list[dict[str, object]]:
    items = value if isinstance(value, list) else fallback
    repaired = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        start_ms = _coerce_int(item.get("start_ms"), default=0, minimum=0, maximum=duration_ms)
        end_ms = _coerce_int(
            item.get("end_ms"),
            default=min(duration_ms, start_ms + 1500),
            minimum=start_ms + 1,
            maximum=duration_ms,
        )
        keywords = item.get("keywords")
        if isinstance(keywords, str):
            keywords = [keywords]
        keywords = _repair_string_list(keywords, ["brand moment"], limit=5)
        if end_ms <= start_ms:
            continue
        repaired.append(
            {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "keywords": keywords or ["brand moment"],
                "priority": _coerce_float(item.get("priority"), default=0.7, minimum=0, maximum=1),
            }
        )
    return repaired[:12] or list(fallback if isinstance(fallback, list) else [])


def _repair_zoom_moments(value: object, fallback: object, duration_ms: int) -> list[dict[str, object]]:
    items = value if isinstance(value, list) else fallback
    repaired = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        repaired.append(
            {
                "timestamp_ms": _coerce_int(
                    item.get("timestamp_ms") or item.get("start_ms"),
                    default=0,
                    minimum=0,
                    maximum=duration_ms,
                ),
                "intensity": _coerce_float(item.get("intensity"), default=1.12, minimum=1.0, maximum=1.5),
                "reason": _coerce_text(item.get("reason"), "emphasize key moment")[:120],
            }
        )
    return repaired[:12] or list(fallback if isinstance(fallback, list) else [])


def _repair_string_list(value: object, fallback: object, *, limit: int) -> list[str]:
    if isinstance(value, str):
        items: object = [value]
    else:
        items = value
    if not isinstance(items, list):
        items = fallback
    cleaned = [str(item).strip()[:120] for item in items if str(item).strip()]
    return cleaned[:limit]


def _coerce_choice(value: object, choices: set[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in choices else default


def _coerce_text(value: object, default: object) -> str:
    text = str(value or "").strip()
    return text or str(default)


def _coerce_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)


def _coerce_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)


def _validation_error_messages(error: ValidationError) -> list[str]:
    messages = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "root"
        messages.append(f"{location}: {item.get('msg', 'invalid value')}")
    return messages


def _last_word_end(words: list[TranscriptWord]) -> int:
    return max((word.end_ms for word in words), default=1000)
