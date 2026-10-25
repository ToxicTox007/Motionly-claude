"""Tests for the Tier 3 ``generic_http`` extensions (plan Task 28, Section 2.10).

Covers the five extensions that touch ``generic_http``: multipart request
bodies, the ``xyxy_norm``/``xyxy_px`` box formats (with stdlib image-dimension
parsing), ``usage`` taken from a response path, and the typed transcription /
caption / embeddings mappings for non-OpenAI-compatible APIs. Each runs against
a fake API with ``httpx.MockTransport`` and registry config only.
"""

from __future__ import annotations

import struct
from pathlib import Path

import httpx

from app.services.generic_http_adapter import GenericHttpAdapter, GenericHttpRouteConfig
from app.services.inference_http import NoAuth
from app.services.inference_normalization import image_dimensions
from app.services.model_gateway import (
    EmbeddingsOutput,
    EmbeddingsRequest,
    ModelCapability,
    ModelRoute,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    VisionOutput,
    VisionRequest,
)

PNG_2x3 = (
    b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 2, 3) + b"\x00" * 5
)


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="r1",
        capability=ModelCapability.VISION,
        adapter="g-1",
        provider="endpoint",
        model="m",
        model_revision="2024-01-01",
    )


def _adapter(mapping, handler, **cfg):
    adapter = GenericHttpAdapter(
        base_url="https://e.invalid",
        auth=NoAuth(),
        timeout_s=10,
        provider="endpoint",
        transport=httpx.MockTransport(handler),
    )
    adapter.register_route("r1", GenericHttpRouteConfig(mapping=mapping, **cfg))
    return adapter


def _json_responder(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


# --- image dimension header parsing ---------------------------------------
def test_image_dimensions_png() -> None:
    assert image_dimensions(PNG_2x3) == (2, 3)


def test_image_dimensions_gif() -> None:
    gif = b"GIF89a" + struct.pack("<HH", 10, 20) + b"\x00" * 4
    assert image_dimensions(gif) == (10, 20)


def test_image_dimensions_jpeg() -> None:
    # SOI + SOF0 marker with height=40 width=60.
    sof = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 40, 60)
    jpeg = b"\xff\xd8" + sof + b"\x00" * 6
    assert image_dimensions(jpeg) == (60, 40)


def test_image_dimensions_webp_vp8x() -> None:
    # VP8X chunk: fourcc at 12, width-1/height-1 (24-bit LE) at 24/27.
    data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8X" + b"\x00" * 8
    data += (99).to_bytes(3, "little") + (49).to_bytes(3, "little")
    assert image_dimensions(data) == (100, 50)


# --- xyxy box formats ------------------------------------------------------
def test_xyxy_norm_box_format() -> None:
    mapping = {
        "request": {"method": "POST", "path": "/d", "body": {}},
        "items": "response.results",
        "aligned_to_input": True,
        "fields": {
            "detections": "objects",
            "label": "name",
            "score": "score",
            "box": "bbox",
        },
        "box_format": "xyxy_norm",
    }
    payload = {
        "results": [
            {"objects": [{"name": "cat", "score": 0.9, "bbox": [0.1, 0.2, 0.5, 0.8]}]}
        ]
    }
    adapter = _adapter(mapping, _json_responder(payload))
    result = adapter.invoke(route=_route(), request=ObjectDetectionRequest(images=(PNG_2x3,)))
    assert isinstance(result.output, ObjectDetectionOutput)
    box = result.output.detections[0][0]
    assert (box.x, box.y, box.width, box.height) == (0.1, 0.2, 0.4, 0.6)


def test_xyxy_px_box_format_uses_image_dimensions() -> None:
    mapping = {
        "request": {"method": "POST", "path": "/d", "body": {}},
        "items": "response.results",
        "aligned_to_input": True,
        "fields": {
            "detections": "objects",
            "label": "name",
            "score": "score",
            "box": "bbox",
        },
        "box_format": "xyxy_px",
    }
    # 2x3 PNG; a pixel box [0,0, 1, 3] normalises to x=0,y=0,w=0.5,h=1.0.
    payload = {
        "results": [{"objects": [{"name": "cat", "score": 0.9, "bbox": [0, 0, 1, 3]}]}]
    }
    adapter = _adapter(mapping, _json_responder(payload))
    result = adapter.invoke(route=_route(), request=ObjectDetectionRequest(images=(PNG_2x3,)))
    box = result.output.detections[0][0]
    assert (box.x, box.y, box.width, box.height) == (0.0, 0.0, 0.5, 1.0)


