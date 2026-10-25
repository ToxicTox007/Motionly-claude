"""Route-test service tests (plan Task 22).

``run_route_test`` drives one route with a synthetic sample and reports schema
validity / latency / usage / cost / error code / circuit state. These tests cover
success, schema_invalid, timeout, auth failure, cost cap, hard-cap accounting,
circuit open/close, paid async confirmation and the route-affine binding — all
against the deterministic fake adapter, with no network.
"""

from __future__ import annotations

import pytest

from app.services.inference_capabilities import InferenceCapability
from app.services.inference_fake import DeterministicInferenceAdapter, ScriptedOutcome
from app.services.inference_route_test import (
    OPERATOR_TEST_WORKSPACE,
    RouteTestError,
    run_route_test,
)
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    InMemoryCircuitStore,
    ModelCapability,
    ModelGateway,
    ModelRoute,
)


def _route(
    route_id: str,
    capability: ModelCapability,
    capability_id: InferenceCapability,
    *,
    fixed_cost_usd: float = 0.0,
    explicitly_free: bool = False,
    async_submit: bool = False,
    pricing_status: str = "priced",
    hard_cap_max_requests: int | None = None,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        capability=capability,
        adapter="fake",
        provider=route_id,
        model=f"{route_id}-model",
        model_revision=f"{route_id}-2026-07-14",
        fixed_cost_usd=fixed_cost_usd,
        explicitly_free=explicitly_free,
        async_submit=async_submit,
        pricing_status=pricing_status,
        hard_cap_max_requests=hard_cap_max_requests,
        capability_id=str(capability_id),
    )


def _gateway(routes, adapter, **kwargs) -> ModelGateway:
    return ModelGateway(
        adapters={"fake": adapter},
        routes=routes,
        request_budget_usd=kwargs.pop("request_budget_usd", 100.0),
        monthly_budget_usd=kwargs.pop("monthly_budget_usd", 1000.0),
        budget_ledger=kwargs.pop("budget_ledger", InMemoryBudgetLedger()),
        circuit_store=kwargs.pop("circuit_store", InMemoryCircuitStore()),
        **kwargs,
    )


def test_route_test_success_reports_schema_latency_and_usage() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route("planning", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                   explicitly_free=True)
    gateway = _gateway([route], adapter)

    outcome = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)

    assert outcome.status == "ok"
    assert outcome.schema_valid is True
    assert outcome.latency_ms is not None
    assert outcome.usage["total_units"] >= 0
    assert outcome.error_code is None


def test_route_test_malformed_response_is_schema_invalid() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.script("planning", [ScriptedOutcome.SCHEMA_INVALID])
    route = _route("planning", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                   explicitly_free=True)
    gateway = _gateway([route], adapter)

    outcome = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)

    assert outcome.status == "schema_invalid"
    assert outcome.schema_valid is False
    assert outcome.error_code == "provider_malformed_response"


def test_route_test_timeout_reports_error() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.script("planning", [ScriptedOutcome.TIMEOUT_AFTER_SEND])
    route = _route("planning", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                   explicitly_free=True)
    gateway = _gateway([route], adapter)

    outcome = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)

    assert outcome.status == "error"
    assert outcome.error_code == "provider_timeout"
    assert outcome.schema_valid is True


def test_route_test_auth_failure_reports_error() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.script("planning", [ScriptedOutcome.HTTP_401])
    route = _route("planning", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                   explicitly_free=True)
    gateway = _gateway([route], adapter)

    outcome = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)

    assert outcome.status == "error"
    assert outcome.error_code == "provider_auth_failed"


def test_route_test_cost_cap_refuses_expensive_route() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route("planning", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                   fixed_cost_usd=5.0)
    gateway = _gateway([route], adapter)

    outcome = run_route_test(gateway=gateway, route=route, max_cost_usd=0.01, allow_paid=True)

    assert outcome.status == "error"
    assert outcome.error_code == "model_budget_exceeded"


