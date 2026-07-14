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
    price_cents: int
    currency: str = "usd"
    interval: BillingInterval = BillingInterval.MONTH
    stripe_price_env: str | None = None
    entitlements: PlanEntitlements
    features: list[str]


class SubscriptionSummary(BaseModel):
    workspace_id: UUID
    plan: BillingPlanKey
    status: SubscriptionStatus
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    stripe_price_id: str | None = None
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
    renders_limit: int
    remaining_renders: int
    percent_used: int = Field(ge=0, le=100)


class BillingStatusResponse(BaseModel):
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


class CustomerPortalResponse(BaseModel):
    portal_url: str


class StripeWebhookResponse(BaseModel):
    received: bool
    duplicate: bool = False


class UsageLedgerEntryResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    event_type: UsageEventType
    quantity: int
    source_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
