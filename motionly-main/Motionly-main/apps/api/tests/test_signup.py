"""Task 11: sign-up without email.

Auth's public sign-up is off (GOTRUE_DISABLE_SIGNUP). The API's rate-limited
``POST /auth/signup`` creates an already-confirmed user through Auth's admin
API; the sign-up page calls it and then signs the user in. Nothing sends or
waits for a confirmation email.
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
from app.services.auth_admin import AuthAdminClient
from app.services.signup_policy import password_problem

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
VIEWER = "198.51.100.10"


class GoTrue:
    """Records calls to Auth's admin API and answers like GoTrue v2.170."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.users: dict[str, str] = {}
        self.next_error: tuple[int, dict[str, object]] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.next_error is not None:
            status, body = self.next_error
            self.next_error = None
            return httpx.Response(status, json=body)
        assert request.method == "POST"
        assert request.url.path == "/auth/v1/admin/users"
        body = json.loads(request.content)
        if body["email"] in self.users:
            return httpx.Response(
                422,
                json={
                    "code": 422,
                    "error_code": "email_exists",
                    "msg": "A user with this email address has already been registered",
                },
            )
        user_id = str(uuid4())
        self.users[body["email"]] = user_id
        return httpx.Response(
            200,
            json={
                "id": user_id,
                "email": body["email"],
                "email_confirmed_at": "2026-10-19T00:00:00Z",
                "user_metadata": body.get("user_metadata", {}),
            },
        )


@pytest.fixture
def gotrue() -> GoTrue:
    return GoTrue()


@pytest.fixture
def client(gotrue: GoTrue) -> TestClient:
    app = create_app()
    app.state.auth_admin = AuthAdminClient(
        supabase_url="http://auth.internal:8000",
        service_role_key="service-role-key",
        transport=httpx.MockTransport(gotrue.handler),
    )
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.settings.signup_rate_limit_per_hour = 3
    app.state.settings.signup_global_rate_limit_per_hour = 5
    return TestClient(app)


def _signup(client: TestClient, *, viewer: str | None = VIEWER, **overrides: object):
    body = {
        "full_name": "Asha Rao",
        "email": f"asha-{uuid4().hex[:8]}@example.com",
        "password": "correct-horse-battery",
        **overrides,
    }
    headers = {"x-motionly-viewer-ip": viewer} if viewer else {}
    return client.post("/auth/signup", json=body, headers=headers)


def test_signup_creates_an_already_confirmed_user_through_the_admin_api(
    client: TestClient, gotrue: GoTrue
) -> None:
    response = _signup(client, email="  Asha.Rao@Example.COM ")

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["email"] == "asha.rao@example.com"
    assert payload["user_id"] == gotrue.users["asha.rao@example.com"]
    assert set(payload) == {"user_id", "email"}, "no tokens or user record are returned"

    (call,) = gotrue.calls
    assert call.headers["apikey"] == "service-role-key"
    assert call.headers["authorization"] == "Bearer service-role-key"
    sent = json.loads(call.content)
    assert sent == {
        "email": "asha.rao@example.com",
        "password": "correct-horse-battery",
        "email_confirm": True,
        "user_metadata": {"full_name": "Asha Rao"},
    }


def test_a_duplicate_email_is_a_conflict_that_points_to_login(client: TestClient) -> None:
    first = _signup(client, email="taken@example.com")
    assert first.status_code == 201
    again = _signup(client, email="TAKEN@example.com")

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize(
    "password",
    [
        "short1!",  # under 8 characters
        "password",  # common
        "12345678",  # common
        "aaaaaaaaaaaa",  # too few distinct characters
        "asha-signup-2026",  # contains the email's local part
        "é" * 37,  # 74 bytes: beyond bcrypt's 72-byte input
    ],
)
def test_weak_passwords_are_refused_before_auth_is_called(
    client: TestClient, gotrue: GoTrue, password: str
) -> None:
    response = _signup(client, email="asha-signup@example.com", password=password)

    assert response.status_code == 422, password
    assert response.json()["error"]["code"] == "password_weak"
    assert gotrue.calls == []


def test_auth_rejecting_the_password_is_reported_as_weak(
    client: TestClient, gotrue: GoTrue
) -> None:
    gotrue.next_error = (
        422,
        {"code": 422, "error_code": "weak_password", "msg": "Password is known to be weak"},
    )
    response = _signup(client)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "password_weak"


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"email": "not-an-email"}, "email_invalid"),
        ({"email": "a@b"}, "email_invalid"),
        ({"email": f"{'a' * 250}@example.com"}, "email_invalid"),
        ({"full_name": "   "}, "name_required"),
        ({"full_name": "x" * 121}, "name_required"),
    ],
)
def test_malformed_details_are_refused_before_auth_is_called(
    client: TestClient, gotrue: GoTrue, overrides: dict[str, object], code: str
) -> None:
    response = _signup(client, **overrides)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert gotrue.calls == []


