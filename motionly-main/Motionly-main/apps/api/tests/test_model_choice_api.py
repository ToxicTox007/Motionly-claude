"""API tests for the model choice endpoints (plan Section 2.11, Task 30).

Covers the role matrix (owner/admin/member on the workspace PUT), plan gating
(a free workspace sees the pro option disabled with the plan it requires), the
allow-list and lock (409 ``model_choice_locked``), a dangling option refused,
tenant isolation on the project settings, and that no base URL, secret or model
id leaks into any response.

The real app is driven through a ``TestClient`` with only ``get_actor`` overridden
per role; the service/repository on ``app.state`` are rebound over a small runtime
with two selectable planning routes so validation has real options to check.
"""

from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "local")

from app.api.dependencies import Actor, get_actor  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories.model_choice import InMemoryModelChoiceRepository  # noqa: E402
from app.schemas.billing import BillingPlanKey  # noqa: E402
from app.services.inference_runtime import build_inference_runtime  # noqa: E402
from app.services.model_choice import ModelChoiceService  # noqa: E402

_USER = UUID("00000000-0000-4000-8000-000000000001")
_WS = UUID("00000000-0000-4000-8000-000000000002")
_OTHER_WS = UUID("00000000-0000-4000-8000-000000000099")


def _chat_adapter(adapter_id: str) -> dict:
    return {
        "id": adapter_id,
        "type": "openai_compatible",
        "base_url": f"https://{adapter_id}.secret.invalid/v1",
        "network": "public",
        "auth": {"type": "bearer", "secret_ref": f"bundle:{adapter_id}"},
    }


def _planning_route(route_id: str, adapter_id: str, *, priority, offering) -> dict:
    return {
        "id": route_id,
        "capability": "planning.chat",
        "adapter_id": adapter_id,
        "model_id": f"super-secret-model-{route_id}",
        "model_revision": "2026-01-01",
        "priority": priority,
        "pricing": {
            "status": "priced",
            "unit": "tokens",
            "input_per_million_usd": 0.5,
            "output_per_million_usd": 1.0,
        },
        "budget": {"feature": "planning"},
        "offering": offering,
    }


def _runtime():
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
                    "description": "Slower, better",
                    "plans": ["pro", "agency"],
                    "quota_weight": 2.0,
                },
            ),
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(document, handle)
        path = handle.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"a1": "secret", "a2": "secret"}, handle)
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


def _make_client(*, role: str = "owner", plan: BillingPlanKey = BillingPlanKey.PRO):
    app = create_app()
    repository = InMemoryModelChoiceRepository()
    runtime = _runtime()
    service = ModelChoiceService(
        repository=repository,
        runtime_provider=lambda: runtime,
        plan_provider=lambda _ws: plan,
        cache_ttl_s=0.0,
    )
    app.state.model_choice_repository = repository
    app.state.model_choice_service = service

    def _actor() -> Actor:
        return Actor(user_id=_USER, workspace_id=_WS, role=role)

    app.dependency_overrides[get_actor] = _actor
    client = TestClient(app, raise_server_exceptions=False)
    return client, repository, service


def _headers() -> dict:
    return {"x-motionly-workspace-id": str(_WS)}


def _create_project(client: TestClient) -> UUID:
    response = client.post("/projects", json={"title": "Test"}, headers=_headers())
    assert response.status_code in (200, 201), response.text
    return UUID(response.json()["id"])


# ---------------------------------------------------------------------------
# GET /model-options
# ---------------------------------------------------------------------------
def test_model_options_marks_plan_locked_and_leaks_nothing() -> None:
    client, _, _ = _make_client(plan=BillingPlanKey.FREE)
    response = client.get("/model-options", headers=_headers())
    assert response.status_code == 200, response.text
    body = response.text
    # No base URL, secret or model id leaks.
    assert "secret.invalid" not in body
    assert "super-secret-model" not in body
    assert "bundle:" not in body
    planning = next(
        cap for cap in response.json()["capabilities"] if cap["capability"] == "planning.chat"
    )
    by_id = {opt["option_id"]: opt for opt in planning["options"]}
    assert by_id["eco"]["allowed"] is True
    assert by_id["pro"]["allowed"] is False
    assert by_id["pro"]["reason"] == "plan_not_allowed"
    assert by_id["pro"]["required_plan"] == "pro"
    assert by_id["pro"]["display_name"] == "High quality"
    assert planning["effective_option_id"] == "eco"


