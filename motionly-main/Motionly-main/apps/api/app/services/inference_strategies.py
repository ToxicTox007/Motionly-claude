"""Motionly-owned inference strategies: prompts, response schemas and parsing.

Captions (``vision.caption``) and voice emotion (``audio.emotion``) are served
by *any* configured route for the capability — an ``openai_compatible`` chat
route (image parts / ``input_audio``), a local GPU route, or a ``generic_http``
caption mapping (plan Task 28). The provider contract (what we ask the model and
how we read its answer) belongs to Motionly, not to any vendor, so the prompt
strings, the structured-output JSON schemas and the response parsers live here
and are shared by every route (plan Task 11, Section 2.10).

Routing preferences such as zero-data-retention are adapter ``extra_body`` data,
never part of these strategies.
"""

from __future__ import annotations

import json
import re

# -- captions (vision.caption) -------------------------------------------------

#: Captions are trimmed to this many characters before they enter the manifest.
MAX_CAPTION_CHARS = 240

CAPTION_PROMPT = """You write short captions for video keyframes used to plan edits.

There are {count} images, in order; the first image has index 0. For each image
write one factual sentence of at most 24 words describing the main subject, the
setting, and any visible screen, device or on-screen text.

Do not name or identify people, and do not guess age, ethnicity, health or other
sensitive traits. Respond with JSON only."""


def caption_response_format(count: int) -> dict[str, object]:
    """Structured-output JSON schema for a batch of ``count`` keyframe captions."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "keyframe_captions",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["captions"],
                "properties": {
                    "captions": {
                        "type": "array",
                        "maxItems": count,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["index", "caption"],
                            "properties": {
                                "index": {"type": "integer", "minimum": 0, "maximum": count - 1},
                                "caption": {"type": "string", "maxLength": MAX_CAPTION_CHARS},
                            },
                        },
                    }
                },
            },
        },
    }


class CaptionResponseInvalid(ValueError):
    """The model response could not be read as keyframe captions."""


def parse_captions(content: str, *, count: int) -> list[str]:
    """Captions in image order; images the model skipped get an empty caption.

    Raises :class:`CaptionResponseInvalid` when nothing usable can be read.
    """
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CaptionResponseInvalid("caption response is not JSON") from exc
    items = payload.get("captions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise CaptionResponseInvalid("caption response has no captions list")
    captions: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        index, caption = item.get("index"), item.get("caption")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
            continue
        if not isinstance(caption, str) or not caption.strip() or index in captions:
            continue
        captions[index] = " ".join(caption.split())[:MAX_CAPTION_CHARS]
    if not captions:
        raise CaptionResponseInvalid("caption response has no usable captions")
    return [captions.get(index, "") for index in range(count)]


# -- voice emotion (audio.emotion) ---------------------------------------------

EMOTION_LABELS = (
    "neutral",
    "joy",
    "excitement",
    "calm",
    "sadness",
    "anger",
    "fear",
    "surprise",
)
#: Numeric fields rated per window.
EMOTION_SCORE_FIELDS = ("joy", "sadness", "urgency", "intensity")

EMOTION_PROMPT = """You rate HOW a person speaks in an audio clip: tone, pitch, pace and
vocal energy. Ignore the meaning of the words.

The clip is {duration:.1f} seconds long. Split it into consecutive windows of
{window:.1f} seconds; window i covers [i*{window:.1f}, (i+1)*{window:.1f}) seconds.
There are {count} windows, indexed 0 to {last}.

For every window that contains speech, return:
- index: the window index
- emotion: one of {labels}
- joy, sadness, urgency: how strongly the voice expresses each, from 0 to 1
- intensity: overall vocal energy/arousal, from 0 to 1

Skip windows without speech. Respond with JSON only."""


def emotion_response_format(window_count: int) -> dict[str, object]:
    """Structured-output JSON schema for ``window_count`` voice-emotion windows."""
    score = {"type": "number", "minimum": 0, "maximum": 1}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "voice_emotion_windows",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["windows"],
                "properties": {
                    "windows": {
                        "type": "array",
                        "maxItems": window_count,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["index", "emotion", *EMOTION_SCORE_FIELDS],
                            "properties": {
                                "index": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": max(0, window_count - 1),
                                },
                                "emotion": {"type": "string", "enum": list(EMOTION_LABELS)},
                                **{name: score for name in EMOTION_SCORE_FIELDS},
                            },
                        },
                    }
                },
            },
        },
    }


class EmotionResponseInvalid(ValueError):
    """The model response could not be read as emotion windows."""


def parse_emotion_windows(
    content: str,
    *,
    count: int,
    window_ms: int,
    offset_ms: int,
    duration_ms: int,
) -> list[dict[str, object]]:
    """Absolute-time voice-emotion windows read from a structured JSON reply.

    Raises :class:`EmotionResponseInvalid` when the shape is unreadable.
    """
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EmotionResponseInvalid("emotion response is not JSON") from exc
    raw_windows = payload.get("windows") if isinstance(payload, dict) else None
    if not isinstance(raw_windows, list):
        raise EmotionResponseInvalid("emotion response has no windows list")
    windows: dict[int, dict[str, object]] = {}
    for item in raw_windows:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
            continue
        if index in windows:
            continue
        scores = {name: _emotion_score(item.get(name)) for name in EMOTION_SCORE_FIELDS}
        if scores["intensity"] is None:
            present = [value for value in scores.values() if value is not None]
            if not present:
                continue
            scores["intensity"] = max(present)
        emotion = str(item.get("emotion") or "").strip().lower()
        start_ms = offset_ms + index * window_ms
        windows[index] = {
            "start_ms": start_ms,
            "end_ms": min(start_ms + window_ms, offset_ms + duration_ms),
            "emotion": emotion if emotion in EMOTION_LABELS else "neutral",
            **scores,
        }
    if raw_windows and not windows:
        raise EmotionResponseInvalid("emotion response has no usable windows")
    return list(windows.values())


def _emotion_score(value: object) -> float | None:
    import math

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(float(value)):
        return None
    return round(min(1.0, max(0.0, float(value))), 3)
