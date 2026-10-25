"""API unit tests for workspace API keys (Task 24).

Covers:
* keys are stored fingerprint-only (no raw secret persisted);
* the raw secret is returned exactly once, at creation;
* API keys reach only allow-listed routes and are refused elsewhere;
* only plans with ``api_access`` (Agency) may mint keys;
* a per-key per-minute rate limit keys on the key, not the workspace;
* revoking a key makes it stop working immediately (401).

Management routes (create/list/revoke) run as a signed-in owner/admin via a
``get_actor`` override. The API-key *auth* path is exercised end-to-end by
sending a real ``Authorization: Bearer mtly_...`` header through the live
``get_actor``.
"""

import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "local")

from app.api.dependencies import Actor, get_actor  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories.api_keys import InMemoryApiKeyRepository, generate_api_key  # noqa: E402
from app.repositories.billing import InMemoryBillingRepository, SubscriptionRecord  # noqa: E402
from app.schemas.billing import BillingPlanKey, SubscriptionStatus  # noqa: E402

_USER_ID = UUID("00000000-0000-4000-8000-000000000001")
_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")


def _agency_subscription() -> SubscriptionRecord:
    return SubscriptionRecord(
        workspace_id=_WORKSPACE_ID,
        plan=BillingPlanKey.AGENCY,
        status=SubscriptionStatus.ACTIVE,
    )


def _free_subscription() -> SubscriptionRecord:
    return SubscriptionRecord(
        workspace_id=_WORKSPACE_ID,
        plan=BillingPlanKey.FREE,
        status=SubscriptionStatus.NONE,
    )


def _management_client(role: str = "owner", *, subscription: SubscriptionRecord | None = None):
    """Client whose actor is a signed-in owner/admin (for create/list/revoke)."""
    app = create_app()
    app.state.api_key_repository = InMemoryApiKeyRepository()
    billing = InMemoryBillingRepository()
    billing.subscriptions[_WORKSPACE_ID] = subscription or _agency_subscription()
    app.state.billing_repository = billing

    def _actor() -> Actor:
        return Actor(user_id=_USER_ID, workspace_id=_WORKSPACE_ID, role=role, kind="user")

    app.dependency_overrides[get_actor] = _actor
    client = TestClient(app, raise_server_exceptions=False)
    return client, app


def _headers() -> dict[str, str]:
    return {"x-motionly-workspace-id": str(_WORKSPACE_ID)}


# ---------------------------------------------------------------------------
# Creation: shown once; fingerprint only
# ---------------------------------------------------------------------------


def test_create_returns_secret_once_and_persists_fingerprint_only():
    client, app = _management_client()
    response = client.post("/api-keys", json={"name": "CI"}, headers=_headers())
    assert response.status_code == 201, response.text
    body = response.json()
    secret = body["secret"]
    assert secret.startswith("mtly_")

    # What is persisted is the fingerprint + display hints, never the secret.
    repo: InMemoryApiKeyRepository = app.state.api_key_repository
    stored = list(repo._keys.values())
    assert len(stored) == 1
    record = stored[0]
    assert record.fingerprint != secret
    assert len(record.fingerprint) == 64
    assert record.prefix.startswith("mtly_")
    assert len(record.last4) == 4
    # No attribute anywhere holds the raw secret.
    assert secret not in record.__dict__.values()

    # The list view never exposes the secret again.
    listed = client.get("/api-keys", headers=_headers())
    assert listed.status_code == 200
    key_view = listed.json()["keys"][0]
    assert "secret" not in key_view
    assert key_view["prefix"] == record.prefix
    assert key_view["last4"] == record.last4


def test_list_is_empty_initially_then_reflects_created_keys():
    client, _app = _management_client()
    assert client.get("/api-keys", headers=_headers()).json() == {"keys": []}
    client.post("/api-keys", json={"name": "one"}, headers=_headers())
    client.post("/api-keys", json={"name": "two"}, headers=_headers())
    keys = client.get("/api-keys", headers=_headers()).json()["keys"]
    assert {k["name"] for k in keys} == {"one", "two"}


# ---------------------------------------------------------------------------
# Plan gating
# ---------------------------------------------------------------------------


def test_free_plan_cannot_create_keys():
    client, _app = _management_client(subscription=_free_subscription())
    response = client.post("/api-keys", json={"name": "CI"}, headers=_headers())
    assert response.status_code == 402, response.text
    assert response.json()["error"]["code"] == "plan_feature_required"


def test_agency_plan_can_create_keys():
    client, _app = _management_client(subscription=_agency_subscription())
    response = client.post("/api-keys", json={"name": "CI"}, headers=_headers())
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# Role gating (owner/admin only) — members are refused
# ---------------------------------------------------------------------------


def test_member_cannot_manage_keys():
    client, _app = _management_client(role="member")
    created = client.post("/api-keys", json={"name": "CI"}, headers=_headers())
    assert created.status_code == 403
    assert created.json()["error"]["code"] == "workspace_role_forbidden"


