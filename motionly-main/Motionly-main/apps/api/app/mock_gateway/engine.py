"""Payment engine for the mock payment gateway.

Deterministic UPI and card flows that never move money. The engine classifies
test credentials, mutates store rows and emits signed webhooks. Card PANs are
classified and then discarded — only the brand + last four reach a row.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    CardDetails,
    OrderResource,
    RefundResource,
    SubscriptionResource,
    WebhookEnvelope,
)
from .store import GatewayStore, OrderRow, SubscriptionRow
from .webhooks import WebhookDeliverer, new_event_id

DEMO_NOTE = "Demo payment — no money is charged"

# UPI shortcuts / VPAs.
UPI_SUCCESS = "success@upi"
UPI_FAILURE = "failure@upi"

# Test card numbers (never stored).
CARD_SUCCESS = "4111111111111111"
CARD_DECLINE = "4000000000000002"
CARD_OTP = "4000000000003220"
OTP_CODE = "123456"


class PaymentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _digits(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


def classify_card(raw_number: str) -> CardDetails:
    """Return the brand + last four for a card number, discarding the PAN."""
    digits = _digits(raw_number)
    if digits.startswith("4"):
        brand = "visa"
    elif digits[:2] in {"51", "52", "53", "54", "55"} or digits[:2] == "22":
        brand = "mastercard"
    elif digits[:4] in {"6011"} or digits.startswith("65"):
        brand = "discover"
    else:
        brand = "unknown"
    return CardDetails(brand=brand, last4=digits[-4:])


@dataclass
class CardOutcome:
    kind: str  # "success" | "decline" | "otp_required"


def classify_card_outcome(raw_number: str) -> CardOutcome:
    digits = _digits(raw_number)
    if digits == CARD_DECLINE:
        return CardOutcome("decline")
    if digits == CARD_OTP:
        return CardOutcome("otp_required")
    return CardOutcome("success")


def _order_resource(row: OrderRow) -> OrderResource:
    card = (
        CardDetails(brand=row.card_brand, last4=row.card_last4)
        if row.card_brand and row.card_last4
        else None
    )
    return OrderResource(
        id=row.id,
        status=row.status,
        amount_paise=row.amount_paise,
        amount_refunded_paise=row.amount_refunded_paise,
        currency=row.currency,
        method=row.method,
        receipt=row.receipt,
        card=card,
        notes=row.notes,
        created_at=row.created_at,
    )


def _subscription_resource(row: SubscriptionRow) -> SubscriptionResource:
    card = (
        CardDetails(brand=row.card_brand, last4=row.card_last4)
        if row.card_brand and row.card_last4
        else None
    )
    return SubscriptionResource(
        id=row.id,
        status=row.status,
        amount_paise=row.amount_paise,
        currency=row.currency,
        total_cycles=row.total_cycles,
        completed_cycles=row.completed_cycles,
        card=card,
        notes=row.notes,
        created_at=row.created_at,
    )


class PaymentEngine:
    def __init__(self, store: GatewayStore, deliverer: WebhookDeliverer) -> None:
        self.store = store
        self.deliverer = deliverer

    # -- webhook helpers -----------------------------------------------------
    async def _emit(self, event: str, **payload) -> WebhookEnvelope:
        envelope = WebhookEnvelope(
            id=new_event_id(),
            event=event,
            created_at=_iso_now(),
            note=DEMO_NOTE,
            **payload,
        )
        self.store.record_webhook_event(envelope.id, envelope.event)
        await self.deliverer.deliver(envelope)
        return envelope

    # -- UPI -----------------------------------------------------------------
    async def pay_order_upi(self, row: OrderRow, action: str, vpa: str | None = None) -> OrderRow:
        row.method = "upi"
        if vpa == UPI_SUCCESS or action == "approve":
            return await self._capture_order(row)
        if vpa == UPI_FAILURE or action in {"decline", "timeout"}:
            reason = "upi_timeout" if action == "timeout" else "upi_declined"
            return await self._fail_order(row, reason)
        return await self._capture_order(row)

    # -- Cards ---------------------------------------------------------------
    async def pay_order_card(self, row: OrderRow, raw_number: str) -> OrderRow:
        row.method = "card"
        card = classify_card(raw_number)
        row.card_brand = card.brand
        row.card_last4 = card.last4
        outcome = classify_card_outcome(raw_number)
        if outcome.kind == "decline":
            return await self._fail_order(row, "card_declined")
        if outcome.kind == "otp_required":
            raise PaymentError("otp_required", "This card needs an OTP step")
        return await self._capture_order(row)

    async def confirm_card_otp(self, row: OrderRow, otp: str) -> OrderRow:
        if otp != OTP_CODE:
            return await self._fail_order(row, "otp_incorrect")
        return await self._capture_order(row)

    # -- order transitions ---------------------------------------------------
    async def _capture_order(self, row: OrderRow) -> OrderRow:
        row.status = "captured"
        self.store.save_order(row)
        await self._emit("payment.captured", order=_order_resource(row))
        return row

    async def _fail_order(self, row: OrderRow, reason: str) -> OrderRow:
        row.status = "failed"
        row.notes = {**row.notes, "failure_reason": reason}
        self.store.save_order(row)
        await self._emit("payment.failed", order=_order_resource(row))
        return row

    # -- refunds -------------------------------------------------------------
    async def refund_order(self, row: OrderRow, amount_paise: int | None) -> RefundResource:
        if row.status not in {"captured", "partially_refunded"}:
            raise PaymentError("refund_not_allowed", "Only captured orders can be refunded")
        remaining = row.amount_paise - row.amount_refunded_paise
        amount = amount_paise if amount_paise is not None else remaining
        if amount <= 0 or amount > remaining:
            raise PaymentError("invalid_refund_amount", "Refund exceeds the captured amount")
        refund = self.store.create_refund(order_id=row.id, amount_paise=amount)
        row.amount_refunded_paise += amount
        row.status = (
            "refunded" if row.amount_refunded_paise >= row.amount_paise else "partially_refunded"
        )
        self.store.save_order(row)
        resource = RefundResource(
            id=refund.id,
            order_id=row.id,
            amount_paise=amount,
            created_at=refund.created_at,
        )
        await self._emit(
            "payment.refunded", order=_order_resource(row), refund=resource
        )
        return resource

    # -- subscriptions -------------------------------------------------------
    async def activate_subscription(
        self, row: SubscriptionRow, raw_number: str | None
    ) -> SubscriptionRow:
        if raw_number:
            card = classify_card(raw_number)
            row.card_brand = card.brand
            row.card_last4 = card.last4
        self.store.save_subscription(row)
        await self._emit("subscription.created", subscription=_subscription_resource(row))
        return row

    async def run_renewal(self, row: SubscriptionRow) -> bool:
        """Charge one subscription cycle. Returns True if charged."""
        if row.status != "active":
            return False
        if row.fail_next_renewal:
            row.fail_next_renewal = False
            row.status = "past_due"
            self.store.save_subscription(row)
            await self._emit(
                "subscription.charge_failed", subscription=_subscription_resource(row)
            )
            return False
        row.completed_cycles += 1
        order = self.store.create_order(
            amount_paise=row.amount_paise,
            currency=row.currency,
            receipt=None,
            notes={"subscription_id": row.id},
        )
        order.method = "card" if row.card_brand else "upi"
        order.card_brand = row.card_brand
        order.card_last4 = row.card_last4
        order.status = "captured"
        self.store.save_order(order)
        if row.completed_cycles >= row.total_cycles:
            row.status = "cancelled"
        self.store.save_subscription(row)
        await self._emit(
            "subscription.charged",
            subscription=_subscription_resource(row),
            order=_order_resource(order),
        )
        return True

    async def cancel_subscription(self, row: SubscriptionRow) -> SubscriptionRow:
        row.status = "cancelled"
        self.store.save_subscription(row)
        await self._emit("subscription.cancelled", subscription=_subscription_resource(row))
        return row


def _iso_now() -> str:
    from .store import _now_iso

    return _now_iso()
