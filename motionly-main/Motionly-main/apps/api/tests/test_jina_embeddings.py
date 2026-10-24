"""Jina remote embeddings (EMBEDDINGS_PROVIDER=jina)."""

from __future__ import annotations

import base64

import httpx
import pytest

from app.core.config import get_settings
from app.main import _validate_production_settings, create_app
from app.services.model_adapters import JinaEmbeddingsAdapter
from app.services.model_domain_adapters import (
    GatewayClipEmbeddingProvider,
    register_clip_routes,
    register_jina_embedding_routes,
)
from app.services.model_gateway import (
    EmbeddingsRequest,
    ImageEmbeddingsRequest,
    InMemoryBudgetLedger,
    ModelCallContext,
    ModelGateway,
    ModelOperation,
    ModelProvidersExhausted,
    budget_scope,
    feature_budget_scope,
)
from app.services.provider_resilience import ProviderCallFailure
from tests.test_service_hardening import _production_settings

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes"
PNG = b"\x89PNG\r\n\x1a\n" + b"png-bytes"
REVISION = "jina-clip-v2-2024-11-05"


def _install_fake_jina(monkeypatch: pytest.MonkeyPatch, responder) -> list[dict]:
    calls: list[dict] = []

    def fake_request(client, method, url, **kwargs):  # noqa: ANN001
        del client
        calls.append({"method": method, "url": url, **kwargs})
        status, body = responder(kwargs)
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    return calls


def _vectors_for(kwargs: dict, *, dims: int = 4, reverse: bool = False) -> dict:
    inputs = kwargs["json"]["input"]
    data = [
        {"index": index, "embedding": [round(0.1 * (index + 1), 3)] * dims}
        for index in range(len(inputs))
    ]
    if reverse:
        data.reverse()
    return {"model": "jina-clip-v2", "data": data, "usage": {"total_tokens": 12}}


def _gateway(*, text_price: float = 0.05, image_price: float = 0.001, **kwargs) -> ModelGateway:
    gateway = ModelGateway(adapters={}, routes=[], **kwargs)
    register_jina_embedding_routes(
        gateway,
        adapter=JinaEmbeddingsAdapter(api_key="jina-key", dimensions=4),
        model="jina-clip-v2",
        revision=REVISION,
        text_cost_per_million_tokens_usd=text_price,
        image_cost_per_image_usd=image_price,
    )
    return gateway


def test_text_request_maps_to_jina_multimodal_input(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_jina(monkeypatch, lambda kwargs: (200, _vectors_for(kwargs)))
    gateway = _gateway()

    result = gateway.invoke(EmbeddingsRequest(inputs=("a beach at sunset", "city skyline")))

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://api.jina.ai/v1/embeddings"
    assert calls[0]["headers"]["authorization"] == "Bearer jina-key"
    assert calls[0]["json"] == {
        "model": "jina-clip-v2",
        "input": [{"text": "a beach at sunset"}, {"text": "city skyline"}],
        "normalized": True,
        "embedding_type": "float",
        "dimensions": 4,
    }
    assert len(result.output.vectors) == 2
    assert result.metadata.provider == "jina"
    assert result.metadata.model_revision == REVISION
    # Vectors are not duplicated into the stored raw response.
    assert "data" not in result.output.raw_response


def test_image_request_sends_typed_data_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_jina(monkeypatch, lambda kwargs: (200, _vectors_for(kwargs)))

    _gateway().invoke(ImageEmbeddingsRequest(images=(JPEG, PNG)))

    sent = calls[0]["json"]["input"]
    assert sent[0]["image"] == "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    assert sent[1]["image"].startswith("data:image/png;base64,")


def test_unsupported_image_is_rejected_before_any_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_jina(monkeypatch, lambda kwargs: (200, _vectors_for(kwargs)))
    ledger = InMemoryBudgetLedger()

    with pytest.raises(ModelProvidersExhausted) as raised:
        _gateway(budget_ledger=ledger).invoke(
            ImageEmbeddingsRequest(images=(b"not-an-image",)),
            context=ModelCallContext(workspace_id="workspace-one"),
        )

    assert calls == []
    assert raised.value.failures[0]["code"] == "provider_request_rejected"
    # A local rejection is not billable.
    assert ledger.usage(budget_scope("workspace-one")) == 0.0


def test_vectors_are_returned_in_input_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_jina(monkeypatch, lambda kwargs: (200, _vectors_for(kwargs, reverse=True)))

    result = _gateway().invoke(EmbeddingsRequest(inputs=("one", "two", "three")))

    assert [vector[0] for vector in result.output.vectors] == [0.1, 0.2, 0.3]


def test_wrong_dimension_is_a_malformed_response() -> None:
    adapter = JinaEmbeddingsAdapter(api_key="jina-key", dimensions=4)
    gateway = _gateway()
    route = next(r for r in gateway.routes if r.route_id == "jina-embeddings-text")

    with pytest.MonkeyPatch.context() as patch:
        _install_fake_jina(patch, lambda kwargs: (200, _vectors_for(kwargs, dims=3)))
        with pytest.raises(ProviderCallFailure) as raised:
            adapter.invoke(route=route, request=EmbeddingsRequest(inputs=("x",)))

    assert raised.value.error_info.code == "provider_malformed_response"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "provider_rate_limited", True),
        (401, "provider_auth_failed", False),
        (503, "provider_unavailable", True),
    ],
)
def test_http_errors_are_classified(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str, retryable: bool
) -> None:
    _install_fake_jina(monkeypatch, lambda kwargs: (status, {"detail": "nope"}))
    adapter = JinaEmbeddingsAdapter(api_key="jina-key", dimensions=4)
    route = next(r for r in _gateway().routes if r.route_id == "jina-embeddings-text")

    with pytest.raises(ProviderCallFailure) as raised:
        adapter.invoke(route=route, request=EmbeddingsRequest(inputs=("x",)))

    assert raised.value.error_info.code == code
    assert raised.value.error_info.retryable is retryable


