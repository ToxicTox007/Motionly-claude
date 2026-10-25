"""Daily hard-cap enforcement on any capped route (plan S2 daily caps).

These tests cover the additive per-UTC-day hard caps introduced alongside the
existing monthly caps:

- :func:`route_daily_hard_cap_scope` buckets by the full UTC date and stays
  disjoint from the monthly ``route-hard-cap:`` scopes;
- the registry parses ``max_requests_per_day`` / ``max_units_per_day`` and
  :func:`to_model_route` carries them onto :class:`ModelRoute`;
- ``_reserve_route_hard_cap`` enforces monthly AND daily caps for ANY route that
  declares a cap (not only ``pricing.status == "unknown"``): a daily cap
  exhaustion skips the route and falls back, resets on the next UTC day, a
  priced route with caps is now enforced, a route without caps is unaffected,
  and a poll/status call never consumes the cap.

All invocations use the deterministic in-process fake adapter; no network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services.inference_registry import HardCap, _parse_hard_cap
from app.services.model_gateway import (
    AdapterResult,
    ChatMessage,
    InMemoryBudgetLedger,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelProvidersExhausted,
    ModelRoute,
    ModelUsage,
    PlanningOutput,
    PlanningRequest,
    route_daily_hard_cap_scope,
    route_hard_cap_scope,
)


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
        del request
        self.calls.append(route.route_id)
        return AdapterResult(
            output=PlanningOutput(content=route.route_id, raw_response={}),
            usage=ModelUsage(input_units=10, output_units=5),
            provider_request_id=f"{route.route_id}-req",
        )


def _planning_request() -> PlanningRequest:
    return PlanningRequest(messages=(ChatMessage(role="user", content="plan this video"),))


def _route(
    route_id: str,
    *,
    priority: int = 100,
    pricing_status: str = "priced",
    explicitly_free: bool = True,
    hard_cap_max_requests: int | None = None,
    hard_cap_max_units: int | None = None,
    hard_cap_max_requests_per_day: int | None = None,
    hard_cap_max_units_per_day: int | None = None,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        capability=ModelCapability.PLANNING,
        adapter="fake",
        provider=route_id,
        model=f"{route_id}-model",
        model_revision=f"{route_id}-2026-07-14",
        priority=priority,
        explicitly_free=explicitly_free,
        pricing_status=pricing_status,
        hard_cap_max_requests=hard_cap_max_requests,
        hard_cap_max_units=hard_cap_max_units,
        hard_cap_max_requests_per_day=hard_cap_max_requests_per_day,
        hard_cap_max_units_per_day=hard_cap_max_units_per_day,
    )


def _gateway(routes: list[ModelRoute], adapter: _FakeAdapter, **kwargs) -> ModelGateway:
    return ModelGateway(
        adapters={"fake": adapter},
        routes=routes,
        request_budget_usd=kwargs.pop("request_budget_usd", 100.0),
        monthly_budget_usd=kwargs.pop("monthly_budget_usd", 1000.0),
        budget_ledger=kwargs.pop("budget_ledger", InMemoryBudgetLedger()),
        **kwargs,
    )


# --- scope function ---------------------------------------------------------


def test_daily_scope_buckets_by_utc_date_and_is_disjoint_from_monthly() -> None:
    now = datetime(2026, 3, 9, 23, 59, tzinfo=UTC)
    daily = route_daily_hard_cap_scope("planning", unit="requests", now=now)
    monthly = route_hard_cap_scope("planning", unit="requests", now=now)
    assert daily == "route-hard-cap-daily:requests:planning:2026-03-09"
    assert monthly == "route-hard-cap:requests:planning:2026-03"
    # Prefixes differ, so the two caps never collide on the same ledger.
    assert daily != monthly
    assert not daily.startswith("route-hard-cap:")


def test_daily_scope_units_and_requests_do_not_share_a_scope() -> None:
    now = datetime(2026, 3, 9, tzinfo=UTC)
    assert route_daily_hard_cap_scope(
        "r", unit="requests", now=now
    ) != route_daily_hard_cap_scope("r", unit="units", now=now)


def test_daily_scope_rolls_over_at_utc_midnight() -> None:
    before = datetime(2026, 3, 9, 23, 59, 59, tzinfo=UTC)
    after = datetime(2026, 3, 10, 0, 0, 1, tzinfo=UTC)
    assert route_daily_hard_cap_scope("r", now=before) != route_daily_hard_cap_scope(
        "r", now=after
    )


# --- registry parse + to_model_route ---------------------------------------


def test_parse_hard_cap_reads_daily_fields() -> None:
    cap = _parse_hard_cap(
        {
            "max_requests_per_month": 500,
            "max_units_per_month": 18000,
            "max_requests_per_day": 50,
            "max_units_per_day": 1800,
        },
        "route-x",
        "routes[0].budget.hard_cap",
    )
    assert cap.max_requests_per_day == 50
    assert cap.max_units_per_day == 1800
    # Monthly fields and has_positive_cap are unchanged.
    assert cap.max_requests_per_month == 500
    assert cap.has_positive_cap is True


def test_has_positive_cap_ignores_daily_only_caps() -> None:
    # The unknown-pricing rule still requires a positive MONTHLY cap: a route
    # that declares only daily caps does not satisfy has_positive_cap.
    daily_only = HardCap(max_requests_per_day=10, max_units_per_day=10)
    assert daily_only.has_positive_cap is False
    monthly = HardCap(max_requests_per_month=1)
    assert monthly.has_positive_cap is True


# --- enforcement in the gateway --------------------------------------------


def test_daily_request_cap_exhaustion_skips_route_and_falls_back() -> None:
    adapter = _FakeAdapter()
    primary = _route("primary", priority=1, hard_cap_max_requests_per_day=1)
    secondary = _route("secondary", priority=2)
    gateway = _gateway([primary, secondary], adapter)
    ctx = ModelCallContext(workspace_id=uuid4())

    first = gateway.invoke(_planning_request(), context=ctx)
    assert first.metadata.route_id == "primary"

    # The daily cap of 1 is now full; the next call skips primary and falls back.
    second = gateway.invoke(_planning_request(), context=ctx)
    assert second.metadata.route_id == "secondary"
    assert adapter.calls == ["primary", "secondary"]


def test_daily_cap_resets_on_next_utc_day(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.model_gateway as mg

    adapter = _FakeAdapter()
    route = _route("solo", hard_cap_max_requests_per_day=1)
    gateway = _gateway([route], adapter)

    class _Clock:
        value = datetime(2026, 3, 9, 10, 0, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):  # noqa: ANN001 - drop-in for datetime.now
            return cls.value

    monkeypatch.setattr(mg, "datetime", _Clock)

    # Day 1: one success, then the daily cap is exhausted.
    assert gateway.invoke(_planning_request()).metadata.route_id == "solo"
    with pytest.raises(ModelProvidersExhausted) as exc_info:
        gateway.invoke(_planning_request())
    assert exc_info.value.failures == (
        {"route_id": "solo", "code": "route_hard_cap_exhausted"},
    )

    # Advance to the next UTC day: the daily scope changes, so the cap resets.
    _Clock.value = datetime(2026, 3, 10, 0, 0, 1, tzinfo=UTC)
    assert gateway.invoke(_planning_request()).metadata.route_id == "solo"
    assert adapter.calls == ["solo", "solo"]


def test_priced_route_with_caps_is_now_enforced() -> None:
    # Regression guard for S2: enforcement is no longer gated on
    # pricing_status == "unknown". A PRICED route that declares a cap is capped.
    adapter = _FakeAdapter()
    route = _route("priced", pricing_status="priced", hard_cap_max_requests_per_day=1)
    gateway = _gateway([route], adapter)

    assert gateway.invoke(_planning_request()).metadata.route_id == "priced"
    with pytest.raises(ModelProvidersExhausted) as exc_info:
        gateway.invoke(_planning_request())
    assert exc_info.value.failures == (
        {"route_id": "priced", "code": "route_hard_cap_exhausted"},
    )
    assert adapter.calls == ["priced"]


def test_priced_monthly_cap_is_now_enforced() -> None:
    adapter = _FakeAdapter()
    route = _route("priced", pricing_status="priced", hard_cap_max_requests=1)
    gateway = _gateway([route], adapter)

    assert gateway.invoke(_planning_request()).metadata.route_id == "priced"
    with pytest.raises(ModelProvidersExhausted) as exc_info:
        gateway.invoke(_planning_request())
    assert exc_info.value.failures == (
        {"route_id": "priced", "code": "route_hard_cap_exhausted"},
    )


def test_route_without_any_cap_is_unaffected() -> None:
    adapter = _FakeAdapter()
    route = _route("free", pricing_status="priced")
    gateway = _gateway([route], adapter)

    for _ in range(5):
        assert gateway.invoke(_planning_request()).metadata.route_id == "free"
    assert adapter.calls == ["free"] * 5


def test_daily_and_monthly_caps_both_enforced_independently() -> None:
    # A daily cap smaller than the monthly cap bites first; after a UTC-day
    # rollover the daily resets while the monthly keeps counting.
    import app.services.model_gateway as mg

    adapter = _FakeAdapter()
    route = _route(
        "both",
        hard_cap_max_requests=3,
        hard_cap_max_requests_per_day=1,
    )
    gateway = _gateway([route], adapter)

    class _Clock:
        value = datetime(2026, 3, 9, 10, 0, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return cls.value

    import pytest as _pytest  # local alias to keep monkeypatch scoped

    mp = _pytest.MonkeyPatch()
    mp.setattr(mg, "datetime", _Clock)
    try:
        assert gateway.invoke(_planning_request()).metadata.route_id == "both"
        # Daily cap (1) full for day 1.
        with pytest.raises(ModelProvidersExhausted):
            gateway.invoke(_planning_request())
        # Day 2: daily resets, monthly now at 2/3.
        _Clock.value = datetime(2026, 3, 10, 1, 0, tzinfo=UTC)
        assert gateway.invoke(_planning_request()).metadata.route_id == "both"
        # Day 3: daily resets, monthly now at 3/3 -> monthly exhausts.
        _Clock.value = datetime(2026, 3, 11, 1, 0, tzinfo=UTC)
        assert gateway.invoke(_planning_request()).metadata.route_id == "both"
        _Clock.value = datetime(2026, 3, 12, 1, 0, tzinfo=UTC)
        with pytest.raises(ModelProvidersExhausted) as exc_info:
            gateway.invoke(_planning_request())
        assert exc_info.value.failures == (
            {"route_id": "both", "code": "route_hard_cap_exhausted"},
        )
    finally:
        mp.undo()


def test_daily_unit_cap_exhaustion_falls_back() -> None:
    # A PlanningRequest with 15-byte content estimates 15 input units; a daily
    # unit cap below that is exhausted on the first call.
    adapter = _FakeAdapter()
    primary = _route("primary", priority=1, hard_cap_max_units_per_day=10)
    secondary = _route("secondary", priority=2)
    gateway = _gateway([primary, secondary], adapter)

    # 15 units requested > 10 cap: primary is skipped immediately, fall back.
    result = gateway.invoke(_planning_request())
    assert result.metadata.route_id == "secondary"
    assert adapter.calls == ["secondary"]


def test_poll_like_release_does_not_consume_daily_cap_on_non_billable_failure() -> None:
    # A non-billable provider failure releases the daily reservation, so the cap
    # is not consumed and a retry on the same day still goes through.
    from app.services.provider_resilience import ProviderCallFailure, ProviderErrorInfo

    class _FlakyAdapter:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._script = [
                ProviderCallFailure(
                    provider="solo",
                    error_info=ProviderErrorInfo(
                        "provider_auth_failed",
                        False,
                        401,
                        fallback_eligible=True,
                        circuit_worthy=False,
                        billable=False,
                        sent=False,
                    ),
                ),
            ]

        def invoke(self, *, route: ModelRoute, request: object) -> AdapterResult:
            del request
            self.calls.append(route.route_id)
            if self._script:
                raise self._script.pop(0)
            return AdapterResult(
                output=PlanningOutput(content=route.route_id, raw_response={}),
                usage=ModelUsage(input_units=10, output_units=5),
                provider_request_id="ok",
            )

    adapter = _FlakyAdapter()
    route = _route("solo", hard_cap_max_requests_per_day=1)
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route],
        request_budget_usd=100.0,
        monthly_budget_usd=1000.0,
        budget_ledger=InMemoryBudgetLedger(),
    )

    # First call: non-billable auth failure releases the reservation.
    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(_planning_request())
    # Second call on the SAME day still has its daily budget: cap not consumed.
    assert gateway.invoke(_planning_request()).metadata.route_id == "solo"
    assert adapter.calls == ["solo", "solo"]
