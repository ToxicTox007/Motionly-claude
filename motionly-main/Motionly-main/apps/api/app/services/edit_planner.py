import hashlib
import json
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

PLANNER_SCHEMA_HASH = hashlib.sha256(
    json.dumps(EditPlan.model_json_schema(), sort_keys=True).encode("utf-8")
).hexdigest()

SYSTEM_PROMPT = (
    "You are an expert short-form video editor for Instagram Reels, TikTok, and YouTube Shorts. "
    "Convert transcript, metadata, user intent, and selected style into strict JSON matching the "
    "provided schema. Output JSON only. Preserve meaning. Do not invent timestamps outside video "
    "duration. Do not overuse effects."
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
        try:
            raw_plan = json.loads(_extract_json_object(content))
            plan = EditPlan.model_validate(raw_plan)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ApiError(
                502,
                "planner_validation_failed",
                "The edit plan was not valid.",
            ) from exc
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
            raw_payload={"usage": usage or {}},
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
