"""PostgREST-backed gateway store (production persistence for the mock gateway).

The gateway persists its rows into the private ``mock_gateway`` Postgres schema
through PostgREST — the same component the API uses — but with its OWN JWT whose
``role`` claim is ``mock_gateway_role`` (generated in
``scripts/release/write_compose_test_env.py`` like Task 18's
``supabase_worker_key`` and mounted only into the payment-gateway service). Every
request carries ``Accept-Profile``/``Content-Profile: mock_gateway`` so PostgREST
reads and writes that schema rather than ``public``; the gateway role cannot
touch ``public``/``auth``/``storage`` at all (see migration 202610190801 and the
pgTAP tests).

This mirrors ``app.services.supabase.SupabaseRestClient`` (httpx, JWT, retry on
idempotent verbs) but is self-contained so the gateway adds no new Python
dependency and does not import the API's config. Idempotency keys and webhook
event ids carry UNIQUE constraints, so inserting a duplicate returns a 409 which
this store maps to the already-persisted outcome — idempotency and
duplicate-event suppression therefore survive a restart.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .store import OrderRow, RefundRow, SubscriptionRow, _new_id, _now_iso

logger = logging.getLogger("mock_gateway.store_db")

_RETRYABLE = frozenset({"GET", "HEAD", "PUT", "PATCH", "DELETE"})


class PostgrestGatewayStore:
    """A :class:`app.mock_gateway.store.GatewayStore` backed by PostgREST."""

    def __init__(
        self,
        *,
        rest_url: str,
        jwt: str,
        schema: str = "mock_gateway",
        timeout_seconds: float = 10.0,
        max_retries: int = 1,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._rest_url = rest_url.rstrip("/")
        self._schema = schema
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._transport = transport
        self._auth = {
            "apikey": jwt,
            "authorization": f"Bearer {jwt}",
        }

    # -- low-level request ---------------------------------------------------
    def _headers(self, *, write: bool, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            **self._auth,
            "accept-profile": self._schema,
            "content-type": "application/json",
        }
        if write:
            # PostgREST writes land in the profile named by Content-Profile.
            headers["content-profile"] = self._schema
            headers["prefer"] = "return=representation"
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = f"{self._rest_url}/{path.lstrip('/')}"
        attempt = 0
        while True:
            attempt += 1
            try:
                with httpx.Client(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    response = client.request(
                        method, url, headers=headers, params=params, json=json
                    )
            except (httpx.TimeoutException, httpx.TransportError):
                if method.upper() in _RETRYABLE and attempt <= self._max_retries:
                    continue
                raise
            if (
                response.status_code >= 500
                and method.upper() in _RETRYABLE
                and attempt <= self._max_retries
            ):
                continue
            return response

    # -- idempotency ---------------------------------------------------------
    def idempotent_id(self, key: str | None) -> str | None:
        if not key:
            return None
        response = self._request(
            "GET",
            "idempotency_keys",
            params={"key": f"eq.{key}", "select": "resource_id", "limit": "1"},
            headers=self._headers(write=False),
        )
        response.raise_for_status()
        rows = response.json()
        return rows[0]["resource_id"] if rows else None

    def remember_idempotency(self, key: str | None, resource_id: str) -> None:
        if not key:
            return
        # The UNIQUE primary key makes a concurrent/duplicate insert a 409; we
        # ignore it so the first writer wins (survives restarts).
        response = self._request(
            "POST",
            "idempotency_keys",
            json={"key": key, "resource_id": resource_id},
            headers=self._headers(
                write=True, extra={"prefer": "resolution=ignore-duplicates,return=minimal"}
            ),
        )
        if response.status_code >= 400 and response.status_code != 409:
            response.raise_for_status()

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
        self._insert("orders", _order_payload(row))
        return row

    def get_order(self, order_id: str) -> OrderRow | None:
        rows = self._select("orders", order_id)
        return _order_from_row(rows[0]) if rows else None

    def save_order(self, row: OrderRow) -> None:
        self._update("orders", row.id, _order_payload(row))

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
        self._insert("subscriptions", _subscription_payload(row))
        return row

    def get_subscription(self, subscription_id: str) -> SubscriptionRow | None:
        rows = self._select("subscriptions", subscription_id)
        return _subscription_from_row(rows[0]) if rows else None

    def save_subscription(self, row: SubscriptionRow) -> None:
        self._update("subscriptions", row.id, _subscription_payload(row))

    def active_subscriptions(self) -> list[SubscriptionRow]:
        response = self._request(
            "GET",
            "subscriptions",
            params={"status": "eq.active", "select": "*"},
            headers=self._headers(write=False),
        )
        response.raise_for_status()
        return [_subscription_from_row(row) for row in response.json()]

    # -- refunds -------------------------------------------------------------
    def create_refund(self, *, order_id: str, amount_paise: int) -> RefundRow:
        row = RefundRow(id=_new_id("rfnd"), order_id=order_id, amount_paise=amount_paise)
        self._insert(
            "refunds",
            {
                "id": row.id,
                "order_id": row.order_id,
                "amount_paise": row.amount_paise,
                "created_at": row.created_at,
            },
        )
        return row

    # -- webhook events ------------------------------------------------------
    def record_webhook_event(self, event_id: str, event: str) -> bool:
        """Insert the event id. The UNIQUE PK makes a duplicate a 409 → False."""
        response = self._request(
            "POST",
            "webhook_events",
            json={"id": event_id, "event": event},
            headers=self._headers(
                write=True, extra={"prefer": "resolution=ignore-duplicates,return=minimal"}
            ),
        )
        if response.status_code == 409:
            return False
        if response.status_code >= 400:
            response.raise_for_status()
        # ignore-duplicates returns 201 for a fresh insert and 200 when it was a
        # no-op duplicate; treat the "201 Created" as the new-event signal.
        return response.status_code == 201

    # -- helpers -------------------------------------------------------------
    def _insert(self, table: str, payload: dict[str, Any]) -> None:
        response = self._request(
            "POST", table, json=payload, headers=self._headers(write=True)
        )
        response.raise_for_status()

    def _update(self, table: str, row_id: str, payload: dict[str, Any]) -> None:
        response = self._request(
            "PATCH",
            table,
            params={"id": f"eq.{row_id}"},
            json=payload,
            headers=self._headers(write=True),
        )
        response.raise_for_status()

    def _select(self, table: str, row_id: str) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            table,
            params={"id": f"eq.{row_id}", "select": "*", "limit": "1"},
            headers=self._headers(write=False),
        )
        response.raise_for_status()
        return response.json()


# -- row (de)serialization ---------------------------------------------------
def _order_payload(row: OrderRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "amount_paise": row.amount_paise,
        "currency": row.currency,
        "status": row.status,
        "amount_refunded_paise": row.amount_refunded_paise,
        "method": row.method,
        "receipt": row.receipt,
        "card_brand": row.card_brand,
        "card_last4": row.card_last4,
        "notes": row.notes,
        "created_at": row.created_at,
    }


def _order_from_row(data: dict[str, Any]) -> OrderRow:
    return OrderRow(
        id=data["id"],
        amount_paise=data["amount_paise"],
        currency=data["currency"],
        status=data["status"],
        amount_refunded_paise=data.get("amount_refunded_paise", 0),
        method=data.get("method"),
        receipt=data.get("receipt"),
        card_brand=data.get("card_brand"),
        card_last4=data.get("card_last4"),
        notes=data.get("notes") or {},
        created_at=data.get("created_at") or _now_iso(),
    )


def _subscription_payload(row: SubscriptionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "amount_paise": row.amount_paise,
        "currency": row.currency,
        "status": row.status,
        "total_cycles": row.total_cycles,
        "completed_cycles": row.completed_cycles,
        "card_brand": row.card_brand,
        "card_last4": row.card_last4,
        "notes": row.notes,
        "fail_next_renewal": row.fail_next_renewal,
        "created_at": row.created_at,
    }


def _subscription_from_row(data: dict[str, Any]) -> SubscriptionRow:
    return SubscriptionRow(
        id=data["id"],
        amount_paise=data["amount_paise"],
        currency=data["currency"],
        status=data["status"],
        total_cycles=data["total_cycles"],
        completed_cycles=data.get("completed_cycles", 0),
        card_brand=data.get("card_brand"),
        card_last4=data.get("card_last4"),
        notes=data.get("notes") or {},
        fail_next_renewal=data.get("fail_next_renewal", False),
        created_at=data.get("created_at") or _now_iso(),
    )
