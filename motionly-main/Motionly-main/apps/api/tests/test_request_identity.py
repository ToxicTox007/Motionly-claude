"""Task 10: request identity and the viewer's address.

The API logs who made a request from the verified token, never from a header
the caller chooses, and the unused ``x-motionly-user-id`` header is gone.
Auth's rate limits key on the viewer's address, which only the ingress sets
(from CloudFront's ``CloudFront-Viewer-Address``, after the origin guard).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import logging
import re
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.dependencies import get_actor
from app.core.middleware import rate_limit_middleware, request_id_middleware

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
VIEWER_IP_HEADER = "X-Motionly-Viewer-IP"


def _jwt(claims: dict[str, object], secret: bytes = b"secret") -> str:
    def encode(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    header, payload = encode({"alg": "HS256", "typ": "JWT"}), encode(claims)
    signature = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


class _Limiter:
    async def allow(self, *, key: str, limit: int):
        return True, 0


def test_request_logs_name_the_verified_user_not_a_caller_supplied_header(caplog) -> None:
    app = FastAPI()
    # Registered as in app.main: the rate limiter wraps the request logger.
    app.middleware("http")(request_id_middleware)
    app.middleware("http")(rate_limit_middleware)
    app.state.settings = SimpleNamespace(
        app_env="test",
        supabase_jwt_secret="secret",
        supabase_url=None,
        supabase_service_role_key=None,
        api_rate_limit_per_minute=1000,
        rate_limit_per_minute=1000,
    )
    app.state.rate_limiter = _Limiter()

    @app.get("/projects")
    def projects(request: Request) -> dict[str, str]:
        return {"ok": "yes"}

    user_id = uuid4()
    forged = uuid4()
    token = _jwt({"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 60})
    with caplog.at_level(logging.INFO):
        response = TestClient(app).get(
            "/projects",
            headers={
                "authorization": f"Bearer {token}",
                "x-motionly-workspace-id": str(uuid4()),
                "x-motionly-user-id": str(forged),
            },
        )

    assert response.status_code == 200
    records = [r for r in caplog.records if r.getMessage() == "api_request_completed"]
    assert records, "the request was logged"
    assert records[-1].user_id == str(user_id)
    assert str(forged) not in json.dumps({k: str(v) for k, v in records[-1].__dict__.items()})


def test_the_api_neither_reads_nor_allows_the_user_id_header() -> None:
    assert "x_motionly_user_id" not in inspect.signature(get_actor).parameters
    main = (ROOT / "apps" / "api" / "app" / "main.py").read_text("utf-8")
    middleware = (ROOT / "apps" / "api" / "app" / "core" / "middleware.py").read_text("utf-8")
    assert "x-motionly-user-id" not in main
    assert "x-motionly-user-id" not in middleware
    openapi = (ROOT / "packages" / "shared" / "openapi.json").read_text("utf-8")
    assert "x-motionly-user-id" not in openapi


# --- the viewer's address -------------------------------------------------


def _guard() -> str:
    return (INFRA / "Caddyfile.origin-guard").read_text("utf-8")


def test_the_edge_contract_names_the_header_the_origin_forwards_the_viewer_address_in() -> None:
    contract = json.loads((INFRA / "aws" / "edge-contract.json").read_text("utf-8"))
    client = contract["client_address"]
    assert client["header"] == "CloudFront-Viewer-Address"
    assert client["origin_header"] == VIEWER_IP_HEADER


def test_after_the_guard_the_ingress_replaces_any_viewer_supplied_address() -> None:
    guard = _guard()
    deny = guard.index("@origin_unverified")
    strip = guard.index(f"request_header -{VIEWER_IP_HEADER}")
    mapping = guard.index("map {http.request.header.CloudFront-Viewer-Address}")
    set_header = guard.index(f"request_header {VIEWER_IP_HEADER} {{motionly_viewer_ip}}")
    assert deny < strip < mapping < set_header
    # Unguarded routes never set it: only the guarded snippet does.
    routes = (INFRA / "Caddyfile.routes").read_text("utf-8")
    assert VIEWER_IP_HEADER not in routes


def test_the_port_is_dropped_so_one_viewer_is_one_rate_limit_key() -> None:
    match = re.search(r'~(\^[^\s"]+)\s+"\$\{1\}"', _guard())
    assert match, "the CloudFront-Viewer-Address map has one regular expression"
    pattern = re.compile(match.group(1))
    cases = {
        "198.51.100.10:46532": "198.51.100.10",
        "198.51.100.10:1": "198.51.100.10",
        "2001:db8::1:46532": "2001:db8::1",
        "2001:0db8:85a3:0000:0000:8a2e:0370:7334:443": "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
    }
    for value, address in cases.items():
        found = pattern.match(value)
        assert found and found.group(1) == address, value
    for value in ("198.51.100.10", "", "evil value:1", "198.51.100.10:port", "1.2.3.4:5\r\n"):
        assert not pattern.match(value), value


def test_auth_rate_limits_key_on_the_viewer_address() -> None:
    compose = yaml.safe_load((INFRA / "docker-compose.gpu.yml").read_text("utf-8"))
    auth = compose["services"]["supabase-auth"]["environment"]
    assert auth["GOTRUE_RATE_LIMIT_HEADER"] == VIEWER_IP_HEADER


def test_the_local_audit_ingress_uses_the_connecting_address() -> None:
    caddyfile = (INFRA / "Caddyfile.local-audit").read_text("utf-8")
    assert f"request_header {VIEWER_IP_HEADER} {{remote_host}}" in caddyfile


def _compose(path: Path) -> dict:
    """Compose files use merge tags (!override, !reset) that safe_load rejects."""

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


def test_cpu_stage_tokens_expire_during_a_session_but_outlast_the_renewal_window() -> None:
    """Tokens must expire while a browser test runs (so renewal is exercised),
    yet live longer than the proxy's renewal margin: shorter tokens would be
    renewed by server components, which cannot save the rotated refresh token."""
    overlay = _compose(INFRA / "docker-compose.cpu-stage.yml")
    auth = overlay["services"]["supabase-auth"]["environment"]
    lifetime = int(auth["GOTRUE_JWT_EXP"])
    renewal = (ROOT / "apps" / "web" / "lib" / "supabase" / "session-renewal.mjs").read_text()
    margin_ms = int(
        re.search(r"SESSION_RENEWAL_MARGIN_MS = ([\d_]+)", renewal).group(1).replace("_", "")
    )
    assert margin_ms / 1000 + 20 <= lifetime <= 300
    # Every browser test shares one viewer address, and short tokens are
    # renewed often, so the CPU stage raises the per-viewer renewal limit.
    assert int(auth["GOTRUE_RATE_LIMIT_TOKEN_REFRESH"]) > 150
    production = yaml.safe_load((INFRA / "docker-compose.gpu.yml").read_text("utf-8"))
    production_auth = production["services"]["supabase-auth"]["environment"]
    assert "GOTRUE_JWT_EXP" not in production_auth
    assert "GOTRUE_RATE_LIMIT_TOKEN_REFRESH" not in production_auth
