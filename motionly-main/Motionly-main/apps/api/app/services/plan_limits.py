"""Plan limit helpers — the single enforcement point for plan entitlements.

Every seat / brand-kit / bulk-batch / API-access / premium-effects check reads
the limits from the ONE plan source (``app/config/plans.json`` via
``app.services.billing.PLAN_DEFINITIONS``). Do not hard-code limits elsewhere.

Consumed by:
- Task 20 (seats): ``plan_seat_limit(plan_key)`` or
  ``require_plan_feature(subscription, "seats", current=n)``.
- Task 24 (API keys): ``require_plan_feature(subscription_or_plan, "api_access")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.api.errors import ApiError
from app.schemas.billing import BillingPlanKey, PlanEntitlements
from app.services.billing import PLAN_DEFINITIONS, plan_for_subscription_status

if TYPE_CHECKING:
    from app.repositories.billing import SubscriptionRecord

# Human-readable labels for the 402 message.
_FEATURE_LABELS = {
    "seats": "additional team seats",
    "brand_kits": "brand kits",
    "bulk_uploads": "bulk uploads",
    "api_access": "API access",
    "premium_effects": "premium effects",
    "broll": "B-roll",
    "priority_queue": "priority queue",
    "watermark": "watermark removal",
}


def _coerce_plan_key(value: object) -> BillingPlanKey:
    """Accept a BillingPlanKey, its string value, or legacy aliases."""
    if isinstance(value, BillingPlanKey):
        return value
    normalized = str(value or "free").lower()
    if normalized in {"starter", ""}:
        return BillingPlanKey.FREE
    try:
        return BillingPlanKey(normalized)
    except ValueError:
        return BillingPlanKey.FREE


def _effective_plan_key(plan_or_subscription: object) -> BillingPlanKey:
    """Resolve the entitlement-granting plan key.

    A SubscriptionRecord is downgraded to Free unless its status is active
    (so a cancelled/none subscription cannot unlock paid features).
    """
    if hasattr(plan_or_subscription, "plan") and hasattr(plan_or_subscription, "status"):
        return plan_for_subscription_status(
            plan=_coerce_plan_key(plan_or_subscription.plan),
            status=plan_or_subscription.status,
        )
    return _coerce_plan_key(plan_or_subscription)


def plan_entitlements(plan_key: object) -> PlanEntitlements:
    """Return the entitlements for a plan key (string or enum)."""
    return PLAN_DEFINITIONS[_coerce_plan_key(plan_key)].entitlements


def plan_seat_limit(plan_key: object) -> int:
    """The number of seats a plan allows (Task 20 reads this)."""
    return int(plan_entitlements(plan_key).seats)


def require_plan_feature(
    plan_or_subscription: BillingPlanKey | str | SubscriptionRecord,
    feature: str,
    *,
    current: int | None = None,
) -> None:
    """Raise ``ApiError(402, plan_feature_required)`` when a plan lacks a feature.

    - Boolean entitlements (``api_access``, ``brand_kits``, ``bulk_uploads``,
      ``premium_effects`` ...): raises when the plan's flag is falsy.
    - Numeric entitlements (``seats``, ``monthly_renders``): pass ``current=n``;
      raises when adding one more would exceed the limit (i.e. current >= limit).

    Accepts either a plan key (enum or string) or a SubscriptionRecord, in which
    case an inactive subscription is treated as Free.
    """
    plan_key = _effective_plan_key(plan_or_subscription)
    entitlements = PLAN_DEFINITIONS[plan_key].entitlements
    value = getattr(entitlements, feature, None)
    if value is None:
        raise ApiError(
            422,
            "plan_feature_unknown",
            f"Unknown plan feature: {feature!r}.",
        )

    label = _FEATURE_LABELS.get(feature, feature.replace("_", " "))

    if isinstance(value, bool):
        if not value:
            raise ApiError(
                402,
                "plan_feature_required",
                f"Your plan does not include {label}. Upgrade to unlock it.",
            )
        return

    # Numeric limit.
    if current is None:
        return
    if current >= int(value):
        raise ApiError(
            402,
            "plan_feature_required",
            (
                f"Your plan allows {int(value)} {label}. "
                "Upgrade your plan to add more."
            ),
        )
