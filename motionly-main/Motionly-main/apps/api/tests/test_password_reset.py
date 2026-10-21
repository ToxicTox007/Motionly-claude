"""Task 12: admin-assisted password reset and user admin.

A user who forgot their password asks for help on the public /forgot-password
page (POST /auth/password-reset-requests), which never reveals whether the
email has an account. An admin finds the request on the Users page, issues a
one-time GoTrue recovery link (valid one hour, single use, superseding any
earlier link), bans/unbans users, and dismisses requests. Every admin action
is written to an append-only audit log that never holds a token or link.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.middleware import InMemoryRateLimiter
from app.main import create_app
from app.services.admin_user_directory import AdminUserDirectory
from app.services.auth_admin import AuthAdminClient

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
VIEWER = "198.51.100.10"
ADMIN_TOKEN = "admin-secret-token"


class GoTrue:
    """A GoTrue v2.170 fake for admin list, generate_link, verify, and ban."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.users: dict[str, dict] = {}
        self.links: dict[str, str] = {}  # hashed_token -> email (latest only)

    def add_user(self, email: str, *, user_id: str | None = None) -> str:
        uid = user_id or str(uuid4())
        self.users[email] = {"id": uid, "email": email, "banned_until": None}
        return uid

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/auth/v1/admin/users" and request.method == "GET":
            needle = request.url.params.get("filter", "")
            matched = [
                dict(user)
                for email, user in self.users.items()
                if needle.lower() in email.lower()
            ]
            return httpx.Response(200, json={"users": matched})
        if path == "/auth/v1/admin/generate_link":
            body = json.loads(request.content)
            email = body["email"]
            if email not in self.users:
                return httpx.Response(
                    404,
                    json={"code": 404, "error_code": "user_not_found", "msg": "not found"},
                )
            token = uuid4().hex
            # A new link supersedes the previous one for that email.
            self.links = {t: e for t, e in self.links.items() if e != email}
            self.links[token] = email
            return httpx.Response(
                200,
                json={
                    "action_link": f"http://localhost/verify?token={token}&type=recovery",
                    "hashed_token": token,
                    "verification_type": "recovery",
                    "redirect_to": "http://localhost",
                },
            )
        if path == "/auth/v1/verify":
            body = json.loads(request.content)
            token = body.get("token_hash")
            if token in self.links:
                del self.links[token]  # single use
                return httpx.Response(200, json={"access_token": "a", "refresh_token": "r"})
            return httpx.Response(
                403, json={"code": 403, "error_code": "otp_expired", "msg": "expired"}
            )
        if path.startswith("/auth/v1/admin/users/") and request.method == "PUT":
            uid = path.rsplit("/", 1)[-1]
            body = json.loads(request.content)
            for user in self.users.values():
                if user["id"] == uid:
                    user["banned_until"] = (
                        None if body["ban_duration"] == "none" else "2126-01-01T00:00:00Z"
                    )
                    return httpx.Response(200, json=dict(user))
            return httpx.Response(
                404, json={"code": 404, "error_code": "user_not_found", "msg": "not found"}
            )
        raise AssertionError(f"unexpected GoTrue call: {request.method} {path}")


@pytest.fixture
def gotrue() -> GoTrue:
    return GoTrue()


@pytest.fixture
def directory() -> AdminUserDirectory:
    return AdminUserDirectory()


@pytest.fixture
def client(gotrue: GoTrue, directory: AdminUserDirectory) -> TestClient:
    app = create_app()
    app.state.auth_admin = AuthAdminClient(
        supabase_url="http://auth.internal:8000",
        service_role_key="service-role-key",
        anon_key="anon-key",
        transport=httpx.MockTransport(gotrue.handler),
    )
    app.state.admin_user_directory = directory
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.settings.password_reset_request_rate_limit_per_hour = 3
    app.state.settings.password_reset_request_global_rate_limit_per_hour = 5
    app.state.settings.admin_api_token = ADMIN_TOKEN
    app.state.settings.public_origin = "https://app.example.test"
    # Admin routes run get_actor first; the local auth bypass gives it a fixed
    # mock actor (not an admin) so require_admin alone decides access.
    app.state.settings.app_env = "local"
    app.state.settings.skip_api_auth = True
    return TestClient(app)


def _admin_headers() -> dict[str, str]:
    return {"x-motionly-admin-token": ADMIN_TOKEN, "x-motionly-viewer-ip": VIEWER}


# --- POST /auth/password-reset-requests -----------------------------------


def _request_reset(client: TestClient, *, email: str, viewer: str | None = VIEWER):
    headers = {"x-motionly-viewer-ip": viewer} if viewer else {}
    return client.post("/auth/password-reset-requests", json={"email": email}, headers=headers)


def test_a_known_email_records_a_pending_request(
    client: TestClient, gotrue: GoTrue, directory: AdminUserDirectory
) -> None:
    uid = gotrue.add_user("asha@example.com")
    response = _request_reset(client, email="  Asha@Example.COM ")

    assert response.status_code == 202
    assert response.json() == {"status": "received"}
    (row,) = directory.recorded_requests
    assert row["email"] == "asha@example.com"
    assert row["user_id"] == uid
    assert row["status"] == "pending"