def test_admin_can_manage_keys():
    client, _app = _management_client(role="admin")
    assert client.post("/api-keys", json={"name": "CI"}, headers=_headers()).status_code == 201


# ---------------------------------------------------------------------------
# API-key authentication path: allowed vs refused routes, revoke -> 401
# ---------------------------------------------------------------------------


@pytest.fixture
def api_key_app(monkeypatch):
    """A live app where a real ``mtly_`` key is registered for the workspace.

    ``SUPABASE_JWT_SECRET`` is set (without ``SUPABASE_URL``) so the real
    ``get_actor`` runs its mtly_ branch rather than the local bypass.
    """
    monkeypatch.setenv("SKIP_API_AUTH", "false")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unit-test-secret")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    repo = InMemoryApiKeyRepository()
    raw_key, fingerprint, prefix, last4 = generate_api_key()
    record = repo.create_key(
        workspace_id=_WORKSPACE_ID,
        name="CI",
        fingerprint=fingerprint,
        prefix=prefix,
        last4=last4,
        created_by=_USER_ID,
    )
    app.state.api_key_repository = repo
    # Keys exist only for workspaces whose plan has api_access (Agency), and the
    # entitlement is re-checked on every keyed request.
    app.state.billing_repository.subscriptions[_WORKSPACE_ID] = _agency_subscription()
    client = TestClient(app, raise_server_exceptions=False)
    yield client, app, repo, raw_key, record
    get_settings.cache_clear()


def _auth(raw_key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {raw_key}", "x-motionly-workspace-id": str(_WORKSPACE_ID)}


def test_api_key_rejected_on_route_not_in_allow_list(api_key_app):
    client, _app, _repo, raw_key, _record = api_key_app
    # /billing/status is a normal member route, never on the API-key allow-list.
    response = client.get("/billing/status", headers=_auth(raw_key))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "api_key_route_forbidden"


def test_api_key_rejected_on_management_route(api_key_app):
    client, _app, _repo, raw_key, _record = api_key_app
    # A key cannot manage keys, even though the key's workspace is Agency.
    response = client.get("/api-keys", headers=_auth(raw_key))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "api_key_route_forbidden"


def test_api_key_allowed_on_list_projects(api_key_app):
    client, _app, _repo, raw_key, _record = api_key_app
    # GET /projects is allow-listed: the key authenticates (not a 401/403 auth
    # error). The route then runs as the key's workspace actor.
    response = client.get("/projects", headers=_auth(raw_key))
    assert response.status_code == 200, response.text


def test_unknown_key_is_rejected(api_key_app):
    client, _app, _repo, _raw_key, _record = api_key_app
    bogus = "mtly_" + "x" * 43
    response = client.get("/projects", headers=_auth(bogus))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "api_key_invalid"


def test_revoked_key_stops_working_immediately(api_key_app):
    client, _app, repo, raw_key, record = api_key_app
    # Works before revoke.
    assert client.get("/projects", headers=_auth(raw_key)).status_code == 200
    repo.revoke_key(workspace_id=_WORKSPACE_ID, key_id=record.id)
    after = client.get("/projects", headers=_auth(raw_key))
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "api_key_invalid"


def test_api_key_updates_last_used(api_key_app):
    client, _app, repo, raw_key, record = api_key_app
    assert record.last_used_at is None
    client.get("/projects", headers=_auth(raw_key))
    assert repo._keys[record.id].last_used_at is not None


# ---------------------------------------------------------------------------
# Per-key rate limiting (middleware keys on the key fingerprint)
# ---------------------------------------------------------------------------


def test_rate_limit_is_per_key(api_key_app, monkeypatch):
    client, app, repo, raw_key, _record = api_key_app
    app.state.settings.rate_limit_per_minute = 2

    # Two requests on the same route with the same key: allowed, then limited.
    first = client.get("/projects", headers=_auth(raw_key))
    assert first.status_code != 429
    second = client.get("/projects", headers=_auth(raw_key))
    assert second.status_code != 429
    third = client.get("/projects", headers=_auth(raw_key))
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limit_exceeded"

    # A different key is tracked independently (not blocked by the first key).
    other_raw, fp, prefix, last4 = generate_api_key()
    repo.create_key(
        workspace_id=_WORKSPACE_ID,
        name="other",
        fingerprint=fp,
        prefix=prefix,
        last4=last4,
        created_by=_USER_ID,
    )
    other = client.get("/projects", headers=_auth(other_raw))
    assert other.status_code != 429


def test_revoke_is_idempotent(api_key_app):
    client, _app, repo, _raw_key, record = api_key_app
    first = repo.revoke_key(workspace_id=_WORKSPACE_ID, key_id=record.id)
    revoked_at = first.revoked_at
    second = repo.revoke_key(workspace_id=_WORKSPACE_ID, key_id=record.id)
    assert second.revoked_at == revoked_at


def test_revoke_unknown_key_404():
    client, _app = _management_client()
    response = client.post(f"/api-keys/{uuid4()}/revoke", headers=_headers())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "api_key_not_found"
