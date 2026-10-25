"""ModelChoiceService resolution tests (plan Section 2.11, Tasks 30 and 31).

Covers the resolution order and validation reasons, plan gating (including lapsed
paid plans counting as free), the ``allowed_option_ids`` allow-list, the lock, a
dangling option id falling back with a reason, the short-TTL cache invalidated on
a write, and ``expected_route_for``.
"""

from __future__ import annotations

import json
import tempfile
from types import SimpleNamespace
from uuid import UUID

from app.repositories.model_choice import InMemoryModelChoiceRepository
from app.schemas.billing import BillingPlanKey
from app.services.inference_runtime import build_inference_runtime
from app.services.model_choice import (
    REASON_OPTION_UNAVAILABLE,
    REASON_PLAN_NOT_ALLOWED,
    REASON_WORKSPACE_LOCKED,
    REASON_WORKSPACE_RESTRICTED,
    SOURCE_OPERATOR,
    SOURCE_PROJECT,
    SOURCE_WORKSPACE,
    ModelChoiceService,
)

WS = UUID("11111111-1111-4111-8111-111111111111")
PROJECT = UUID("33333333-3333-4333-8333-333333333333")


def _chat_adapter(adapter_id: str) -> dict:
    return {
        "id": adapter_id,
        "type": "openai_compatible",
        "base_url": f"https://{adapter_id}.example.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": f"bundle:{adapter_id}"},
    }


def _planning_route(route_id: str, adapter_id: str, *, priority=100, offering=None) -> dict:
    route = {
        "id": route_id,
        "capability": "planning.chat",
        "adapter_id": adapter_id,
        "model_id": f"model-{route_id}",
        "model_revision": "2026-01-01",
        "priority": priority,
        "pricing": {
            "status": "priced",
            "unit": "tokens",
            "input_per_million_usd": 0.5,
            "output_per_million_usd": 1.0,
        },
        "budget": {"feature": "planning"},
    }
    if offering is not None:
        route["offering"] = offering
    return route