def test_an_unknown_email_gets_the_same_answer_and_is_still_recorded(
    client: TestClient, directory: AdminUserDirectory
) -> None:
    response = _request_reset(client, email="nobody@example.com")

    assert response.status_code == 202
    assert response.json() == {"status": "received"}
    # Recorded (so admins can see probing) but with no user id: nothing leaks.
    (row,) = directory.recorded_requests
    assert row["email"] == "nobody@example.com"
    assert row["user_id"] is None


def test_a_malformed_email_still_returns_the_same_202(client: TestClient) -> None:
    response = _request_reset(client, email="not-an-email")
    assert response.status_code == 202
    assert response.json() == {"status": "received"}


def test_reset_requests_are_rate_limited_per_viewer(client: TestClient) -> None:
    statuses = [_request_reset(client, email="a@example.com").status_code for _ in range(3)]
    assert statuses == [202, 202, 202]
    assert _request_reset(client, email="a@example.com").status_code == 429


def test_reset_requests_hit_the_global_limit(client: TestClient) -> None:
    statuses = [
        _request_reset(client, email="a@example.com", viewer=f"203.0.113.{i}").status_code
        for i in range(6)
    ]
    assert statuses == [202, 202, 202, 202, 202, 429]


def test_the_reset_request_viewer_hash_is_not_the_raw_address(
    client: TestClient, directory: AdminUserDirectory
) -> None:
    _request_reset(client, email="a@example.com")
    (row,) = directory.recorded_requests
    assert VIEWER not in row["viewer_hash"]
    assert len(row["viewer_hash"]) == 12


# --- admin: require_admin on every route ----------------------------------

ADMIN_ROUTES = [
    ("GET", "/admin/users?email=a"),
    ("GET", "/admin/users/reset-requests"),
    ("POST", "/admin/users/reset-links"),
    ("POST", "/admin/users/ban"),
    ("POST", "/admin/users/unban"),
    ("POST", "/admin/users/reset-requests/dismiss"),
]


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_non_admins_are_refused_on_every_user_admin_route(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, json={})
    assert response.status_code in (401, 403), path
    if response.status_code == 403:
        assert response.json()["error"]["code"] == "admin_access_denied"


# --- admin: search --------------------------------------------------------


def test_admin_searches_users_by_email(client: TestClient, gotrue: GoTrue) -> None:
    gotrue.add_user("asha@example.com")
    gotrue.add_user("other@example.com")
    response = client.get("/admin/users?email=asha", headers=_admin_headers())

    assert response.status_code == 200
    users = response.json()["users"]
    assert [u["email"] for u in users] == ["asha@example.com"]
    assert users[0]["banned"] is False


# --- admin: reset link ----------------------------------------------------