# --- multipart -------------------------------------------------------------
def test_multipart_request_body(tmp_path: Path) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "hi", "words": []})

    mapping = {
        "output": "transcription",
        "request": {
            "method": "POST",
            "path": "/asr",
            "body": {
                "$multipart": {
                    "model": "whisper",
                    "file": {"$file": {"$ref": "input.file"}, "filename": "a.wav"},
                }
            },
        },
        "fields": {"text": "response.text", "words": "response.words"},
    }
    adapter = _adapter(mapping, handler)
    tmp = tmp_path / "a.wav"
    tmp.write_bytes(b"RIFFfakeaudio")
    result = adapter.invoke(route=_route(), request=TranscriptionRequest(audio_path=tmp))
    assert isinstance(result.output, TranscriptionOutput)
    assert "multipart/form-data" in seen["content_type"]
    assert b"whisper" in seen["body"]


# --- typed transcription with word timestamps -----------------------------
def test_typed_transcription_word_timestamps(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFfake")
    mapping = {
        "output": "transcription",
        "time_unit": "seconds",
        "request": {
            "method": "POST",
            "path": "/asr",
            "body": {"audio": {"$fn": "base64", "args": [{"$ref": "input.file"}]}},
        },
        "fields": {
            "text": "response.transcript",
            "language": "response.lang",
            "words": "response.words",
            "word": "w",
            "start": "s",
            "end": "e",
            "confidence": "c",
        },
    }
    payload = {
        "transcript": "hi there",
        "lang": "en",
        "words": [
            {"w": "hi", "s": 0.0, "e": 0.5, "c": 0.99},
            {"w": "there", "s": 0.5, "e": 1.2, "c": 0.9},
        ],
    }
    adapter = _adapter(mapping, _json_responder(payload))
    result = adapter.invoke(route=_route(), request=TranscriptionRequest(audio_path=audio))
    assert isinstance(result.output, TranscriptionOutput)
    assert result.output.text == "hi there"
    assert result.output.language == "en"
    assert result.output.words[0].start_ms == 0
    assert result.output.words[1].end_ms == 1200


# --- typed caption ---------------------------------------------------------
def test_typed_caption() -> None:
    mapping = {
        "output": "caption",
        "request": {
            "method": "POST",
            "path": "/cap",
            "body": {"image": {"$ref": "input.image_urls[0]"}},
        },
        "fields": {"caption": "response.result.caption"},
    }
    payload = {"result": {"caption": "a sunset"}}
    adapter = _adapter(mapping, _json_responder(payload))
    result = adapter.invoke(
        route=_route(),
        request=VisionRequest(prompt="caption", image_urls=("https://img.invalid/1.jpg",)),
    )
    assert isinstance(result.output, VisionOutput)
    assert result.output.content == "a sunset"


# --- typed embeddings + usage from a response path -------------------------
def test_typed_text_embeddings_with_usage_path() -> None:
    mapping = {
        "output": "text_embeddings",
        "request": {
            "method": "POST",
            "path": "/emb",
            "body": {"inputs": {"$ref": "input.inputs"}},
        },
        "items": "response.data",
        "aligned_to_input": True,
        "fields": {"vector": "embedding"},
        "usage": "response_path",
        "usage_path": "response.usage.total_tokens",
    }
    payload = {
        "data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}],
        "usage": {"total_tokens": 11},
    }
    adapter = _adapter(mapping, _json_responder(payload))
    result = adapter.invoke(route=_route(), request=EmbeddingsRequest(inputs=("a", "b")))
    assert isinstance(result.output, EmbeddingsOutput)
    assert result.output.vectors == ((0.1, 0.2), (0.3, 0.4))
    assert result.usage.input_units == 11  # taken from response.usage.total_tokens
