"""Plan gating for API-key access (Task 24).

Only plans whose entitlements include ``api_access`` (today: Agency) may mint
API keys. Task 23 owns ``app.services.plan_limits.require_plan_feature``; this
module prefers that shared helper and falls back to reading the ``api_access``
entitlement off ``PLAN_DEFINITIONS`` directly if the module is not yet present.
Either way the failure is a single, stable error so the UI and tests can rely
on it.
"""

from __future__ import annotations

from app.api.errors import ApiError
from app.schemas.billing import BillingPlanKey
from app.services.billing import PLAN_DEFINITIONS, effective_plan_key

API_ACCESS_FEATURE = "api_access"


def _plan_has_api_access(plan_key: BillingPlanKey) -> bool:
    definition = PLAN_DEFINITIONS.get(plan_key)
    if definition is None:
        return False
    return bool(getattr(definition.entitlements, API_ACCESS_FEATURE, False))


def require_api_access(subscription) -> BillingPlanKey:
    """Ensure the workspace's effective plan includes API access.

    Returns the effective plan key on success; raises ``ApiError`` 402
    ``plan_feature_required`` otherwise. ``subscription`` is a billing
    ``SubscriptionRecord``.
    """
    effective_plan = effective_plan_key(subscription)

    # Prefer Task 23's shared helper when it is on disk; it raises the canonical
    # 402 plan_feature_required. Import lazily so Task 24 does not hard-depend on
    # the module landing first.
    try:
        from app.services.plan_limits import require_plan_feature
    except ImportError:
        require_plan_feature = None

    if require_plan_feature is not None:
        require_plan_feature(effective_plan, API_ACCESS_FEATURE)
        return effective_plan

    if not _plan_has_api_access(effective_plan):
        raise ApiError(
            402,
            "plan_feature_required",
            "API access requires the Agency plan.",
        )
    return effective_plan
