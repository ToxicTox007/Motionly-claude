"""End-to-end role enforcement for the ``require_role`` dependency (Task 19).

``test_role_route_inventory.py`` proves every route carries the *right* guard;
this proves the guard actually admits/denies owner, admin and member *actors* at
request time and returns the stable 403 code ``workspace_role_forbidden``.

We drive the real app through a ``TestClient`` and only override ``get_actor``
so each request runs as a chosen workspace role; the ``require_role`` guards on
the live routes are exercised unchanged.
"""

import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "local")

from app.api.dependencies import Actor, get_actor  # noqa: E402
from app.main import create_app  # noqa: E402

_USER_ID = UUID("00000000-0000-4000-8000-000000000001")
_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")


def _client_as(role: str) -> TestClient:
    app = create_app()

    def _actor() -> Actor:
        return Actor(user_id=_USER_ID, workspace_id=_WORKSPACE_ID, role=role)

    app.dependency_overrides[get_actor] = _actor
    return TestClient(app, raise_server_exceptions=False)


# (method, path, body) tuples for one representative route per protected area.
_OWNER_ONLY = [
    ("POST", "/billing/checkout", {"plan": "creator"}),
    ("POST", "/billing/cancel", {}),
]
_OWNER_OR_ADMIN = [
    ("GET", "/phase5/brand-kits", None),
    ("POST", "/phase5/brand-kits", {"name": "Kit", "settings": {}}),
    ("GET", "/phase5/templates", None),
    ("POST", "/phase5/templates", {"name": "Tpl", "definition": {}}),
]


def _call(client: TestClient, method: str, path: str, body):
    if method == "GET":
        return client.get(path, headers={"x-motionly-workspace-id": str(_WORKSPACE_ID)})
    return client.request(
        method, path, json=body, headers={"x-motionly-workspace-id": str(_WORKSPACE_ID)}
    )


def _is_role_forbidden(response) -> bool:
    if response.status_code != 403:
        return False
    body = response.json()
    return body.get("error", {}).get("code") == "workspace_role_forbidden"


@pytest.mark.parametrize("method, path, body", _OWNER_ONLY)
def test_owner_only_routes_block_admin_and_member(method, path, body):
    for role in ("admin", "member"):
        response = _call(_client_as(role), method, path, body)
        assert _is_role_forbidden(response), (
            f"{role} should be forbidden on {method} {path}: {response.status_code} {response.text}"
        )


@pytest.mark.parametrize("method, path, body", _OWNER_ONLY)
def test_owner_only_routes_admit_owner(method, path, body):
    response = _call(_client_as("owner"), method, path, body)
    assert not _is_role_forbidden(response), (
        f"owner must not be role-forbidden on {method} {path}: {response.text}"
    )


@pytest.mark.parametrize("method, path, body", _OWNER_OR_ADMIN)
def test_owner_admin_routes_block_member(method, path, body):
    response = _call(_client_as("member"), method, path, body)
    assert _is_role_forbidden(response), (
        f"member should be forbidden on {method} {path}: {response.status_code} {response.text}"
    )


@pytest.mark.parametrize("method, path, body", _OWNER_OR_ADMIN)
@pytest.mark.parametrize("role", ["owner", "admin"])
def test_owner_admin_routes_admit_owner_and_admin(role, method, path, body):
    response = _call(_client_as(role), method, path, body)
    assert not _is_role_forbidden(response), (
        f"{role} must not be role-forbidden on {method} {path}: {response.text}"
    )


def test_members_can_edit_and_render_projects():
    """A member hits project edit/render routes without a role 403 (the body may
    still 404/422 — we only assert the role guard does not block members)."""
    client = _client_as("member")
    project_id = uuid4()
    for method, path, body in [
        ("POST", "/projects", {"name": "Draft"}),
        ("POST", f"/projects/{project_id}/renders", {}),
    ]:
        response = _call(client, method, path, body)
        assert not _is_role_forbidden(response), (
            f"member must be able to edit/render ({method} {path}): {response.text}"
        )
