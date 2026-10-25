"""Tests for the Bedrock Converse object-detection helpers (spec item B).

Pure helpers: no network, no transport. These exercise image-format sniffing,
Converse image blocks (raw bytes and ``data:`` URLs), the detection request body
and the detection-text parser (fences, prose, Amazon Nova 0-1000 box rescaling,
aliases, clamping, ordering and the empty/garbage edge cases).
"""

from __future__ import annotations

import base64
import json

import pytest

from app.services.aws_bedrock_vision import (
    DETECTION_PROMPT,
    DetectionParseError,
    detection_converse_body,
    image_block_from_bytes,
    image_block_from_data_url,
    parse_detection_text,
    sniff_image_format,
)
from app.services.model_gateway import DetectedObjectOutput

# --- minimal, valid magic-byte headers for each accepted format ------------
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 8
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
GIF_BYTES = b"GIF89a" + b"\x00" * 16
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16


# --- sniff_image_format ----------------------------------------------------
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (JPEG_BYTES, "jpeg"),
        (PNG_BYTES, "png"),
        (GIF_BYTES, "gif"),
        (WEBP_BYTES, "webp"),
        (b"GIF87a" + b"\x00" * 10, "gif"),
    ],
)
def test_sniff_image_format_recognizes_each_format(data: bytes, expected: str) -> None:
    assert sniff_image_format(data) == expected


@pytest.mark.parametrize("data", [b"", b"not an image", b"RIFF----XXXX", 1234, None])
def test_sniff_image_format_unknown_returns_none(data: object) -> None:
    assert sniff_image_format(data) is None


# --- image_block_from_bytes ------------------------------------------------
def test_image_block_from_bytes_encodes_base64_and_format() -> None:
    block = image_block_from_bytes(PNG_BYTES)
    image = block["image"]
    assert image["format"] == "png"
    decoded = base64.b64decode(image["source"]["bytes"])
    assert decoded == PNG_BYTES


def test_image_block_from_bytes_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unsupported or unrecognized image format"):
        image_block_from_bytes(b"nope")


# --- image_block_from_data_url ---------------------------------------------
def test_image_block_from_data_url_decodes_base64_payload() -> None:
    encoded = base64.b64encode(JPEG_BYTES).decode("ascii")
    block = image_block_from_data_url(f"data:image/jpeg;base64,{encoded}")
    assert block is not None
    assert block["image"]["format"] == "jpeg"
    assert base64.b64decode(block["image"]["source"]["bytes"]) == JPEG_BYTES


def test_image_block_from_data_url_decodes_percent_encoded_payload() -> None:
    # Percent-encoded (non-base64) data URL carrying GIF magic bytes.
    payload = "".join(f"%{byte:02X}" for byte in GIF_BYTES)
    block = image_block_from_data_url(f"data:image/gif,{payload}")
    assert block is not None
    assert block["image"]["format"] == "gif"
    assert base64.b64decode(block["image"]["source"]["bytes"]) == GIF_BYTES


def test_image_block_from_data_url_returns_none_for_http_url() -> None:
    assert image_block_from_data_url("https://example.com/cat.jpg") is None
    assert image_block_from_data_url(1234) is None


def test_image_block_from_data_url_rejects_bad_base64() -> None:
    with pytest.raises(ValueError, match="invalid base64 payload"):
        image_block_from_data_url("data:image/png;base64,!!!notbase64!!!")


def test_image_block_from_data_url_rejects_missing_separator() -> None:
    with pytest.raises(ValueError, match="missing a payload separator"):
        image_block_from_data_url("data:image/png;base64")