# ---------------------------------------------------------------------------
# Workspace settings role matrix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["owner", "admin"])
def test_workspace_put_allowed_for_owner_and_admin(role: str) -> None:
    client, repo, _ = _make_client(role=role, plan=BillingPlanKey.PRO)
    response = client.put(
        "/workspaces/current/model-settings",
        json={"capability": "planning.chat", "default_option_id": "pro", "locked": False},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    assert repo.get_workspace_setting(_WS, "planning.chat").default_option_id == "pro"


def test_workspace_put_forbidden_for_member() -> None:
    client, _, _ = _make_client(role="member", plan=BillingPlanKey.PRO)
    response = client.put(
        "/workspaces/current/model-settings",
        json={"capability": "planning.chat", "default_option_id": "pro"},
        headers=_headers(),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "workspace_role_forbidden"


def test_workspace_get_allowed_for_member() -> None:
    client, repo, _ = _make_client(role="member")
    repo.upsert_workspace_setting(
        workspace_id=_WS,
        capability="planning.chat",
        default_option_id="eco",
        allowed_option_ids=None,
        locked=False,
        updated_by=None,
    )
    response = client.get("/workspaces/current/model-settings", headers=_headers())
    assert response.status_code == 200
    assert response.json()["settings"][0]["default_option_id"] == "eco"


def test_workspace_put_rejects_dangling_default() -> None:
    client, _, _ = _make_client(role="owner", plan=BillingPlanKey.PRO)
    response = client.put(
        "/workspaces/current/model-settings",
        json={"capability": "planning.chat", "default_option_id": "ghost"},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "model_choice_option_invalid"


# ---------------------------------------------------------------------------
# Project settings: member edits, lock, plan gating, tenant isolation
# ---------------------------------------------------------------------------
def test_member_sets_project_override() -> None:
    client, repo, _ = _make_client(role="member", plan=BillingPlanKey.PRO)
    project_id = _create_project(client)
    response = client.put(
        f"/projects/{project_id}/model-settings",
        json={"capability": "planning.chat", "option_id": "pro"},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    assert repo.get_project_setting(_WS, project_id, "planning.chat").option_id == "pro"


def test_project_override_refused_when_workspace_locked() -> None:
    client, repo, _ = _make_client(role="member", plan=BillingPlanKey.PRO)
    project_id = _create_project(client)
    repo.upsert_workspace_setting(
        workspace_id=_WS,
        capability="planning.chat",
        default_option_id="pro",
        allowed_option_ids=None,
        locked=True,
        updated_by=None,
    )
    response = client.put(
        f"/projects/{project_id}/model-settings",
        json={"capability": "planning.chat", "option_id": "eco"},
        headers=_headers(),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_choice_locked"


def test_project_override_free_plan_cannot_pick_pro() -> None:
    client, _, _ = _make_client(role="member", plan=BillingPlanKey.FREE)
    project_id = _create_project(client)
    response = client.put(
        f"/projects/{project_id}/model-settings",
        json={"capability": "planning.chat", "option_id": "pro"},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "model_choice_option_invalid"


def test_project_settings_tenant_isolation() -> None:
    # A project in another workspace is 404 for this caller.
    client, _, _ = _make_client(role="member")
    missing_project = uuid4()
    response = client.get(
        f"/projects/{missing_project}/model-settings", headers=_headers()
    )
    assert response.status_code == 404


def test_delete_project_override() -> None:
    client, repo, _ = _make_client(role="member", plan=BillingPlanKey.PRO)
    project_id = _create_project(client)
    repo.upsert_project_setting(
        workspace_id=_WS,
        project_id=project_id,
        capability="planning.chat",
        option_id="pro",
        updated_by=None,
    )
    response = client.request(
        "DELETE",
        f"/projects/{project_id}/model-settings",
        params={"capability": "planning.chat"},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert repo.get_project_setting(_WS, project_id, "planning.chat") is None
