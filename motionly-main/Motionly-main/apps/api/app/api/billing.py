from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from app.api.dependencies import (
    Actor,
    get_actor,
    get_billing_repository,
    get_stripe_billing_client,
)
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository, SubscriptionRecord
from app.schemas.billing import (
    BillingPlanKey,
    BillingStatusResponse,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    CustomerPortalResponse,
    StripeWebhookResponse,
    SubscriptionStatus,
    SubscriptionSummary,
    UsageEventType,
    UsageSummary,
)
from app.services.billing import (
    PLAN_DEFINITIONS,
    StripeBillingClient,
    monthly_period,
    parse_verified_stripe_event,
    plan_for_subscription_status,
)

router = APIRouter(tags=["billing"])

ActorDep = Annotated[Actor, Depends(get_actor)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
StripeClientDep = Annotated[StripeBillingClient, Depends(get_stripe_billing_client)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


@router.get("/billing/status", response_model=BillingStatusResponse)
async def get_billing_status(
    actor: ActorDep,
    repository: BillingRepositoryDep,
) -> BillingStatusResponse:
    return await build_billing_status_async(actor.workspace_id, repository)


@router.post("/billing/checkout", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    payload: CheckoutSessionRequest,
    request: Request,
    actor: ActorDep,
    repository: BillingRepositoryDep,
    stripe_client: StripeClientDep,
) -> CheckoutSessionResponse:
    if not request.app.state.settings.billing_enabled:
        raise ApiError(503, "billing_disabled", "Billing is not enabled yet.")
    if payload.plan == BillingPlanKey.FREE:
        raise ApiError(422, "checkout_free_plan", "Free plan does not require checkout.")
    price_id = stripe_price_for_plan(request, payload.plan)
    subscription = await _repository_call(
        repository,
        "get_subscription_async",
        "get_subscription",
        workspace_id=actor.workspace_id,
    )
    success_url = str(request.app.state.settings.billing_success_url or "")
    cancel_url = str(request.app.state.settings.billing_cancel_url or "")
    if not success_url or not cancel_url:
        raise ApiError(
            503,
            "billing_urls_not_configured",
            "Billing return URLs are not configured.",
        )

    checkout_url, session_id = stripe_client.create_checkout_session(
        price_id=price_id,
        plan=payload.plan,
        workspace_id=actor.workspace_id,
        success_url=success_url,
        cancel_url=cancel_url,
        customer_id=subscription.stripe_customer_id,
    )
    return CheckoutSessionResponse(checkout_url=checkout_url, session_id=session_id)


@router.post("/billing/customer-portal", response_model=CustomerPortalResponse)
async def create_customer_portal_session(
    request: Request,
    actor: ActorDep,
    repository: BillingRepositoryDep,
    stripe_client: StripeClientDep,
) -> CustomerPortalResponse:
    if not request.app.state.settings.billing_enabled:
        raise ApiError(503, "billing_disabled", "Billing is not enabled yet.")
    subscription = await _repository_call(
        repository,
        "get_subscription_async",
        "get_subscription",
        workspace_id=actor.workspace_id,
    )
    if not subscription.stripe_customer_id:
        raise ApiError(404, "stripe_customer_missing", "Upgrade before opening billing management.")
    return_url = str(request.app.state.settings.billing_portal_return_url or "")
    if not return_url:
        raise ApiError(
            503,
            "billing_portal_url_not_configured",
            "Billing portal return URL is not configured.",
        )
    return CustomerPortalResponse(
        portal_url=stripe_client.create_customer_portal_session(
            customer_id=subscription.stripe_customer_id,
            return_url=return_url,
        ),
    )


@router.post("/webhooks/stripe", response_model=StripeWebhookResponse)
async def handle_stripe_webhook(
    request: Request,
    repository: BillingRepositoryDep,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> StripeWebhookResponse:
    if not request.app.state.settings.billing_enabled:
        raise ApiError(503, "billing_disabled", "Billing is not enabled yet.")
    payload = await request.body()
    event = parse_verified_stripe_event(
        payload=payload,
        signature_header=stripe_signature,
        webhook_secret=request.app.state.settings.stripe_webhook_secret,
    )
    event_id = str(event["id"])
    event_type = str(event["type"])
    if await _repository_call(
        repository,
        "has_processed_stripe_event_async",
        "has_processed_stripe_event",
        event_id=event_id,
    ):
        return StripeWebhookResponse(received=True, duplicate=True)

    await apply_stripe_event_async(
        event=event,
        repository=repository,
        settings=request.app.state.settings,
    )
    await _repository_call(
        repository,
        "record_stripe_event_async",
        "record_stripe_event",
        event_id=event_id,
        event_type=event_type,
    )
    return StripeWebhookResponse(received=True)


def build_billing_status(
    workspace_id: UUID,
    repository: InMemoryBillingRepository,
) -> BillingStatusResponse:
    subscription = repository.get_subscription(workspace_id=workspace_id)
    effective_plan_key = plan_for_subscription_status(
        plan=subscription.plan,
        status=subscription.status,
    )
    plan = PLAN_DEFINITIONS[effective_plan_key]
    period_start, period_end = monthly_period()
    renders_used = repository.count_usage(
        workspace_id=workspace_id,
        event_type=UsageEventType.RENDER_SUCCEEDED,
        period_start=period_start,
        period_end=period_end,
    )
    limit = plan.entitlements.monthly_renders
    remaining = max(0, limit - renders_used)
    percent = min(100, round((renders_used / limit) * 100)) if limit else 100
    return BillingStatusResponse(
        subscription=subscription_to_summary(subscription),
        current_plan=plan,
        plans=list(PLAN_DEFINITIONS.values()),
        usage=UsageSummary(
            workspace_id=workspace_id,
            period_start=period_start,
            period_end=period_end,
            renders_used=renders_used,
            renders_limit=limit,
            remaining_renders=remaining,
            percent_used=percent,
        ),
        can_render=remaining > 0,
        entitlements=plan.entitlements,
    )


async def build_billing_status_async(
    workspace_id: UUID,
    repository: InMemoryBillingRepository,
) -> BillingStatusResponse:
    subscription = await _repository_call(
        repository,
        "get_subscription_async",
        "get_subscription",
        workspace_id=workspace_id,
    )
    effective_plan_key = plan_for_subscription_status(
        plan=subscription.plan,
        status=subscription.status,
    )
    plan = PLAN_DEFINITIONS[effective_plan_key]
    period_start, period_end = monthly_period()
    renders_used = await _repository_call(
        repository,
        "count_usage_async",
        "count_usage",
        workspace_id=workspace_id,
        event_type=UsageEventType.RENDER_SUCCEEDED,
        period_start=period_start,
        period_end=period_end,
    )
    limit = plan.entitlements.monthly_renders
    remaining = max(0, limit - renders_used)
    percent = min(100, round((renders_used / limit) * 100)) if limit else 100
    return BillingStatusResponse(
        subscription=subscription_to_summary(subscription),
        current_plan=plan,
        plans=list(PLAN_DEFINITIONS.values()),
        usage=UsageSummary(
            workspace_id=workspace_id,
            period_start=period_start,
            period_end=period_end,
            renders_used=renders_used,
            renders_limit=limit,
            remaining_renders=remaining,
            percent_used=percent,
        ),
        can_render=remaining > 0,
        entitlements=plan.entitlements,
    )


def enforce_render_entitlement(
    *,
    workspace_id: UUID,
    repository: InMemoryBillingRepository,
) -> BillingStatusResponse:
    status = build_billing_status(workspace_id, repository)
    if not status.can_render:
        raise ApiError(
            402,
            "render_limit_exceeded",
            "You have used all renders for this billing period. "
            "Upgrade or wait for the next cycle.",
        )
    return status


def record_render_success_usage(
    *,
    workspace_id: UUID,
    render_job_id: UUID,
    repository: InMemoryBillingRepository,
    plan: BillingPlanKey,
) -> None:
    repository.record_usage(
        workspace_id=workspace_id,
        event_type=UsageEventType.RENDER_SUCCEEDED,
        source_id=render_job_id,
        metadata={"plan": plan.value},
    )


def subscription_to_summary(subscription: SubscriptionRecord) -> SubscriptionSummary:
    return SubscriptionSummary(
        workspace_id=subscription.workspace_id,
        plan=subscription.plan,
        status=subscription.status,
        stripe_customer_id=subscription.stripe_customer_id,
        stripe_subscription_id=subscription.stripe_subscription_id,
        stripe_price_id=subscription.stripe_price_id,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=subscription.cancel_at_period_end,
        payment_issue=subscription.status
        in {SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID},
        updated_at=subscription.updated_at,
    )


def stripe_price_for_plan(request: Request, plan: BillingPlanKey) -> str:
    settings = request.app.state.settings
    price_id = {
        BillingPlanKey.CREATOR: settings.stripe_price_creator,
        BillingPlanKey.PRO: settings.stripe_price_pro,
        BillingPlanKey.AGENCY: settings.stripe_price_agency,
    }.get(plan)
    if not price_id:
        raise ApiError(
            503,
            "stripe_price_missing",
            f"Stripe price for {plan.value} is not configured.",
        )
    return price_id


def apply_stripe_event(
    *,
    event: dict[str, object],
    repository: InMemoryBillingRepository,
    settings: object,
) -> None:
    event_type = str(event["type"])
    obj = event_object(event)
    event_id = str(event["id"])
    if event_type == "checkout.session.completed":
        workspace_id = UUID(
            str(obj.get("client_reference_id") or obj.get("metadata", {}).get("workspace_id"))
        )
        metadata = obj.get("metadata", {}) if isinstance(obj.get("metadata"), dict) else {}
        plan = plan_from_metadata_or_price(metadata, stripe_price_id_from_checkout(obj), settings)
        repository.upsert_subscription(
            workspace_id=workspace_id,
            plan=plan,
            status=SubscriptionStatus.ACTIVE,
            stripe_customer_id=obj.get("customer") and str(obj["customer"]),
            stripe_subscription_id=obj.get("subscription") and str(obj["subscription"]),
            stripe_price_id=stripe_price_id_from_checkout(obj),
        )
        return
    if event_type.startswith("customer.subscription."):
        workspace_id = UUID(str(obj.get("metadata", {}).get("workspace_id")))
        status = subscription_status(str(obj.get("status", "incomplete")))
        price_id = stripe_price_id_from_subscription(obj)
        repository.upsert_subscription(
            workspace_id=workspace_id,
            plan=plan_from_price(price_id, settings),
            status=status,
            stripe_customer_id=obj.get("customer") and str(obj["customer"]),
            stripe_subscription_id=str(obj["id"]),
            stripe_price_id=price_id,
            current_period_start=timestamp_to_datetime(obj.get("current_period_start")),
            current_period_end=timestamp_to_datetime(obj.get("current_period_end")),
            cancel_at_period_end=bool(obj.get("cancel_at_period_end", False)),
        )
        return
    if event_type == "invoice.payment_failed":
        workspace_id = workspace_id_from_stripe_object(obj, repository)
        if workspace_id is None:
            raise ApiError(400, "stripe_workspace_missing", "Stripe event is missing workspace ID.")
        existing = repository.get_subscription(workspace_id=workspace_id)
        repository.upsert_subscription(
            workspace_id=workspace_id,
            plan=existing.plan,
            status=SubscriptionStatus.PAST_DUE,
            stripe_customer_id=obj.get("customer") and str(obj["customer"]),
            stripe_subscription_id=obj.get("subscription") and str(obj["subscription"]),
            stripe_price_id=existing.stripe_price_id,
            current_period_start=existing.current_period_start,
            current_period_end=existing.current_period_end,
            cancel_at_period_end=existing.cancel_at_period_end,
        )
        return
    if event_type in {"charge.refunded", "charge.refund.updated"}:
        workspace_id = workspace_id_from_stripe_object(obj, repository)
        if workspace_id is None:
            raise ApiError(400, "stripe_workspace_missing", "Stripe event is missing workspace ID.")
        amount_cents = int(obj.get("amount_refunded") or obj.get("amount") or 0)
        if amount_cents <= 0:
            raise ApiError(400, "stripe_refund_invalid", "Stripe refund amount is invalid.")
        repository.record_billing_adjustment(
            workspace_id=workspace_id,
            stripe_event_id=event_id,
            adjustment_type="refund",
            amount_cents=-amount_cents,
            currency=str(obj.get("currency") or "usd"),
            reason=obj.get("failure_message") and str(obj["failure_message"]),
            metadata={
                "stripe_charge_id": obj.get("id"),
                "stripe_customer_id": obj.get("customer"),
                "stripe_invoice_id": obj.get("invoice"),
            },
        )


async def apply_stripe_event_async(
    *,
    event: dict[str, object],
    repository: InMemoryBillingRepository,
    settings: object,
) -> None:
    event_type = str(event["type"])
    obj = event_object(event)
    event_id = str(event["id"])
    if event_type == "checkout.session.completed":
        workspace_id = UUID(
            str(obj.get("client_reference_id") or obj.get("metadata", {}).get("workspace_id"))
        )
        metadata = obj.get("metadata", {}) if isinstance(obj.get("metadata"), dict) else {}
        plan = plan_from_metadata_or_price(metadata, stripe_price_id_from_checkout(obj), settings)
        await _repository_call(
            repository,
            "upsert_subscription_async",
            "upsert_subscription",
            workspace_id=workspace_id,
            plan=plan,
            status=SubscriptionStatus.ACTIVE,
            stripe_customer_id=obj.get("customer") and str(obj["customer"]),
            stripe_subscription_id=obj.get("subscription") and str(obj["subscription"]),
            stripe_price_id=stripe_price_id_from_checkout(obj),
        )
        return
    if event_type.startswith("customer.subscription."):
        workspace_id = UUID(str(obj.get("metadata", {}).get("workspace_id")))
        status = subscription_status(str(obj.get("status", "incomplete")))
        price_id = stripe_price_id_from_subscription(obj)
        await _repository_call(
            repository,
            "upsert_subscription_async",
            "upsert_subscription",
            workspace_id=workspace_id,
            plan=plan_from_price(price_id, settings),
            status=status,
            stripe_customer_id=obj.get("customer") and str(obj["customer"]),
            stripe_subscription_id=str(obj["id"]),
            stripe_price_id=price_id,
            current_period_start=timestamp_to_datetime(obj.get("current_period_start")),
            current_period_end=timestamp_to_datetime(obj.get("current_period_end")),
            cancel_at_period_end=bool(obj.get("cancel_at_period_end", False)),
        )
        return
    if event_type == "invoice.payment_failed":
        workspace_id = await workspace_id_from_stripe_object_async(obj, repository)
        if workspace_id is None:
            raise ApiError(400, "stripe_workspace_missing", "Stripe event is missing workspace ID.")
        existing = await _repository_call(
            repository,
            "get_subscription_async",
            "get_subscription",
            workspace_id=workspace_id,
        )
        await _repository_call(
            repository,
            "upsert_subscription_async",
            "upsert_subscription",
            workspace_id=workspace_id,
            plan=existing.plan,
            status=SubscriptionStatus.PAST_DUE,
            stripe_customer_id=obj.get("customer") and str(obj["customer"]),
            stripe_subscription_id=obj.get("subscription") and str(obj["subscription"]),
            stripe_price_id=existing.stripe_price_id,
            current_period_start=existing.current_period_start,
            current_period_end=existing.current_period_end,
            cancel_at_period_end=existing.cancel_at_period_end,
        )
        return
    if event_type in {"charge.refunded", "charge.refund.updated"}:
        workspace_id = await workspace_id_from_stripe_object_async(obj, repository)
        if workspace_id is None:
            raise ApiError(400, "stripe_workspace_missing", "Stripe event is missing workspace ID.")
        amount_cents = int(obj.get("amount_refunded") or obj.get("amount") or 0)
        if amount_cents <= 0:
            raise ApiError(400, "stripe_refund_invalid", "Stripe refund amount is invalid.")
        await _repository_call(
            repository,
            "record_billing_adjustment_async",
            "record_billing_adjustment",
            workspace_id=workspace_id,
            stripe_event_id=event_id,
            adjustment_type="refund",
            amount_cents=-amount_cents,
            currency=str(obj.get("currency") or "usd"),
            reason=obj.get("failure_message") and str(obj["failure_message"]),
            metadata={
                "stripe_charge_id": obj.get("id"),
                "stripe_customer_id": obj.get("customer"),
                "stripe_invoice_id": obj.get("invoice"),
            },
        )



def event_object(event: dict[str, object]) -> dict[str, Any]:
    data = event.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("object"), dict):
        raise ApiError(400, "stripe_event_invalid", "Stripe webhook event object is invalid.")
    return data["object"]


def stripe_price_id_from_checkout(obj: dict[str, Any]) -> str | None:
    metadata_price = (
        obj.get("metadata", {}).get("stripe_price_id")
        if isinstance(obj.get("metadata"), dict)
        else None
    )
    return metadata_price and str(metadata_price)


def stripe_price_id_from_subscription(obj: dict[str, Any]) -> str | None:
    items = obj.get("items", {}).get("data", []) if isinstance(obj.get("items"), dict) else []
    if not items:
        return None
    price = items[0].get("price", {})
    return price.get("id") and str(price["id"])


def workspace_id_from_stripe_object(
    obj: dict[str, Any],
    repository: InMemoryBillingRepository,
) -> UUID | None:
    metadata = obj.get("metadata", {}) if isinstance(obj.get("metadata"), dict) else {}
    if metadata.get("workspace_id"):
        return UUID(str(metadata["workspace_id"]))
    subscription = obj.get("subscription")
    customer = obj.get("customer")
    return repository.workspace_for_stripe_subscription(
        stripe_subscription_id=subscription and str(subscription),
        stripe_customer_id=customer and str(customer),
    )


async def workspace_id_from_stripe_object_async(
    obj: dict[str, Any],
    repository: InMemoryBillingRepository,
) -> UUID | None:
    metadata = obj.get("metadata", {}) if isinstance(obj.get("metadata"), dict) else {}
    if metadata.get("workspace_id"):
        return UUID(str(metadata["workspace_id"]))
    subscription = obj.get("subscription")
    customer = obj.get("customer")
    return await _repository_call(
        repository,
        "workspace_for_stripe_subscription_async",
        "workspace_for_stripe_subscription",
        stripe_subscription_id=subscription and str(subscription),
        stripe_customer_id=customer and str(customer),
    )


def plan_from_price(price_id: str | None, settings: object) -> BillingPlanKey:
    if price_id == getattr(settings, "stripe_price_creator", None):
        return BillingPlanKey.CREATOR
    if price_id == getattr(settings, "stripe_price_pro", None):
        return BillingPlanKey.PRO
    if price_id == getattr(settings, "stripe_price_agency", None):
        return BillingPlanKey.AGENCY
    return BillingPlanKey.FREE


def plan_from_metadata_or_price(
    metadata: dict[str, Any],
    price_id: str | None,
    settings: object,
) -> BillingPlanKey:
    try:
        return BillingPlanKey(str(metadata.get("plan")))
    except ValueError:
        return plan_from_price(price_id, settings)


def subscription_status(value: str) -> SubscriptionStatus:
    try:
        return SubscriptionStatus(value)
    except ValueError:
        return SubscriptionStatus.INCOMPLETE


def timestamp_to_datetime(value: object) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)
