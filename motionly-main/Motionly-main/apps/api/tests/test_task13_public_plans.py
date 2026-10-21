"""Task 13 / B33: a single public source of truth for billing plans.

The pricing page reads its plans from the API instead of a hand-written copy.
The endpoint is unauthenticated (no Actor dependency) and is backed by the
existing PLAN_DEFINITIONS in app.services.billing.
"""

import asyncio

from app.schemas.billing import BillingPlanKey
from app.services.billing import PLAN_DEFINITIONS


def test_public_plans_endpoint_returns_every_defined_plan() -> None:
    from app.api.billing import list_public_plans

    response = asyncio.run(list_public_plans())

    returned_keys = [plan.key for plan in response.plans]
    assert returned_keys == list(PLAN_DEFINITIONS.keys())
    free = next(plan for plan in response.plans if plan.key == BillingPlanKey.FREE)
    assert free.price_paise == 0
    assert free.features  # non-empty, sourced from PLAN_DEFINITIONS


def test_public_plans_endpoint_matches_plan_definitions_exactly() -> None:
    from app.api.billing import list_public_plans

    response = asyncio.run(list_public_plans())

    for plan in response.plans:
        source = PLAN_DEFINITIONS[plan.key]
        assert plan.name == source.name
        assert plan.price_paise == source.price_paise
        assert plan.features == source.features
        assert plan.entitlements == source.entitlements
