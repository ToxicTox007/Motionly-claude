import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from uuid import UUID

import httpx

from app.api.errors import ApiError
from app.schemas.billing import (
    BillingPlanKey,
    PlanDefinition,
    PlanEntitlements,
    SubscriptionStatus,
)

PLAN_DEFINITIONS: dict[BillingPlanKey, PlanDefinition] = {
    BillingPlanKey.FREE: PlanDefinition(
        key=BillingPlanKey.FREE,
        name="Free",
        price_cents=0,
        stripe_price_env=None,
        entitlements=PlanEntitlements(
            monthly_renders=3,
            watermark=True,
            max_resolution="720p",
            broll=False,
        ),
        features=["3 renders/month", "Watermarked exports", "720p exports", "No B-roll"],
    ),
    BillingPlanKey.CREATOR: PlanDefinition(
        key=BillingPlanKey.CREATOR,
        name="Creator",
        price_cents=2900,
        stripe_price_env="STRIPE_PRICE_CREATOR",
        entitlements=PlanEntitlements(
            monthly_renders=30,
            watermark=False,
            max_resolution="1080p",
            broll=True,
        ),
        features=["30 renders/month", "No watermark", "1080p exports", "B-roll"],
    ),
    BillingPlanKey.PRO: PlanDefinition(
        key=BillingPlanKey.PRO,
        name="Pro",
        price_cents=7900,
        stripe_price_env="STRIPE_PRICE_PRO",
        entitlements=PlanEntitlements(
            monthly_renders=100,
            watermark=False,
            max_resolution="1080p",
            broll=True,
            priority_queue=True,
            premium_effects=True,
        ),
        features=["100 renders/month", "Priority queue", "Premium effects", "B-roll"],
    ),
    BillingPlanKey.AGENCY: PlanDefinition(
        key=BillingPlanKey.AGENCY,
        name="Agency",
        price_cents=19900,
        stripe_price_env="STRIPE_PRICE_AGENCY",
        entitlements=PlanEntitlements(
            monthly_renders=250,
            watermark=False,
            max_resolution="1080p",
            broll=True,
            priority_queue=True,
            premium_effects=True,
            seats=5,
            brand_kits=True,
            bulk_uploads=True,
            api_access=True,
        ),
        features=["5 seats", "Brand kits", "Bulk uploads", "API access"],
    ),
}


ACTIVE_STRIPE_STATUSES = {
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
}


def plan_for_subscription_status(
    *,
    plan: BillingPlanKey,
    status: SubscriptionStatus,
) -> BillingPlanKey:
    if plan == BillingPlanKey.FREE:
        return BillingPlanKey.FREE
    return plan if status in ACTIVE_STRIPE_STATUSES else BillingPlanKey.FREE


def monthly_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return start, end


def stripe_signature_header(*, payload: bytes, secret: str, timestamp: int | None = None) -> str:
    timestamp = timestamp or int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}".encode()
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def parse_verified_stripe_event(
    *,
    payload: bytes,
    signature_header: str | None,
    webhook_secret: str | None,
    tolerance_seconds: int = 300,
) -> dict[str, object]:
    if not webhook_secret:
        raise ApiError(
            503,
            "stripe_webhook_not_configured",
            "Stripe webhook secret is not configured.",
        )
    if not signature_header:
        raise ApiError(400, "stripe_signature_missing", "Stripe signature header is missing.")

    parts: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        key, _, value = part.partition("=")
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts.get("t", [""])[0])
    except ValueError as exc:
        raise ApiError(
            400,
            "stripe_signature_invalid",
            "Stripe signature timestamp is invalid.",
        ) from exc
    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        raise ApiError(400, "stripe_signature_expired", "Stripe signature timestamp expired.")

    expected = stripe_signature_header(
        payload=payload,
        secret=webhook_secret,
        timestamp=timestamp,
    ).split("v1=", 1)[1]
    signatures = parts.get("v1", [])
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise ApiError(400, "stripe_signature_invalid", "Stripe signature does not match.")

    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(
            400,
            "stripe_payload_invalid",
            "Stripe webhook payload is invalid JSON.",
        ) from exc
    if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
        raise ApiError(
            400,
            "stripe_event_invalid",
            "Stripe webhook event is missing required fields.",
        )
    return event


class StripeBillingClient:
    def __init__(self, *, api_key: str | None) -> None:
        self.api_key = api_key

    def create_checkout_session(
        self,
        *,
        price_id: str,
        plan: BillingPlanKey,
        workspace_id: UUID,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> tuple[str, str]:
        if not self.api_key:
            raise ApiError(503, "stripe_not_configured", "Stripe API key is not configured.")
        data = {
            "mode": "subscription",
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": str(workspace_id),
            "metadata[workspace_id]": str(workspace_id),
            "metadata[plan]": plan.value,
            "metadata[stripe_price_id]": price_id,
            "subscription_data[metadata][workspace_id]": str(workspace_id),
            "subscription_data[metadata][plan]": plan.value,
        }
        if customer_id:
            data["customer"] = customer_id
        response = httpx.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(self.api_key, ""),
            data=data,
            timeout=10,
        )
        self._raise_for_stripe(response)
        body = response.json()
        return str(body["url"]), str(body["id"])

    def create_customer_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> str:
        if not self.api_key:
            raise ApiError(503, "stripe_not_configured", "Stripe API key is not configured.")
        response = httpx.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            auth=(self.api_key, ""),
            data={"customer": customer_id, "return_url": return_url},
            timeout=10,
        )
        self._raise_for_stripe(response)
        return str(response.json()["url"])

    @staticmethod
    def _raise_for_stripe(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        code = "stripe_request_failed"
        message = "Stripe request failed."
        try:
            body = response.json()
            error = body.get("error", {})
            code = error.get("code") or error.get("type") or code
            message = error.get("message") or message
        except ValueError:
            pass
        raise ApiError(502, code, message)
