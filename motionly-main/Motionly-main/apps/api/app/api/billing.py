import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from app.api.admin import require_admin
from app.api.dependencies import (
    Actor,
    get_actor,
    get_billing_repository,
    get_payment_gateway,
    require_role,
)
from app.api.errors import ApiError
from app.repositories.billing import (
    BillingAdjustmentRecord,
    InMemoryBillingRepository,
    SubscriptionRecord,
)
from app.schemas.billing import (
    BillingMode,
    BillingPlanKey,
    BillingStatusResponse,
    CancelSubscriptionResponse,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    GatewayWebhookResponse,
    PaymentHistoryResponse,
    PaymentReceipt,
    PublicPlansResponse,
    RefundRequest,
    RefundResponse,
    RetryRenewalResponse,
    SubscriptionStatus,
    SubscriptionSummary,
    UsageEventType,
    UsageSummary,
)
from app.schemas.renders import RenderJobStatus
from app.services.billing import (
    PLAN_DEFINITIONS,
    effective_plan_key,
    monthly_period,
    past_due_grace_ends_at,
    verify_gateway_webhook,
)
from app.services.offload import run_blocking
from app.services.payment_gateway import DEMO_NOTICE, PaymentGateway

router = APIRouter(tags=["billing"])
logger = logging.getLogger(__name__)

