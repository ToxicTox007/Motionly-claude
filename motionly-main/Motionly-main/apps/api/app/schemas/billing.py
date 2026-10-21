from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BillingPlanKey(StrEnum):
    FREE = "free"
    CREATOR = "creator"
    PRO = "pro"
    AGENCY = "agency"


class BillingMode(StrEnum):
    MANAGED = "managed"
    PERSONAL = "personal"


class SubscriptionStatus(StrEnum):
    NONE = "none"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    INCOMPLETE = "incomplete"


class UsageEventType(StrEnum):
    RENDER_SUCCEEDED = "render_succeeded"
    PREMIUM_ACTION = "premium_action"


class BillingInterval(StrEnum):
    MONTH = "month"


class PlanEntitlements(BaseModel):
    monthly_renders: int
    watermark: bool
    max_resolution: str
    broll: bool
    priority_queue: bool = False
    premium_effects: bool = False
    seats: int = 1
    brand_kits: bool = False
    bulk_uploads: bool = False
    api_access: bool = False


class PlanDefinition(BaseModel):
    key: BillingPlanKey
    name: str
    # Price in INR paise (₹1 = 100 paise). Single source: app/config/plans.json.
    price_paise: int
    currency: str = "INR"
    interval: BillingInterval = BillingInterval.MONTH
    entitlements: PlanEntitlements
    features: list[str]


class PublicPlansResponse(BaseModel):
    """Unauthenticated catalogue of billing plans for the public pricing page."""

    plans: list[PlanDefinition]


class SubscriptionSummary(BaseModel):
    workspace_id: UUID
    plan: BillingPlanKey
    status: SubscriptionStatus
    gateway_customer_id: str | None = None
    gateway_subscription_id: str | None = None
    gateway_price_id: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    payment_issue: bool = False
    updated_at: datetime


class UsageSummary(BaseModel):
    workspace_id: UUID
    period_start: datetime
    period_end: datetime
    renders_used: int
    renders_limit: int | None
    remaining_renders: int | None
    percent_used: int | None = Field(default=None, ge=0, le=100)


class BillingStatusResponse(BaseModel):
    billing_enabled: bool
    billing_mode: BillingMode
    unmetered: bool
    subscription: SubscriptionSummary
    current_plan: PlanDefinition
    plans: list[PlanDefinition]
    usage: UsageSummary
    can_render: bool
    entitlements: PlanEntitlements


class CheckoutSessionRequest(BaseModel):
    plan: BillingPlanKey


class CheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str
    demo_notice: str | None = None


class CancelSubscriptionResponse(BaseModel):
    subscription: "SubscriptionSummary"
    demo_notice: str | None = None


class RetryRenewalResponse(BaseModel):
    subscription: "SubscriptionSummary"
    demo_notice: str | None = None
    checkout_url: str | None = None
    session_id: str | None = None


class GatewayWebhookResponse(BaseModel):
    received: bool
    duplicate: bool = False
    note: str = "Demo payment — no money is charged"


class PaymentReceipt(BaseModel):
    id: UUID
    workspace_id: UUID
    kind: str  # "charge" | "refund"
    plan: BillingPlanKey | None = None
    amount_paise: int
    currency: str = "INR"
    gateway_event_id: str | None = None
    gateway_order_id: str | None = None
    reason: str | None = None
    created_at: datetime
    demo_notice: str = "Demo payment — no money is charged"


class PaymentHistoryResponse(BaseModel):
    receipts: list[PaymentReceipt]


class RefundRequest(BaseModel):
    workspace_id: UUID
    amount_paise: int | None = None
    reason: str | None = None


class RefundResponse(BaseModel):
    receipt: PaymentReceipt


class UsageLedgerEntryResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    event_type: UsageEventType
    quantity: int
    source_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
