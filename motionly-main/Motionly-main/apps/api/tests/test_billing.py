import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.billing import (
    apply_stripe_event,
    build_billing_status,
    build_billing_status_async,
    create_checkout_session,
    enforce_render_entitlement,
    get_billing_status,
    handle_stripe_webhook,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.repositories.billing import (
    SUBSCRIPTION_COLUMNS,
    USAGE_LEDGER_COLUMNS,
    InMemoryBillingRepository,
    SupabaseBillingRepository,
)
from app.schemas.billing import (
    BillingPlanKey,
    CheckoutSessionRequest,
    SubscriptionStatus,
    UsageEventType,
)
from app.services.billing import parse_verified_stripe_event, stripe_signature_header


def test_free_plan_uses_usage_ledger_for_monthly_render_limit() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    for _ in range(3):
        repository.record_usage(
            workspace_id=workspace_id,
            event_type=UsageEventType.RENDER_SUCCEEDED,
        )

    status = build_billing_status(workspace_id, repository)

    assert status.current_plan.key == BillingPlanKey.FREE
    assert status.usage.renders_used == 3
    assert status.can_render is False
    with pytest.raises(ApiError) as exc_info:
        enforce_render_entitlement(workspace_id=workspace_id, repository=repository)
    assert exc_info.value.status_code == 402
    assert exc_info.value.code == "render_limit_exceeded"


def test_active_creator_subscription_expands_entitlements() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
    )

    status = build_billing_status(workspace_id, repository)

    assert status.current_plan.key == BillingPlanKey.CREATOR
    assert status.entitlements.monthly_renders == 30
    assert status.entitlements.watermark is False
    assert status.entitlements.broll is True


def test_agency_subscription_delivers_advertised_4k_exports() -> None:
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


def test_supabase_subscription_hydrates_legacy_plan_key_row() -> None:
    workspace_id = uuid4()

    record = SupabaseBillingRepository._subscription_from_row(
        {
            "workspace_id": str(workspace_id),
            "plan_key": "starter",
            "status": None,
        }
    )

    assert record.workspace_id == workspace_id
    assert record.plan == BillingPlanKey.FREE
    assert record.status == SubscriptionStatus.NONE


def test_supabase_billing_async_reads_use_bounded_projections(monkeypatch) -> None:
    repository = SupabaseBillingRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    source_id = uuid4()
    usage_id = uuid4()
    now = "2026-07-06T00:00:00+00:00"
    subscription_row = {
        "workspace_id": str(workspace_id),
        "plan": "creator",
        "status": "active",
        "stripe_customer_id": "cus_123",
        "stripe_subscription_id": "sub_123",
        "stripe_price_id": "price_creator",
        "current_period_start": None,
        "current_period_end": None,
        "cancel_at_period_end": False,
        "updated_at": now,
    }
    usage_row = {
        "id": str(usage_id),
        "workspace_id": str(workspace_id),
        "event_type": "render_succeeded",
        "quantity": 1,
        "source_id": str(source_id),
        "metadata": {},
        "created_at": now,
    }
    responses = [[subscription_row], [{"quantity": 2}], [], [usage_row]]
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0))

    monkeypatch.setattr(
        "app.repositories.billing.SupabaseRestClient.async_request",
        fake_async_request,
    )

    subscription = asyncio.run(repository.get_subscription_async(workspace_id=workspace_id))
    used = asyncio.run(
        repository.count_usage_async(
            workspace_id=workspace_id,
            event_type=UsageEventType.RENDER_SUCCEEDED,
        )
    )
    duplicate_usage = asyncio.run(
        repository.record_usage_async(
            workspace_id=workspace_id,
            event_type=UsageEventType.RENDER_SUCCEEDED,
            source_id=source_id,
        )
    )

    assert subscription.plan == BillingPlanKey.CREATOR
    assert used == 2
    assert duplicate_usage.id == usage_id
    assert calls[0] == (
        "GET",
        "/subscriptions",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "select": SUBSCRIPTION_COLUMNS,
                "limit": "1",
            }
        },
    )
    assert calls[1][0:2] == ("GET", "/usage_ledger")
    assert calls[1][2]["params"]["workspace_id"] == f"eq.{workspace_id}"
    assert calls[1][2]["params"]["event_type"] == "eq.render_succeeded"
    assert calls[1][2]["params"]["select"] == "quantity"
    assert calls[2] == (
        "POST",
        "/usage_ledger",
        {
            "headers": {"prefer": "resolution=ignore-duplicates,return=representation"},
            "params": {"on_conflict": "workspace_id,event_type,source_id"},
            "json": {
                "workspace_id": str(workspace_id),
                "event_type": "render_succeeded",
                "source_id": str(source_id),
            },
        },
    )
    assert calls[3] == (
        "GET",
        "/usage_ledger",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "event_type": "eq.render_succeeded",
                "source_id": f"eq.{source_id}",
                "select": USAGE_LEDGER_COLUMNS,
                "limit": "1",
            }
        },
    )