def test_admin_creates_a_one_hour_reset_link_in_the_url_fragment(
    client: TestClient, gotrue: GoTrue, directory: AdminUserDirectory
) -> None:
    uid = gotrue.add_user("asha@example.com")
    response = client.post(
        "/admin/users/reset-links",
        json={"email": "asha@example.com"},
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in_seconds"] == 3600
    # The token rides in the fragment, keyed to the public /auth/confirm page.
    assert body["link"].startswith("https://app.example.test/auth/confirm#token_hash=")
    assert "type=recovery" in body["link"]
    # Audit: a link_created event with the target user, never the token.
    (event,) = [
        e
        for e in directory.recorded_audit_events
        if e["action"] == "password_reset_link_created"
    ]
    assert event["target_user_id"] == uid
    assert "token" not in json.dumps(event["details"])
    assert "token_hash" not in json.dumps(event["details"])


def test_creating_a_link_marks_a_request_as_handled(
    client: TestClient, gotrue: GoTrue, directory: AdminUserDirectory
) -> None:
    gotrue.add_user("asha@example.com")
    _request_reset(client, email="asha@example.com")
    request_id = directory.recorded_requests[0]["id"]

    response = client.post(
        "/admin/users/reset-links",
        json={"email": "asha@example.com", "request_id": request_id},
        headers=_admin_headers(),
    )
    assert response.status_code == 200
    assert directory.recorded_requests[0]["status"] == "link_issued"


def test_a_reset_link_for_an_unknown_email_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/admin/users/reset-links",
        json={"email": "nobody@example.com"},
        headers=_admin_headers(),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


def test_the_latest_link_supersedes_the_previous_one(client: TestClient, gotrue: GoTrue) -> None:
    gotrue.add_user("asha@example.com")

    def issue() -> str:
        token = client.post(
            "/admin/users/reset-links",
            json={"email": "asha@example.com"},
            headers=_admin_headers(),
        ).json()["link"]
        return token.split("token_hash=", 1)[1].split("&", 1)[0]

    first = issue()
    second = issue()
    assert first != second
    # GoTrue keeps only the latest; verifying the old token now fails.
    assert first not in gotrue.links
    assert second in gotrue.links


# --- admin: ban / unban ---------------------------------------------------


def test_admin_bans_and_unbans_a_user(
    client: TestClient, gotrue: GoTrue, directory: AdminUserDirectory
) -> None:
    uid = gotrue.add_user("asha@example.com")

    ban = client.post("/admin/users/ban", json={"user_id": uid}, headers=_admin_headers())
    assert ban.status_code == 200
    assert ban.json() == {"user_id": uid, "banned": True}

    unban = client.post("/admin/users/unban", json={"user_id": uid}, headers=_admin_headers())
    assert unban.status_code == 200
    assert unban.json() == {"user_id": uid, "banned": False}

    actions = [e["action"] for e in directory.recorded_audit_events]
    assert "user_banned" in actions
    assert "user_unbanned" in actions


# --- admin: dismiss -------------------------------------------------------


def test_admin_dismisses_a_pending_request(
    client: TestClient, gotrue: GoTrue, directory: AdminUserDirectory
) -> None:
    gotrue.add_user("asha@example.com")
    _request_reset(client, email="asha@example.com")
    request_id = directory.recorded_requests[0]["id"]

    response = client.post(
        "/admin/users/reset-requests/dismiss",
        json={"request_id": request_id},
        headers=_admin_headers(),
    )
    assert response.status_code == 204
    assert directory.recorded_requests[0]["status"] == "dismissed"
    assert any(
        e["action"] == "password_reset_request_dismissed"
        for e in directory.recorded_audit_events
    )


def test_dismissing_an_unknown_request_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/admin/users/reset-requests/dismiss",
        json={"request_id": str(uuid4())},
        headers=_admin_headers(),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "reset_request_not_found"


# --- admin: pending list --------------------------------------------------


def test_admin_sees_only_pending_requests(
    client: TestClient, gotrue: GoTrue, directory: AdminUserDirectory
) -> None:
    gotrue.add_user("a@example.com")
    _request_reset(client, email="a@example.com")
    _request_reset(client, email="b@example.com")
    # Dismiss the first.
    first = directory.recorded_requests[0]["id"]
    client.post(
        "/admin/users/reset-requests/dismiss",
        json={"request_id": first},
        headers=_admin_headers(),
    )

    response = client.get("/admin/users/reset-requests", headers=_admin_headers())
    assert response.status_code == 200
    emails = {r["email"] for r in response.json()["requests"]}
    assert emails == {"b@example.com"}


# --- GoTrue call-shape and expiry mapping ---------------------------------


def test_verify_maps_otp_expired_and_passes_the_recovery_type() -> None:
    gotrue = GoTrue()
    gotrue.add_user("asha@example.com")
    auth = AuthAdminClient(
        supabase_url="http://auth.internal:8000",
        service_role_key="service-role-key",
        anon_key="anon-key",
        transport=httpx.MockTransport(gotrue.handler),
    )

    import asyncio

    async def run() -> None:
        recovery = await auth.generate_recovery_link(email="asha@example.com")
        session = await auth.verify_recovery_token(token_hash=recovery.hashed_token)
        assert session["access_token"] == "a"
        # Reuse → otp_expired.
        from app.services.auth_admin import AuthAdminError

        try:
            await auth.verify_recovery_token(token_hash=recovery.hashed_token)
        except AuthAdminError as exc:
            assert exc.code == "otp_expired"
            assert exc.status_code == 403
        else:  # pragma: no cover
            raise AssertionError("reuse should fail")

    asyncio.run(run())
    # The verify call used the anon key, like the browser.
    verify_calls = [c for c in gotrue.calls if c.url.path == "/auth/v1/verify"]
    for call in verify_calls:
        assert call.headers["apikey"] == "anon-key"
        assert json.loads(call.content)["type"] == "recovery"


# --- tokens and links never reach the logs --------------------------------


def test_tokens_and_links_never_reach_the_logs(
    client: TestClient, gotrue: GoTrue, caplog
) -> None:
    gotrue.add_user("asha@example.com")
    with caplog.at_level(logging.DEBUG):
        body = client.post(
            "/admin/users/reset-links",
            json={"email": "asha@example.com"},
            headers=_admin_headers(),
        ).json()
    token = body["link"].split("token_hash=", 1)[1].split("&", 1)[0]
    logged = json.dumps([{k: str(v) for k, v in r.__dict__.items()} for r in caplog.records])
    assert token not in logged
    assert body["link"] not in logged


# --- compose: links expire after one hour ---------------------------------


def _compose(path: Path) -> dict:
    class Loader(yaml.SafeLoader):
        pass

    def construct(loader, _suffix, node):
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        return loader.construct_scalar(node)

    Loader.add_multi_constructor("!", construct)
    return yaml.load(path.read_text("utf-8"), Loader=Loader)


def test_reset_links_expire_after_one_hour() -> None:
    auth = _compose(INFRA / "docker-compose.gpu.yml")["services"]["supabase-auth"]["environment"]
    assert auth["GOTRUE_MAILER_OTP_EXP"] == "3600"
