"""Honest pricing + demo-notice gate (Task 23).

Two invariants:

1. INR prices live ONLY in apps/api/app/config/plans.json. A price literal
   (29900 / 79900 / 199900) appearing anywhere else in application source is a
   duplicated source of truth and fails this test.
2. When BILLING_MODE=demo, every payment surface carries the
   'Demo payment — no money is charged' notice. We assert the API sets it on
   each payment-returning endpoint.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.payment_gateway import DEMO_NOTICE

ROOT = Path(__file__).resolve().parents[3]
PLAN_PRICES = (29900, 79900, 199900)

# Directories whose application source must not hard-code prices.
SOURCE_DIRS = [
    ROOT / "apps" / "api" / "app",
    ROOT / "apps" / "web" / "app",
    ROOT / "apps" / "web" / "components",
    ROOT / "apps" / "web" / "lib",
]
ALLOWED = {
    # The single source of truth.
    ROOT / "apps" / "api" / "app" / "config" / "plans.json",
}
PRICE_RE = re.compile(r"\b(29900|79900|199900)\b")


def _source_files():
    for base in SOURCE_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".mjs", ".json"}:
                continue
            if path in ALLOWED or "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            if path.name.startswith("test_") or path.name.endswith(".test.mjs"):
                continue
            yield path


def test_inr_prices_are_not_hard_coded_outside_plans_json() -> None:
    offenders: list[str] = []
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if PRICE_RE.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "INR plan prices must come from apps/api/app/config/plans.json only; "
        f"found hard-coded price literals in: {offenders}"
    )


def test_demo_notice_constant_is_the_expected_wording() -> None:
    assert DEMO_NOTICE == "Demo payment — no money is charged"


def _demo_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(billing_enabled=True, billing_mode="demo")
            )
        )
    )


def test_checkout_surface_carries_demo_notice() -> None:
    import asyncio

    from app.api.billing import create_checkout_session
    from app.api.dependencies import Actor
    from app.repositories.billing import InMemoryBillingRepository
    from app.schemas.billing import BillingPlanKey, CheckoutSessionRequest
    from app.services.payment_gateway import GatewayOrder

    class _Gateway:
        def create_order(self, **kwargs):
            return GatewayOrder(
                id="order_1",
                status="created",
                amount_paise=kwargs["amount_paise"],
                currency=kwargs["currency"],
                checkout_url="https://pay.example/checkout/order_1",
            )

        def checkout_url_for(self, order_id):
            return f"https://pay.example/checkout/{order_id}"

        def refund_order(self, **kwargs):
            return {}

        def cancel_subscription(self, **kwargs):
            return {}

    response = asyncio.run(
        create_checkout_session(
            payload=CheckoutSessionRequest(plan=BillingPlanKey.AGENCY),
            request=_demo_request(),
            actor=Actor(user_id=uuid4(), workspace_id=uuid4()),
            repository=InMemoryBillingRepository(),
            gateway=_Gateway(),
        )
    )
    assert response.demo_notice == DEMO_NOTICE
    assert response.checkout_url.endswith("/checkout/order_1")


def test_history_receipts_carry_demo_notice() -> None:
    import asyncio

    from app.api.billing import get_payment_history
    from app.api.dependencies import Actor
    from app.repositories.billing import InMemoryBillingRepository

    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    repository.record_billing_adjustment(
        workspace_id=workspace_id,
        gateway_event_id="evt_1",
        adjustment_type="charge",
        amount_paise=199900,
        currency="inr",
        metadata={"plan": "agency", "order_id": "order_1"},
    )
    response = asyncio.run(
        get_payment_history(
            request=_demo_request(),
            actor=Actor(user_id=workspace_id, workspace_id=workspace_id),
            repository=repository,
        )
    )
    assert response.receipts
    assert all(r.demo_notice == DEMO_NOTICE for r in response.receipts)


@pytest.mark.parametrize("billing_mode", ["disabled"])
def test_no_demo_notice_when_not_demo(billing_mode: str) -> None:
    import asyncio

    from app.api.billing import create_checkout_session
    from app.api.dependencies import Actor
    from app.api.errors import ApiError
    from app.repositories.billing import InMemoryBillingRepository
    from app.schemas.billing import BillingPlanKey, CheckoutSessionRequest

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(billing_enabled=True, billing_mode=billing_mode)
            )
        )
    )
    # BILLING_MODE=disabled: billing reports disabled, the gateway is never called.
    with pytest.raises(ApiError) as exc:
        asyncio.run(
            create_checkout_session(
                payload=CheckoutSessionRequest(plan=BillingPlanKey.AGENCY),
                request=request,
                actor=Actor(user_id=uuid4(), workspace_id=uuid4()),
                repository=InMemoryBillingRepository(),
                gateway=object(),  # must not be touched
            )
        )
    assert exc.value.code == "billing_disabled"
