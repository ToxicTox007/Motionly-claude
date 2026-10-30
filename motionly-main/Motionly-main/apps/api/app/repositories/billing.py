import logging
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
from app.services.supabase import SupabaseRestClient

logger = logging.getLogger(__name__)

SUBSCRIPTION_COLUMNS = (
    "workspace_id,plan,status,gateway_customer_id,gateway_subscription_id,gateway_price_id,"
    "current_period_start,current_period_end,cancel_at_period_end,updated_at,"
    "last_gateway_event_at"
)
USAGE_LEDGER_COLUMNS = "id,workspace_id,event_type,quantity,source_id,metadata,created_at"
BILLING_ADJUSTMENT_COLUMNS = (
    "id,workspace_id,gateway_event_id,adjustment_type,amount_paise,currency,reason,"
    "metadata,created_at"
)


@dataclass
class SubscriptionRecord:
    workspace_id: UUID
    plan: BillingPlanKey
    status: SubscriptionStatus
    gateway_customer_id: str | None = None
    gateway_subscription_id: str | None = None
    gateway_price_id: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Creation time of the newest payment gateway event applied to the row
    # (database audit DB-12): an older event delivered late is not applied.
    last_gateway_event_at: datetime | None = None


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
class GatewayWebhookEventRecord:
    id: str
    event_type: str
    processed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class BillingAdjustmentRecord:
    """A money-movement record: a charge (positive) or refund (negative).

    ``amount_paise`` is in INR paise. ``gateway_event_id`` is the webhook event
    that produced it (unique → idempotent). ``adjustment_type`` is one of
    ``charge`` / ``refund`` / ``credit`` / ``chargeback``.
    """

    id: UUID
    workspace_id: UUID
    gateway_event_id: str
    adjustment_type: str
    amount_paise: int
    currency: str = "inr"
    reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryBillingRepository:
    def __init__(self) -> None:
        self.subscriptions: dict[UUID, SubscriptionRecord] = {}
        self.usage_ledger: dict[UUID, UsageLedgerEntry] = {}
        self.gateway_events: dict[str, GatewayWebhookEventRecord] = {}
        self._gateway_claims: set[str] = set()
        self.billing_adjustments: dict[str, BillingAdjustmentRecord] = {}

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
        gateway_customer_id: str | None = None,
        gateway_subscription_id: str | None = None,
        gateway_price_id: str | None = None,
        current_period_start: datetime | None = None,
        current_period_end: datetime | None = None,
        cancel_at_period_end: bool = False,
        last_gateway_event_at: datetime | None = None,
    ) -> SubscriptionRecord | None:
        """Create or update the workspace's subscription.

        A write carrying ``last_gateway_event_at`` older than the event already
        applied is skipped and returns ``None``, as the database trigger
        subscriptions_skip_stale_gateway_event does (database audit DB-12).
        """
        existing = self.get_subscription(workspace_id=workspace_id)
        if (
            last_gateway_event_at is not None
            and existing.last_gateway_event_at is not None
            and last_gateway_event_at < existing.last_gateway_event_at
        ):
            return None
        record = SubscriptionRecord(
            workspace_id=workspace_id,
            plan=plan,
            status=status,
            gateway_customer_id=gateway_customer_id or existing.gateway_customer_id,
            gateway_subscription_id=gateway_subscription_id or existing.gateway_subscription_id,
            gateway_price_id=gateway_price_id or existing.gateway_price_id,
            current_period_start=current_period_start or existing.current_period_start,
            current_period_end=current_period_end or existing.current_period_end,
            cancel_at_period_end=cancel_at_period_end,
            last_gateway_event_at=last_gateway_event_at or existing.last_gateway_event_at,
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
        if source_id is not None:
            existing = next(
                (
                    entry
                    for entry in self.usage_ledger.values()
                    if entry.workspace_id == workspace_id
                    and entry.event_type == event_type
                    and entry.source_id == source_id
                ),
                None,
            )
            if existing:
                return existing
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

    def has_processed_gateway_event(self, event_id: str) -> bool:
        return event_id in self.gateway_events

    def claim_gateway_event(self, *, event_id: str, event_type: str) -> str:
        """'claimed', 'duplicate' or 'in_progress' (audit WJ-23)."""
        if event_id in self.gateway_events:
            return "duplicate"
        if event_id in self._gateway_claims:
            return "in_progress"
        self._gateway_claims.add(event_id)
        return "claimed"

    def finish_gateway_event(self, *, event_id: str, event_type: str, succeeded: bool) -> bool:
        self._gateway_claims.discard(event_id)
        if succeeded:
            self.record_gateway_event(event_id=event_id, event_type=event_type)
        return True

    def record_gateway_event(
        self, *, event_id: str, event_type: str
    ) -> GatewayWebhookEventRecord:
        if event_id in self.gateway_events:
            return self.gateway_events[event_id]
        event = GatewayWebhookEventRecord(id=event_id, event_type=event_type)
        self.gateway_events[event.id] = event
        return event

    def workspace_for_gateway_subscription(
        self,
        *,
        gateway_subscription_id: str | None = None,
        gateway_customer_id: str | None = None,
    ) -> UUID | None:
        for subscription in self.subscriptions.values():
            if (
                gateway_subscription_id
                and subscription.gateway_subscription_id == gateway_subscription_id
            ):
                return subscription.workspace_id
            if gateway_customer_id and subscription.gateway_customer_id == gateway_customer_id:
                return subscription.workspace_id
        return None

    def record_billing_adjustment(
        self,
        *,
        workspace_id: UUID,
        gateway_event_id: str,
        adjustment_type: str,
        amount_paise: int,
        currency: str = "inr",
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> BillingAdjustmentRecord:
        existing = self.billing_adjustments.get(gateway_event_id)
        if existing:
            return existing
        adjustment = BillingAdjustmentRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            gateway_event_id=gateway_event_id,
            adjustment_type=adjustment_type,
            amount_paise=amount_paise,
            currency=currency,
            reason=reason,
            metadata=metadata or {},
        )
        self.billing_adjustments[gateway_event_id] = adjustment
        return adjustment

    def list_billing_adjustments(self, *, workspace_id: UUID) -> list[BillingAdjustmentRecord]:
        rows = [
            adjustment
            for adjustment in self.billing_adjustments.values()
            if adjustment.workspace_id == workspace_id
        ]
        return sorted(rows, key=lambda row: row.created_at, reverse=True)


class SupabaseBillingRepository(InMemoryBillingRepository):
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__()
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="billing",
            metrics_registry=metrics_registry,
        )

    def get_subscription(self, *, workspace_id: UUID) -> SubscriptionRecord:
        response = self.client.request(
            "GET",
            "/subscriptions",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": SUBSCRIPTION_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            return super().get_subscription(workspace_id=workspace_id)
        return self._subscription_from_row(rows[0])

    async def get_subscription_async(self, *, workspace_id: UUID) -> SubscriptionRecord:
        response = await self.client.async_request(
            "GET",
            "/subscriptions",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": SUBSCRIPTION_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            return super().get_subscription(workspace_id=workspace_id)
        return self._subscription_from_row(rows[0])

    def upsert_subscription(self, **values: object) -> SubscriptionRecord | None:
        response = self.client.request(
            "POST",
            "/subscriptions",
            headers={"prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "workspace_id"},
            json=self._json_safe(values),
        )
        self._raise_for_supabase(response)
        return self._upserted_subscription(response.json())

    async def upsert_subscription_async(self, **values: object) -> SubscriptionRecord | None:
        response = await self.client.async_request(
            "POST",
            "/subscriptions",
            headers={"prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "workspace_id"},
            json=self._json_safe(values),
        )
        self._raise_for_supabase(response)
        return self._upserted_subscription(response.json())

    @classmethod
    def _upserted_subscription(cls, rows: object) -> SubscriptionRecord | None:
        """The upserted row, or None when the database skipped a stale gateway
        event (no row comes back; trigger subscriptions_skip_stale_gateway_event)."""
        if isinstance(rows, list) and rows:
            return cls._subscription_from_row(rows[0])
        return None

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
        response = self.client.request(
            "POST",
            "/rpc/motionly_sum_usage_ledger",
            json=_usage_sum_payload(workspace_id, event_type, period_start, period_end),
        )
        self._raise_for_supabase(response)
        return _usage_sum(response.json())

    async def count_usage_async(
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
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_sum_usage_ledger",
            json=_usage_sum_payload(workspace_id, event_type, period_start, period_end),
        )
        self._raise_for_supabase(response)
        return _usage_sum(response.json())

    def record_usage(self, **values: object) -> UsageLedgerEntry:
        event_type_value = getattr(values["event_type"], "value", values["event_type"])
        response = self.client.request(
            "POST",
            "/usage_ledger",
            headers={"prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "workspace_id,event_type,source_id"},
            json=self._json_safe(values),
        )
        self._raise_for_supabase(response)
        if not response.json():
            existing = self.client.request(
                "GET",
                "/usage_ledger",
                params={
                    "workspace_id": f"eq.{values['workspace_id']}",
                    "event_type": f"eq.{event_type_value}",
                    "source_id": f"eq.{values['source_id']}",
                    "select": USAGE_LEDGER_COLUMNS,
                    "limit": "1",
                },
            )
            self._raise_for_supabase(existing)
            return self._usage_from_row(existing.json()[0])
        return self._usage_from_row(response.json()[0])

    async def record_usage_async(self, **values: object) -> UsageLedgerEntry:
        event_type_value = getattr(values["event_type"], "value", values["event_type"])
        response = await self.client.async_request(
            "POST",
            "/usage_ledger",
            headers={"prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "workspace_id,event_type,source_id"},
            json=self._json_safe(values),
        )
        self._raise_for_supabase(response)
        if not response.json():
            existing = await self.client.async_request(
                "GET",
                "/usage_ledger",
                params={
                    "workspace_id": f"eq.{values['workspace_id']}",
                    "event_type": f"eq.{event_type_value}",
                    "source_id": f"eq.{values['source_id']}",
                    "select": USAGE_LEDGER_COLUMNS,
                    "limit": "1",
                },
            )
            self._raise_for_supabase(existing)
            return self._usage_from_row(existing.json()[0])
        return self._usage_from_row(response.json()[0])

    def has_processed_gateway_event(self, event_id: str) -> bool:
        response = self.client.request(
            "GET",
            "/gateway_webhook_events",
            params={"id": f"eq.{event_id}", "select": "id", "limit": "1"},
        )
        self._raise_for_supabase(response)
        return bool(response.json())

    def claim_gateway_event(self, *, event_id: str, event_type: str) -> str:
        """Claim a webhook event before applying it (audit WJ-23).

        'claimed' (apply it), 'duplicate' (already applied) or 'in_progress'
        (another delivery is applying it right now).
        """
        response = self.client.request(
            "POST",
            "/rpc/motionly_claim_gateway_webhook_event",
            json={"p_event_id": event_id, "p_event_type": event_type},
        )
        self._raise_for_supabase(response)
        return _claim_result(response.json())

    async def claim_gateway_event_async(self, *, event_id: str, event_type: str) -> str:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_claim_gateway_webhook_event",
            json={"p_event_id": event_id, "p_event_type": event_type},
        )
        self._raise_for_supabase(response)
        return _claim_result(response.json())

    def finish_gateway_event(self, *, event_id: str, event_type: str, succeeded: bool) -> bool:
        response = self.client.request(
            "POST",
            "/rpc/motionly_finish_gateway_webhook_event",
            json={"p_event_id": event_id, "p_succeeded": succeeded},
        )
        self._raise_for_supabase(response)
        return bool(response.json())

    async def finish_gateway_event_async(
        self, *, event_id: str, event_type: str, succeeded: bool
    ) -> bool:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_finish_gateway_webhook_event",
            json={"p_event_id": event_id, "p_succeeded": succeeded},
        )
        self._raise_for_supabase(response)
        return bool(response.json())

    async def has_processed_gateway_event_async(self, event_id: str) -> bool:
        response = await self.client.async_request(
            "GET",
            "/gateway_webhook_events",
            params={"id": f"eq.{event_id}", "select": "id", "limit": "1"},
        )
        self._raise_for_supabase(response)
        return bool(response.json())

    def record_gateway_event(
        self, *, event_id: str, event_type: str
    ) -> GatewayWebhookEventRecord:
        response = self.client.request(
            "POST",
            "/gateway_webhook_events",
            headers={"prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "id"},
            json={"id": event_id, "event_type": event_type},
        )
        self._raise_for_supabase(response)
        if not response.json():
            return GatewayWebhookEventRecord(id=event_id, event_type=event_type)
        return GatewayWebhookEventRecord(
            id=response.json()[0]["id"],
            event_type=response.json()[0]["event_type"],
            processed_at=self._parse_datetime(response.json()[0]["processed_at"]),
        )

    async def record_gateway_event_async(
        self,
        *,
        event_id: str,
        event_type: str,
    ) -> GatewayWebhookEventRecord:
        response = await self.client.async_request(
            "POST",
            "/gateway_webhook_events",
            headers={"prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "id"},
            json={"id": event_id, "event_type": event_type},
        )
        self._raise_for_supabase(response)
        if not response.json():
            return GatewayWebhookEventRecord(id=event_id, event_type=event_type)
        return GatewayWebhookEventRecord(
            id=response.json()[0]["id"],
            event_type=response.json()[0]["event_type"],
            processed_at=self._parse_datetime(response.json()[0]["processed_at"]),
        )

    def workspace_for_gateway_subscription(
        self,
        *,
        gateway_subscription_id: str | None = None,
        gateway_customer_id: str | None = None,
    ) -> UUID | None:
        filters: dict[str, str] = {"select": "workspace_id", "limit": "1"}
        if gateway_subscription_id:
            filters["gateway_subscription_id"] = f"eq.{gateway_subscription_id}"
        elif gateway_customer_id:
            filters["gateway_customer_id"] = f"eq.{gateway_customer_id}"
        else:
            return None
        response = self.client.request("GET", "/subscriptions", params=filters)
        self._raise_for_supabase(response)
        rows = response.json()
        return UUID(str(rows[0]["workspace_id"])) if rows else None

    async def workspace_for_gateway_subscription_async(
        self,
        *,
        gateway_subscription_id: str | None = None,
        gateway_customer_id: str | None = None,
    ) -> UUID | None:
        filters: dict[str, str] = {"select": "workspace_id", "limit": "1"}
        if gateway_subscription_id:
            filters["gateway_subscription_id"] = f"eq.{gateway_subscription_id}"
        elif gateway_customer_id:
            filters["gateway_customer_id"] = f"eq.{gateway_customer_id}"
        else:
            return None
        response = await self.client.async_request("GET", "/subscriptions", params=filters)
        self._raise_for_supabase(response)
        rows = response.json()
        return UUID(str(rows[0]["workspace_id"])) if rows else None

    def record_billing_adjustment(self, **values: object) -> BillingAdjustmentRecord:
        response = self.client.request(
            "POST",
            "/billing_adjustments",
            headers={"prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "gateway_event_id"},
            json=self._json_safe(values),
        )
        self._raise_for_supabase(response)
        if not response.json():
            existing = self.client.request(
                "GET",
                "/billing_adjustments",
                params={
                    "gateway_event_id": f"eq.{values['gateway_event_id']}",
                    "select": BILLING_ADJUSTMENT_COLUMNS,
                    "limit": "1",
                },
            )
            self._raise_for_supabase(existing)
            return self._adjustment_from_row(existing.json()[0])
        return self._adjustment_from_row(response.json()[0])

    async def record_billing_adjustment_async(self, **values: object) -> BillingAdjustmentRecord:
        response = await self.client.async_request(
            "POST",
            "/billing_adjustments",
            headers={"prefer": "resolution=ignore-duplicates,return=representation"},
            params={"on_conflict": "gateway_event_id"},
            json=self._json_safe(values),
        )
        self._raise_for_supabase(response)
        if not response.json():
            existing = await self.client.async_request(
                "GET",
                "/billing_adjustments",
                params={
                    "gateway_event_id": f"eq.{values['gateway_event_id']}",
                    "select": BILLING_ADJUSTMENT_COLUMNS,
                    "limit": "1",
                },
            )
            self._raise_for_supabase(existing)
            return self._adjustment_from_row(existing.json()[0])
        return self._adjustment_from_row(response.json()[0])

    def list_billing_adjustments(self, *, workspace_id: UUID) -> list[BillingAdjustmentRecord]:
        response = self.client.request(
            "GET",
            "/billing_adjustments",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": BILLING_ADJUSTMENT_COLUMNS,
                "order": "created_at.desc",
            },
        )
        self._raise_for_supabase(response)
        return [self._adjustment_from_row(row) for row in response.json()]

    async def list_billing_adjustments_async(
        self, *, workspace_id: UUID
    ) -> list[BillingAdjustmentRecord]:
        response = await self.client.async_request(
            "GET",
            "/billing_adjustments",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": BILLING_ADJUSTMENT_COLUMNS,
                "order": "created_at.desc",
            },
        )
        self._raise_for_supabase(response)
        return [self._adjustment_from_row(row) for row in response.json()]

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
            gateway_customer_id=row.get("gateway_customer_id")
            and str(row["gateway_customer_id"]),
            gateway_subscription_id=row.get("gateway_subscription_id")
            and str(row["gateway_subscription_id"]),
            gateway_price_id=row.get("gateway_price_id") and str(row["gateway_price_id"]),
            current_period_start=SupabaseBillingRepository._parse_optional_datetime(
                row.get("current_period_start")
            ),
            current_period_end=SupabaseBillingRepository._parse_optional_datetime(
                row.get("current_period_end")
            ),
            cancel_at_period_end=bool(row.get("cancel_at_period_end", False)),
            updated_at=SupabaseBillingRepository._parse_optional_datetime(row.get("updated_at"))
            or datetime.now(UTC),
            last_gateway_event_at=SupabaseBillingRepository._parse_optional_datetime(
                row.get("last_gateway_event_at")
            ),
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
    def _adjustment_from_row(row: dict[str, object]) -> BillingAdjustmentRecord:
        return BillingAdjustmentRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            gateway_event_id=str(row["gateway_event_id"]),
            adjustment_type=str(row["adjustment_type"]),
            amount_paise=int(row["amount_paise"]),
            currency=str(row.get("currency") or "inr"),
            reason=row.get("reason") and str(row["reason"]),
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
        # The PostgREST body names tables, constraints and values; log it for
        # operators, never return it to the caller (database audit DB-19).
        logger.warning(
            "supabase_billing_request_failed",
            extra={"status_code": response.status_code, "response": response.text[:1000]},
        )
        raise ApiError(502, "supabase_request_failed", "Billing data could not be saved.")


def _usage_sum_payload(
    workspace_id: UUID,
    event_type: UsageEventType | str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, str]:
    return {
        "p_workspace_id": str(workspace_id),
        "p_event_type": str(getattr(event_type, "value", event_type)),
        "p_period_start": period_start.isoformat(),
        "p_period_end": period_end.isoformat(),
    }


def _usage_sum(value: object) -> int:
    """motionly_sum_usage_ledger's result: a bare bigint, or wrapped by name."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("motionly_sum_usage_ledger")
    if isinstance(value, bool) or value is None:
        raise ApiError(502, "billing_persistence_failed", "Usage could not be read.")
    return int(value)


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


def _claim_result(value: object) -> str:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("motionly_claim_gateway_webhook_event")
    if value not in {"claimed", "duplicate", "in_progress"}:
        raise ApiError(502, "billing_persistence_failed", "Webhook claim returned no state.")
    return str(value)
