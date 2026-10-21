"""Pydantic models for the mock payment gateway.

These describe the server-to-server API and, importantly, the webhook envelope
the Task 23 receiver must parse. Import :class:`WebhookEnvelope` rather than
re-deriving the shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Currency = Literal["INR"]
PaymentMethod = Literal["upi", "card"]

OrderStatus = Literal["created", "captured", "failed", "refunded", "partially_refunded"]
SubscriptionStatus = Literal["active", "cancelled", "past_due"]

# Webhook event types the gateway emits.
EventType = Literal[
    "payment.captured",
    "payment.failed",
    "payment.refunded",
    "subscription.created",
    "subscription.charged",
    "subscription.charge_failed",
    "subscription.cancelled",
]


class CreateOrderRequest(BaseModel):
    amount_paise: int = Field(gt=0, description="Order amount in paise (INR x 100).")
    currency: Currency = "INR"
    receipt: str | None = Field(default=None, max_length=120)
    notes: dict[str, str] = Field(default_factory=dict)


class CreateSubscriptionRequest(BaseModel):
    amount_paise: int = Field(gt=0, description="Per-period amount in paise.")
    currency: Currency = "INR"
    total_cycles: int = Field(default=12, ge=1, le=120)
    notes: dict[str, str] = Field(default_factory=dict)


class RefundRequest(BaseModel):
    amount_paise: int | None = Field(
        default=None, gt=0, description="Partial refund amount; full refund when omitted."
    )


class CardDetails(BaseModel):
    """Only the brand and last four of a stored card are ever persisted."""

    brand: str
    last4: str = Field(min_length=4, max_length=4)


class OrderResource(BaseModel):
    id: str
    status: OrderStatus
    amount_paise: int
    amount_refunded_paise: int = 0
    currency: Currency
    method: PaymentMethod | None = None
    receipt: str | None = None
    card: CardDetails | None = None
    notes: dict[str, str] = Field(default_factory=dict)
    created_at: str


class SubscriptionResource(BaseModel):
    id: str
    status: SubscriptionStatus
    amount_paise: int
    currency: Currency
    total_cycles: int
    completed_cycles: int
    card: CardDetails | None = None
    notes: dict[str, str] = Field(default_factory=dict)
    created_at: str


class RefundResource(BaseModel):
    id: str
    order_id: str
    amount_paise: int
    created_at: str


class WebhookEnvelope(BaseModel):
    """The JSON body of every webhook POST (signed; see package docstring)."""

    id: str = Field(description="Unique event id; retries reuse it.")
    event: EventType
    created_at: str
    # Exactly one of these is populated depending on the event.
    order: OrderResource | None = None
    subscription: SubscriptionResource | None = None
    refund: RefundResource | None = None
    note: str = Field(
        default="Demo payment — no money is charged",
        description="Human-readable reminder that no money moved.",
    )
