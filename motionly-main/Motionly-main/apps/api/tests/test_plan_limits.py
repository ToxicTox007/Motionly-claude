"""Tests for app.services.plan_limits (Task 23; consumed by Tasks 20 & 24)."""

import json
from pathlib import Path

import pytest

from app.api.errors import ApiError
from app.schemas.billing import BillingPlanKey
from app.services.billing import PLAN_DEFINITIONS
from app.services.plan_limits import (
    plan_entitlements,
    plan_seat_limit,
    require_plan_feature,
)

PLANS_JSON = Path(__file__).resolve().parents[1] / "app" / "config" / "plans.json"


def test_plan_definitions_load_from_plans_json() -> None:
    data = json.loads(PLANS_JSON.read_text())
    by_key = {p["key"]: p for p in data["plans"]}
    # Prices (INR paise) come straight from plans.json, not hard-coded code.
    assert PLAN_DEFINITIONS[BillingPlanKey.FREE].price_paise == by_key["free"]["price_paise"] == 0
    assert PLAN_DEFINITIONS[BillingPlanKey.CREATOR].price_paise == 29900
    assert PLAN_DEFINITIONS[BillingPlanKey.PRO].price_paise == 79900
    assert PLAN_DEFINITIONS[BillingPlanKey.AGENCY].price_paise == 199900
    assert PLAN_DEFINITIONS[BillingPlanKey.AGENCY].currency == "INR"


def test_plan_seat_limit_reads_the_same_source() -> None:
    assert plan_seat_limit(BillingPlanKey.FREE) == 1
    assert plan_seat_limit(BillingPlanKey.PRO) == 2
    assert plan_seat_limit(BillingPlanKey.AGENCY) == 5
    # String keys are accepted too (callers may pass raw db values).
    assert plan_seat_limit("agency") == 5


def test_require_plan_feature_boolean_gate() -> None:
    # Agency has api_access; Free does not.
    require_plan_feature(BillingPlanKey.AGENCY, "api_access")
    with pytest.raises(ApiError) as exc:
        require_plan_feature(BillingPlanKey.FREE, "api_access")
    assert exc.value.status_code == 402
    assert exc.value.code == "plan_feature_required"


def test_require_plan_feature_numeric_seat_limit() -> None:
    # Agency allows 5 seats; current=5 means adding one more exceeds.
    require_plan_feature(BillingPlanKey.AGENCY, "seats", current=4)
    with pytest.raises(ApiError) as exc:
        require_plan_feature(BillingPlanKey.AGENCY, "seats", current=5)
    assert exc.value.status_code == 402
    assert exc.value.code == "plan_feature_required"


def test_require_plan_feature_accepts_subscription_record() -> None:
    from app.repositories.billing import SubscriptionRecord
    from app.schemas.billing import SubscriptionStatus

    active = SubscriptionRecord(
        workspace_id=__import__("uuid").uuid4(),
        plan=BillingPlanKey.AGENCY,
        status=SubscriptionStatus.ACTIVE,
    )
    require_plan_feature(active, "api_access")

    past_due_free = SubscriptionRecord(
        workspace_id=__import__("uuid").uuid4(),
        plan=BillingPlanKey.FREE,
        status=SubscriptionStatus.NONE,
    )
    with pytest.raises(ApiError):
        require_plan_feature(past_due_free, "api_access")


def test_plan_entitlements_returns_model() -> None:
    ent = plan_entitlements(BillingPlanKey.PRO)
    assert ent.premium_effects is True
    assert ent.brand_kits is True
    assert ent.api_access is False
