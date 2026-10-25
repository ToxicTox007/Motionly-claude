"""OpenAI-compatible embeddings adapter: openai vs typed_objects input styles.

Task 12 adds the closed ``embeddings_input_style`` option (plan Section 2.10).
Text embeddings use the OpenAI ``input: [str]`` shape by default; a
``typed_objects`` adapter sends ``input: [{"text": ...}]`` / ``[{"image": ...}]``
so text and image embeddings share one vector space. These tests cover the
request shape, dimensions, input order, image MIME check and the normalization
request fields.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from app.services.model_adapters import OpenAICompatibleAdapter
from app.services.model_gateway import (
    EmbeddingsOutput,
    EmbeddingsRequest,
    ImageEmbeddingsRequest,
    ModelCapability,
    ModelRoute,
)
from app.services.provider_resilience import ProviderCallFailure

BASE_URL = "https://embeddings.example/v1"
_JPEG = b"\xff\xd8\xff\xe0jpeg-bytes"
_PNG = b"\x89PNG\r\n\x1a\npng-bytes"


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="embeddings",
        capability=ModelCapability.EMBEDDINGS,
        adapter="a",
        provider="remote",
        model="embed-model",
        model_revision="embed-model-2026-07-14",
    )


def _install(monkeypatch: pytest.MonkeyPatch, data: list[dict]) -> list[dict]:
    calls: list[dict] = []

    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client
        calls.append({"method": method, "url": url, **kwargs})
        return httpx.Response(200, json={"data": data}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    return calls


def _vectors(count: int, dims: int = 3) -> list[dict]:
    return [{"index": i, "embedding": [0.1 * (i + 1)] * dims} for i in range(count)]


def test_openai_style_sends_flat_string_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, _vectors(2))
    adapter = OpenAICompatibleAdapter(base_url=BASE_URL, api_key="key")

    result = adapter.invoke(route=_route(), request=EmbeddingsRequest(inputs=("a", "b")))

    body = calls[0]["json"]
    assert body["input"] == ["a", "b"]
    assert "normalized" not in body and "embedding_type" not in body
    assert calls[0]["url"] == f"{BASE_URL}/embeddings"
    assert isinstance(result.output, EmbeddingsOutput)
    assert len(result.output.vectors) == 2


def test_typed_objects_style_wraps_text_and_requests_normalized_vectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch, _vectors(2))
    adapter = OpenAICompatibleAdapter(
        base_url=BASE_URL, api_key="key", embeddings_input_style="typed_objects"
    )

    adapter.invoke(route=_route(), request=EmbeddingsRequest(inputs=("a", "b"), dimensions=3))

    body = calls[0]["json"]
    assert body["input"] == [{"text": "a"}, {"text": "b"}]
    assert body["normalized"] is True
    assert body["embedding_type"] == "float"
    assert body["dimensions"] == 3


def test_input_order_is_preserved_when_provider_reorders_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The provider returns the second vector first; the adapter restores order.
    _install(
        monkeypatch,
        [
            {"index": 1, "embedding": [0.9, 0.9, 0.9]},
            {"index": 0, "embedding": [0.1, 0.1, 0.1]},
        ],
    )
    adapter = OpenAICompatibleAdapter(base_url=BASE_URL, api_key="key")

    result = adapter.invoke(route=_route(), request=EmbeddingsRequest(inputs=("first", "second")))

    assert result.output.vectors == ((0.1, 0.1, 0.1), (0.9, 0.9, 0.9))


def test_typed_objects_image_embeddings_use_sniffed_data_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch, _vectors(2))
    adapter = OpenAICompatibleAdapter(
        base_url=BASE_URL, api_key="key", embeddings_input_style="typed_objects"
    )

    adapter.invoke(route=_route(), request=ImageEmbeddingsRequest(images=(_JPEG, _PNG)))

    body = calls[0]["json"]
    first, second = body["input"]
    assert first["image"] == f"data:image/jpeg;base64,{base64.b64encode(_JPEG).decode()}"
    assert second["image"] == f"data:image/png;base64,{base64.b64encode(_PNG).decode()}"
    assert body["normalized"] is True


def test_image_embeddings_require_typed_objects_style() -> None:
    adapter = OpenAICompatibleAdapter(base_url=BASE_URL, api_key="key")

    with pytest.raises(ProviderCallFailure) as raised:
        adapter.invoke(route=_route(), request=ImageEmbeddingsRequest(images=(_JPEG,)))

    assert raised.value.error_info.code == "provider_request_rejected"


def test_image_mime_check_rejects_unknown_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _vectors(1))
    adapter = OpenAICompatibleAdapter(
        base_url=BASE_URL, api_key="key", embeddings_input_style="typed_objects"
    )

    with pytest.raises(ProviderCallFailure):
        adapter.invoke(route=_route(), request=ImageEmbeddingsRequest(images=(b"not-an-image",)))


def test_unknown_input_style_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="embeddings_input_style"):
        OpenAICompatibleAdapter(
            base_url=BASE_URL, api_key="key", embeddings_input_style="nonstandard"
        )


def test_dimension_mismatch_is_a_malformed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [{"index": 0, "embedding": [0.1, 0.2]}])
    adapter = OpenAICompatibleAdapter(base_url=BASE_URL, api_key="key")

    with pytest.raises(ProviderCallFailure):
        adapter.invoke(route=_route(), request=EmbeddingsRequest(inputs=("x",), dimensions=4))
