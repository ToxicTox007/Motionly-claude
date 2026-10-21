"""Billing tests against the mock payment gateway (Task 23).

These exercise the webhook receiver, plan-limit enforcement, and an end-to-end
flow that runs the REAL Task 22 mock gateway app in-process (ASGI transport)
and feeds its signed webhooks into the API billing receiver:

- UPI upgrade unlocks Agency features.
- card with OTP (123456) works.
- declined card leaves the workspace on Free.
- a failed renewal flips the plan to past_due and a retry restores it.
- cancel and refund.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.billing import (
    apply_gateway_event_async,
    build_billing_status,
    build_billing_status_async,
    get_billing_status,
    handle_gateway_webhook,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.mock_gateway.config import load_config as _load_gateway_config
from app.mock_gateway.main import create_app as create_gateway_app
from app.mock_gateway.models import WebhookEnvelope
from app.mock_gateway.signing import compute_signature
from app.mock_gateway.webhooks import WebhookDeliverer
from app.repositories.billing import (
    SUBSCRIPTION_COLUMNS,
    InMemoryBillingRepository,
    SubscriptionRecord,
)
from app.schemas.billing import (
    BillingMode,
    BillingPlanKey,
    SubscriptionStatus,
    UsageEventType,
)

WEBHOOK_SECRET = "test-webhook-secret"


# --------------------------------------------------------------------------
# Status / entitlements (unchanged behaviour, gateway-named)
# --------------------------------------------------------------------------


def test_free_plan_uses_usage_ledger_for_monthly_render_limit() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    for _ in range(3):
        repository.record_usage(
            workspace_id=workspace_id, event_type=UsageEventType.RENDER_SUCCEEDED
        )

    status = build_billing_status(workspace_id, repository)

    assert status.current_plan.key == BillingPlanKey.FREE
    assert status.usage.renders_used == 3
    assert status.can_render is False


def test_active_agency_subscription_unlocks_agency_entitlements() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.AGENCY,
        status=SubscriptionStatus.ACTIVE,
    )

    status = build_billing_status(workspace_id, repository)

    assert status.current_plan.key == BillingPlanKey.AGENCY
    assert status.entitlements.max_resolution == "4k"
    assert status.entitlements.api_access is True


def test_billing_disabled_status_exposes_unmetered_personal_entitlements() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    status = build_billing_status(workspace_id, repository, billing_enabled=False)
    assert status.billing_enabled is False
    assert status.billing_mode == BillingMode.PERSONAL
    assert status.unmetered is True
    assert status.current_plan.key == BillingPlanKey.AGENCY


def test_billing_status_endpoint_reports_personal_mode_when_disabled() -> None:
    workspace_id = uuid4()
    status = asyncio.run(
        get_billing_status(
            request=SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(settings=SimpleNamespace(billing_enabled=False))
                )
            ),
            actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
            repository=InMemoryBillingRepository(),
        )
    )
    assert status.billing_enabled is False
    assert status.current_plan.key == BillingPlanKey.AGENCY


def test_subscription_columns_are_gateway_named() -> None:
    assert "gateway_customer_id" in SUBSCRIPTION_COLUMNS
    assert "stripe" not in SUBSCRIPTION_COLUMNS


def test_supabase_billing_async_reads_use_gateway_projection(monkeypatch) -> None:
    from app.repositories.billing import SupabaseBillingRepository

    repository = SupabaseBillingRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    subscription_row = {
        "workspace_id": str(workspace_id),
        "plan": "creator",
        "status": "active",
        "gateway_customer_id": "cus_123",
        "gateway_subscription_id": "sub_123",
        "gateway_price_id": "creator",
        "current_period_start": None,
        "current_period_end": None,
        "cancel_at_period_end": False,
        "updated_at": "2026-07-06T00:00:00+00:00",
    }
    responses = [[subscription_row]]

    async def fake_async_request(_self, method, path, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0))

    monkeypatch.setattr(
        "app.repositories.billing.SupabaseRestClient.async_request",
        fake_async_request,
    )
    subscription = asyncio.run(repository.get_subscription_async(workspace_id=workspace_id))
    assert subscription.plan == BillingPlanKey.CREATOR
    assert subscription.gateway_customer_id == "cus_123"


# --------------------------------------------------------------------------
# Webhook receiver — signature / idempotency
# --------------------------------------------------------------------------


def _signed_headers(
    envelope: WebhookEnvelope, *, secret: str = WEBHOOK_SECRET, ts: int | None = None
):
    body = envelope.model_dump_json().encode()
    timestamp = ts if ts is not None else int(time.time())
    signature = compute_signature(secret, timestamp, body)
    return body, {
        "X-Mock-Gateway-Event": str(envelope.event),
        "X-Mock-Gateway-Event-Id": envelope.id,
        "X-Mock-Gateway-Timestamp": str(timestamp),
        "X-Mock-Gateway-Signature": signature,
    }


class _WebhookRequest:
    def __init__(self, *, payload: bytes, billing_mode: str = "demo") -> None:
        self._payload = payload
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    billing_enabled=True,
                    billing_mode=billing_mode,
                    mock_gateway_webhook_secret=WEBHOOK_SECRET,
                )
            )
        )

    async def body(self) -> bytes:
        return self._payload


def _run_webhook(envelope: WebhookEnvelope, repository, **header_kwargs):
    body, headers = _signed_headers(envelope, **header_kwargs)
    return asyncio.run(
        handle_gateway_webhook(
            request=_WebhookRequest(payload=body),
            repository=repository,
            x_mock_gateway_event=headers["X-Mock-Gateway-Event"],
            x_mock_gateway_event_id=headers["X-Mock-Gateway-Event-Id"],
            x_mock_gateway_timestamp=headers["X-Mock-Gateway-Timestamp"],
            x_mock_gateway_signature=headers["X-Mock-Gateway-Signature"],
        )
    )


def _captured_order_envelope(*, event_id: str, workspace_id, plan="agency") -> WebhookEnvelope:
    return WebhookEnvelope.model_validate(
        {
            "id": event_id,
            "event": "payment.captured",
            "created_at": "2026-10-19T00:00:00Z",
            "order": {
                "id": "order_abc",
                "status": "captured",
                "amount_paise": 199900,
                "currency": "INR",
                "method": "upi",
                "notes": {"workspace_id": str(workspace_id), "plan": plan},
                "created_at": "2026-10-19T00:00:00Z",
            },
        }
    )


def test_webhook_rejects_bad_signature() -> None:
    repository = InMemoryBillingRepository()
    envelope = _captured_order_envelope(event_id="evt_bad", workspace_id=uuid4())
    body = envelope.model_dump_json().encode()
    ts = int(time.time())
    with pytest.raises(ApiError) as exc:
        asyncio.run(
            handle_gateway_webhook(
                request=_WebhookRequest(payload=body),
                repository=repository,
                x_mock_gateway_event="payment.captured",
                x_mock_gateway_event_id=envelope.id,
                x_mock_gateway_timestamp=str(ts),
                x_mock_gateway_signature=compute_signature("wrong-secret", ts, body),
            )
        )
    assert exc.value.status_code == 400
    assert exc.value.code == "gateway_signature_invalid"


def test_webhook_rejects_stale_timestamp() -> None:
    repository = InMemoryBillingRepository()
    envelope = _captured_order_envelope(event_id="evt_stale", workspace_id=uuid4())
    with pytest.raises(ApiError) as exc:
        _run_webhook(envelope, repository, ts=int(time.time()) - 10_000)
    assert exc.value.status_code == 400
    assert exc.value.code == "gateway_signature_expired"


def test_upi_capture_webhook_unlocks_agency_and_is_idempotent() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    envelope = _captured_order_envelope(event_id="evt_upi", workspace_id=workspace_id)

    first = _run_webhook(envelope, repository)
    second = _run_webhook(envelope, repository)

    assert first.received is True and first.duplicate is False
    assert second.duplicate is True
    status = build_billing_status(workspace_id, repository)
    assert status.current_plan.key == BillingPlanKey.AGENCY
    assert status.entitlements.api_access is True
    # One charge receipt only (idempotent).
    assert len(repository.list_billing_adjustments(workspace_id=workspace_id)) == 1


def test_charge_failed_marks_past_due_then_charged_restores_plan() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.AGENCY,
        status=SubscriptionStatus.ACTIVE,
        gateway_subscription_id="sub_1",
    )

    failed = WebhookEnvelope.model_validate(
        {
            "id": "evt_fail",
            "event": "subscription.charge_failed",
            "created_at": "2026-10-19T00:00:00Z",
            "subscription": {
                "id": "sub_1",
                "status": "past_due",
                "amount_paise": 199900,
                "currency": "INR",
                "total_cycles": 12,
                "completed_cycles": 1,
                "notes": {"workspace_id": str(workspace_id), "plan": "agency"},
                "created_at": "2026-10-19T00:00:00Z",
            },
        }
    )
    _run_webhook(failed, repository)
    assert build_billing_status(workspace_id, repository).subscription.payment_issue is True

    charged = WebhookEnvelope.model_validate(
        {
            "id": "evt_charged",
            "event": "subscription.charged",
            "created_at": "2026-10-19T00:00:00Z",
            "subscription": {
                "id": "sub_1",
                "status": "active",
                "amount_paise": 199900,
                "currency": "INR",
                "total_cycles": 12,
                "completed_cycles": 2,
                "notes": {"workspace_id": str(workspace_id), "plan": "agency"},
                "created_at": "2026-10-19T00:00:00Z",
            },
            "order": {
                "id": "order_renew",
                "status": "captured",
                "amount_paise": 199900,
                "currency": "INR",
                "notes": {"workspace_id": str(workspace_id), "plan": "agency"},
                "created_at": "2026-10-19T00:00:00Z",
            },
        }
    )
    _run_webhook(charged, repository)
    status = build_billing_status(workspace_id, repository)
    assert status.current_plan.key == BillingPlanKey.AGENCY
    assert status.subscription.payment_issue is False


def test_cancelled_webhook_downgrades_to_free() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.AGENCY,
        status=SubscriptionStatus.ACTIVE,
        gateway_subscription_id="sub_c",
    )
    envelope = WebhookEnvelope.model_validate(
        {
            "id": "evt_cancel",
            "event": "subscription.cancelled",
            "created_at": "2026-10-19T00:00:00Z",
            "subscription": {
                "id": "sub_c",
                "status": "cancelled",
                "amount_paise": 199900,
                "currency": "INR",
                "total_cycles": 12,
                "completed_cycles": 3,
                "notes": {"workspace_id": str(workspace_id), "plan": "agency"},
                "created_at": "2026-10-19T00:00:00Z",
            },
        }
    )
    _run_webhook(envelope, repository)
    status = build_billing_status(workspace_id, repository)
    assert status.current_plan.key == BillingPlanKey.FREE
    assert status.subscription.status == SubscriptionStatus.CANCELED


def test_refund_webhook_records_negative_adjustment() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    envelope = WebhookEnvelope.model_validate(
        {
            "id": "evt_refund",
            "event": "payment.refunded",
            "created_at": "2026-10-19T00:00:00Z",
            "order": {
                "id": "order_r",
                "status": "refunded",
                "amount_paise": 199900,
                "amount_refunded_paise": 199900,
                "currency": "INR",
                "notes": {"workspace_id": str(workspace_id), "plan": "agency"},
                "created_at": "2026-10-19T00:00:00Z",
            },
            "refund": {
                "id": "rfnd_1",
                "order_id": "order_r",
                "amount_paise": 199900,
                "created_at": "2026-10-19T00:00:00Z",
            },
        }
    )
    _run_webhook(envelope, repository)
    receipts = repository.list_billing_adjustments(workspace_id=workspace_id)
    assert len(receipts) == 1
    assert receipts[0].adjustment_type == "refund"
    assert receipts[0].amount_paise == -199900


# --------------------------------------------------------------------------
# End-to-end: real mock gateway app in-process → signed webhook → API receiver
# --------------------------------------------------------------------------


def _gateway_config():
    """Build a demo gateway config via load_config (robust to field churn)."""
    return _load_gateway_config(
        {
            "APP_ENV": "test",
            "BILLING_MODE": "demo",
            "MERCHANT_API_KEY": "test-merchant-key",
            "MOCK_GATEWAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "MOCK_GATEWAY_RENEWAL_PERIOD_SECONDS": "10000",
            "MOCK_GATEWAY_WEBHOOK_MAX_ATTEMPTS": "1",
            "MOCK_GATEWAY_WEBHOOK_BACKOFF_SECONDS": "0",
        }
    )


def _run_end_to_end(*, card_number=None, upi_action="approve", plan="agency"):
    """Drive the real gateway app via ASGI; forward its webhooks to the API."""
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()

    config = _gateway_config()

    captured_events: list[WebhookEnvelope] = []

    class _CapturingDeliverer(WebhookDeliverer):
        def __init__(self) -> None:
            super().__init__(secret=WEBHOOK_SECRET, url=None, transport=None)

        async def deliver(self, envelope: WebhookEnvelope) -> None:
            captured_events.append(envelope)

    gateway_app = create_gateway_app(config, deliverer=_CapturingDeliverer())

    async def scenario():
        transport = httpx.ASGITransport(app=gateway_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway"
        ) as client:
            headers = {"Authorization": "Bearer test-merchant-key"}
            created = await client.post(
                "/v1/orders",
                headers=headers,
                json={
                    "amount_paise": 199900,
                    "currency": "INR",
                    "receipt": "r",
                    "notes": {"workspace_id": str(workspace_id), "plan": plan},
                },
            )
            order_id = created.json()["id"]
            if card_number is not None:
                resp = await client.post(
                    f"/pay/checkout/{order_id}/card",
                    content=f"card_number={card_number}".encode(),
                    headers={"content-type": "application/x-www-form-urlencoded"},
                )
                # OTP card: a second step with the correct OTP.
                if "Enter the OTP" in resp.text or "otp" in resp.text.lower():
                    await client.post(
                        f"/pay/checkout/{order_id}/otp",
                        content=b"otp=123456",
                        headers={"content-type": "application/x-www-form-urlencoded"},
                    )
            else:
                await client.post(
                    f"/pay/checkout/{order_id}/upi",
                    content=f"action={upi_action}".encode(),
                    headers={"content-type": "application/x-www-form-urlencoded"},
                )

        # Forward each captured webhook into the API receiver.
        for envelope in captured_events:
            body = envelope.model_dump_json().encode()
            await handle_gateway_webhook(
                request=_WebhookRequest(payload=body),
                repository=repository,
                x_mock_gateway_event=str(envelope.event),
                x_mock_gateway_event_id=envelope.id,
                x_mock_gateway_timestamp=str(int(time.time())),
                x_mock_gateway_signature=compute_signature(
                    WEBHOOK_SECRET, int(time.time()), body
                ),
            )

    asyncio.run(scenario())
    return workspace_id, repository, captured_events


def test_end_to_end_upi_upgrade_unlocks_agency() -> None:
    workspace_id, repository, events = _run_end_to_end(upi_action="approve")
    assert any(e.event == "payment.captured" for e in events)
    status = build_billing_status(workspace_id, repository)
    assert status.current_plan.key == BillingPlanKey.AGENCY
    assert status.entitlements.api_access is True


def test_end_to_end_card_with_otp_succeeds() -> None:
    # 4000000000003220 triggers the OTP step; 123456 confirms it.
    workspace_id, repository, events = _run_end_to_end(card_number="4000000000003220")
    assert any(e.event == "payment.captured" for e in events)
    status = build_billing_status(workspace_id, repository)
    assert status.current_plan.key == BillingPlanKey.AGENCY


def test_end_to_end_declined_card_stays_on_free() -> None:
    # 4000000000000002 is declined.
    workspace_id, repository, events = _run_end_to_end(card_number="4000000000000002")
    assert any(e.event == "payment.failed" for e in events)
    assert not any(e.event == "payment.captured" for e in events)
    status = build_billing_status(workspace_id, repository)
    assert status.current_plan.key == BillingPlanKey.FREE


def test_build_billing_status_async_matches_sync_summary() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    repository.record_usage(workspace_id=workspace_id, event_type=UsageEventType.RENDER_SUCCEEDED)
    sync_status = build_billing_status(workspace_id, repository)
    async_status = asyncio.run(build_billing_status_async(workspace_id, repository))
    assert async_status.usage.renders_used == sync_status.usage.renders_used
    assert async_status.current_plan.key == sync_status.current_plan.key


def test_require_plan_feature_from_subscription_record() -> None:
    from app.services.plan_limits import require_plan_feature

    record = SubscriptionRecord(
        workspace_id=uuid4(),
        plan=BillingPlanKey.AGENCY,
        status=SubscriptionStatus.ACTIVE,
    )
    require_plan_feature(record, "api_access")

    free = SubscriptionRecord(
        workspace_id=uuid4(),
        plan=BillingPlanKey.FREE,
        status=SubscriptionStatus.NONE,
    )
    with pytest.raises(ApiError):
        require_plan_feature(free, "api_access")


def test_apply_gateway_event_requires_workspace() -> None:
    repository = InMemoryBillingRepository()
    envelope = WebhookEnvelope.model_validate(
        {
            "id": "evt_no_ws",
            "event": "payment.captured",
            "created_at": "2026-10-19T00:00:00Z",
            "order": {
                "id": "o",
                "status": "captured",
                "amount_paise": 199900,
                "currency": "INR",
                "notes": {},
                "created_at": "2026-10-19T00:00:00Z",
            },
        }
    )
    with pytest.raises(ApiError) as exc:
        asyncio.run(apply_gateway_event_async(envelope=envelope, repository=repository))
    assert exc.value.code == "gateway_workspace_missing"


_ = json  # keep import referenced for readers scanning payload shapes
