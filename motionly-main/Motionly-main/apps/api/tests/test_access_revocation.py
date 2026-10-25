"""Bans, removals, demotions and downgrades take effect on the next request.

* The membership check resolves roles through the ban-aware
  ``motionly_workspace_member_role`` RPC (pgTAP covers its SQL behaviour in
  supabase/tests/user_access_revocation_test.sql).
* Banning a user drops their cached roles in every workspace, so a role cached
  before the ban cannot keep admitting them.
* An API key stops working when its creator is removed, demoted, banned, or
  when the workspace loses the ``api_access`` plan entitlement.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "local")

from app.api.errors import ApiError  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories.api_keys import InMemoryApiKeyRepository, generate_api_key  # noqa: E402
from app.repositories.billing import SubscriptionRecord  # noqa: E402
from app.schemas.billing import BillingPlanKey, SubscriptionStatus  # noqa: E402
from app.services import membership_cache  # noqa: E402
from app.services.auth import require_workspace_membership  # noqa: E402
from app.services.auth_admin import AuthAdminClient  # noqa: E402

_USER = UUID("00000000-0000-4000-8000-0000000000a1")
_WORKSPACE = UUID("00000000-0000-4000-8000-0000000000b1")
SUPABASE = "https://supabase.example.test"


class FakeMembershipRpc:
    """motionly_workspace_member_role over PostgREST: role rows or [] (denied)."""

    def __init__(self) -> None:
        self.roles: dict[tuple[str, str], str] = {}
        self.banned: set[str] = set()
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, headers, params, timeout):  # noqa: ANN001
        self.calls.append((url, dict(params)))
        assert url == f"{SUPABASE}/rest/v1/rpc/motionly_workspace_member_role"
        key = (params["p_workspace_id"], params["p_user_id"])
        if params["p_user_id"] in self.banned or key not in self.roles:
            return SimpleNamespace(status_code=200, json=lambda: [])
        role = self.roles[key]
        return SimpleNamespace(status_code=200, json=lambda: [{"role": role}])


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setenv("AUTH_MEMBERSHIP_CACHE_TTL_SECONDS", "300")
    membership_cache.set_membership_cache_for_test(membership_cache.InMemoryMembershipCache())
    yield
    membership_cache.set_membership_cache_for_test(None)


@pytest.fixture
def rpc(monkeypatch) -> FakeMembershipRpc:
    fake = FakeMembershipRpc()
    monkeypatch.setattr("app.services.auth.httpx.get", fake.get)
    return fake


def _check(user_id: UUID = _USER, workspace_id: UUID = _WORKSPACE) -> str:
    return require_workspace_membership(
        supabase_url=SUPABASE,
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
    )


# --------------------------------------------------------------------------
# Membership check
# --------------------------------------------------------------------------


def test_membership_is_resolved_by_the_ban_aware_rpc(rpc) -> None:
    rpc.roles[(str(_WORKSPACE), str(_USER))] = "admin"
    assert _check() == "admin"
    url, params = rpc.calls[0]
    assert url.endswith("/rest/v1/rpc/motionly_workspace_member_role")
    assert params == {"p_workspace_id": str(_WORKSPACE), "p_user_id": str(_USER)}


def test_a_banned_user_is_refused_once_the_rpc_reports_it(rpc) -> None:
    rpc.banned.add(str(_USER))
    rpc.roles[(str(_WORKSPACE), str(_USER))] = "owner"
    with pytest.raises(ApiError) as exc:
        _check()
    assert exc.value.status_code == 403
    assert exc.value.code == "workspace_access_denied"


def test_invalidate_user_drops_cached_roles_in_every_workspace(rpc) -> None:
    other_workspace, other_user = uuid4(), uuid4()
    for workspace in (_WORKSPACE, other_workspace):
        rpc.roles[(str(workspace), str(_USER))] = "owner"
    rpc.roles[(str(_WORKSPACE), str(other_user))] = "member"
    _check(_USER, _WORKSPACE)
    _check(_USER, other_workspace)
    _check(other_user, _WORKSPACE)
    assert len(rpc.calls) == 3

    membership_cache.invalidate_user(_USER)

    assert membership_cache.cached_role(_USER, _WORKSPACE) is None
    assert membership_cache.cached_role(_USER, other_workspace) is None
    assert membership_cache.cached_role(other_user, _WORKSPACE) == "member"


def test_redis_backend_invalidates_a_user_across_workspaces() -> None:
    deleted: list[str] = []

    class FakeRedis:
        def scan_iter(self, match):  # noqa: ANN001
            assert match == membership_cache.user_scan_pattern(_USER)
            return iter([f"motionly:workspace-membership:{uuid4()}:{_USER}"])

        def delete(self, *keys):  # noqa: ANN002
            deleted.extend(keys)

    backend = membership_cache.RedisMembershipCache.__new__(membership_cache.RedisMembershipCache)
    backend.redis = FakeRedis()
    backend.invalidate_user(_USER)
    assert len(deleted) == 1 and deleted[0].endswith(f":{_USER}")


# --------------------------------------------------------------------------
# Admin ban -> cached roles dropped
# --------------------------------------------------------------------------

ADMIN_TOKEN = "admin-token-for-tests"


def _gotrue_transport(user_id: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/auth/v1/admin/users/{user_id}"
        body = request.read().decode()
        banned = "876000h" in body
        return httpx.Response(
            200,
            json={
                "id": user_id,
                "email": "user@example.test",
                "banned_until": "2126-01-01T00:00:00Z" if banned else None,
            },
        )

    return httpx.MockTransport(handler)


def test_banning_a_user_revokes_their_cached_workspace_access(rpc) -> None:
    rpc.roles[(str(_WORKSPACE), str(_USER))] = "owner"
    assert _check() == "owner"

    app = create_app()
    app.state.settings.admin_api_token = ADMIN_TOKEN
    app.state.settings.skip_api_auth = True
    app.state.auth_admin = AuthAdminClient(
        supabase_url=SUPABASE,
        service_role_key="service-role",
        transport=_gotrue_transport(str(_USER)),
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/admin/users/ban",
        json={"user_id": str(_USER)},
        headers={"x-motionly-admin-token": ADMIN_TOKEN},
    )
    assert response.status_code == 200, response.text
    assert response.json()["banned"] is True

    # GoTrue now reports the ban; the cached "owner" entry is gone, so the next
    # request re-resolves and is refused.
    rpc.banned.add(str(_USER))
    assert membership_cache.cached_role(_USER, _WORKSPACE) is None
    with pytest.raises(ApiError) as exc:
        _check()
    assert exc.value.code == "workspace_access_denied"


# --------------------------------------------------------------------------
# API keys are re-validated on every request
# --------------------------------------------------------------------------


@pytest.fixture
def keyed_app(monkeypatch, rpc):
    monkeypatch.setenv("SKIP_API_AUTH", "false")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unit-test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.state.settings.supabase_url = SUPABASE
    app.state.settings.supabase_service_role_key = "service-role"
    repo = InMemoryApiKeyRepository()
    raw_key, fingerprint, prefix, last4 = generate_api_key()
    repo.create_key(
        workspace_id=_WORKSPACE,
        name="CI",
        fingerprint=fingerprint,
        prefix=prefix,
        last4=last4,
        created_by=_USER,
    )
    app.state.api_key_repository = repo
    app.state.billing_repository.subscriptions[_WORKSPACE] = SubscriptionRecord(
        workspace_id=_WORKSPACE, plan=BillingPlanKey.AGENCY, status=SubscriptionStatus.ACTIVE
    )
    rpc.roles[(str(_WORKSPACE), str(_USER))] = "owner"
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"authorization": f"Bearer {raw_key}", "x-motionly-workspace-id": str(_WORKSPACE)}
    yield client, app, rpc, headers
    get_settings.cache_clear()


def _list_projects(client, headers):
    return client.get("/projects", headers=headers)


def test_api_key_works_while_its_creator_and_plan_allow_it(keyed_app) -> None:
    client, _app, _rpc, headers = keyed_app
    response = _list_projects(client, headers)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "change",
    ["removed", "banned", "demoted"],
)
def test_api_key_stops_working_when_its_creator_loses_access(keyed_app, change) -> None:
    client, _app, rpc, headers = keyed_app
    assert _list_projects(client, headers).status_code == 200

    key = (str(_WORKSPACE), str(_USER))
    if change == "removed":
        rpc.roles.pop(key)
    elif change == "banned":
        rpc.banned.add(str(_USER))
    else:
        rpc.roles[key] = "member"
    # Team changes and bans invalidate the cache (team.py / admin_users.py).
    membership_cache.invalidate_user(_USER)

    response = _list_projects(client, headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "api_key_creator_inactive"


@pytest.mark.parametrize(
    ("plan", "status"),
    [
        (BillingPlanKey.PRO, SubscriptionStatus.ACTIVE),
        (BillingPlanKey.AGENCY, SubscriptionStatus.CANCELED),
        (BillingPlanKey.FREE, SubscriptionStatus.NONE),
    ],
)
def test_api_key_stops_working_when_the_plan_loses_api_access(keyed_app, plan, status) -> None:
    client, app, _rpc, headers = keyed_app
    assert _list_projects(client, headers).status_code == 200
    app.state.billing_repository.subscriptions[_WORKSPACE] = SubscriptionRecord(
        workspace_id=_WORKSPACE, plan=plan, status=status
    )

    response = _list_projects(client, headers)
    assert response.status_code == 402, response.text
    assert response.json()["error"]["code"] == "plan_feature_required"

    # Upgrading again restores the key (it was refused, not revoked).
    app.state.billing_repository.subscriptions[_WORKSPACE] = SubscriptionRecord(
        workspace_id=_WORKSPACE, plan=BillingPlanKey.AGENCY, status=SubscriptionStatus.ACTIVE
    )
    assert _list_projects(client, headers).status_code == 200


def test_api_key_creator_check_does_not_mask_membership_service_failures(
    keyed_app, monkeypatch
) -> None:
    client, _app, _rpc, headers = keyed_app
    membership_cache.invalidate_user(_USER)
    monkeypatch.setattr(
        "app.services.auth.httpx.get",
        lambda *args, **kwargs: SimpleNamespace(status_code=503, json=lambda: {}),
    )
    response = _list_projects(client, headers)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "auth_membership_check_failed"
