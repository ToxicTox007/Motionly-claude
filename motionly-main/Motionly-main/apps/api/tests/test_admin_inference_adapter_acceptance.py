"""Task 24/25 acceptance: a registry-only ``huggingface_endpoint`` route and a
registry-only ``aws_bedrock`` route each pass the admin route test against fakes
(plan Tier 3 gate item 3).

Both adapter types are wired into the registry validator and the runtime by the
parallel Task 24/25 work. These tests build each adapter with a fake transport,
register it on a gateway, and drive it through the same ``run_route_test`` the
admin API uses, asserting a schema-valid ``ok`` outcome with latency and usage.

If either adapter type is not wired when this suite runs, the matching test is
skipped with reason ``"adapter type integration pending"``.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.inference_capabilities import InferenceCapability
from app.services.inference_route_test import run_route_test
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    InMemoryCircuitStore,
    ModelCapability,
    ModelGateway,
    ModelRoute,
)

hf_adapter = pytest.importorskip(
    "app.services.huggingface_endpoint_adapter", reason="adapter type integration pending"
)
bedrock_adapter = pytest.importorskip(
    "app.services.aws_bedrock_adapter", reason="adapter type integration pending"
)


def _gateway(adapter_id: str, adapter, route: ModelRoute) -> ModelGateway:
    return ModelGateway(
        adapters={adapter_id: adapter},
        routes=[route],
        budget_ledger=InMemoryBudgetLedger(),
        circuit_store=InMemoryCircuitStore(),
        request_budget_usd=100.0,
        monthly_budget_usd=1000.0,
    )


def _planning_route(adapter_id: str, provider: str) -> ModelRoute:
    return ModelRoute(
        route_id=f"{provider}-planning",
        capability=ModelCapability.PLANNING,
        adapter=adapter_id,
        provider=provider,
        model="test-model-v1",
        model_revision="2024-01-01",
        explicitly_free=True,
        capability_id=str(InferenceCapability.PLANNING_CHAT),
    )


def test_huggingface_endpoint_route_passes_admin_route_test() -> None:
    from app.services.inference_http import NoAuth

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "a plan"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    config = hf_adapter.HuggingFaceEndpointConfig(base_url="https://e.invalid", mode="tgi")
    adapter = hf_adapter.huggingface_endpoint_factory(
        config=config,
        auth=NoAuth(),
        provider="endpoint",
        transport=httpx.MockTransport(handler),
    )
    route = _planning_route("hf-1", "endpoint")
    gateway = _gateway("hf-1", adapter, route)

    outcome = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)

    assert outcome.status == "ok"
    assert outcome.schema_valid is True
    assert outcome.latency_ms is not None
    assert outcome.usage["input_units"] == 5


def test_aws_bedrock_route_passes_admin_route_test() -> None:
    from datetime import UTC, datetime

    def clock() -> datetime:
        return datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {"message": {"content": [{"text": "a plan"}]}},
                "usage": {"inputTokens": 5, "outputTokens": 2},
            },
        )

    adapter = bedrock_adapter.aws_bedrock_factory(
        config=bedrock_adapter.BedrockConfig(region="us-east-1", api="converse"),
        env={"AWS_ACCESS_KEY_ID": "AKIDEXAMPLE", "AWS_SECRET_ACCESS_KEY": "s" * 20},
        provider="bedrock",
        bedrock_transport=httpx.MockTransport(handler),
        clock=clock,
    )
    route = _planning_route("bedrock-1", "bedrock")
    gateway = _gateway("bedrock-1", adapter, route)

    outcome = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)

    assert outcome.status == "ok"
    assert outcome.schema_valid is True
    assert outcome.latency_ms is not None
    assert outcome.usage["input_units"] == 5
