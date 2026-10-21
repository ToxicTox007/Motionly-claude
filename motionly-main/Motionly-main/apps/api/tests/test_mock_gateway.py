"""Tests for the mock payment gateway (Task 22).

Covers: each payment method end to end via TestClient, webhook signatures,
retries with backoff, duplicate event suppression, renewals + the fail-next
control, idempotency keys, production refusing to start without
BILLING_MODE=demo, and the guarantee that full card numbers never appear in
stored rows or logs.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.mock_gateway.config import GatewayConfigError, load_config
from app.mock_gateway.engine import (
    CARD_DECLINE,
    CARD_OTP,
    CARD_SUCCESS,
    DEMO_NOTE,
    OTP_CODE,
)
from app.mock_gateway.main import create_app
from app.mock_gateway.models import WebhookEnvelope
from app.mock_gateway.signing import compute_signature, verify_signature
from app.mock_gateway.webhooks import DeliveryResult, WebhookDeliverer, new_event_id

MERCHANT_KEY = "test-merchant-key"
WEBHOOK_SECRET = "unit-webhook-secret"
AUTH = {"Authorization": f"Bearer {MERCHANT_KEY}"}


class RecordingTransport:
    """Captures delivered webhooks; can be told to fail the first N attempts."""

    def __init__(self, fail_first: int = 0) -> None:
        self.calls: list[dict] = []
        self._fail_first = fail_first

    async def post(self, url, *, headers, body) -> DeliveryResult:
        self.calls.append({"url": url, "headers": headers, "body": body})
        if len(self.calls) <= self._fail_first:
            return DeliveryResult(status_code=500)
        return DeliveryResult(status_code=200)


def _config(**overrides):
    env = {
        "APP_ENV": "test",
        "BILLING_MODE": "demo",
        "MERCHANT_API_KEY": MERCHANT_KEY,
        "MOCK_GATEWAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "MOCK_GATEWAY_RENEWAL_PERIOD_SECONDS": "3600",
        **overrides,
    }
    return load_config(env)


@pytest.fixture
def transport() -> RecordingTransport:
    return RecordingTransport()


@pytest.fixture
def client(transport) -> TestClient:
    config = _config()
    deliverer = WebhookDeliverer(
        secret=config.webhook_secret,
        url="https://receiver.invalid/webhook",
        transport=transport,
        max_attempts=config.webhook_max_attempts,
        backoff_base_seconds=0,
    )
    app = create_app(config, deliverer=deliverer)
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# Configuration gate
# --------------------------------------------------------------------------
def test_production_refuses_to_start_without_demo_billing_mode() -> None:
    with pytest.raises(GatewayConfigError):
        load_config({"APP_ENV": "production", "BILLING_MODE": "live"})
    with pytest.raises(GatewayConfigError):
        load_config({"APP_ENV": "production"})


def test_production_starts_with_demo_billing_mode() -> None:
    config = load_config(
        {
            "APP_ENV": "production",
            "BILLING_MODE": "demo",
            "MERCHANT_API_KEY": MERCHANT_KEY,
            "MOCK_GATEWAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
            # A production demo gateway must persist (PostgREST + gateway JWT).
            "MOCK_GATEWAY_POSTGREST_URL": "http://supabase-rest:3000/rest/v1",
            "MOCK_GATEWAY_DB_JWT": "gateway.jwt.token",
        }
    )
    assert config.is_demo and config.is_production
    assert config.uses_database


def test_production_demo_requires_database_persistence() -> None:
    # In production the in-memory test double must never be selectable.
    with pytest.raises(GatewayConfigError):
        load_config(
            {
                "APP_ENV": "production",
                "BILLING_MODE": "demo",
                "MERCHANT_API_KEY": MERCHANT_KEY,
                "MOCK_GATEWAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
            }
        )


# --------------------------------------------------------------------------
# Demo landing (self-service, no merchant key) for the browser flow
# --------------------------------------------------------------------------
def test_demo_landing_creates_an_order_and_shows_checkout(client) -> None:
    landing = client.get("/pay/")
    assert landing.status_code == 200
    assert DEMO_NOTE in landing.text
    started = client.post("/pay/new", data={"amount_rupees": "499"})
    assert started.status_code == 200
    assert DEMO_NOTE in started.text
    # The checkout page offers both UPI and card.
    assert "Pay by UPI" in started.text and "Pay by card" in started.text


# --------------------------------------------------------------------------
# Auth + orders + idempotency
# --------------------------------------------------------------------------
def test_server_api_requires_merchant_key(client: TestClient) -> None:
    assert client.post("/v1/orders", json={"amount_paise": 10000}).status_code == 401


def test_order_idempotency_key_reuses_the_same_order(client: TestClient) -> None:
    headers = {**AUTH, "Idempotency-Key": "abc-123"}
    first = client.post("/v1/orders", json={"amount_paise": 25000}, headers=headers).json()
    second = client.post("/v1/orders", json={"amount_paise": 25000}, headers=headers).json()
    assert first["id"] == second["id"]
    assert first["currency"] == "INR" and first["amount_paise"] == 25000


# --------------------------------------------------------------------------
# UPI end to end
# --------------------------------------------------------------------------
def test_upi_success_captures_and_emits_webhook(client, transport) -> None:
    order = client.post("/v1/orders", json={"amount_paise": 50000}, headers=AUTH).json()
    page = client.post(
        f"/pay/checkout/{order['id']}/upi", data={"vpa": "success@upi", "action": "approve"}
    )
    assert page.status_code == 200
    assert DEMO_NOTE in page.text
    assert client.get(f"/v1/orders/{order['id']}", headers=AUTH).json()["status"] == "captured"
    assert transport.calls[-1]["headers"]["X-Mock-Gateway-Event"] == "payment.captured"


def test_upi_failure_and_timeout_fail_the_order(client) -> None:
    for vpa, action, reason in [
        ("failure@upi", "decline", "upi_declined"),
        ("", "timeout", "upi_timeout"),
    ]:
        order = client.post("/v1/orders", json={"amount_paise": 1000}, headers=AUTH).json()
        client.post(f"/pay/checkout/{order['id']}/upi", data={"vpa": vpa, "action": action})
        fetched = client.get(f"/v1/orders/{order['id']}", headers=AUTH).json()
        assert fetched["status"] == "failed"
        assert fetched["notes"]["failure_reason"] == reason


# --------------------------------------------------------------------------
# Card end to end (success, decline, OTP) + no PAN stored
# --------------------------------------------------------------------------
def test_card_success_stores_only_brand_and_last4(client) -> None:
    order = client.post("/v1/orders", json={"amount_paise": 99900}, headers=AUTH).json()
    client.post(f"/pay/checkout/{order['id']}/card", data={"card_number": CARD_SUCCESS})
    fetched = client.get(f"/v1/orders/{order['id']}", headers=AUTH).json()
    assert fetched["status"] == "captured"
    assert fetched["card"] == {"brand": "visa", "last4": CARD_SUCCESS[-4:]}


def test_card_decline_fails_the_order(client) -> None:
    order = client.post("/v1/orders", json={"amount_paise": 500}, headers=AUTH).json()
    client.post(f"/pay/checkout/{order['id']}/card", data={"card_number": CARD_DECLINE})
    assert client.get(f"/v1/orders/{order['id']}", headers=AUTH).json()["status"] == "failed"


def test_card_otp_step_then_success(client) -> None:
    order = client.post("/v1/orders", json={"amount_paise": 500}, headers=AUTH).json()
    otp_response = client.post(
        f"/pay/checkout/{order['id']}/card", data={"card_number": CARD_OTP}
    )
    assert "one-time password" in otp_response.text.lower()
    # Wrong OTP is rejected.
    wrong = client.post(f"/pay/checkout/{order['id']}/otp", data={"otp": "000000"})
    assert wrong.status_code == 400
    good = client.post(f"/pay/checkout/{order['id']}/otp", data={"otp": OTP_CODE})
    assert good.status_code == 200
    assert client.get(f"/v1/orders/{order['id']}", headers=AUTH).json()["status"] == "captured"


def test_full_card_number_never_appears_in_storage_or_logs(client, caplog) -> None:
    order = client.post("/v1/orders", json={"amount_paise": 500}, headers=AUTH).json()
    with caplog.at_level(logging.INFO):
        client.post(f"/pay/checkout/{order['id']}/card", data={"card_number": CARD_SUCCESS})
    store = client.app.state.store
    row = store.get_order(order["id"])
    # The PAN is not in any stored field.
    serialized = repr(vars(row))
    assert CARD_SUCCESS not in serialized
    assert row.card_last4 == CARD_SUCCESS[-4:] and row.card_brand == "visa"
    # The PAN is not in any log record.
    for record in caplog.records:
        assert CARD_SUCCESS not in record.getMessage()


# --------------------------------------------------------------------------
# Refund + lookup
# --------------------------------------------------------------------------
def test_refund_full_and_partial(client) -> None:
    order = client.post("/v1/orders", json={"amount_paise": 10000}, headers=AUTH).json()
    client.post(f"/pay/checkout/{order['id']}/upi", data={"action": "approve"})
    partial = client.post(
        f"/v1/orders/{order['id']}/refund", json={"amount_paise": 4000}, headers=AUTH
    )
    assert partial.status_code == 200
    assert client.get(f"/v1/orders/{order['id']}", headers=AUTH).json()["status"] == (
        "partially_refunded"
    )
    client.post(f"/v1/orders/{order['id']}/refund", json={"amount_paise": 6000}, headers=AUTH)
    assert client.get(f"/v1/orders/{order['id']}", headers=AUTH).json()["status"] == "refunded"
    over = client.post(
        f"/v1/orders/{order['id']}/refund", json={"amount_paise": 1}, headers=AUTH
    )
    assert over.status_code == 409


# --------------------------------------------------------------------------
# Subscriptions + renewals + fail-next control
# --------------------------------------------------------------------------
def test_subscription_lifecycle_renewal_and_fail_next(client, transport) -> None:
    sub = client.post(
        "/v1/subscriptions", json={"amount_paise": 29900, "total_cycles": 3}, headers=AUTH
    ).json()
    assert sub["status"] == "active"
    events = [call["headers"]["X-Mock-Gateway-Event"] for call in transport.calls]
    assert "subscription.created" in events

    first = client.post(f"/v1/subscriptions/{sub['id']}/run-renewal", headers=AUTH).json()
    assert first["charged"] is True
    fetched = client.get(f"/v1/subscriptions/{sub['id']}", headers=AUTH).json()
    assert fetched["completed_cycles"] == 1

    # Arm a failure for the next renewal.
    client.post(f"/v1/subscriptions/{sub['id']}/fail-next-renewal", headers=AUTH)
    failed = client.post(f"/v1/subscriptions/{sub['id']}/run-renewal", headers=AUTH).json()
    assert failed["charged"] is False
    assert client.get(f"/v1/subscriptions/{sub['id']}", headers=AUTH).json()["status"] == "past_due"

    events = [call["headers"]["X-Mock-Gateway-Event"] for call in transport.calls]
    assert "subscription.charged" in events
    assert "subscription.charge_failed" in events


def test_subscription_cancel(client) -> None:
    sub = client.post("/v1/subscriptions", json={"amount_paise": 100}, headers=AUTH).json()
    cancelled = client.post(f"/v1/subscriptions/{sub['id']}/cancel", headers=AUTH).json()
    assert cancelled["status"] == "cancelled"


# --------------------------------------------------------------------------
# Webhook signatures, retries, duplicates
# --------------------------------------------------------------------------
def test_webhook_signature_verifies(client, transport) -> None:
    order = client.post("/v1/orders", json={"amount_paise": 100}, headers=AUTH).json()
    client.post(f"/pay/checkout/{order['id']}/upi", data={"action": "approve"})
    call = transport.calls[-1]
    timestamp = int(call["headers"]["X-Mock-Gateway-Timestamp"])
    signature = call["headers"]["X-Mock-Gateway-Signature"]
    assert signature.startswith("sha256=")
    assert verify_signature(WEBHOOK_SECRET, timestamp, call["body"], signature)
    # Tampering is rejected.
    assert not verify_signature(WEBHOOK_SECRET, timestamp, call["body"] + b"x", signature)
    # The body round-trips through the shared envelope model.
    envelope = WebhookEnvelope.model_validate_json(call["body"])
    assert envelope.event == "payment.captured" and envelope.note == DEMO_NOTE


def test_webhook_retries_with_backoff() -> None:
    transport = RecordingTransport(fail_first=2)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    deliverer = WebhookDeliverer(
        secret=WEBHOOK_SECRET,
        url="https://receiver.invalid",
        transport=transport,
        max_attempts=5,
        backoff_base_seconds=1.0,
        sleep=fake_sleep,
    )
    envelope = WebhookEnvelope(
        id=new_event_id(), event="payment.captured", created_at="2026-01-01T00:00:00Z"
    )
    assert asyncio.run(deliverer.deliver(envelope)) is True
    assert len(transport.calls) == 3  # two failures + one success
    assert sleeps == [1.0, 2.0]  # exponential backoff between attempts


async def _noop_sleep(_seconds: float) -> None:
    return None


def test_duplicate_event_is_suppressed_and_retries_reuse_the_event_id() -> None:
    transport = RecordingTransport(fail_first=1)
    deliverer = WebhookDeliverer(
        secret=WEBHOOK_SECRET,
        url="https://receiver.invalid",
        transport=transport,
        max_attempts=3,
        backoff_base_seconds=0,
        sleep=_noop_sleep,
    )
    envelope = WebhookEnvelope(
        id=new_event_id(), event="payment.captured", created_at="2026-01-01T00:00:00Z"
    )
    assert asyncio.run(deliverer.deliver(envelope)) is True
    # Retries kept the same event id.
    ids = {call["headers"]["X-Mock-Gateway-Event-Id"] for call in transport.calls}
    assert ids == {envelope.id}
    # Re-delivering the same event is suppressed (no new HTTP call).
    before = len(transport.calls)
    assert asyncio.run(deliverer.deliver(envelope)) is True
    assert len(transport.calls) == before


def test_signature_is_stable_for_the_documented_scheme() -> None:
    body = b'{"hello":"world"}'
    assert compute_signature("secret", 1000, body) == compute_signature("secret", 1000, body)
    assert compute_signature("secret", 1000, body) != compute_signature("secret", 1001, body)


# --------------------------------------------------------------------------
# DB-backed store (PostgREST) — persistence, profile headers, restart safety
# --------------------------------------------------------------------------
import json as _json  # noqa: E402

import httpx as _httpx  # noqa: E402

from app.mock_gateway.config import GatewayConfig  # noqa: E402
from app.mock_gateway.main import build_store  # noqa: E402
from app.mock_gateway.store import InMemoryGatewayStore  # noqa: E402
from app.mock_gateway.store_db import PostgrestGatewayStore  # noqa: E402

GATEWAY_JWT = "header.payload.signature"


class FakePostgrest:
    """A tiny PostgREST stand-in over in-memory tables with UNIQUE primary keys.

    Shared between two store instances to simulate a gateway restart hitting the
    same durable database. It records the profile headers every request carries.
    """

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict]] = {
            "orders": {},
            "subscriptions": {},
            "refunds": {},
            "idempotency_keys": {},
            "webhook_events": {},
        }
        self.seen_profiles: list[tuple[str, str | None, str | None, str | None]] = []

    def transport(self) -> _httpx.MockTransport:
        return _httpx.MockTransport(self._handle)

    def _handle(self, request: _httpx.Request) -> _httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        self.seen_profiles.append(
            (
                table,
                request.headers.get("accept-profile"),
                request.headers.get("content-profile"),
                request.headers.get("authorization"),
            )
        )
        store = self.tables.setdefault(table, {})
        prefer = request.headers.get("prefer", "")
        if request.method == "GET":
            key = _eq_value(request.url.params.get("id"))
            if key is None and request.url.params.get("status"):
                status = _eq_value(request.url.params.get("status"))
                rows = [r for r in store.values() if r.get("status") == status]
            elif key is not None:
                rows = [store[key]] if key in store else []
            else:
                # e.g. idempotency_keys lookup by `key`
                col = next(
                    (c for c in request.url.params if c not in {"select", "limit", "order"}),
                    None,
                )
                want = _eq_value(request.url.params.get(col)) if col else None
                rows = [r for r in store.values() if r.get(col) == want]
            return _httpx.Response(200, json=rows)
        if request.method == "POST":
            payload = _json.loads(request.content.decode() or "{}")
            pk = payload.get("id") or payload.get("key")
            if pk in store:
                if "ignore-duplicates" in prefer:
                    return _httpx.Response(200, json=[] if "minimal" in prefer else [store[pk]])
                return _httpx.Response(409, json={"message": "duplicate key"})
            store[pk] = payload
            if "minimal" in prefer:
                return _httpx.Response(201, text="")
            return _httpx.Response(201, json=[payload])
        if request.method == "PATCH":
            key = _eq_value(request.url.params.get("id"))
            payload = _json.loads(request.content.decode() or "{}")
            if key in store:
                store[key].update(payload)
            return _httpx.Response(200, json=[store.get(key, {})])
        return _httpx.Response(405)


def _eq_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    return raw[3:] if raw.startswith("eq.") else raw


def _db_store(fake: FakePostgrest) -> PostgrestGatewayStore:
    return PostgrestGatewayStore(
        rest_url="http://supabase-rest:3000/rest/v1",
        jwt=GATEWAY_JWT,
        schema="mock_gateway",
        transport=fake.transport(),
    )


def test_db_store_persists_orders_with_mock_gateway_profile_and_jwt() -> None:
    fake = FakePostgrest()
    store = _db_store(fake)
    order = store.create_order(amount_paise=50000, currency="INR", receipt="r1", notes={"a": "b"})
    order.status = "captured"
    store.save_order(order)
    fetched = store.get_order(order.id)
    assert fetched is not None
    assert fetched.status == "captured" and fetched.amount_paise == 50000
    # Every request targeted the mock_gateway profile and carried the gateway JWT.
    assert fake.seen_profiles, "no requests were made"
    for _table, accept_profile, _content_profile, authorization in fake.seen_profiles:
        assert accept_profile == "mock_gateway"
        assert authorization == f"Bearer {GATEWAY_JWT}"
    # Writes also set Content-Profile: mock_gateway.
    writes = [p for p in fake.seen_profiles if p[2] is not None]
    assert writes and all(p[2] == "mock_gateway" for p in writes)


def test_db_store_idempotency_and_duplicate_events_survive_restart() -> None:
    fake = FakePostgrest()
    # First process life.
    store1 = _db_store(fake)
    order = store1.create_order(amount_paise=1000, currency="INR", receipt=None, notes={})
    store1.remember_idempotency("key-1", order.id)
    assert store1.record_webhook_event("evt-1", "payment.captured") is True

    # Restart: a brand-new store instance over the same durable database.
    store2 = _db_store(fake)
    assert store2.idempotent_id("key-1") == order.id  # idempotency held
    # Re-remembering the same key is a no-op (first writer wins).
    store2.remember_idempotency("key-1", "order_other")
    assert store2.idempotent_id("key-1") == order.id
    # The webhook event id is already recorded → duplicate suppressed.
    assert store2.record_webhook_event("evt-1", "payment.captured") is False
    assert store2.record_webhook_event("evt-2", "payment.captured") is True


def test_db_store_active_subscriptions_filters_on_status() -> None:
    fake = FakePostgrest()
    store = _db_store(fake)
    sub = store.create_subscription(amount_paise=29900, currency="INR", total_cycles=3, notes={})
    assert [s.id for s in store.active_subscriptions()] == [sub.id]
    sub.status = "cancelled"
    store.save_subscription(sub)
    assert store.active_subscriptions() == []


def _gateway_config(**overrides) -> GatewayConfig:
    env = {
        "APP_ENV": "test",
        "BILLING_MODE": "demo",
        "MERCHANT_API_KEY": MERCHANT_KEY,
        "MOCK_GATEWAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
        **overrides,
    }
    return load_config(env)


def test_build_store_selects_db_when_configured_else_in_memory() -> None:
    in_memory_cfg = _gateway_config()
    assert isinstance(build_store(in_memory_cfg), InMemoryGatewayStore)

    db_cfg = _gateway_config(
        MOCK_GATEWAY_POSTGREST_URL="http://supabase-rest:3000/rest/v1",
        MOCK_GATEWAY_DB_JWT=GATEWAY_JWT,
    )
    assert db_cfg.uses_database
    assert isinstance(build_store(db_cfg), PostgrestGatewayStore)


def test_the_entrypoint_serves_one_process_whatever_web_concurrency_says(monkeypatch) -> None:
    """The gateway runs from the API image, which sets WEB_CONCURRENCY=2 for the
    API. uvicorn reads that as a worker count and exits when given an app object,
    so the container restarted in a loop. One process is also required: renewals
    are scheduled in-process and must not run twice."""
    import uvicorn

    from app.mock_gateway import __main__ as entrypoint

    calls: list[dict] = []
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setattr(entrypoint, "get_config", lambda: SimpleNamespace(app_env="cpu-stage"))
    monkeypatch.setattr(entrypoint, "create_app", lambda config: object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))

    entrypoint.main()

    assert calls and calls[0]["workers"] == 1
