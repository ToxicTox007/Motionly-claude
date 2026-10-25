"""Motionly-owned inference strategies: prompts, schemas and parsers (Task 11)."""

from __future__ import annotations

import json

import pytest

from app.services.inference_strategies import (
    EMOTION_LABELS,
    EMOTION_SCORE_FIELDS,
    MAX_CAPTION_CHARS,
    CaptionResponseInvalid,
    EmotionResponseInvalid,
    caption_response_format,
    emotion_response_format,
    parse_captions,
    parse_emotion_windows,
)

# -- captions ------------------------------------------------------------------


def test_caption_schema_is_strict_and_bounded() -> None:
    schema = caption_response_format(3)
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True
    item = schema["json_schema"]["schema"]["properties"]["captions"]["items"]
    assert item["properties"]["index"]["maximum"] == 2
    assert item["properties"]["caption"]["maxLength"] == MAX_CAPTION_CHARS


def test_parse_captions_orders_and_fills_gaps() -> None:
    reply = json.dumps(
        {
            "captions": [
                {"index": 2, "caption": "  a  city\nstreet "},
                {"index": 0, "caption": "first"},
                {"index": 0, "caption": "ignored duplicate"},
                {"index": 7, "caption": "out of range"},
                {"index": True, "caption": "bool index"},
                {"index": 1, "caption": "   "},
            ]
        }
    )

    assert parse_captions(reply, count=3) == ["first", "", "a city street"]
    # Fenced code blocks are tolerated.
    assert parse_captions("```json\n" + reply + "\n```", count=3)[0] == "first"


def test_parse_captions_truncates_to_the_cap() -> None:
    long = "x " * 400
    captions = parse_captions(json.dumps({"captions": [{"index": 0, "caption": long}]}), count=1)
    assert len(captions[0]) <= MAX_CAPTION_CHARS


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"items": []}), json.dumps({"captions": []}), json.dumps([])],
)
def test_parse_captions_rejects_unusable_replies(content: str) -> None:
    with pytest.raises(CaptionResponseInvalid):
        parse_captions(content, count=2)


# -- emotion -------------------------------------------------------------------


def test_emotion_schema_lists_labels_and_score_fields() -> None:
    schema = emotion_response_format(4)
    props = schema["json_schema"]["schema"]["properties"]["windows"]["items"]["properties"]
    assert props["emotion"]["enum"] == list(EMOTION_LABELS)
    for field in EMOTION_SCORE_FIELDS:
        assert props[field]["maximum"] == 1


def test_parse_emotion_windows_clamps_and_maps_to_absolute_time() -> None:
    reply = json.dumps(
        {
            "windows": [
                {"index": 0, "emotion": "ecstatic", "joy": 1.7, "sadness": -0.4, "intensity": 0.5},
                {"index": 0, "emotion": "dup"},  # duplicate: first wins
                {"index": 9, "emotion": "joy"},  # out of range
                {"index": "1", "emotion": "joy", "intensity": 0.5},  # non-integer index
                {"index": 1, "emotion": "calm", "joy": 0.2, "sadness": 0.1, "urgency": 0.3},
            ]
        }
    )

    windows = parse_emotion_windows(
        reply, count=2, window_ms=5000, offset_ms=10000, duration_ms=10000
    )

    assert windows[0]["emotion"] == "neutral"  # unknown label falls back
    assert (windows[0]["joy"], windows[0]["sadness"]) == (1.0, 0.0)
    assert windows[0]["start_ms"] == 10000 and windows[0]["end_ms"] == 15000
    # Missing intensity derives from the strongest present score.
    assert windows[1]["intensity"] == 0.3
    assert len(windows) == 2


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"result": []}), json.dumps({"windows": [{"index": 0}]})],
)
def test_parse_emotion_windows_rejects_unusable_replies(content: str) -> None:
    with pytest.raises(EmotionResponseInvalid):
        parse_emotion_windows(content, count=2, window_ms=5000, offset_ms=0, duration_ms=10000)