def test_checkout_uses_only_configured_return_urls() -> None:
    workspace_id = uuid4()
    repository = InMemoryBillingRepository()
    captured: dict[str, object] = {}

    class StripeClient:
        def create_checkout_session(self, **kwargs):
            captured.update(kwargs)
            return "https://checkout.stripe.test/session", "cs_test"

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    billing_enabled=True,
                    stripe_price_creator="price_creator",
                    stripe_price_pro=None,
                    stripe_price_agency=None,
                    billing_success_url="https://app.motionly.test/billing?checkout=success",
                    billing_cancel_url="https://app.motionly.test/billing?checkout=canceled",
                )
            )
        )
    )
    payload = CheckoutSessionRequest.model_validate(
        {
            "plan": "creator",
            "success_url": "https://attacker.example/success",
            "cancel_url": "https://attacker.example/cancel",
        }
    )

    response = asyncio.run(
        create_checkout_session(
            payload=payload,
            request=request,
            actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
            repository=repository,
            stripe_client=StripeClient(),
        )
    )

    assert response.session_id == "cs_test"
    assert captured["success_url"] == request.app.state.settings.billing_success_url
    assert captured["cancel_url"] == request.app.state.settings.billing_cancel_url
    assert "attacker.example" not in str(captured)


def test_billing_api_prefers_async_repository_methods() -> None:
    workspace_id = uuid4()

    class AsyncBillingRepository(InMemoryBillingRepository):
        def __init__(self) -> None:
            super().__init__()
            self.async_calls: list[str] = []

        async def get_subscription_async(self, **kwargs):
            self.async_calls.append("get-subscription")
            return self.get_subscription(**kwargs)

        async def count_usage_async(self, **kwargs):
            self.async_calls.append("count-usage")
            return self.count_usage(**kwargs)

        async def has_processed_stripe_event_async(self, event_id: str):
            self.async_calls.append("has-stripe-event")
            return self.has_processed_stripe_event(event_id)

        async def upsert_subscription_async(self, **kwargs):
            self.async_calls.append("upsert-subscription")
            return self.upsert_subscription(**kwargs)

        async def record_stripe_event_async(self, **kwargs):
            self.async_calls.append("record-stripe-event")
            return self.record_stripe_event(**kwargs)

    repository = AsyncBillingRepository()

    status = asyncio.run(
        get_billing_status(
            actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
            repository=repository,
        )
    )
    payload = _checkout_payload(event_id="evt_async_checkout", workspace_id=workspace_id)
    response = _run_webhook(
        payload=payload,
        signature=stripe_signature_header(payload=payload, secret="whsec_test"),
        repository=repository,
    )

    assert status.current_plan.key == BillingPlanKey.FREE
    assert response.received is True
    assert repository.async_calls == [
        "get-subscription",
        "count-usage",
        "has-stripe-event",
        "upsert-subscription",
        "record-stripe-event",
    ]


