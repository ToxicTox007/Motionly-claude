"""FastAPI application for the mock payment gateway (Task 22).

See the package docstring for the webhook contract. This module builds the app,
wires the store/engine/webhook deliverer/renewal scheduler, authenticates the
server-to-server API with a merchant API key, serves the accessible checkout
pages, and scrubs card data from request logs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import GatewayConfig, get_config
from .engine import (
    DEMO_NOTE,
    PaymentEngine,
    PaymentError,
    classify_card_outcome,
)
from .logging_scrub import install_scrubbing, scrub
from .models import (
    CreateOrderRequest,
    CreateSubscriptionRequest,
    OrderResource,
    RefundRequest,
    SubscriptionResource,
)
from .pages import checkout_page, demo_landing_page, otp_page, result_page
from .runtime import HttpxWebhookTransport, RenewalScheduler
from .store import GatewayStore, InMemoryGatewayStore, OrderRow
from .webhooks import WebhookDeliverer

logger = logging.getLogger("mock_gateway")


def _order_resource(engine: PaymentEngine, row: OrderRow) -> OrderResource:
    from .engine import _order_resource as to_resource

    return to_resource(row)


def build_store(config: GatewayConfig) -> GatewayStore:
    """Select the persistence backend.

    Production (and any run with a configured PostgREST URL + gateway JWT) uses
    the DB-backed :class:`PostgrestGatewayStore`, so idempotency keys and webhook
    event ids survive restarts via their UNIQUE constraints. Everything else
    falls back to the in-memory test double. No setting ever selects the
    in-memory store in production: ``load_config`` requires the database there.
    """
    if config.uses_database:
        from .store_db import PostgrestGatewayStore

        return PostgrestGatewayStore(
            rest_url=config.postgrest_url,  # type: ignore[arg-type]
            jwt=config.database_jwt,  # type: ignore[arg-type]
            schema=config.database_schema,
        )
    return InMemoryGatewayStore()


def create_app(
    config: GatewayConfig | None = None,
    *,
    deliverer: WebhookDeliverer | None = None,
    store: GatewayStore | None = None,
) -> FastAPI:
    config = config or get_config()
    install_scrubbing(logging.getLogger())
    install_scrubbing(logging.getLogger("mock_gateway"))

    store = store if store is not None else build_store(config)
    if deliverer is None:
        deliverer = WebhookDeliverer(
            secret=config.webhook_secret,
            url=config.webhook_url,
            transport=HttpxWebhookTransport(),
            max_attempts=config.webhook_max_attempts,
            backoff_base_seconds=config.webhook_backoff_base_seconds,
        )
    engine = PaymentEngine(store, deliverer)
    scheduler = RenewalScheduler(engine, config.renewal_period_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(title="Motionly mock payment gateway", lifespan=lifespan)
    app.state.config = config
    app.state.store = store
    app.state.engine = engine
    app.state.deliverer = deliverer
    app.state.scheduler = scheduler

    # -- request logging with card scrubbing --------------------------------
    @app.middleware("http")
    async def _scrubbed_request_log(request: Request, call_next):
        response = await call_next(request)
        logger.info(scrub("%s %s -> %d"), request.method, request.url.path, response.status_code)
        return response

    def require_merchant(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {config.merchant_api_key}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid merchant API key")

    # ======================= server-to-server API =========================
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "billing_mode": config.billing_mode, "note": DEMO_NOTE}

    @app.post("/v1/orders", dependencies=[Depends(require_merchant)])
    async def create_order(
        body: CreateOrderRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> OrderResource:
        existing = store.idempotent_id(idempotency_key)
        if existing:
            row = store.get_order(existing)
            if row:
                return _order_resource(engine, row)
        row = store.create_order(
            amount_paise=body.amount_paise,
            currency=body.currency,
            receipt=body.receipt,
            notes=body.notes,
        )
        store.remember_idempotency(idempotency_key, row.id)
        return _order_resource(engine, row)

    @app.get("/v1/orders/{order_id}", dependencies=[Depends(require_merchant)])
    async def get_order(order_id: str) -> OrderResource:
        row = store.get_order(order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="order not found")
        return _order_resource(engine, row)

    @app.post("/v1/orders/{order_id}/refund", dependencies=[Depends(require_merchant)])
    async def refund_order(order_id: str, body: RefundRequest):
        row = store.get_order(order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="order not found")
        try:
            refund = await engine.refund_order(row, body.amount_paise)
        except PaymentError as error:
            raise HTTPException(status_code=409, detail=error.message) from error
        return refund

    @app.post("/v1/subscriptions", dependencies=[Depends(require_merchant)])
    async def create_subscription(
        body: CreateSubscriptionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> SubscriptionResource:
        existing = store.idempotent_id(idempotency_key)
        if existing:
            row = store.get_subscription(existing)
            if row:
                from .engine import _subscription_resource

                return _subscription_resource(row)
        row = store.create_subscription(
            amount_paise=body.amount_paise,
            currency=body.currency,
            total_cycles=body.total_cycles,
            notes=body.notes,
        )
        store.remember_idempotency(idempotency_key, row.id)
        return await _activate_and_serialize(row)

    async def _activate_and_serialize(row) -> SubscriptionResource:
        from .engine import _subscription_resource

        await engine.activate_subscription(row, raw_number=None)
        return _subscription_resource(row)

    @app.get("/v1/subscriptions/{subscription_id}", dependencies=[Depends(require_merchant)])
    async def get_subscription(subscription_id: str) -> SubscriptionResource:
        from .engine import _subscription_resource

        row = store.get_subscription(subscription_id)
        if row is None:
            raise HTTPException(status_code=404, detail="subscription not found")
        return _subscription_resource(row)

    @app.post(
        "/v1/subscriptions/{subscription_id}/cancel",
        dependencies=[Depends(require_merchant)],
    )
    async def cancel_subscription(subscription_id: str) -> SubscriptionResource:
        from .engine import _subscription_resource

        row = store.get_subscription(subscription_id)
        if row is None:
            raise HTTPException(status_code=404, detail="subscription not found")
        await engine.cancel_subscription(row)
        return _subscription_resource(row)

    @app.post(
        "/v1/subscriptions/{subscription_id}/fail-next-renewal",
        dependencies=[Depends(require_merchant)],
    )
    async def fail_next_renewal(subscription_id: str) -> dict[str, str]:
        row = store.get_subscription(subscription_id)
        if row is None:
            raise HTTPException(status_code=404, detail="subscription not found")
        row.fail_next_renewal = True
        return {"status": "armed"}

    @app.post(
        "/v1/subscriptions/{subscription_id}/run-renewal",
        dependencies=[Depends(require_merchant)],
    )
    async def run_renewal(subscription_id: str) -> dict[str, bool]:
        row = store.get_subscription(subscription_id)
        if row is None:
            raise HTTPException(status_code=404, detail="subscription not found")
        charged = await engine.run_renewal(row)
        return {"charged": charged}

    # ========================= checkout pages =============================
    @app.get("/pay/", response_class=HTMLResponse)
    @app.get("/pay", response_class=HTMLResponse)
    async def demo_landing() -> HTMLResponse:
        return HTMLResponse(demo_landing_page())

    @app.post("/pay/new", response_class=HTMLResponse)
    async def start_demo_order(request: Request) -> HTMLResponse:
        form = await _form(request)
        try:
            rupees = max(1, int(float(form.get("amount_rupees", "499"))))
        except ValueError:
            rupees = 499
        row = store.create_order(
            amount_paise=rupees * 100,
            currency="INR",
            receipt=None,
            notes={"source": "demo-landing"},
        )
        return HTMLResponse(checkout_page(row.id, row.amount_paise))

    @app.get("/pay/checkout/{order_id}", response_class=HTMLResponse)
    async def checkout(order_id: str) -> HTMLResponse:
        row = store.get_order(order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="order not found")
        return HTMLResponse(checkout_page(order_id, row.amount_paise))

    @app.post("/pay/checkout/{order_id}/upi", response_class=HTMLResponse)
    async def pay_upi(order_id: str, request: Request) -> HTMLResponse:
        form = await _form(request)
        row = store.get_order(order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="order not found")
        vpa = form.get("vpa", "")
        action = form.get("action", "approve")
        await engine.pay_order_upi(row, action, vpa or None)
        reason = row.notes.get("failure_reason")
        return HTMLResponse(result_page(order_id, row.status, reason))

    @app.post("/pay/checkout/{order_id}/card", response_class=HTMLResponse)
    async def pay_card(order_id: str, request: Request) -> HTMLResponse:
        form = await _form(request)
        card_number = form.get("card_number", "")
        row = store.get_order(order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="order not found")
        # OTP cards go to the OTP step but we must classify + store brand/last4
        # first; the PAN is never echoed, stored or logged.
        if classify_card_outcome(card_number).kind == "otp_required":
            from .engine import classify_card

            card = classify_card(card_number)
            row.method = "card"
            row.card_brand = card.brand
            row.card_last4 = card.last4
            store.save_order(row)
            return HTMLResponse(otp_page(order_id))
        await engine.pay_order_card(row, card_number)
        reason = row.notes.get("failure_reason")
        return HTMLResponse(result_page(order_id, row.status, reason))

    @app.post("/pay/checkout/{order_id}/otp", response_class=HTMLResponse)
    async def confirm_otp(order_id: str, request: Request) -> HTMLResponse:
        form = await _form(request)
        otp = form.get("otp", "")
        row = store.get_order(order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="order not found")
        await engine.confirm_card_otp(row, otp)
        if row.status == "captured":
            return HTMLResponse(result_page(order_id, row.status))
        return HTMLResponse(otp_page(order_id, error="That OTP was not correct."), status_code=400)

    @app.exception_handler(PaymentError)
    async def _payment_error_handler(_request: Request, error: PaymentError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": error.code, "message": error.message}},
        )

    return app


async def _form(request: Request) -> dict[str, str]:
    """Parse an ``application/x-www-form-urlencoded`` body.

    Done by hand so the gateway does not need python-multipart (not in the API
    lock). The raw body is never logged — only the parsed, scrubbed fields are
    used, and card PANs are discarded after classification.
    """
    from urllib.parse import parse_qs

    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


# Module-level app for `uvicorn app.mock_gateway.main:app`.
# Guarded so importing the module (e.g. for signing/models) does not require a
# full valid configuration; the config check runs when the app is built.
try:
    app = create_app()
except Exception:  # noqa: BLE001 - surfaced by __main__ / uvicorn at boot
    app = None
