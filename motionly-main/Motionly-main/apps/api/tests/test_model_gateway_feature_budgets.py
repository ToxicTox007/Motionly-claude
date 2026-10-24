"""Per-feature (deployment-wide) monthly budget caps in the model gateway."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.model_gateway import (
    AdapterResult,
    ChatMessage,
    InMemoryBudgetLedger,
    ModelBudgetExceeded,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelRoute,
    ModelUsage,
    PlanningOutput,
    PlanningRequest,
    budget_scope,
    feature_budget_scope,
    parse_feature_budgets,
)
from app.services.model_gateway_factory import model_gateway_from_settings
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo


class FakeAdapter:
    def __init__(self, outcome: AdapterResult | Exception) -> None:
        self.outcome = outcome
        self.calls = 0

    def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
        del route, request
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class RecordingTelemetry:
    def __init__(self) -> None:
        self.attempts: list[dict[str, object]] = []

    def record_attempt(self, **values: object) -> None:
        self.attempts.append(values)


def _ok() -> AdapterResult:
    return AdapterResult(
        output=PlanningOutput(content="{}", raw_response={}),
        usage=ModelUsage(input_units=10, output_units=10),
        usage_reported=False,
    )


def _route(*, fixed_cost_usd: float = 0.6, budget_feature: str | None = "planning") -> ModelRoute:
    return ModelRoute(
        route_id="paid",
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider="custom",
        model="planner",
        model_revision="planner-2026-07-14",
        fixed_cost_usd=fixed_cost_usd,
        budget_feature=budget_feature,
    )


def _request() -> PlanningRequest:
    return PlanningRequest(messages=(ChatMessage(role="user", content="plan"),))


def _gateway(adapter, *, ledger, feature_budgets, route=None, telemetry=None) -> ModelGateway:
    return ModelGateway(
        adapters={"fake": adapter},
        routes=[route or _route()],
        budget_ledger=ledger,
        telemetry=telemetry,
        monthly_budget_usd=100.0,
        request_budget_usd=5.0,
        feature_budgets_usd=feature_budgets,
    )


def test_feature_cap_blocks_when_workspace_budget_still_has_room() -> None:
    adapter = FakeAdapter(_ok())
    ledger = InMemoryBudgetLedger()
    telemetry = RecordingTelemetry()
    gateway = _gateway(
        adapter, ledger=ledger, feature_budgets={"planning": 1.0}, telemetry=telemetry
    )
    context = ModelCallContext(workspace_id="workspace-one")

    gateway.invoke(_request(), context=context)
    with pytest.raises(ModelBudgetExceeded):
        gateway.invoke(_request(), context=context)

    assert adapter.calls == 1
    assert ledger.usage(budget_scope("workspace-one")) == pytest.approx(0.6)
    assert ledger.usage(feature_budget_scope("planning")) == pytest.approx(0.6)
    assert telemetry.attempts[-1]["error_code"] == "model_feature_budget_exceeded"


def test_feature_cap_is_shared_across_workspaces() -> None:
    adapter = FakeAdapter(_ok())
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(adapter, ledger=ledger, feature_budgets={"planning": 1.0})

    gateway.invoke(_request(), context=ModelCallContext(workspace_id="workspace-one"))
    with pytest.raises(ModelBudgetExceeded):
        gateway.invoke(_request(), context=ModelCallContext(workspace_id="workspace-two"))

    # The rejected attempt released its workspace reservation.
    assert ledger.usage(budget_scope("workspace-two")) == 0.0


def test_non_billable_adapter_failure_releases_both_scopes() -> None:
    failure = ProviderCallFailure(
        provider="custom",
        error_info=ProviderErrorInfo(
            "provider_request_rejected", False, fallback_eligible=False, billable=False
        ),
    )
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(FakeAdapter(failure), ledger=ledger, feature_budgets={"planning": 5.0})

    with pytest.raises(Exception):  # noqa: B017 - exhausted providers surface as gateway errors
        gateway.invoke(_request(), context=ModelCallContext(workspace_id="workspace-one"))

    assert ledger.usage(budget_scope("workspace-one")) == 0.0
    assert ledger.usage(feature_budget_scope("planning")) == 0.0


def test_billable_adapter_failure_charges_both_scopes() -> None:
    failure = ProviderCallFailure(
        provider="custom",
        error_info=ProviderErrorInfo("provider_timeout", True, billable=True),
    )
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(FakeAdapter(failure), ledger=ledger, feature_budgets={"planning": 5.0})

    with pytest.raises(Exception):  # noqa: B017
        gateway.invoke(_request(), context=ModelCallContext(workspace_id="workspace-one"))

    assert ledger.usage(budget_scope("workspace-one")) == pytest.approx(0.6)
    assert ledger.usage(feature_budget_scope("planning")) == pytest.approx(0.6)


def test_uncapped_feature_behaves_exactly_as_before() -> None:
    adapter = FakeAdapter(_ok())
    ledger = InMemoryBudgetLedger()
    gateway = _gateway(adapter, ledger=ledger, feature_budgets={"embeddings": 1.0})

    for _ in range(3):
        gateway.invoke(_request(), context=ModelCallContext(workspace_id="workspace-one"))

    assert adapter.calls == 3
    assert ledger.usage(feature_budget_scope("planning")) == 0.0
    assert ledger.usage(budget_scope("workspace-one")) == pytest.approx(1.8)


def test_zero_cost_route_is_never_blocked_by_an_exhausted_feature_cap() -> None:
    ledger = InMemoryBudgetLedger()
    ledger.reserve(scope=feature_budget_scope("planning"), amount_usd=1.0, limit_usd=1.0)
    adapter = FakeAdapter(_ok())
    gateway = _gateway(
        adapter,
        ledger=ledger,
        feature_budgets={"planning": 1.0},
        route=_route(fixed_cost_usd=0.0),
    )

    gateway.invoke(_request())

    assert adapter.calls == 1


def test_route_rejects_unknown_budget_feature() -> None:
    with pytest.raises(ValueError, match="budget_feature"):
        _route(budget_feature="gpu_time")


def test_gateway_rejects_unknown_or_non_positive_feature_budgets() -> None:
    with pytest.raises(ValueError, match="unknown features"):
        _gateway(FakeAdapter(_ok()), ledger=None, feature_budgets={"gpu_time": 1.0})
    with pytest.raises(ValueError, match="positive"):
        _gateway(FakeAdapter(_ok()), ledger=None, feature_budgets={"planning": 0.0})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("{not json", "valid JSON"),
        ("[1, 2]", "JSON object"),
        ('{"gpu_time": 5}', "unknown feature"),
        ('{"planning": 0}', "positive"),
        ('{"planning": -1}', "positive"),
        ('{"planning": true}', "number"),
        ('{"planning": "5"}', "number"),
    ],
)
def test_parse_feature_budgets_rejects_invalid_values(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_feature_budgets(raw)


def test_parse_feature_budgets_accepts_empty_and_valid_objects() -> None:
    assert parse_feature_budgets("") == {}
    assert parse_feature_budgets(None) == {}
    assert parse_feature_budgets('{"vision_captions": 20, "audio_stems": 12.5}') == {
        "vision_captions": 20.0,
        "audio_stems": 12.5,
    }


def _factory_settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "model_gateway_enabled": True,
        "model_gateway_routes": "",
        "model_gateway_openai_base_url": None,
        "model_gateway_openai_api_key": None,
        "azure_openai_endpoint": None,
        "openrouter_api_key": "openrouter-key",
        "groq_api_key": "groq-key",
        "edit_planner_provider": "openrouter",
        "edit_planner_model": "openai/gpt-4.1-mini",
        "edit_planner_model_revision": "openai/gpt-4.1-mini-2025-04-14",
        "transcription_provider": "groq",
        "transcription_model": "whisper-large-v3-turbo",
        "transcription_model_revision": "whisper-large-v3-turbo-2025-02-01",
        "redis_url": None,
        "model_gateway_failure_threshold": 3,
        "model_gateway_circuit_cooldown_seconds": 60,
        "model_gateway_monthly_budget_usd": 100,
        "model_gateway_request_budget_usd": 5,
        "model_gateway_feature_budgets_usd": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_factory_tags_legacy_routes_and_applies_feature_budgets() -> None:
    gateway = model_gateway_from_settings(
        _factory_settings(model_gateway_feature_budgets_usd='{"planning": 10}')
    )

    assert gateway is not None
    features = {route.route_id: route.budget_feature for route in gateway.routes}
    assert features == {
        "openrouter-planning-primary": "planning",
        "groq-transcription-primary": "transcription",
    }
    assert gateway.feature_budgets_usd == {"planning": 10.0}


def test_factory_rejects_invalid_feature_budget_json_at_startup() -> None:
    with pytest.raises(ValueError, match="MODEL_GATEWAY_FEATURE_BUDGETS_USD"):
        model_gateway_from_settings(
            _factory_settings(model_gateway_feature_budgets_usd="{oops")
        )


def test_factory_custom_routes_default_or_validate_budget_feature() -> None:
    base = {
        "adapter": "openai_compatible",
        "provider": "private",
        "model": "model",
        "model_revision": "model-2026-07-14",
        "fixed_cost_usd": 0.1,
    }
    routes = [
        {**base, "route_id": "plan", "capability": "planning"},
        {**base, "route_id": "vision", "capability": "vision"},
        {
            **base,
            "route_id": "captions",
            "capability": "vision",
            "budget_feature": "vision_captions",
        },
    ]
    gateway = model_gateway_from_settings(
        _factory_settings(
            model_gateway_routes=json.dumps(routes),
            model_gateway_openai_base_url="https://gpu.motionly.test/v1",
        )
    )

    assert gateway is not None
    assert {route.route_id: route.budget_feature for route in gateway.routes} == {
        "plan": "planning",
        "vision": None,
        "captions": "vision_captions",
    }

    bad = [{**base, "route_id": "bad", "capability": "vision", "budget_feature": "gpu"}]
    with pytest.raises(ValueError, match="budget_feature"):
        model_gateway_from_settings(
            _factory_settings(
                model_gateway_routes=json.dumps(bad),
                model_gateway_openai_base_url="https://gpu.motionly.test/v1",
            )
        )