def test_costs_are_deterministic_and_charged_to_the_embeddings_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_jina(monkeypatch, lambda kwargs: (200, _vectors_for(kwargs)))
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(
        text_price=0.05,
        image_price=0.001,
        budget_ledger=ledger,
        feature_budgets_usd={"embeddings": 10.0},
    )
    context = ModelCallContext(workspace_id="workspace-one")

    text = gateway.invoke(EmbeddingsRequest(inputs=("hello world",)), context=context)
    image = gateway.invoke(ImageEmbeddingsRequest(images=(JPEG, PNG)), context=context)

    # 11 UTF-8 bytes at $0.05 per million; 2 images at $0.001 each.
    assert text.metadata.actual_cost_usd == pytest.approx(11 * 0.05 / 1_000_000)
    assert image.metadata.actual_cost_usd == pytest.approx(0.002)
    total = text.metadata.actual_cost_usd + image.metadata.actual_cost_usd
    assert ledger.usage(budget_scope("workspace-one")) == pytest.approx(total)
    assert ledger.usage(feature_budget_scope("embeddings")) == pytest.approx(total)


def test_routes_are_gated_remote_routes_for_each_embedding_operation() -> None:
    routes = {route.route_id: route for route in _gateway().routes}

    assert routes["jina-embeddings-text"].operations == (ModelOperation.EMBEDDINGS_TEXT,)
    assert routes["jina-embeddings-image"].operations == (ModelOperation.EMBEDDINGS_IMAGE,)
    for route in routes.values():
        assert route.endpoint_host == "api.jina.ai"
        assert route.requires_live_effects_gate is True
        assert route.budget_feature == "embeddings"


def test_gateway_provider_reports_jina_revision_and_new_cache_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_jina(monkeypatch, lambda kwargs: (200, _vectors_for(kwargs)))
    jina_provider = GatewayClipEmbeddingProvider(gateway=_gateway())
    local_gateway = ModelGateway(adapters={}, routes=[])
    register_clip_routes(
        local_gateway,
        primary_provider=object(),
        primary_revision="3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
        fallback_provider=None,
    )
    local_provider = GatewayClipEmbeddingProvider(gateway=local_gateway)

    vector = jina_provider.embed_text("product close up")
    image_vector = jina_provider.embed_image(image_bytes=JPEG)

    assert len(vector) == 4 and len(image_vector) == 4
    assert jina_provider.model_version == REVISION
    assert jina_provider.preferred_model_revision == REVISION
    # Switching providers must invalidate cached stock-asset embeddings.
    assert jina_provider.cache_version != local_provider.cache_version


def _route_ids(app) -> set[str]:
    return {route.route_id for route in app.state.model_gateway.routes}


def test_create_app_registers_jina_routes_instead_of_local_clip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "jina")
    monkeypatch.setenv("JINA_API_KEY", "jina-key")
    get_settings.cache_clear()

    app = create_app()

    route_ids = _route_ids(app)
    assert {"jina-embeddings-text", "jina-embeddings-image"} <= route_ids
    assert "local-clip-primary" not in route_ids
    assert "compatible-hash-clip-fallback" not in route_ids
    assert app.state.embedding_gateway_provider.preferred_model_revision == REVISION


def test_create_app_defaults_to_local_clip() -> None:
    app = create_app()

    route_ids = _route_ids(app)
    assert "local-clip-primary" in route_ids
    assert not any(route_id.startswith("jina-") for route_id in route_ids)


def test_create_app_requires_a_jina_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "jina")
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="JINA_API_KEY"):
        create_app()


_PRICED_JINA = {
    "embeddings_provider": "jina",
    "jina_api_key": "jina-key",
    "jina_text_cost_per_million_tokens_usd": 0.05,
    "jina_image_cost_per_image_usd": 0.0005,
}


def test_production_accepts_a_priced_jina_configuration() -> None:
    _validate_production_settings(_production_settings(**_PRICED_JINA))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"jina_api_key": None}, "JINA_API_KEY"),
        ({"jina_text_cost_per_million_tokens_usd": 0.0}, "must be positive"),
        ({"jina_image_cost_per_image_usd": 0.0}, "must be positive"),
        ({"jina_embeddings_model_revision": "jina-clip-v2"}, "immutable"),
        ({"jina_embeddings_model_revision": "latest"}, "immutable"),
    ],
)
def test_production_rejects_incomplete_jina_configuration(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _validate_production_settings(_production_settings(**{**_PRICED_JINA, **overrides}))


def test_env_examples_document_the_embeddings_switch() -> None:
    from tests.test_service_hardening import ROOT

    for name in (".env.example", "apps/api/.env.example"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "EMBEDDINGS_PROVIDER=local_clip" in text, name
        assert "JINA_API_KEY=" in text, name
    # The production template keeps optional settings commented.
    assert "# export EMBEDDINGS_PROVIDER=" in (ROOT / "prod.env.example").read_text(
        encoding="utf-8"
    )
