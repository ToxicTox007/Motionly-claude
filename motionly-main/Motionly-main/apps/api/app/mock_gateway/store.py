"""Gateway store interface + the in-memory test double.

The mock payment gateway keeps orders, subscriptions, refunds, idempotency keys
and emitted webhook events. Production persists these rows into the private
``mock_gateway`` Postgres schema through PostgREST
(:class:`app.mock_gateway.store_db.PostgrestGatewayStore`); idempotency keys and
webhook event ids carry UNIQUE constraints, so idempotency and duplicate-event
suppression survive a restart.

:class:`InMemoryGatewayStore` is a **test double only** — it is never selected
by any production setting. Tests inject it directly (``create_app`` defaults to
it only outside a configured database). It is registered in
``fixtures/registry.json``.

CRITICAL: only the card *brand* and *last four* are ever stored. The raw PAN is
accepted transiently to classify the test card and then discarded; it is never
written to any row.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


@dataclass
class OrderRow:
    id: str
    amount_paise: int
    currency: str
    status: str = "created"
    amount_refunded_paise: int = 0
    method: str | None = None
    receipt: str | None = None
    card_brand: str | None = None
    card_last4: str | None = None
    notes: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)


@dataclass
class SubscriptionRow:
    id: str
    amount_paise: int
    currency: str
    total_cycles: int
    status: str = "active"
    completed_cycles: int = 0
    card_brand: str | None = None
    card_last4: str | None = None
    notes: dict[str, str] = field(default_factory=dict)
    fail_next_renewal: bool = False
    created_at: str = field(default_factory=_now_iso)


@dataclass
class RefundRow:
    id: str
    order_id: str
    amount_paise: int
    created_at: str = field(default_factory=_now_iso)


@runtime_checkable
class GatewayStore(Protocol):
    """The persistence contract the engine and API depend on.

    Both the in-memory test double and the PostgREST-backed production store
    implement this. The engine mutates a row in place and then calls
    ``save_order``/``save_subscription`` to persist the new state; appends
    (refunds, webhook events, idempotency keys) are written straight through.
    """

    # -- idempotency ---------------------------------------------------------
    def idempotent_id(self, key: str | None) -> str | None: ...
    def remember_idempotency(self, key: str | None, resource_id: str) -> None: ...

    # -- orders --------------------------------------------------------------
    def create_order(
        self, *, amount_paise: int, currency: str, receipt: str | None, notes: dict[str, str]
    ) -> OrderRow: ...
    def get_order(self, order_id: str) -> OrderRow | None: ...
    def save_order(self, row: OrderRow) -> None: ...

    # -- subscriptions -------------------------------------------------------
    def create_subscription(
        self, *, amount_paise: int, currency: str, total_cycles: int, notes: dict[str, str]
    ) -> SubscriptionRow: ...
    def get_subscription(self, subscription_id: str) -> SubscriptionRow | None: ...
    def save_subscription(self, row: SubscriptionRow) -> None: ...
    def active_subscriptions(self) -> list[SubscriptionRow]: ...

    # -- refunds -------------------------------------------------------------
    def create_refund(self, *, order_id: str, amount_paise: int) -> RefundRow: ...

    # -- webhook events (unique event id survives restarts) ------------------
    def record_webhook_event(self, event_id: str, event: str) -> bool:
        """Record an emitted event id. Returns False if it already existed."""
        ...


class InMemoryGatewayStore:
    """In-memory :class:`GatewayStore` — a test double, never used in production."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.orders: dict[str, OrderRow] = {}
        self.subscriptions: dict[str, SubscriptionRow] = {}
        self.refunds: dict[str, RefundRow] = {}
        # idempotency key -> resource id
        self._idempotency: dict[str, str] = {}
        # emitted webhook event ids (for duplicate suppression)
        self.webhook_event_ids: set[str] = set()

    # -- idempotency ---------------------------------------------------------
    def idempotent_id(self, key: str | None) -> str | None:
        if not key:
            return None
        with self._lock:
            return self._idempotency.get(key)

    def remember_idempotency(self, key: str | None, resource_id: str) -> None:
        if not key:
            return
        with self._lock:
            self._idempotency.setdefault(key, resource_id)

    # -- orders --------------------------------------------------------------
    def create_order(
        self,
        *,
        amount_paise: int,
        currency: str,
        receipt: str | None,
        notes: dict[str, str],
    ) -> OrderRow:
        row = OrderRow(
            id=_new_id("order"),
            amount_paise=amount_paise,
            currency=currency,
            receipt=receipt,
            notes=dict(notes),
        )
        with self._lock:
            self.orders[row.id] = row
        return row

    def get_order(self, order_id: str) -> OrderRow | None:
        with self._lock:
            return self.orders.get(order_id)

    def save_order(self, row: OrderRow) -> None:
        with self._lock:
            self.orders[row.id] = row

    # -- subscriptions -------------------------------------------------------
    def create_subscription(
        self,
        *,
        amount_paise: int,
        currency: str,
        total_cycles: int,
        notes: dict[str, str],
    ) -> SubscriptionRow:
        row = SubscriptionRow(
            id=_new_id("sub"),
            amount_paise=amount_paise,
            currency=currency,
            total_cycles=total_cycles,
            notes=dict(notes),
        )
        with self._lock:
            self.subscriptions[row.id] = row
        return row

    def get_subscription(self, subscription_id: str) -> SubscriptionRow | None:
        with self._lock:
            return self.subscriptions.get(subscription_id)

    def save_subscription(self, row: SubscriptionRow) -> None:
        with self._lock:
            self.subscriptions[row.id] = row

    def active_subscriptions(self) -> list[SubscriptionRow]:
        with self._lock:
            return [row for row in self.subscriptions.values() if row.status == "active"]

    # -- refunds -------------------------------------------------------------
    def create_refund(self, *, order_id: str, amount_paise: int) -> RefundRow:
        row = RefundRow(id=_new_id("rfnd"), order_id=order_id, amount_paise=amount_paise)
        with self._lock:
            self.refunds[row.id] = row
        return row

    # -- webhook events ------------------------------------------------------
    def record_webhook_event(self, event_id: str, event: str) -> bool:
        with self._lock:
            if event_id in self.webhook_event_ids:
                return False
            self.webhook_event_ids.add(event_id)
            return True
