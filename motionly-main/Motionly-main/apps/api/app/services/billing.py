import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

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


# Statuses that grant the paid plan unconditionally. PAST_DUE grants it only
# during a bounded grace period (see effective_plan_key).
ACTIVE_SUBSCRIPTION_STATUSES = {
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
}

DEFAULT_PAST_DUE_GRACE_DAYS = 7
MAX_PAST_DUE_GRACE_DAYS = 60


def past_due_grace_days() -> int:
    """Days a past-due subscription keeps its paid plan (BILLING_PAST_DUE_GRACE_DAYS)."""
    try:
        days = int(os.getenv("BILLING_PAST_DUE_GRACE_DAYS", str(DEFAULT_PAST_DUE_GRACE_DAYS)))
    except ValueError:
        return DEFAULT_PAST_DUE_GRACE_DAYS
    return max(0, min(MAX_PAST_DUE_GRACE_DAYS, days))


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def past_due_grace_ends_at(subscription: object) -> datetime | None:
    """When a past-due subscription stops granting its paid plan.

    A renewal fails at the end of the paid period, so the grace period runs
    from ``current_period_end`` (the period the failed renewal should have
    extended). Without a recorded period it runs from the last subscription
    update. ``None`` for any other status, or when there is no anchor at all
    (which grants no grace).
    """
    if getattr(subscription, "status", None) != SubscriptionStatus.PAST_DUE:
        return None
    anchor = _aware(getattr(subscription, "current_period_end", None)) or _aware(
        getattr(subscription, "updated_at", None)
    )
    if anchor is None:
        return None
    return anchor + timedelta(days=past_due_grace_days())


def plan_for_subscription_status(
    *,
    plan: BillingPlanKey,
    status: SubscriptionStatus,
    past_due_grace_ends_at: datetime | None = None,
    now: datetime | None = None,
) -> BillingPlanKey:
    """The plan whose entitlements apply. Prefer :func:`effective_plan_key`.

    A past-due subscription keeps its plan only until ``past_due_grace_ends_at``;
    without that bound it is treated as Free, so a lapsed payment can never
    keep paid features indefinitely.
    """
    if plan == BillingPlanKey.FREE:
        return BillingPlanKey.FREE
    if status in ACTIVE_SUBSCRIPTION_STATUSES:
        return plan
    if status == SubscriptionStatus.PAST_DUE and past_due_grace_ends_at is not None:
        if (now or datetime.now(UTC)) < past_due_grace_ends_at:
            return plan
    return BillingPlanKey.FREE


def effective_plan_key(subscription: object, *, now: datetime | None = None) -> BillingPlanKey:
    """The entitlement-granting plan for a subscription record (grace-aware).

    Every entitlement check (render limits, feature gates, quotas, seats, API
    keys) goes through this so they agree on when a lapsed plan ends.
    """
    return plan_for_subscription_status(
        plan=subscription.plan,
        status=subscription.status,
        past_due_grace_ends_at=past_due_grace_ends_at(subscription),
        now=now,
    )


def monthly_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return start, end


@dataclass(frozen=True)
class QuotaSpec:
    """One per-plan usage quota.

    The limit for a plan is the setting ``quota_<name>_<plan>_limit_<unit>``
    (env ``QUOTA_<NAME>_<PLAN>_LIMIT_<UNIT>``), falling back to ``defaults``.
    Usage is the sum of ``cost_attribution_events.quantity`` for ``category``
    in the current billing period.
    """

    name: str
    unit: str
    category: str
    defaults: Mapping[BillingPlanKey, float]

    def setting_name(self, plan: BillingPlanKey) -> str:
        return f"quota_{self.name}_{plan.value}_limit_{self.unit}"

    def limit(self, settings: object, plan: BillingPlanKey) -> float:
        return float(getattr(settings, self.setting_name(plan), self.defaults[plan]))


def _plan_limits(
    free: float, creator: float, pro: float, agency: float
) -> dict[BillingPlanKey, float]:
    return {
        BillingPlanKey.FREE: free,
        BillingPlanKey.CREATOR: creator,
        BillingPlanKey.PRO: pro,
        BillingPlanKey.AGENCY: agency,
    }


# Provider quotas checked explicitly by their callers (check_quota).
PROVIDER_QUOTAS: dict[str, QuotaSpec] = {
    "runway": QuotaSpec(
        name="runway",
        unit="seconds",
        category="generated_seconds",
        defaults=_plan_limits(0.0, 60.0, 300.0, 1200.0),
    ),
    "openrouter": QuotaSpec(
        name="openrouter",
        unit="tokens",
        category="tokens",
        defaults=_plan_limits(100_000.0, 500_000.0, 2_000_000.0, 10_000_000.0),
    ),
}