ActorDep = Annotated[Actor, Depends(get_actor)]
AdminActorDep = Annotated[Actor, Depends(require_admin)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
GatewayDep = Annotated[PaymentGateway, Depends(get_payment_gateway)]


def _billing_mode(request: Request) -> str:
    return str(getattr(request.app.state.settings, "billing_mode", "") or "").strip().lower()


def _demo_notice(request: Request) -> str | None:
    """The 'Demo payment — no money is charged' notice when BILLING_MODE=demo.

    Returned on every payment surface in demo mode; the leakage gate asserts it.
    """
    return DEMO_NOTICE if _billing_mode(request) == "demo" else None


def _require_billing_enabled(request: Request) -> None:
    if not request.app.state.settings.billing_enabled:
        raise ApiError(503, "billing_disabled", "Billing is not enabled yet.")
    if _billing_mode(request) == "disabled":
        raise ApiError(503, "billing_disabled", "Billing is disabled.")


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


@router.get("/plans", response_model=PublicPlansResponse, tags=["plans"])
async def list_public_plans() -> PublicPlansResponse:
    """Public catalogue of billing plans (single source of truth).

    Unauthenticated: the pricing page reads plans from here instead of keeping a
    hand-written duplicate. Backed by PLAN_DEFINITIONS, loaded from
    app/config/plans.json (INR paise).
    """
    return PublicPlansResponse(plans=list(PLAN_DEFINITIONS.values()))


@router.get("/billing/status", response_model=BillingStatusResponse)
async def get_billing_status(
    request: Request,
    actor: ActorDep,
    repository: BillingRepositoryDep,
) -> BillingStatusResponse:
    return await build_billing_status_async(
        actor.workspace_id,
        repository,
        billing_enabled=bool(request.app.state.settings.billing_enabled),
    )


@router.post(
    "/billing/checkout",
    response_model=CheckoutSessionResponse,
    dependencies=[Depends(require_role("owner"))],
)
async def create_checkout_session(
    payload: CheckoutSessionRequest,
    request: Request,
    actor: ActorDep,
    repository: BillingRepositoryDep,
    gateway: GatewayDep,
) -> CheckoutSessionResponse:
    _require_billing_enabled(request)
    if payload.plan == BillingPlanKey.FREE:
        raise ApiError(422, "checkout_free_plan", "Free plan does not require checkout.")
    plan = PLAN_DEFINITIONS[payload.plan]
    # The gateway client is synchronous HTTP; keep it off the event loop.
    order = await run_blocking(
        gateway.create_order,
        amount_paise=plan.price_paise,
        currency=plan.currency,
        receipt=f"ws_{actor.workspace_id}",
        notes={"workspace_id": str(actor.workspace_id), "plan": payload.plan.value},
    )
    return CheckoutSessionResponse(
        checkout_url=order.checkout_url,
        session_id=order.id,
        demo_notice=_demo_notice(request),
    )


@router.post(
    "/billing/cancel",
    response_model=CancelSubscriptionResponse,
    dependencies=[Depends(require_role("owner"))],
)
async def cancel_subscription(
    request: Request,
    actor: ActorDep,
    repository: BillingRepositoryDep,
) -> CancelSubscriptionResponse:
    """Cancel at period end — the plan stays active until the period closes."""
    _require_billing_enabled(request)
    subscription = await _repository_call(
        repository,
        "get_subscription_async",
        "get_subscription",
        workspace_id=actor.workspace_id,
    )
    if subscription.plan == BillingPlanKey.FREE or subscription.status in {
        SubscriptionStatus.NONE,
        SubscriptionStatus.CANCELED,
    }:
        raise ApiError(422, "no_active_subscription", "There is no active subscription to cancel.")
    updated = await _repository_call(
        repository,
        "upsert_subscription_async",
        "upsert_subscription",
        workspace_id=actor.workspace_id,
        plan=subscription.plan,
        status=subscription.status,
        gateway_customer_id=subscription.gateway_customer_id,
        gateway_subscription_id=subscription.gateway_subscription_id,
        gateway_price_id=subscription.gateway_price_id,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=True,
    )
    if updated is None:  # only gateway-event writes can be skipped
        updated = subscription
    return CancelSubscriptionResponse(
        subscription=subscription_to_summary(updated),
        demo_notice=_demo_notice(request),
    )


@router.post(
    "/billing/retry",
    response_model=RetryRenewalResponse,
    dependencies=[Depends(require_role("owner"))],
)
async def retry_renewal(
    request: Request,
    actor: ActorDep,
    repository: BillingRepositoryDep,
    gateway: GatewayDep,
) -> RetryRenewalResponse:
    """Retry a failed renewal — returns a fresh checkout to re-authorise.

    The plan is restored to active only once the gateway webhook lands
    (subscription.charged); this endpoint produces the order the user pays.
    """
    _require_billing_enabled(request)
    subscription = await _repository_call(
        repository,
        "get_subscription_async",
        "get_subscription",
        workspace_id=actor.workspace_id,
    )
    if subscription.status != SubscriptionStatus.PAST_DUE:
        raise ApiError(422, "no_failed_renewal", "There is no failed renewal to retry.")
    plan = PLAN_DEFINITIONS[subscription.plan]
    order = await run_blocking(
        gateway.create_order,
        amount_paise=plan.price_paise,
        currency=plan.currency,
        receipt=f"retry_{actor.workspace_id}",
        notes={
            "workspace_id": str(actor.workspace_id),
            "plan": subscription.plan.value,
            "retry": "true",
        },
    )
    return RetryRenewalResponse(
        subscription=subscription_to_summary(subscription),
        demo_notice=_demo_notice(request),
        checkout_url=order.checkout_url,
        session_id=order.id,
    )


@router.get("/billing/history", response_model=PaymentHistoryResponse)
async def get_payment_history(
    request: Request,
    actor: ActorDep,
    repository: BillingRepositoryDep,
) -> PaymentHistoryResponse:
    adjustments = await _repository_call(
        repository,
        "list_billing_adjustments_async",
        "list_billing_adjustments",
        workspace_id=actor.workspace_id,
    )
    notice = _demo_notice(request) or ""
    return PaymentHistoryResponse(
        receipts=[_adjustment_to_receipt(adjustment, notice) for adjustment in adjustments]
    )


def refund_ledger_key(refund_id: str) -> str:
    """Ledger idempotency key for one gateway refund.

    Both :func:`issue_refund` and the ``payment.refunded`` webhook record the
    same refund; keying the ledger row by the gateway refund id (rather than by
    request or webhook event) makes whichever lands second a no-op.
    """
    return f"refund_{refund_id}"


def _latest_refundable_charge(
    adjustments: list[BillingAdjustmentRecord],
) -> tuple[BillingAdjustmentRecord, str, int] | None:
    """The newest captured charge with money left to refund.

    Returns ``(charge, order_id, remaining_paise)`` where ``remaining_paise`` is
    the captured amount minus every refund already recorded against that
    gateway order. Charges without a gateway order id cannot be refunded
    through the gateway and are skipped.
    """
    refunded_by_order: dict[str, int] = {}
    for adjustment in adjustments:
        if adjustment.adjustment_type != "refund":
            continue
        order_id = str((adjustment.metadata or {}).get("order_id") or "")
        if order_id:
            refunded_by_order[order_id] = refunded_by_order.get(order_id, 0) + abs(
                adjustment.amount_paise
            )
    seen_orders: set[str] = set()
    charges = sorted(
        (adjustment for adjustment in adjustments if adjustment.adjustment_type == "charge"),
        key=lambda adjustment: adjustment.created_at,
        reverse=True,
    )
    for charge in charges:
        order_id = str((charge.metadata or {}).get("order_id") or "")
        if not order_id or order_id in seen_orders:
            continue
        seen_orders.add(order_id)
        remaining = charge.amount_paise - refunded_by_order.get(order_id, 0)
        if remaining > 0:
            return charge, order_id, remaining
    return None


@router.post("/billing/refunds", response_model=RefundResponse)
async def issue_refund(
    payload: RefundRequest,
    request: Request,
    actor: AdminActorDep,
    repository: BillingRepositoryDep,
    gateway: GatewayDep,
) -> RefundResponse:
    """Refund a workspace's most recent captured payment. Platform admin only.

    The target workspace comes from the request body, so a role in the
    caller's own workspace is not sufficient authority: ``require_admin`` gates
    the route. The amount defaults to (and may not exceed) what was captured on
    that gateway order minus refunds already recorded. The gateway performs the
    refund first; the ledger row is keyed by the gateway refund id so the
    matching ``payment.refunded`` webhook cannot double-count it. Audit-logged.
    """
    _require_billing_enabled(request)
    adjustments = await _repository_call(
        repository,
        "list_billing_adjustments_async",
        "list_billing_adjustments",
        workspace_id=payload.workspace_id,
    )
    refundable = _latest_refundable_charge(adjustments)
    if refundable is None:
        raise ApiError(
            409,
            "refund_no_captured_payment",
            "This workspace has no captured payment left to refund.",
        )
    charge, order_id, remaining = refundable
    amount = payload.amount_paise if payload.amount_paise is not None else remaining
    if amount > remaining:
        raise ApiError(
            409,
            "refund_exceeds_captured",
            "The refund is larger than the amount still refundable on the latest payment.",
        )
    # Synchronous gateway HTTP; offloaded so the event loop stays free (the demo
    # gateway delivers its payment.refunded webhook back to this API mid-call).
    refund = await run_blocking(gateway.refund_order, order_id=order_id, amount_paise=amount)
    refund_id = str(refund.get("id") or "") if isinstance(refund, dict) else ""
    if not refund_id:
        raise ApiError(
            502, "gateway_refund_invalid", "The payment gateway did not confirm the refund."
        )
    refunded_paise = int(refund.get("amount_paise") or amount)
    adjustment = await _repository_call(
        repository,
        "record_billing_adjustment_async",
        "record_billing_adjustment",
        workspace_id=payload.workspace_id,
        gateway_event_id=refund_ledger_key(refund_id),
        adjustment_type="refund",
        amount_paise=-refunded_paise,
        currency=charge.currency.lower(),
        reason=payload.reason,
        metadata={
            "order_id": order_id,
            "refund_id": refund_id,
            "issued_by": str(actor.user_id),
            "source": "admin_refund",
        },
    )
    _audit_log(
        request,
        action="billing.refund",
        actor=actor,
        workspace_id=payload.workspace_id,
        order_id=order_id,
        refund_id=refund_id,
        amount_paise=refunded_paise,
        reason=payload.reason,
    )
    notice = _demo_notice(request) or ""
    return RefundResponse(receipt=_adjustment_to_receipt(adjustment, notice))


@router.post(
    "/billing/webhooks/mock-gateway",
    response_model=GatewayWebhookResponse,
    tags=["webhooks"],
)
async def handle_gateway_webhook(
    request: Request,
    repository: BillingRepositoryDep,
    x_mock_gateway_event: str | None = Header(default=None, alias="X-Mock-Gateway-Event"),
    x_mock_gateway_event_id: str | None = Header(default=None, alias="X-Mock-Gateway-Event-Id"),
    x_mock_gateway_timestamp: str | None = Header(default=None, alias="X-Mock-Gateway-Timestamp"),
    x_mock_gateway_signature: str | None = Header(default=None, alias="X-Mock-Gateway-Signature"),
) -> GatewayWebhookResponse:
    _require_billing_enabled(request)
    payload = await request.body()
    envelope = verify_gateway_webhook(
        payload=payload,
        event_id=x_mock_gateway_event_id,
        timestamp_header=x_mock_gateway_timestamp,
        signature=x_mock_gateway_signature,
        webhook_secret=getattr(request.app.state.settings, "mock_gateway_webhook_secret", None),
    )
    # Claim the event before applying it (audit WJ-23): a concurrent delivery
    # of the same event gets 409 and is retried by the gateway, and a delivery
    # after success is acknowledged as a duplicate. A failed apply releases the
    # claim so the redelivery applies it.
    claim = await _repository_call(
        repository,
        "claim_gateway_event_async",
        "claim_gateway_event",
        event_id=envelope.id,
        event_type=str(envelope.event),
    )
    if claim == "duplicate":
        return GatewayWebhookResponse(received=True, duplicate=True)
    if claim == "in_progress":
        raise ApiError(
            409,
            "gateway_event_in_progress",
            "This webhook event is being processed; deliver it again later.",
        )
    try:
        await apply_gateway_event_async(envelope=envelope, repository=repository)
    except Exception:
        await _repository_call(
            repository,
            "finish_gateway_event_async",
            "finish_gateway_event",
            event_id=envelope.id,
            event_type=str(envelope.event),
            succeeded=False,
        )
        raise
    await _repository_call(
        repository,
        "finish_gateway_event_async",
        "finish_gateway_event",
        event_id=envelope.id,
        event_type=str(envelope.event),
        succeeded=True,
    )
    return GatewayWebhookResponse(received=True)


# ------------------------------------------------------------------------
# Webhook application
# ------------------------------------------------------------------------


def _workspace_and_plan(notes: dict[str, str]) -> tuple[UUID | None, BillingPlanKey]:
    workspace_id = None
    if notes.get("workspace_id"):
        try:
            workspace_id = UUID(str(notes["workspace_id"]))
        except ValueError:
            workspace_id = None
    try:
        plan = BillingPlanKey(str(notes.get("plan")))
    except ValueError:
        plan = BillingPlanKey.FREE
    return workspace_id, plan


def _gateway_event_time(envelope) -> datetime | None:
    """When the gateway created the event (UTC), or None if it cannot be read.

    The envelope's ``created_at`` is set once per event, so every redelivery
    carries the same value (unlike the per-attempt signature timestamp).
    """
    raw = getattr(envelope, "created_at", None)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _event_order(event_at: datetime | None) -> dict[str, datetime]:
    """The ordering field a webhook-driven subscription write carries.

    Omitted when the event time is unreadable: the write then applies in
    arrival order, as before, and leaves the stored watermark alone.
    """
    return {"last_gateway_event_at": event_at} if event_at is not None else {}


def _log_stale_gateway_event(envelope, workspace_id: UUID) -> None:
    logger.info(
        "gateway_event_stale",
        extra={
            "event_id": str(envelope.id),
            "event_type": str(envelope.event),
            "workspace_id": str(workspace_id),
        },
    )


async def apply_gateway_event_async(
    *,
    envelope,
    repository: InMemoryBillingRepository,
) -> None:
    """Apply one gateway webhook event (database audit DB-12).

    Subscription changes are applied in the order the gateway created the
    events: a write for an event older than the one the subscription already
    reflects is skipped (the repository returns None). Money movements (charge
    and refund ledger rows) are always recorded, whatever the order.
    """
    event = str(envelope.event)
    order = envelope.order
    subscription = envelope.subscription
    refund = envelope.refund
    event_at = _gateway_event_time(envelope)

    if event in {"payment.captured", "subscription.charged"} and order is not None:
        workspace_id, plan = _workspace_and_plan(order.notes)
        # subscription.charged carries plan in the subscription notes instead.
        if subscription is not None:
            ws_sub, plan_sub = _workspace_and_plan(subscription.notes)
            workspace_id = workspace_id or ws_sub
            if plan == BillingPlanKey.FREE:
                plan = plan_sub
        if workspace_id is None:
            raise ApiError(400, "gateway_workspace_missing", "Event is missing workspace id.")
        # The billing month the payment was made in, not the month the
        # webhook happened to be delivered in.
        start, end = monthly_period(event_at)
        applied = await _repository_call(
            repository,
            "upsert_subscription_async",
            "upsert_subscription",
            workspace_id=workspace_id,
            plan=plan,
            status=SubscriptionStatus.ACTIVE,
            gateway_customer_id=None,
            gateway_subscription_id=subscription.id if subscription else None,
            gateway_price_id=plan.value,
            current_period_start=start,
            current_period_end=end,
            cancel_at_period_end=False,
            **_event_order(event_at),
        )
        if applied is None:
            _log_stale_gateway_event(envelope, workspace_id)
        await _repository_call(
            repository,
            "record_billing_adjustment_async",
            "record_billing_adjustment",
            workspace_id=workspace_id,
            gateway_event_id=envelope.id,
            adjustment_type="charge",
            amount_paise=order.amount_paise,
            currency=order.currency.lower(),
            reason=None,
            metadata={"plan": plan.value, "order_id": order.id},
        )
        return

    if event == "subscription.charge_failed" and subscription is not None:
        workspace_id, _ = _workspace_and_plan(subscription.notes)
        if workspace_id is None:
            workspace_id = await _repository_call(
                repository,
                "workspace_for_gateway_subscription_async",
                "workspace_for_gateway_subscription",
                gateway_subscription_id=subscription.id,
            )
        if workspace_id is None:
            raise ApiError(400, "gateway_workspace_missing", "Event is missing workspace id.")
        existing = await _repository_call(
            repository,
            "get_subscription_async",
            "get_subscription",
            workspace_id=workspace_id,
        )
        applied = await _repository_call(
            repository,
            "upsert_subscription_async",
            "upsert_subscription",
            workspace_id=workspace_id,
            plan=existing.plan,
            status=SubscriptionStatus.PAST_DUE,
            gateway_customer_id=existing.gateway_customer_id,
            gateway_subscription_id=subscription.id,
            gateway_price_id=existing.gateway_price_id,
            current_period_start=existing.current_period_start,
            current_period_end=existing.current_period_end,
            cancel_at_period_end=existing.cancel_at_period_end,
            **_event_order(event_at),
        )
        if applied is None:
            _log_stale_gateway_event(envelope, workspace_id)
        return

    if event == "subscription.cancelled" and subscription is not None:
        workspace_id, _ = _workspace_and_plan(subscription.notes)
        if workspace_id is None:
            workspace_id = await _repository_call(
                repository,
                "workspace_for_gateway_subscription_async",
                "workspace_for_gateway_subscription",
                gateway_subscription_id=subscription.id,
            )
        if workspace_id is None:
            raise ApiError(400, "gateway_workspace_missing", "Event is missing workspace id.")
        existing = await _repository_call(
            repository,
            "get_subscription_async",
            "get_subscription",
            workspace_id=workspace_id,
        )
        applied = await _repository_call(
            repository,
            "upsert_subscription_async",
            "upsert_subscription",
            workspace_id=workspace_id,
            plan=BillingPlanKey.FREE,
            status=SubscriptionStatus.CANCELED,
            gateway_customer_id=existing.gateway_customer_id,
            gateway_subscription_id=subscription.id,
            gateway_price_id=existing.gateway_price_id,
            cancel_at_period_end=False,
            **_event_order(event_at),
        )
        if applied is None:
            _log_stale_gateway_event(envelope, workspace_id)
        return

    if event == "payment.refunded" and order is not None and refund is not None:
        workspace_id, _ = _workspace_and_plan(order.notes)
        if workspace_id is None:
            raise ApiError(400, "gateway_workspace_missing", "Event is missing workspace id.")
        await _repository_call(
            repository,
            "record_billing_adjustment_async",
            "record_billing_adjustment",
            workspace_id=workspace_id,
            # Same key issue_refund uses, so an admin refund and its webhook
            # produce one ledger row, whichever arrives first.
            gateway_event_id=refund_ledger_key(refund.id),
            adjustment_type="refund",
            amount_paise=-int(refund.amount_paise),
            currency=order.currency.lower(),
            reason=None,
            metadata={"order_id": order.id, "refund_id": refund.id},
        )
        return

    if event == "payment.failed" and order is not None:
        # A failed one-off checkout (an upgrade attempt, or paying a retry
        # order) changes nothing: the paid period the workspace already has is
        # unaffected, and a past-due subscription stays past due. Only
        # subscription.charge_failed (a failed renewal) marks a subscription
        # past due and starts its grace period.
        return


# ------------------------------------------------------------------------
# Billing status helpers (unchanged shape, gateway-named summary)
# ------------------------------------------------------------------------


def _build_billing_status_response(
    *,
    workspace_id: UUID,
    subscription: SubscriptionRecord,
    period_start: datetime,
    period_end: datetime,
    renders_used: int,
    billing_enabled: bool,
) -> BillingStatusResponse:
    if not billing_enabled:
        plan = PLAN_DEFINITIONS[BillingPlanKey.AGENCY]
        return BillingStatusResponse(
            billing_enabled=False,
            billing_mode=BillingMode.PERSONAL,
            unmetered=True,
            subscription=subscription_to_summary(subscription),
            current_plan=plan,
            plans=list(PLAN_DEFINITIONS.values()),
            usage=UsageSummary(
                workspace_id=workspace_id,
                period_start=period_start,
                period_end=period_end,
                renders_used=renders_used,
                renders_limit=None,
                remaining_renders=None,
                percent_used=None,
            ),
            can_render=True,
            entitlements=plan.entitlements,
        )

    effective_plan = effective_plan_key(subscription)
    plan = PLAN_DEFINITIONS[effective_plan]
    limit = plan.entitlements.monthly_renders
    remaining = max(0, limit - renders_used)
    percent = min(100, round((renders_used / limit) * 100)) if limit else 100
    return BillingStatusResponse(
        billing_enabled=True,
        billing_mode=BillingMode.MANAGED,
        unmetered=False,
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


def build_billing_status(
    workspace_id: UUID,
    repository: InMemoryBillingRepository,
    *,
    billing_enabled: bool = True,
) -> BillingStatusResponse:
    subscription = repository.get_subscription(workspace_id=workspace_id)
    period_start, period_end = monthly_period()
    renders_used = repository.count_usage(
        workspace_id=workspace_id,
        event_type=UsageEventType.RENDER_SUCCEEDED,
        period_start=period_start,
        period_end=period_end,
    )
    return _build_billing_status_response(
        workspace_id=workspace_id,
        subscription=subscription,
        period_start=period_start,
        period_end=period_end,
        renders_used=renders_used,
        billing_enabled=billing_enabled,
    )


async def build_billing_status_async(
    workspace_id: UUID,
    repository: InMemoryBillingRepository,
    *,
    billing_enabled: bool = True,
) -> BillingStatusResponse:
    subscription = await _repository_call(
        repository,
        "get_subscription_async",
        "get_subscription",
        workspace_id=workspace_id,
    )
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
    return _build_billing_status_response(
        workspace_id=workspace_id,
        subscription=subscription,
        period_start=period_start,
        period_end=period_end,
        renders_used=renders_used,
        billing_enabled=billing_enabled,
    )


_RENDER_SLOT_COMPLETED = frozenset({RenderJobStatus.COMPLETE})


def render_slots_in_use(
    *,
    renders_succeeded: int,
    slot_jobs: list[tuple[UUID, RenderJobStatus]],
    ahead_of: UUID | None = None,
) -> int | None:
    """Monthly render slots already taken, from the ledger and the render jobs.

    ``slot_jobs`` are this period's renders that have not failed or been
    canceled, oldest first. A render takes its slot when it is created, so
    queued and in-progress renders count, not only finished ones. Completed
    renders are counted from the jobs as well as from the usage ledger (the
    larger wins), because not every engine records ledger usage on completion.

    With ``ahead_of`` (a render that was just created), only in-progress
    renders created before it count, so concurrent requests agree on which of
    them got the last slots. Returns ``None`` if ``ahead_of`` is not among
    ``slot_jobs`` (the list was truncated, so that render is past the limit).
    """
    completed = sum(1 for _, status in slot_jobs if status in _RENDER_SLOT_COMPLETED)
    in_progress = [job_id for job_id, status in slot_jobs if status not in _RENDER_SLOT_COMPLETED]
    if ahead_of is not None:
        if ahead_of not in in_progress:
            return None
        in_progress = in_progress[: in_progress.index(ahead_of)]
    return max(renders_succeeded, completed) + len(in_progress)


async def enforce_render_entitlement(
    *,
    workspace_id: UUID,
    repository: InMemoryBillingRepository,
    render_repository: object | None = None,
    billing_enabled: bool = True,
    requested: int = 1,
    render_job_id: UUID | None = None,
) -> BillingStatusResponse:
    """Refuse a render (402) that would exceed the plan's monthly render limit.

    Without ``render_job_id`` this is the up-front check: ``requested`` more
    renders must fit beside every render already taking a slot. With
    ``render_job_id`` it confirms a render that was just created still fits
    once every concurrently created render is visible, which closes the
    check-then-create race between parallel requests.
    """
    status = await build_billing_status_async(
        workspace_id,
        repository,
        billing_enabled=billing_enabled,
    )
    limit = status.usage.renders_limit
    if status.unmetered or limit is None:
        return status
    if render_repository is None:
        in_use: int | None = status.usage.renders_used
    else:
        slot_jobs = await _repository_call(
            render_repository,
            "list_render_slot_jobs_async",
            "list_render_slot_jobs",
            workspace_id=workspace_id,
            period_start=status.usage.period_start,
            period_end=status.usage.period_end,
            # One past what can fit is enough to tell "over the limit".
            limit=limit + requested + 1,
        )
        in_use = render_slots_in_use(
            renders_succeeded=status.usage.renders_used,
            slot_jobs=slot_jobs,
            ahead_of=render_job_id,
        )
    if in_use is None or in_use + requested > limit:
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
        gateway_customer_id=subscription.gateway_customer_id,
        gateway_subscription_id=subscription.gateway_subscription_id,
        gateway_price_id=subscription.gateway_price_id,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=subscription.cancel_at_period_end,
        payment_issue=subscription.status
        in {SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID},
        past_due_grace_ends_at=past_due_grace_ends_at(subscription),
        updated_at=subscription.updated_at,
    )


def _adjustment_to_receipt(adjustment: BillingAdjustmentRecord, notice: str) -> PaymentReceipt:
    kind = "refund" if adjustment.amount_paise < 0 else "charge"
    plan_value = adjustment.metadata.get("plan") if adjustment.metadata else None
    plan = None
    if plan_value:
        try:
            plan = BillingPlanKey(str(plan_value))
        except ValueError:
            plan = None
    return PaymentReceipt(
        id=adjustment.id,
        workspace_id=adjustment.workspace_id,
        kind=kind,
        plan=plan,
        amount_paise=abs(adjustment.amount_paise),
        currency=adjustment.currency.upper(),
        gateway_event_id=adjustment.gateway_event_id,
        gateway_order_id=(adjustment.metadata or {}).get("order_id"),
        reason=adjustment.reason,
        created_at=adjustment.created_at,
        demo_notice=notice or DEMO_NOTICE,
    )


def _audit_log(request: Request, *, action: str, actor: Actor, **fields) -> None:
    audit = getattr(request.app.state, "audit_logger", None)
    entry = {
        "action": action,
        "actor_user_id": str(actor.user_id),
        "actor_workspace_id": str(actor.workspace_id),
        "at": datetime.now(UTC).isoformat(),
        **{key: str(value) for key, value in fields.items() if value is not None},
    }
    if audit is not None and hasattr(audit, "record"):
        audit.record(entry)
    else:  # pragma: no cover - fallback path
        import logging

        logging.getLogger("motionly.audit").info("audit %s", entry)
