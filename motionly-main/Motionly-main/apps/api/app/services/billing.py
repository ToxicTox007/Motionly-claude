import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from app.api.errors import ApiError
from app.schemas.billing import (
    BillingPlanKey,
    PlanDefinition,
    PlanEntitlements,
    SubscriptionStatus,
)

# ---------------------------------------------------------------------------
# Single source of truth for plan prices + entitlements: app/config/plans.json.
# Prices are INR paise (₹1 = 100 paise). Never hard-code prices anywhere else;
# a test (test_prices_are_not_hard_coded) fails on duplicated price literals.
# ---------------------------------------------------------------------------
PLANS_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "plans.json"


def _load_plan_definitions(
    path: Path = PLANS_CONFIG_PATH,
) -> dict[BillingPlanKey, PlanDefinition]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    currency = str(raw.get("currency", "INR"))
    interval = str(raw.get("interval", "month"))
    definitions: dict[BillingPlanKey, PlanDefinition] = {}
    for entry in raw["plans"]:
        key = BillingPlanKey(entry["key"])
        definitions[key] = PlanDefinition(
            key=key,
            name=entry["name"],
            price_paise=int(entry["price_paise"]),
            currency=currency,
            interval=interval,
            entitlements=PlanEntitlements(**entry["entitlements"]),
            features=list(entry["features"]),
        )
    return definitions


PLAN_DEFINITIONS: dict[BillingPlanKey, PlanDefinition] = _load_plan_definitions()


ACTIVE_SUBSCRIPTION_STATUSES = {
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
    return plan if status in ACTIVE_SUBSCRIPTION_STATUSES else BillingPlanKey.FREE


def monthly_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return start, end


def check_quota(
    *,
    workspace_id: UUID,
    provider: str,
    category: str,
    billing_repository,
    operations_repository,
    estimated_quantity: float,
) -> None:
    # Get workspace subscription plan
    subscription = billing_repository.get_subscription(workspace_id=workspace_id)
    plan_key = plan_for_subscription_status(plan=subscription.plan, status=subscription.status)

    # Get current billing period
    start = subscription.current_period_start
    end = subscription.current_period_end
    if not start or not end:
        start, end = monthly_period()

    # Get the usage limit from settings
    from app.core.config import get_settings

    settings = get_settings()

    limit = 0.0
    if provider == "runway":
        if plan_key == BillingPlanKey.FREE:
            limit = float(getattr(settings, "quota_runway_free_limit_seconds", 0.0))
        elif plan_key == BillingPlanKey.CREATOR:
            limit = float(getattr(settings, "quota_runway_creator_limit_seconds", 60.0))
        elif plan_key == BillingPlanKey.PRO:
            limit = float(getattr(settings, "quota_runway_pro_limit_seconds", 300.0))
        elif plan_key == BillingPlanKey.AGENCY:
            limit = float(getattr(settings, "quota_runway_agency_limit_seconds", 1200.0))
    elif provider == "openrouter":
        if plan_key == BillingPlanKey.FREE:
            limit = float(getattr(settings, "quota_openrouter_free_limit_tokens", 100000.0))
        elif plan_key == BillingPlanKey.CREATOR:
            limit = float(getattr(settings, "quota_openrouter_creator_limit_tokens", 500000.0))
        elif plan_key == BillingPlanKey.PRO:
            limit = float(getattr(settings, "quota_openrouter_pro_limit_tokens", 2000000.0))
        elif plan_key == BillingPlanKey.AGENCY:
            limit = float(getattr(settings, "quota_openrouter_agency_limit_tokens", 10000000.0))
    else:
        return

    # Count current usage in this period
    used = operations_repository.count_usage(
        workspace_id=workspace_id,
        provider=provider,
        category=category,
        period_start=start,
        period_end=end,
    )

    if used + estimated_quantity > limit:
        raise ApiError(
            402,
            f"{provider}_quota_exceeded",
            (
                f"Quota exceeded for provider {provider}. Used: {used:.1f}, "
                f"Limit: {limit:.1f}. Required: {estimated_quantity:.1f}"
            ),
        )


def verify_gateway_webhook(
    *,
    payload: bytes,
    event_id: str | None,
    timestamp_header: str | None,
    signature: str | None,
    webhook_secret: str | None,
    tolerance_seconds: int = 300,
) -> "WebhookEnvelope":
    """Verify and parse a signed mock-gateway webhook.

    Uses the Task 22 signing scheme (HMAC-SHA256 over ``f"{timestamp}.{body}"``,
    constant-time compare) and the Task 22 ``WebhookEnvelope`` model. Rejects a
    missing/invalid signature, a stale timestamp, or a malformed body.
    """
    # Imported lazily so importing app.services.billing does not require the
    # gateway package at module import time.
    from app.mock_gateway.models import WebhookEnvelope
    from app.mock_gateway.signing import verify_signature

    if not webhook_secret:
        raise ApiError(
            503,
            "gateway_webhook_not_configured",
            "Gateway webhook secret is not configured.",
        )
    if not signature:
        raise ApiError(400, "gateway_signature_missing", "Gateway signature header is missing.")
    try:
        timestamp = int(str(timestamp_header))
    except (TypeError, ValueError) as exc:
        raise ApiError(
            400,
            "gateway_signature_invalid",
            "Gateway signature timestamp is invalid.",
        ) from exc
    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        raise ApiError(400, "gateway_signature_expired", "Gateway signature timestamp expired.")
    if not verify_signature(
        webhook_secret,
        timestamp,
        payload,
        signature,
        tolerance_seconds=tolerance_seconds,
    ):
        raise ApiError(400, "gateway_signature_invalid", "Gateway signature does not match.")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(
            400,
            "gateway_payload_invalid",
            "Gateway webhook payload is invalid JSON.",
        ) from exc
    try:
        envelope = WebhookEnvelope.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - surface as a 400
        raise ApiError(
            400,
            "gateway_event_invalid",
            "Gateway webhook event is missing required fields.",
        ) from exc
    if event_id and event_id != envelope.id:
        raise ApiError(
            400,
            "gateway_event_id_mismatch",
            "Gateway event id header does not match the signed body.",
        )
    return envelope


if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.mock_gateway.models import WebhookEnvelope  # noqa: F401
