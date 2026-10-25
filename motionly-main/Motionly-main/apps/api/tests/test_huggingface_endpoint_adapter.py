"""Tests for the Hugging Face Inference Endpoint adapter (plan Task 24).

Each mode (tgi, tei, pipeline×4 tasks) is driven against a fake endpoint with
``httpx.MockTransport``. Bearer auth, timeouts, error classification and typed
schema validation are all checked. No credentials other than fake example
tokens appear.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.services.huggingface_endpoint_adapter import (
    HuggingFaceConfigError,
    HuggingFaceEndpointConfig,
    huggingface_endpoint_factory,
    validate_huggingface_config,
)
from app.services.inference_http import NoAuth
from app.services.model_gateway import (
    ChatMessage,
    EmbeddingsOutput,
    EmbeddingsRequest,
    ModelCapability,
    ModelRoute,
    PlanningOutput,
    PlanningRequest,
    SoundClassificationOutput,
    SoundClassificationRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    VisionOutput,
    VisionRequest,
)
from app.services.provider_resilience import ProviderCallFailure


class _BearerCapture:
    """An AuthApplier that records that a bearer token was applied."""

    def __init__(self, token: str) -> None:
        self._token = token
        self.applied = False

    def apply(self, headers) -> None:
        headers["authorization"] = f"Bearer {self._token}"
        self.applied = True


def _route(capability: ModelCapability = ModelCapability.PLANNING) -> ModelRoute:
    return ModelRoute(
        route_id="r1",
        capability=capability,
        adapter="hf-1",
        provider="endpoint",
        model="model-x",
        model_revision="2024-01-01",
    )


def _adapter(config, handler, auth=None):
    return huggingface_endpoint_factory(
        config=config,
        auth=auth or NoAuth(),
        provider="endpoint",
        transport=httpx.MockTransport(handler),
    )


# --- validation ------------------------------------------------------------
def test_validate_rejects_pipeline_without_task() -> None:
    errors = validate_huggingface_config(
        base_url="https://e.invalid", mode="pipeline", pipeline_task=None, timeout_s=1
    )
    assert any("pipeline_task" in e for e in errors)


def test_validate_rejects_task_without_pipeline_mode() -> None:
    errors = validate_huggingface_config(
        base_url="https://e.invalid", mode="tgi", pipeline_task="image-to-text", timeout_s=1
    )
    assert any("only valid for pipeline" in e for e in errors)


def test_config_raises_on_invalid() -> None:
    with pytest.raises(HuggingFaceConfigError):
        HuggingFaceEndpointConfig(base_url="not-a-url", mode="bogus")


# --- tgi -------------------------------------------------------------------
def test_tgi_chat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer tok-123456"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hello there"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    auth = _BearerCapture("tok-123456")
    config = HuggingFaceEndpointConfig(base_url="https://e.invalid", mode="tgi")
    adapter = _adapter(config, handler, auth)
    result = adapter.invoke(
        route=_route(),
        request=PlanningRequest(messages=(ChatMessage(role="user", content="hi"),)),
    )
    assert isinstance(result.output, PlanningOutput)
    assert result.output.content == "hello there"
    assert result.usage.input_units == 7
    assert auth.applied


def test_tgi_rejects_wrong_request() -> None:
    adapter = _adapter(
        HuggingFaceEndpointConfig(base_url="https://e.invalid", mode="tgi"),
        lambda r: httpx.Response(200, json={}),
    )
    with pytest.raises(TypeError):
        adapter.invoke(
            route=_route(),
            request=EmbeddingsRequest(inputs=("x",)),
        )


# --- tei -------------------------------------------------------------------
def test_tei_embeddings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embed"
        return httpx.Response(200, json=[[0.1, 0.2], [0.3, 0.4]])

    adapter = _adapter(HuggingFaceEndpointConfig(base_url="https://e.invalid", mode="tei"), handler)
    result = adapter.invoke(
        route=_route(),
        request=EmbeddingsRequest(inputs=("a", "b")),
    )
    assert isinstance(result.output, EmbeddingsOutput)
    assert result.output.vectors == ((0.1, 0.2), (0.3, 0.4))


def test_tei_length_mismatch_is_malformed() -> None:
    adapter = _adapter(
        HuggingFaceEndpointConfig(base_url="https://e.invalid", mode="tei"),
        lambda r: httpx.Response(200, json=[[0.1, 0.2]]),
    )
    with pytest.raises(ProviderCallFailure) as exc:
        adapter.invoke(
            route=_route(),
            request=EmbeddingsRequest(inputs=("a", "b")),
        )
    assert exc.value.error_info.code == "provider_malformed_response"


# --- pipeline --------------------------------------------------------------
def _pipeline_config(task: str):
    return HuggingFaceEndpointConfig(
        base_url="https://e.invalid", mode="pipeline", pipeline_task=task
    )


def test_pipeline_caption() -> None:
    adapter = _adapter(
        _pipeline_config("image-to-text"),
        lambda r: httpx.Response(200, json=[{"generated_text": "a cat"}]),
    )
    result = adapter.invoke(
        route=_route(),
        request=VisionRequest(prompt="describe", image_urls=("https://img.invalid/1.jpg",)),
    )
    assert isinstance(result.output, VisionOutput)
    assert result.output.content == "a cat"


def test_pipeline_asr_word_timestamps(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFfake")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "hello world",
                "chunks": [
                    {"text": "hello", "timestamp": [0.0, 0.5]},
                    {"text": "world", "timestamp": [0.5, 1.0]},
                ],
            },
        )

    adapter = _adapter(_pipeline_config("automatic-speech-recognition"), handler)
    result = adapter.invoke(
        route=_route(),
        request=TranscriptionRequest(audio_path=audio),
    )
    assert isinstance(result.output, TranscriptionOutput)
    assert result.output.text == "hello world"
    assert len(result.output.words) == 2
    assert result.output.words[0].start_ms == 0
    assert result.output.words[1].end_ms == 1000


def test_pipeline_audio_classification(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFfake")
    adapter = _adapter(
        _pipeline_config("audio-classification"),
        lambda r: httpx.Response(
            200, json=[{"label": "happy", "score": 0.9}, {"label": "sad", "score": 0.1}]
        ),
    )
    result = adapter.invoke(
        route=_route(),
        request=SoundClassificationRequest(audio_path=audio, max_results=5),
    )
    assert isinstance(result.output, SoundClassificationOutput)
    assert result.output.labels[0].label == "happy"
    assert result.output.labels[0].score == pytest.approx(0.9)


def test_pipeline_feature_extraction() -> None:
    adapter = _adapter(
        _pipeline_config("feature-extraction"),
        lambda r: httpx.Response(200, json=[[1.0, 2.0, 3.0]]),
    )
    result = adapter.invoke(
        route=_route(),
        request=EmbeddingsRequest(inputs=("x",)),
    )
    assert isinstance(result.output, EmbeddingsOutput)
    assert result.output.vectors == ((1.0, 2.0, 3.0),)


# --- errors / timeouts -----------------------------------------------------
def test_auth_error_classified() -> None:
    adapter = _adapter(
        HuggingFaceEndpointConfig(base_url="https://e.invalid", mode="tei"),
        lambda r: httpx.Response(401, json={"error": "nope"}),
    )
    with pytest.raises(ProviderCallFailure) as exc:
        adapter.invoke(
            route=_route(),
            request=EmbeddingsRequest(inputs=("a",)),
        )
    assert exc.value.error_info.status_code == 401


def test_timeout_classified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    adapter = _adapter(HuggingFaceEndpointConfig(base_url="https://e.invalid", mode="tei"), handler)
    with pytest.raises(ProviderCallFailure):
        adapter.invoke(
            route=_route(),
            request=EmbeddingsRequest(inputs=("a",)),
        )