def test_build_billing_status_async_matches_sync_summary() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    repository.record_usage(
        workspace_id=workspace_id,
        event_type=UsageEventType.RENDER_SUCCEEDED,
    )

    sync_status = build_billing_status(workspace_id, repository)
    async_status = asyncio.run(build_billing_status_async(workspace_id, repository))

    assert async_status.usage.renders_used == sync_status.usage.renders_used
    assert async_status.current_plan.key == sync_status.current_plan.key


def test_stripe_webhook_signature_verification_and_idempotency() -> None:
    secret = "whsec_test"
    payload = json.dumps(
        {
            "id": "evt_123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(uuid4()),
                    "customer": "cus_123",
                    "subscription": "sub_123",
                    "metadata": {
                        "plan": "creator",
                        "stripe_price_id": "price_creator",
                    },
                }
            },
        },
    ).encode()

    event = parse_verified_stripe_event(
        payload=payload,
        signature_header=stripe_signature_header(payload=payload, secret=secret),
        webhook_secret=secret,
    )

    repository = InMemoryBillingRepository()
    apply_stripe_event(
        event=event,
        repository=repository,
        settings=SimpleNamespace(stripe_price_creator="price_creator"),
    )
    assert repository.has_processed_stripe_event("evt_123") is False
    repository.record_stripe_event(event_id="evt_123", event_type=str(event["type"]))
    assert repository.has_processed_stripe_event("evt_123") is True


def test_stripe_webhook_rejects_bad_signature() -> None:
    payload = b'{"id":"evt_bad","type":"invoice.payment_failed"}'

    with pytest.raises(ApiError) as exc_info:
        parse_verified_stripe_event(
            payload=payload,
            signature_header=stripe_signature_header(payload=payload, secret="right"),
            webhook_secret="wrong",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "stripe_signature_invalid"


def test_stripe_webhook_endpoint_rejects_missing_signature() -> None:
    payload = b'{"id":"evt_missing","type":"checkout.session.completed"}'
    repository = InMemoryBillingRepository()

    with pytest.raises(ApiError) as exc_info:
        _run_webhook(payload=payload, signature=None, repository=repository)

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "stripe_signature_missing"


def test_stripe_webhook_endpoint_rejects_invalid_signature() -> None:
    payload = b'{"id":"evt_invalid","type":"checkout.session.completed"}'
    repository = InMemoryBillingRepository()

    with pytest.raises(ApiError) as exc_info:
        _run_webhook(
            payload=payload,
            signature=stripe_signature_header(payload=payload, secret="wrong-secret"),
            repository=repository,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "stripe_signature_invalid"


def test_stripe_webhook_endpoint_accepts_signed_checkout_event() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    payload = _checkout_payload(event_id="evt_checkout_signed", workspace_id=workspace_id)

    response = _run_webhook(
        payload=payload,
        signature=stripe_signature_header(payload=payload, secret="whsec_test"),
        repository=repository,
    )

    subscription = repository.get_subscription(workspace_id=workspace_id)
    assert response.received is True
    assert response.duplicate is False
    assert subscription.plan == BillingPlanKey.CREATOR
    assert subscription.status == SubscriptionStatus.ACTIVE


def test_stripe_webhook_duplicate_event_does_not_duplicate_subscription_state() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    payload = _checkout_payload(event_id="evt_duplicate", workspace_id=workspace_id)
    signature = stripe_signature_header(payload=payload, secret="whsec_test")

    first = _run_webhook(payload=payload, signature=signature, repository=repository)
    second = _run_webhook(payload=payload, signature=signature, repository=repository)

    assert first.duplicate is False
    assert second.duplicate is True
    assert len(repository.subscriptions) == 1
    assert len(repository.stripe_events) == 1
    assert (
        repository.get_subscription(workspace_id=workspace_id).stripe_subscription_id == "sub_123"
    )


def test_checkout_webhook_entitlement_sync_works_with_staging_test_secret() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    payload = _checkout_payload(event_id="evt_staging_checkout", workspace_id=workspace_id)

    _run_webhook(
        payload=payload,
        signature=stripe_signature_header(payload=payload, secret="whsec_staging_test"),
        repository=repository,
        webhook_secret="whsec_staging_test",
        app_env="staging",
    )

    status = build_billing_status(workspace_id, repository)
    assert status.current_plan.key == BillingPlanKey.CREATOR
    assert status.entitlements.broll is True
    assert status.entitlements.watermark is False


def test_stripe_payment_failure_marks_subscription_past_due_from_signed_sandbox_event() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.PRO,
        status=SubscriptionStatus.ACTIVE,
        stripe_customer_id="cus_failure",
        stripe_subscription_id="sub_failure",
        stripe_price_id="price_pro",
    )
    payload = json.dumps(
        {
            "id": "evt_payment_failed",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "customer": "cus_failure",
                    "subscription": "sub_failure",
                }
            },
        },
    ).encode()

    event = parse_verified_stripe_event(
        payload=payload,
        signature_header=stripe_signature_header(payload=payload, secret="whsec_sandbox"),
        webhook_secret="whsec_sandbox",
    )
    apply_stripe_event(
        event=event,
        repository=repository,
        settings=SimpleNamespace(stripe_price_pro="price_pro"),
    )

    subscription = repository.get_subscription(workspace_id=workspace_id)
    assert subscription.plan == BillingPlanKey.PRO
    assert subscription.status == SubscriptionStatus.PAST_DUE
    assert build_billing_status(workspace_id, repository).subscription.payment_issue is True