def test_one_viewer_is_rate_limited_and_other_viewers_are_not(
    client: TestClient, gotrue: GoTrue
) -> None:
    statuses = [_signup(client).status_code for _ in range(3)]
    assert statuses == [201, 201, 201]

    limited = _signup(client)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "signup_rate_limited"
    assert len(gotrue.calls) == 3, "a refused attempt never reaches Auth"

    # Failed attempts count too, so probing for taken emails is limited.
    assert _signup(client, viewer="203.0.113.7", password="password").status_code == 422
    assert _signup(client, viewer="203.0.113.7").status_code == 201


def test_bulk_sign_ups_from_many_viewers_hit_the_global_limit(client: TestClient) -> None:
    statuses = [_signup(client, viewer=f"203.0.113.{index}").status_code for index in range(6)]

    assert statuses == [201, 201, 201, 201, 201, 429]


def test_a_forged_or_malformed_viewer_address_is_not_a_fresh_rate_limit_key(
    client: TestClient,
) -> None:
    # Without a valid address from the ingress, attempts are keyed on the
    # connecting peer: a different garbage value each time is still one viewer.
    statuses = [
        _signup(client, viewer=value).status_code
        for value in ("not-an-ip", "1.2.3.4, 5.6.7.8", "", "1.2.3.4:80")
    ]
    assert statuses == [201, 201, 201, 429]


def test_auth_failing_is_a_bad_gateway_and_creates_nothing(
    client: TestClient, gotrue: GoTrue
) -> None:
    gotrue.next_error = (500, {"code": 500, "msg": "database is down"})
    response = _signup(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "signup_unavailable"


def test_without_auth_configured_signup_is_unavailable() -> None:
    app = create_app()
    app.state.auth_admin = None
    response = TestClient(app).post(
        "/auth/signup",
        json={"full_name": "A", "email": "a@example.com", "password": "correct-horse-battery"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "signup_unavailable"


def test_passwords_never_reach_the_logs(client: TestClient, caplog) -> None:
    secret = "correct-horse-battery-staple"
    with caplog.at_level(logging.DEBUG):
        _signup(client, password=secret)
        # A request that fails validation logs its errors, never the input.
        client.post("/auth/signup", json={"email": "a@example.com", "password": [secret]})
    logged = json.dumps([{k: str(v) for k, v in r.__dict__.items()} for r in caplog.records])
    assert secret not in logged


def test_password_policy() -> None:
    assert password_problem("correct-horse-battery", email="asha@example.com") is None
    assert password_problem("Tr0ub4dor&3", email="asha@example.com") is None
    assert password_problem("short", email="asha@example.com") == "too_short"
    assert password_problem("qwertyuiop", email="asha@example.com") == "common"
    assert password_problem("PASSWORD1", email="asha@example.com") == "common"
    assert password_problem("abababababab", email="asha@example.com") == "too_simple"
    assert password_problem("my-asha-password", email="asha@example.com") == "contains_email"
    assert password_problem("€" * 25, email="asha@example.com") == "too_long"


# --- Auth's own sign-up is off --------------------------------------------


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


def test_auth_public_sign_up_is_disabled_and_nothing_sends_email() -> None:
    auth = _compose(INFRA / "docker-compose.gpu.yml")["services"]["supabase-auth"]["environment"]

    assert auth["GOTRUE_DISABLE_SIGNUP"] == "true"
    # Sign-up and reset never email; nothing is configured to send mail.
    assert not any(key.startswith("GOTRUE_SMTP_") for key in auth)
    assert "GOTRUE_MAILER_AUTOCONFIRM" not in auth
    # Auth's own minimum matches the API's policy (resets use it, Task 12).
    assert auth["GOTRUE_PASSWORD_MIN_LENGTH"] == "8"

    for overlay in ("docker-compose.cpu-stage.yml", "docker-compose.local-audit.yml"):
        services = _compose(INFRA / overlay)["services"]
        environment = (services.get("supabase-auth") or {}).get("environment") or {}
        assert "GOTRUE_DISABLE_SIGNUP" not in environment, overlay


def test_the_local_supabase_used_by_the_db_stage_has_sign_up_disabled_too() -> None:
    import tomllib

    config = tomllib.loads((ROOT / "supabase" / "config.toml").read_text("utf-8"))
    assert config["auth"]["enable_signup"] is False
    # The email provider stays on: it is how people sign in with a password.
    assert config["auth"]["email"]["enable_signup"] is True
    assert config["auth"]["email"]["enable_confirmations"] is False
    assert config["auth"]["minimum_password_length"] == 8


def test_the_cpu_stage_raises_the_per_viewer_limit_for_the_browser_suite() -> None:
    """Every browser test is one viewer behind the CDN simulator."""
    overlay = _compose(INFRA / "docker-compose.cpu-stage.yml")
    api = overlay["services"]["api"]["environment"]
    assert int(api["SIGNUP_RATE_LIMIT_PER_HOUR"]) >= 100
    production = _compose(INFRA / "docker-compose.gpu.yml")["services"]["api"]["environment"]
    assert "SIGNUP_RATE_LIMIT_PER_HOUR" not in production