# Remote AI features metered by the model gateway, keyed by ModelRoute.budget_feature.
# Units: images sent, embedding inputs, or seconds of audio. Defaults are sized for
# roughly 5 / 40 / 120 / 300 analysed videos a month (24 keyframes, ~6 min each).
FEATURE_QUOTAS: dict[str, QuotaSpec] = {
    "vision_objects": QuotaSpec(
        name="vision_objects",
        unit="images",
        category="vision_object_images",
        defaults=_plan_limits(120.0, 1_000.0, 3_000.0, 7_500.0),
    ),
    "vision_captions": QuotaSpec(
        name="vision_captions",
        unit="images",
        category="vision_caption_images",
        defaults=_plan_limits(120.0, 1_000.0, 3_000.0, 7_500.0),
    ),
    "embeddings": QuotaSpec(
        name="embeddings",
        unit="inputs",
        category="embedding_inputs",
        defaults=_plan_limits(1_000.0, 8_000.0, 24_000.0, 60_000.0),
    ),
    "audio_diarization": QuotaSpec(
        name="audio_diarization",
        unit="seconds",
        category="diarization_seconds",
        defaults=_plan_limits(1_800.0, 14_400.0, 43_200.0, 108_000.0),
    ),
    "audio_emotion": QuotaSpec(
        name="audio_emotion",
        unit="seconds",
        category="emotion_seconds",
        defaults=_plan_limits(1_800.0, 14_400.0, 43_200.0, 108_000.0),
    ),
    "audio_stems": QuotaSpec(
        name="audio_stems",
        unit="seconds",
        category="stem_seconds",
        defaults=_plan_limits(600.0, 3_600.0, 10_800.0, 36_000.0),
    ),
}


def quota_plan_and_period(
    *, workspace_id: UUID, billing_repository
) -> tuple[BillingPlanKey, datetime, datetime]:
    """The plan quotas apply to, and the billing period usage is counted in."""
    subscription = billing_repository.get_subscription(workspace_id=workspace_id)
    plan_key = effective_plan_key(subscription)
    start = subscription.current_period_start
    end = subscription.current_period_end
    if not start or not end:
        start, end = monthly_period()
    return plan_key, start, end


def check_quota(
    *,
    workspace_id: UUID,
    provider: str,
    category: str,
    billing_repository,
    operations_repository,
    estimated_quantity: float,
) -> None:
    spec = PROVIDER_QUOTAS.get(provider)
    if spec is None:
        return
    plan_key, start, end = quota_plan_and_period(
        workspace_id=workspace_id, billing_repository=billing_repository
    )
    from app.core.config import get_settings

    limit = spec.limit(get_settings(), plan_key)
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


class PlanFeatureQuotaGuard:
    """Per-plan quotas for the remote AI features, enforced by the model gateway.

    Applies only to routes whose ``budget_feature`` is in FEATURE_QUOTAS (the
    remote vision, embeddings and audio routes) and to calls attributed to a
    workspace. Local GPU/CPU routes carry no such feature and are never metered.
    Usage is counted per vendor (the route's provider) and feature category.
    """

    def __init__(self, *, billing_repository, operations_repository, settings=None) -> None:
        self.billing_repository = billing_repository
        self.operations_repository = operations_repository
        self._settings = settings

    def _settings_or_default(self) -> object:
        if self._settings is not None:
            return self._settings
        from app.core.config import get_settings

        return get_settings()

    def applies(self, *, route, context) -> bool:
        return route.budget_feature in FEATURE_QUOTAS and context.workspace_id is not None

    def check(self, *, route, context, quantity: float) -> None:
        from app.services.model_gateway import FeatureQuotaExceeded

        spec = FEATURE_QUOTAS[route.budget_feature]
        workspace_id = _as_uuid(context.workspace_id)
        plan_key, start, end = quota_plan_and_period(
            workspace_id=workspace_id, billing_repository=self.billing_repository
        )
        limit = spec.limit(self._settings_or_default(), plan_key)
        used = float(
            self.operations_repository.count_usage(
                workspace_id=workspace_id,
                provider=route.provider,
                category=spec.category,
                period_start=start,
                period_end=end,
            )
        )
        if used + quantity > limit + 1e-9:
            raise FeatureQuotaExceeded(
                (
                    f"The {plan_key.value} plan's {spec.name} quota is used up. Used: "
                    f"{used:.1f} {spec.unit}, Limit: {limit:.1f}. Required: {quantity:.1f}"
                ),
                feature=spec.name,
            )

    def record(self, *, route, context, quantity: float, actual_cost_usd: float) -> None:
        spec = FEATURE_QUOTAS[route.budget_feature]
        quantity = max(0.0, float(quantity))
        self.operations_repository.record_cost(
            workspace_id=_as_uuid(context.workspace_id),
            project_id=context.project_id,
            provider=route.provider,
            category=spec.category,
            quantity=quantity,
            unit=spec.unit,
            unit_cost_usd=round(actual_cost_usd / quantity, 8) if quantity > 0 else 0.0,
            metadata={
                "feature": spec.name,
                "route_id": route.route_id,
                "model_revision": route.model_revision,
                # One row per successful call (also the idempotency source id).
                "usage_id": uuid4().hex,
            },
        )


def _as_uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


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