# --- detection_converse_body -----------------------------------------------
def test_detection_converse_body_structure() -> None:
    body = detection_converse_body(JPEG_BYTES, max_results=5)
    messages = body["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    # Image block first, then the instruction text.
    assert "image" in content[0]
    assert content[0]["image"]["format"] == "jpeg"
    assert DETECTION_PROMPT in content[1]["text"]
    assert "at most 5 objects" in content[1]["text"]
    assert body["inferenceConfig"]["temperature"] == 0
    assert body["inferenceConfig"]["maxTokens"] >= 256


def test_detection_converse_body_accepts_custom_prompt() -> None:
    body = detection_converse_body(PNG_BYTES, max_results=2, prompt="find dogs")
    assert "find dogs" in body["messages"][0]["content"][1]["text"]


def test_detection_converse_body_rejects_unknown_image() -> None:
    with pytest.raises(ValueError):
        detection_converse_body(b"garbage", max_results=3)


# --- parse_detection_text --------------------------------------------------
def test_parse_detection_text_scales_nova_boxes_to_unit() -> None:
    text = json.dumps(
        [{"label": "Cat", "box": [100, 200, 600, 800], "score": 0.9}]
    )
    detections = parse_detection_text(text, max_results=10)
    assert len(detections) == 1
    obj = detections[0]
    assert isinstance(obj, DetectedObjectOutput)
    assert obj.label == "cat"
    assert obj.confidence == 0.9
    # 0-1000 corners -> [0, 1]: x=0.1, y=0.2, w=0.5, h=0.6
    assert obj.x == pytest.approx(0.1)
    assert obj.y == pytest.approx(0.2)
    assert obj.width == pytest.approx(0.5)
    assert obj.height == pytest.approx(0.6)


def test_parse_detection_text_tolerates_json_fence_and_prose() -> None:
    text = (
        "Here are the objects I found:\n"
        "```json\n"
        '[{"label": "dog", "box": [0, 0, 500, 500], "score": 0.7}]\n'
        "```\n"
        "Hope that helps!"
    )
    detections = parse_detection_text(text, max_results=10)
    assert [d.label for d in detections] == ["dog"]


def test_parse_detection_text_extracts_array_from_surrounding_prose() -> None:
    text = 'The result: [{"label": "car", "box": [10, 10, 990, 990]}] done.'
    detections = parse_detection_text(text, max_results=10)
    assert detections[0].label == "car"
    # Missing score falls back to default_score.
    assert detections[0].confidence == 0.5


def test_parse_detection_text_applies_default_score_override() -> None:
    text = '[{"label": "tree", "box": [0, 0, 100, 100]}]'
    detections = parse_detection_text(text, max_results=5, default_score=0.3)
    assert detections[0].confidence == 0.3


def test_parse_detection_text_applies_label_aliases() -> None:
    text = '[{"label": "automobile", "box": [0, 0, 100, 100], "score": 0.8}]'
    detections = parse_detection_text(
        text, max_results=5, label_aliases={"automobile": "car"}
    )
    assert detections[0].label == "car"


def test_parse_detection_text_sorts_by_confidence_and_caps() -> None:
    text = json.dumps(
        [
            {"label": "a", "box": [0, 0, 100, 100], "score": 0.2},
            {"label": "b", "box": [0, 0, 100, 100], "score": 0.9},
            {"label": "c", "box": [0, 0, 100, 100], "score": 0.5},
        ]
    )
    detections = parse_detection_text(text, max_results=2)
    assert [d.label for d in detections] == ["b", "c"]


def test_parse_detection_text_object_wrapper_key() -> None:
    text = json.dumps(
        {"objects": [{"label": "bird", "box": [0, 0, 300, 300], "score": 0.6}]}
    )
    detections = parse_detection_text(text, max_results=5)
    assert detections[0].label == "bird"


def test_parse_detection_text_skips_invalid_entries() -> None:
    text = json.dumps(
        [
            {"label": "ok", "box": [0, 0, 100, 100], "score": 0.9},
            {"label": "no-box"},
            {"label": "bad-box", "box": [1, 2, 3]},
            {"label": "nan-box", "box": ["a", "b", "c", "d"]},
            "not-an-object",
            {"box": [0, 0, 50, 50], "score": 0.4},  # empty label dropped
        ]
    )
    detections = parse_detection_text(text, max_results=10)
    assert [d.label for d in detections] == ["ok"]


def test_parse_detection_text_empty_array_returns_empty_tuple() -> None:
    assert parse_detection_text("[]", max_results=10) == ()
    assert parse_detection_text("```json\n[]\n```", max_results=10) == ()


@pytest.mark.parametrize("text", ["", "   ", "not json at all", "{}", 1234, None])
def test_parse_detection_text_unparsable_raises(text: object) -> None:
    with pytest.raises(DetectionParseError):
        parse_detection_text(text, max_results=10)


def test_parse_detection_text_clamps_out_of_range_boxes() -> None:
    # Corners beyond 0-1000 scale to > 1 and must clamp into [0, 1].
    text = '[{"label": "wide", "box": [-100, -100, 1500, 1500], "score": 1.5}]'
    detections = parse_detection_text(text, max_results=5)
    obj = detections[0]
    assert obj.x == 0.0
    assert obj.y == 0.0
    assert obj.width == 1.0
    assert obj.height == 1.0
    assert obj.confidence == 1.0