def test_stripe_refund_records_idempotent_billing_adjustment_from_signed_sandbox_event() -> None:
    repository = InMemoryBillingRepository()
    workspace_id = uuid4()
    payload = json.dumps(
        {
            "id": "evt_refund",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_refund",
                    "customer": "cus_refund",
                    "invoice": "in_refund",
                    "amount_refunded": 2900,
                    "currency": "usd",
                    "metadata": {"workspace_id": str(workspace_id)},
                }
            },
        },
    ).encode()

    event = parse_verified_stripe_event(
        payload=payload,
        signature_header=stripe_signature_header(payload=payload, secret="whsec_sandbox"),
        webhook_secret="whsec_sandbox",
    )
    apply_stripe_event(
        event=event,
        repository=repository,
        settings=SimpleNamespace(),
    )
    apply_stripe_event(
        event=event,
        repository=repository,
        settings=SimpleNamespace(),
    )

    adjustments = list(repository.billing_adjustments.values())
    assert len(adjustments) == 1
    adjustment = adjustments[0]
    assert adjustment.workspace_id == workspace_id
    assert adjustment.adjustment_type == "refund"
    assert adjustment.amount_cents == -2900
    assert adjustment.metadata["stripe_charge_id"] == "ch_refund"


class _WebhookRequest:
    def __init__(
        self,
        *,
        payload: bytes,
        webhook_secret: str,
        app_env: str = "test",
    ) -> None:
        self._payload = payload
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env=app_env,
                    billing_enabled=True,
                    stripe_webhook_secret=webhook_secret,
                    stripe_price_creator="price_creator",
                )
            )
        )

    async def body(self) -> bytes:
        return self._payload


def _run_webhook(
    *,
    payload: bytes,
    signature: str | None,
    repository: InMemoryBillingRepository,
    webhook_secret: str = "whsec_test",
    app_env: str = "test",
):
    import asyncio

    return asyncio.run(
        handle_stripe_webhook(
            request=_WebhookRequest(
                payload=payload,
                webhook_secret=webhook_secret,
                app_env=app_env,
            ),
            repository=repository,
            stripe_signature=signature,
        )
    )


def _checkout_payload(*, event_id: str, workspace_id) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(workspace_id),
                    "customer": "cus_123",
                    "subscription": "sub_123",
                    "metadata": {
                        "plan": "creator",
                        "stripe_price_id": "price_creator",
                    },
                }
            },
        },
    ).encode()
