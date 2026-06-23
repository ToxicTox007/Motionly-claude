import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.billing import apply_stripe_event, build_billing_status, enforce_render_entitlement
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository, SupabaseBillingRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus, UsageEventType
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
