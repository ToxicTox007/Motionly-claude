"""Gateway and resolver tests for user model choice (plan Section 2.11, Task 31).

Covers: the preferred route serves; a fallback-eligible failure falls back to the
operator default with a ``fallback_reason``; an async submit after send does not
fall back; the served route's ``quota_weight`` is applied to the feature quota;
two workspaces with different choices get different served routes; status calls
stay route-affine (a ``required_route_id`` beats any preference).
"""

from __future__ import annotations

from uuid import UUID

from app.services.model_gateway import (
    AdapterResult,
    ChatMessage,
    InMemoryBudgetLedger,
    InMemoryCircuitStore,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelRoute,
    ModelUsage,
    OperationRouteUnavailable,
    PlanningOutput,
    PlanningRequest,
)
from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

WS_A = UUID("11111111-1111-4111-8111-111111111111")
WS_B = UUID("22222222-2222-4222-8222-222222222222")


class _Adapter:
    def __init__(self, outcomes: dict[str, AdapterResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
        del request
        self.calls.append(route.route_id)
        outcome = self.outcomes[route.route_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _planning_result(content: str = "{}") -> AdapterResult:
    payload = {"choices": [{"message": {"content": content}}]}
    return AdapterResult(
        output=PlanningOutput(content=content, raw_response=payload),
        usage=ModelUsage(input_units=10, output_units=5),
        provider_request_id="req",
    )


def _route(
    route_id: str,
    *,
    priority: int = 100,
    selectable: bool = True,
    quota_weight: float = 1.0,
    budget_feature: str | None = None,
    async_submit: bool = False,
    capability: ModelCapability = ModelCapability.PLANNING,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        capability=capability,
        adapter="fake",
        provider=route_id,
        model=f"{route_id}-model",
        model_revision=f"{route_id}-2026-07-14",
        priority=priority,
        selectable=selectable,
        quota_weight=quota_weight,
        budget_feature=budget_feature,
        async_submit=async_submit,
    )


class _StaticResolver:
    """A resolver that returns a fixed preference per workspace id."""

    def __init__(self, mapping: dict[UUID, tuple[str | None, str, str | None, str | None]]):
        # workspace_id -> (route_id, source, requested_option_id, rejected_reason)
        self._mapping = mapping

    def preference_for(self, *, operation, context):  # noqa: ANN001
        entry = self._mapping.get(_as_uuid(context.workspace_id))
        if entry is None or entry[0] is None:
            return None
        return entry[0], entry[1]

    def resolve(self, *, operation, context):  # noqa: ANN001
        from app.services.model_choice import ResolvedChoice

        entry = self._mapping.get(_as_uuid(context.workspace_id))
        if entry is None:
            return None
        route_id, source, requested, reason = entry
        return ResolvedChoice(
            capability="planning.chat",
            route_id=route_id,
            choice_source=source,
            requested_option_id=requested,
            rejected_reason=reason,
        )


def _as_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _request() -> PlanningRequest:
    return PlanningRequest(messages=(ChatMessage(role="user", content="plan this"),))


def _gateway(adapter: _Adapter, routes: list[ModelRoute]) -> ModelGateway:
    return ModelGateway(
        adapters={"fake": adapter},
        routes=routes,
        budget_ledger=InMemoryBudgetLedger(),
        circuit_store=InMemoryCircuitStore(),
        monthly_budget_usd=0.0,
        request_budget_usd=0.0,
    )


def test_preferred_route_serves_the_call() -> None:
    adapter = _Adapter({"high": _planning_result("high"), "default": _planning_result("default")})
    gateway = _gateway(adapter, [_route("default", priority=10), _route("high", priority=20)])
    gateway.set_choice_resolver(
        _StaticResolver({WS_A: ("high", "workspace", "high", None)})
    )
    result = gateway.invoke(_request(), context=ModelCallContext(workspace_id=WS_A))
    assert result.metadata.route_id == "high"
    assert adapter.calls == ["high"]
    assert result.model_choice is not None
    assert result.model_choice.served_option_id == "high"
    assert result.model_choice.requested_option_id == "high"
    assert result.model_choice.choice_source == "workspace"
    assert result.model_choice.fallback_reason is None


def test_fallback_eligible_failure_falls_back_with_reason() -> None:
    failure = ProviderCallFailure(
        provider="high", error_info=ProviderErrorInfo("provider_unavailable", True, 503)
    )
    adapter = _Adapter({"high": failure, "default": _planning_result("default")})
    gateway = _gateway(adapter, [_route("default", priority=10), _route("high", priority=20)])
    gateway.set_choice_resolver(_StaticResolver({WS_A: ("high", "workspace", "high", None)}))
    result = gateway.invoke(_request(), context=ModelCallContext(workspace_id=WS_A))
    # The preferred route was tried first, then the operator default served.
    assert adapter.calls == ["high", "default"]
    assert result.metadata.route_id == "default"
    assert result.model_choice is not None
    assert result.model_choice.requested_option_id == "high"
    assert result.model_choice.served_option_id == "default"
    assert result.model_choice.choice_source == "operator"
    assert result.model_choice.fallback_reason == "provider_unavailable"


def test_async_submit_after_send_does_not_fall_back() -> None:
    # A route flagged async_submit whose failure may have been sent must not
    # fall back to another route, even when it is the preferred route.
    sent_failure = ProviderCallFailure(
        provider="async-high",
        error_info=ProviderErrorInfo("provider_timeout", True, None, sent=True),
    )
    adapter = _Adapter(
        {"async-high": sent_failure, "async-default": _planning_result("default")}
    )
    gateway = _gateway(
        adapter,
        [
            _route("async-default", priority=10, async_submit=True),
            _route("async-high", priority=20, async_submit=True),
        ],
    )
    gateway.set_choice_resolver(
        _StaticResolver({WS_A: ("async-high", "workspace", "async-high", None)})
    )
    import pytest

    from app.services.model_gateway import ModelProvidersExhausted

    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(_request(), context=ModelCallContext(workspace_id=WS_A))
    # Only the preferred async route was contacted; no fallback after send.
    assert adapter.calls == ["async-high"]


def test_quota_weight_is_applied_to_the_served_route() -> None:
    recorded: list[float] = []
    checked: list[float] = []

    class _Guard:
        def applies(self, *, route, context) -> bool:  # noqa: ANN001
            return route.budget_feature is not None and context.workspace_id is not None

        def check(self, *, route, context, quantity) -> None:  # noqa: ANN001
            checked.append(quantity)

        def record(self, *, route, context, quantity, actual_cost_usd) -> None:  # noqa: ANN001
            recorded.append(quantity)

    adapter = _Adapter({"heavy": _planning_result("heavy")})
    gateway = _gateway(
        adapter, [_route("heavy", quota_weight=2.0, budget_feature="planning")]
    )
    gateway.set_quota_guard(_Guard())
    gateway.set_choice_resolver(_StaticResolver({WS_A: ("heavy", "workspace", "heavy", None)}))
    gateway.invoke(_request(), context=ModelCallContext(workspace_id=WS_A))
    # Planning units for this request are its byte length; both check and record
    # are multiplied by the served route's quota_weight of 2.0.
    assert checked and recorded
    assert checked[0] == recorded[0]
    base = len(b"plan this")
    assert recorded[0] == base * 2.0


def test_two_workspaces_get_their_own_routes() -> None:
    adapter = _Adapter(
        {"high": _planning_result("high"), "eco": _planning_result("eco")}
    )
    gateway = _gateway(adapter, [_route("eco", priority=10), _route("high", priority=20)])
    gateway.set_choice_resolver(
        _StaticResolver(
            {
                WS_A: ("high", "workspace", "high", None),
                WS_B: ("eco", "workspace", "eco", None),
            }
        )
    )
    result_a = gateway.invoke(_request(), context=ModelCallContext(workspace_id=WS_A))
    result_b = gateway.invoke(_request(), context=ModelCallContext(workspace_id=WS_B))
    assert result_a.metadata.route_id == "high"
    assert result_b.metadata.route_id == "eco"


def test_required_route_beats_preference_and_stays_affine() -> None:
    # A status/poll call names a required_route_id; it is served ONLY by the
    # bound route and is never re-routed, even with a preference set.
    adapter = _Adapter({"bound": _planning_result("bound"), "pref": _planning_result("pref")})
    gateway = _gateway(adapter, [_route("bound", priority=10), _route("pref", priority=20)])
    gateway.set_choice_resolver(_StaticResolver({WS_A: ("pref", "workspace", "pref", None)}))
    result = gateway.invoke(
        _request(),
        context=ModelCallContext(workspace_id=WS_A, required_route_id="bound"),
    )
    assert result.metadata.route_id == "bound"
    assert adapter.calls == ["bound"]


def test_required_route_missing_raises_regardless_of_preference() -> None:
    import pytest

    adapter = _Adapter({"pref": _planning_result("pref")})
    gateway = _gateway(adapter, [_route("pref", priority=20)])
    gateway.set_choice_resolver(_StaticResolver({WS_A: ("pref", "workspace", "pref", None)}))
    with pytest.raises(OperationRouteUnavailable):
        gateway.invoke(
            _request(),
            context=ModelCallContext(workspace_id=WS_A, required_route_id="gone"),
        )


def test_no_resolver_keeps_operator_order() -> None:
    adapter = _Adapter({"default": _planning_result("default")})
    gateway = _gateway(adapter, [_route("default", priority=10)])
    result = gateway.invoke(_request(), context=ModelCallContext(workspace_id=WS_A))
    assert result.metadata.route_id == "default"
    assert result.model_choice is not None
    assert result.model_choice.choice_source == "operator"
    assert result.model_choice.requested_option_id is None