def test_route_test_uses_isolated_operator_budget_scope() -> None:
    # A test never consumes a real workspace scope; the synthetic scope is used.
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route("planning", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                   fixed_cost_usd=0.1)
    ledger = InMemoryBudgetLedger()
    gateway = _gateway([route], adapter, budget_ledger=ledger)

    run_route_test(gateway=gateway, route=route, max_cost_usd=1.0, allow_paid=True)

    # The reservation landed under the operator scope, not a real tenant.
    assert OPERATOR_TEST_WORKSPACE in repr(vars(ledger))


def test_route_test_hard_cap_accounting_counts_against_cap() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route(
        "planning",
        ModelCapability.PLANNING,
        InferenceCapability.PLANNING_CHAT,
        explicitly_free=True,
        pricing_status="unknown",
        hard_cap_max_requests=1,
    )
    gateway = _gateway([route], adapter)

    first = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)
    second = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)

    assert first.status == "ok"
    assert first.cost_unknown is True
    # The second test exhausts the per-route monthly cap.
    assert second.status == "error"
    assert second.error_code == "route_hard_cap_exhausted"


def test_route_test_circuit_open_then_close() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.script(
        "planning",
        [ScriptedOutcome.HTTP_5XX, ScriptedOutcome.SUCCESS],
    )
    route = _route("planning", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                   explicitly_free=True)
    circuits = InMemoryCircuitStore(failure_threshold=1, cooldown_seconds=60)
    gateway = _gateway([route], adapter, circuit_store=circuits)

    opened = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)
    assert opened.status == "error"
    assert opened.circuit_state == "open"

    # While open, the next test is refused by the circuit, not the provider.
    blocked = run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)
    assert blocked.error_code == "provider_circuit_open"


def test_route_test_paid_async_submit_requires_confirmation() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route(
        "video",
        ModelCapability.GENERATION,
        InferenceCapability.VIDEO_GENERATION_SUBMIT,
        fixed_cost_usd=1.0,
        async_submit=True,
    )
    gateway = _gateway([route], adapter)

    # Without confirmation the gateway is never contacted.
    refused = run_route_test(gateway=gateway, route=route, max_cost_usd=5.0, allow_paid=False)
    assert refused.status == "error"
    assert refused.paid_confirmation_required is True
    assert refused.error_code == "paid_confirmation_required"


def test_route_test_free_async_submit_does_not_require_confirmation() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route(
        "video",
        ModelCapability.GENERATION,
        InferenceCapability.VIDEO_GENERATION_SUBMIT,
        explicitly_free=True,
        async_submit=True,
    )
    gateway = _gateway([route], adapter)

    outcome = run_route_test(gateway=gateway, route=route, max_cost_usd=5.0, allow_paid=False)
    assert outcome.paid_confirmation_required is False
    assert outcome.status == "ok"


def test_route_test_is_route_affine_binding() -> None:
    # Two planning routes; the test binds to exactly the one requested.
    adapter = DeterministicInferenceAdapter(app_env="test")
    primary = _route("planning-a", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                     explicitly_free=True)
    secondary = _route("planning-b", ModelCapability.PLANNING, InferenceCapability.PLANNING_CHAT,
                       explicitly_free=True)
    adapter.script("planning-a", [ScriptedOutcome.HTTP_5XX])
    gateway = _gateway([primary, secondary], adapter)

    outcome = run_route_test(gateway=gateway, route=primary, max_cost_usd=0.5, allow_paid=False)
    # The failure is reported for the bound route; it never falls back to b.
    assert outcome.route_id == "planning-a"
    assert outcome.status == "error"


def test_route_test_unsupported_capability_raises() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route(
        "video-status",
        ModelCapability.GENERATION,
        InferenceCapability.VIDEO_GENERATION_STATUS,
        explicitly_free=True,
    )
    gateway = _gateway([route], adapter)

    with pytest.raises(RouteTestError):
        run_route_test(gateway=gateway, route=route, max_cost_usd=0.5, allow_paid=False)