def _runtime():
    """A runtime with two selectable planning routes: eco (any plan) and pro-only."""
    document = {
        "version": 1,
        "adapters": [_chat_adapter("a1"), _chat_adapter("a2")],
        "models": [],
        "routes": [
            _planning_route(
                "eco",
                "a1",
                priority=50,
                offering={"selectable": True, "display_name": "Economy", "default": True},
            ),
            _planning_route(
                "pro",
                "a2",
                priority=100,
                offering={
                    "selectable": True,
                    "display_name": "High quality",
                    "plans": ["pro", "agency"],
                    "quota_weight": 2.0,
                },
            ),
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(document, handle)
        path = handle.name
    bundle = {"a1": "secret", "a2": "secret"}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(bundle, handle)
        bundle_path = handle.name
    settings = SimpleNamespace(
        app_env="test",
        ai_registry_file=path,
        gpu_workers_enabled=True,
        ai_block_noncommercial=False,
        ai_workspace_scoped_routes=False,
        ai_secrets_dir="/run/secrets",
        ai_secrets_bundle_file=bundle_path,
    )
    return build_inference_runtime(settings)


def _service(plan: BillingPlanKey = BillingPlanKey.FREE, *, repository=None, ttl=0.0):
    runtime = _runtime()
    repository = repository or InMemoryModelChoiceRepository()
    return (
        ModelChoiceService(
            repository=repository,
            runtime_provider=lambda: runtime,
            plan_provider=lambda _ws: plan,
            cache_ttl_s=ttl,
        ),
        repository,
    )


def test_operator_default_when_no_settings() -> None:
    service, _ = _service()
    resolved = service.resolve(capability="planning.chat", workspace_id=WS, project_id=None)
    assert resolved.route_id == "eco"
    assert resolved.choice_source == SOURCE_OPERATOR
    assert resolved.requested_option_id is None


def test_workspace_default_wins_over_operator() -> None:
    service, repo = _service(plan=BillingPlanKey.PRO)
    repo.upsert_workspace_setting(
        workspace_id=WS,
        capability="planning.chat",
        default_option_id="pro",
        allowed_option_ids=None,
        locked=False,
        updated_by=None,
    )
    resolved = service.resolve(capability="planning.chat", workspace_id=WS, project_id=None)
    assert resolved.route_id == "pro"
    assert resolved.choice_source == SOURCE_WORKSPACE


def test_project_override_wins_over_workspace() -> None:
    service, repo = _service(plan=BillingPlanKey.PRO)
    repo.upsert_workspace_setting(
        workspace_id=WS,
        capability="planning.chat",
        default_option_id="eco",
        allowed_option_ids=None,
        locked=False,
        updated_by=None,
    )
    repo.upsert_project_setting(
        workspace_id=WS,
        project_id=PROJECT,
        capability="planning.chat",
        option_id="pro",
        updated_by=None,
    )
    resolved = service.resolve(capability="planning.chat", workspace_id=WS, project_id=PROJECT)
    assert resolved.route_id == "pro"
    assert resolved.choice_source == SOURCE_PROJECT


def test_lock_ignores_project_override_with_reason() -> None:
    service, repo = _service(plan=BillingPlanKey.PRO)
    repo.upsert_workspace_setting(
        workspace_id=WS,
        capability="planning.chat",
        default_option_id="pro",
        allowed_option_ids=None,
        locked=True,
        updated_by=None,
    )
    repo.upsert_project_setting(
        workspace_id=WS,
        project_id=PROJECT,
        capability="planning.chat",
        option_id="eco",
        updated_by=None,
    )
    resolved = service.resolve(capability="planning.chat", workspace_id=WS, project_id=PROJECT)
    assert resolved.route_id == "pro"
    assert resolved.choice_source == SOURCE_WORKSPACE
    assert resolved.rejected_reason == REASON_WORKSPACE_LOCKED


def test_free_plan_cannot_pick_pro_option_falls_back() -> None:
    service, repo = _service(plan=BillingPlanKey.FREE)
    repo.upsert_project_setting(
        workspace_id=WS,
        project_id=PROJECT,
        capability="planning.chat",
        option_id="pro",
        updated_by=None,
    )
    resolved = service.resolve(capability="planning.chat", workspace_id=WS, project_id=PROJECT)
    # The free plan may not pick pro, so resolution falls back to the operator
    # default with the plan reason recorded.
    assert resolved.route_id == "eco"
    assert resolved.choice_source == SOURCE_OPERATOR
    assert resolved.rejected_reason == REASON_PLAN_NOT_ALLOWED


def test_lapsed_paid_plan_counts_as_free() -> None:
    # The service receives the effective (lapsed-aware) plan from plan_provider;
    # a lapsed PRO is passed as FREE and therefore may not pick the pro option.
    service, repo = _service(plan=BillingPlanKey.FREE)
    repo.upsert_workspace_setting(
        workspace_id=WS,
        capability="planning.chat",
        default_option_id="pro",
        allowed_option_ids=None,
        locked=False,
        updated_by=None,
    )
    resolved = service.resolve(capability="planning.chat", workspace_id=WS, project_id=None)
    assert resolved.route_id == "eco"
    assert resolved.rejected_reason == REASON_PLAN_NOT_ALLOWED


def test_allow_list_restricts_options() -> None:
    service, repo = _service(plan=BillingPlanKey.PRO)
    repo.upsert_workspace_setting(
        workspace_id=WS,
        capability="planning.chat",
        default_option_id=None,
        allowed_option_ids=["eco"],  # pro is not allowed
        locked=False,
        updated_by=None,
    )
    repo.upsert_project_setting(
        workspace_id=WS,
        project_id=PROJECT,
        capability="planning.chat",
        option_id="pro",
        updated_by=None,
    )
    resolved = service.resolve(capability="planning.chat", workspace_id=WS, project_id=PROJECT)
    assert resolved.route_id == "eco"
    assert resolved.rejected_reason == REASON_WORKSPACE_RESTRICTED


def test_dangling_option_falls_back_with_reason() -> None:
    service, repo = _service(plan=BillingPlanKey.PRO)
    repo.upsert_project_setting(
        workspace_id=WS,
        project_id=PROJECT,
        capability="planning.chat",
        option_id="deleted-route",
        updated_by=None,
    )
    resolved = service.resolve(capability="planning.chat", workspace_id=WS, project_id=PROJECT)
    assert resolved.route_id == "eco"
    assert resolved.rejected_reason == REASON_OPTION_UNAVAILABLE


def test_expected_route_for_matches_resolution() -> None:
    service, repo = _service(plan=BillingPlanKey.PRO)
    repo.upsert_workspace_setting(
        workspace_id=WS,
        capability="planning.chat",
        default_option_id="pro",
        allowed_option_ids=None,
        locked=False,
        updated_by=None,
    )
    assert service.expected_route_for("planning.chat", WS, None) == "pro"
    assert service.expected_route_for("planning.chat", WS, PROJECT) == "pro"


def test_cache_invalidated_on_write() -> None:
    service, repo = _service(plan=BillingPlanKey.PRO, ttl=60.0)
    # Prime the cache with the operator default.
    primed = service.resolve(capability="planning.chat", workspace_id=WS, project_id=None)
    assert primed.route_id == "eco"
    repo.upsert_workspace_setting(
        workspace_id=WS,
        capability="planning.chat",
        default_option_id="pro",
        allowed_option_ids=None,
        locked=False,
        updated_by=None,
    )
    # Without invalidation the stale "eco" would be served; invalidate then re-resolve.
    service.invalidate_workspace(WS)
    again = service.resolve(capability="planning.chat", workspace_id=WS, project_id=None)
    assert again.route_id == "pro"


def test_options_view_marks_plan_locked_options() -> None:
    service, _ = _service(plan=BillingPlanKey.FREE)
    view = service.options_for_capability(
        capability="planning.chat", workspace_id=WS, project_id=None
    )
    assert view is not None
    by_id = {opt.route_id: opt for opt in view.options}
    assert by_id["eco"].allowed is True
    assert by_id["pro"].allowed is False
    assert by_id["pro"].reason == REASON_PLAN_NOT_ALLOWED
    assert by_id["pro"].required_plan == "pro"
    assert view.effective_route_id == "eco"


def test_validate_selectable_option_reasons() -> None:
    service, repo = _service(plan=BillingPlanKey.FREE)
    assert (
        service.validate_selectable_option(
            capability="planning.chat", workspace_id=WS, option_id="nope"
        )
        == REASON_OPTION_UNAVAILABLE
    )
    assert (
        service.validate_selectable_option(
            capability="planning.chat", workspace_id=WS, option_id="pro"
        )
        == REASON_PLAN_NOT_ALLOWED
    )
    assert (
        service.validate_selectable_option(
            capability="planning.chat", workspace_id=WS, option_id="eco"
        )
        is None
    )
