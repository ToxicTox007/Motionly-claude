from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.billing import (
    BillingPlanKey,
    SubscriptionStatus,
    UsageEventType,
    UsageLedgerEntryResponse,
)
from app.services.billing import monthly_period


@dataclass
class SubscriptionRecord:
    workspace_id: UUID
    plan: BillingPlanKey
    status: SubscriptionStatus
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    stripe_price_id: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class UsageLedgerEntry:
    id: UUID
    workspace_id: UUID
    event_type: UsageEventType
    quantity: int
    source_id: UUID | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> UsageLedgerEntryResponse:
        return UsageLedgerEntryResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            event_type=self.event_type,
            quantity=self.quantity,
            source_id=self.source_id,
            metadata=self.metadata,
            created_at=self.created_at,
        )


@dataclass
class StripeWebhookEventRecord:
    id: str
    event_type: str
    processed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryBillingRepository:
    def __init__(self) -> None:
        self.subscriptions: dict[UUID, SubscriptionRecord] = {}
        self.usage_ledger: dict[UUID, UsageLedgerEntry] = {}
        self.stripe_events: dict[str, StripeWebhookEventRecord] = {}

    def get_subscription(self, *, workspace_id: UUID) -> SubscriptionRecord:
        return self.subscriptions.get(workspace_id) or SubscriptionRecord(
            workspace_id=workspace_id,
            plan=BillingPlanKey.FREE,
            status=SubscriptionStatus.NONE,
        )

    def upsert_subscription(
        self,
        *,
        workspace_id: UUID,
        plan: BillingPlanKey,
        status: SubscriptionStatus,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
        stripe_price_id: str | None = None,
        current_period_start: datetime | None = None,
        current_period_end: datetime | None = None,
        cancel_at_period_end: bool = False,
    ) -> SubscriptionRecord:
        existing = self.get_subscription(workspace_id=workspace_id)
        record = SubscriptionRecord(
            workspace_id=workspace_id,
            plan=plan,
            status=status,
            stripe_customer_id=stripe_customer_id or existing.stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id or existing.stripe_subscription_id,
            stripe_price_id=stripe_price_id or existing.stripe_price_id,
            current_period_start=current_period_start or existing.current_period_start,
            current_period_end=current_period_end or existing.current_period_end,
            cancel_at_period_end=cancel_at_period_end,
        )
        self.subscriptions[workspace_id] = record
        return record

    def count_usage(
        self,
        *,
        workspace_id: UUID,
        event_type: UsageEventType,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> int:
        period_start, period_end = (
            (period_start, period_end) if period_start and period_end else monthly_period()
        )
        return sum(
            entry.quantity
            for entry in self.usage_ledger.values()
            if entry.workspace_id == workspace_id
            and entry.event_type == event_type
            and period_start <= entry.created_at < period_end
        )

    def record_usage(
        self,
        *,
        workspace_id: UUID,
        event_type: UsageEventType,
        quantity: int = 1,
        source_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> UsageLedgerEntry:
        entry = UsageLedgerEntry(
            id=uuid4(),
            workspace_id=workspace_id,
            event_type=event_type,
            quantity=quantity,
            source_id=source_id,
            metadata=metadata or {},
        )
        self.usage_ledger[entry.id] = entry
        return entry

    def has_processed_stripe_event(self, event_id: str) -> bool:
        return event_id in self.stripe_events

    def record_stripe_event(self, *, event_id: str, event_type: str) -> StripeWebhookEventRecord:
        if event_id in self.stripe_events:
            return self.stripe_events[event_id]
        event = StripeWebhookEventRecord(id=event_id, event_type=event_type)
        self.stripe_events[event.id] = event
        return event


class SupabaseBillingRepository(InMemoryBillingRepository):
    def __init__(self, *, supabase_url: str, service_role_key: str) -> None:
        super().__init__()
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def get_subscription(self, *, workspace_id: UUID) -> SubscriptionRecord:
        response = httpx.get(
            f"{self.rest_url}/subscriptions",
            headers=self.headers,
            params={"workspace_id": f"eq.{workspace_id}", "select": "*", "limit": "1"},
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            return super().get_subscription(workspace_id=workspace_id)
        return self._subscription_from_row(rows[0])

    def upsert_subscription(self, **values: object) -> SubscriptionRecord:
        response = httpx.post(
            f"{self.rest_url}/subscriptions",
            headers={**self.headers, "prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "workspace_id"},
            json=self._json_safe(values),
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._subscription_from_row(response.json()[0])

    def count_usage(
        self,
        *,
        workspace_id: UUID,
        event_type: UsageEventType,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> int:
        period_start, period_end = (
            (period_start, period_end) if period_start and period_end else monthly_period()
        )
        response = httpx.get(
            f"{self.rest_url}/usage_ledger",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "event_type": f"eq.{event_type.value}",
                "created_at": [f"gte.{period_start.isoformat()}", f"lt.{period_end.isoformat()}"],
                "select": "quantity",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return sum(int(row["quantity"]) for row in response.json())

    def record_usage(self, **values: object) -> UsageLedgerEntry:
        response = httpx.post(
            f"{self.rest_url}/usage_ledger",
            headers=self.headers,
            json=self._json_safe(values),
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._usage_from_row(response.json()[0])

    def has_processed_stripe_event(self, event_id: str) -> bool:
        response = httpx.get(
            f"{self.rest_url}/stripe_webhook_events",
            headers=self.headers,
            params={"id": f"eq.{event_id}", "select": "id", "limit": "1"},
            timeout=10,
        )
        self._raise_for_supabase(response)
        return bool(response.json())

    def record_stripe_event(self, *, event_id: str, event_type: str) -> StripeWebhookEventRecord:
        response = httpx.post(
            f"{self.rest_url}/stripe_webhook_events",
            headers={
                **self.headers,
                "prefer": "resolution=ignore-duplicates,return=representation",
            },
            params={"on_conflict": "id"},
            json={"id": event_id, "event_type": event_type},
            timeout=10,
        )
        self._raise_for_supabase(response)
        if not response.json():
            return StripeWebhookEventRecord(id=event_id, event_type=event_type)
        return StripeWebhookEventRecord(
            id=response.json()[0]["id"],
            event_type=response.json()[0]["event_type"],
            processed_at=self._parse_datetime(response.json()[0]["processed_at"]),
        )

    @staticmethod
    def _subscription_from_row(row: dict[str, object]) -> SubscriptionRecord:
        raw_plan = row.get("plan") or row.get("plan_key") or BillingPlanKey.FREE.value
        plan = _coerce_plan(raw_plan)
        raw_status = row.get("status") or SubscriptionStatus.NONE.value
        status = _coerce_status(raw_status)
        return SubscriptionRecord(
            workspace_id=UUID(str(row["workspace_id"])),
            plan=plan,
            status=status,
            stripe_customer_id=row.get("stripe_customer_id") and str(row["stripe_customer_id"]),
            stripe_subscription_id=row.get("stripe_subscription_id")
            and str(row["stripe_subscription_id"]),
            stripe_price_id=row.get("stripe_price_id") and str(row["stripe_price_id"]),
            current_period_start=SupabaseBillingRepository._parse_optional_datetime(
                row.get("current_period_start")
            ),
            current_period_end=SupabaseBillingRepository._parse_optional_datetime(
                row.get("current_period_end")
            ),
            cancel_at_period_end=bool(row.get("cancel_at_period_end", False)),
            updated_at=SupabaseBillingRepository._parse_optional_datetime(row.get("updated_at"))
            or datetime.now(UTC),
        )

    @staticmethod
    def _usage_from_row(row: dict[str, object]) -> UsageLedgerEntry:
        return UsageLedgerEntry(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            event_type=UsageEventType(str(row["event_type"])),
            quantity=int(row["quantity"]),
            source_id=row.get("source_id") and UUID(str(row["source_id"])),
            metadata=dict(row.get("metadata") or {}),
            created_at=SupabaseBillingRepository._parse_datetime(str(row["created_at"])),
        )

    @staticmethod
    def _json_safe(values: dict[str, object]) -> dict[str, object]:
        safe: dict[str, object] = {}
        for key, value in values.items():
            if isinstance(value, UUID):
                safe[key] = str(value)
            elif isinstance(value, datetime):
                safe[key] = value.isoformat()
            elif hasattr(value, "value"):
                safe[key] = value.value
            else:
                safe[key] = value
        return safe

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _parse_optional_datetime(value: object) -> datetime | None:
        if not value:
            return None
        return SupabaseBillingRepository._parse_datetime(str(value))

    @staticmethod
    def _raise_for_supabase(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", response.text)


def _coerce_plan(value: object) -> BillingPlanKey:
    normalized = str(value or BillingPlanKey.FREE.value).lower()
    if normalized in {"starter", "free"}:
        return BillingPlanKey.FREE
    try:
        return BillingPlanKey(normalized)
    except ValueError:
        return BillingPlanKey.FREE


def _coerce_status(value: object) -> SubscriptionStatus:
    normalized = str(value or SubscriptionStatus.NONE.value).lower()
    try:
        return SubscriptionStatus(normalized)
    except ValueError:
        return SubscriptionStatus.NONE
