"""Tests for app.services.inference_auth (plan Task 5, Section 2.2)."""

from __future__ import annotations

import json
import logging
import threading

import httpx
import pytest

from app.core.config import Settings
from app.core.logging import (
    RedactingJsonFormatter,
    register_secret_value,
    registered_secret_values,
)
from app.services.inference_auth import (
    AuthConfigurationError,
    build_auth,
)
from app.services.inference_registry import AuthConfig
from app.services.inference_secrets import SecretResolver
from app.services.provider_resilience import ProviderCallFailure


def _settings(tmp_path, **overrides) -> Settings:
    base = {
        "APP_ENV": "test",
        "SUPABASE_JWT_SECRET": "x" * 32,
        "SUPABASE_URL": "https://supabase.invalid",
        "SUPABASE_SERVICE_ROLE_KEY": "y" * 32,
        "AI_SECRETS_DIR": str(tmp_path),
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _resolver_with_secret(tmp_path, name: str, value: str) -> SecretResolver:
    (tmp_path / name).write_text(value, encoding="utf-8")
    return SecretResolver(settings=_settings(tmp_path))


# -- header construction per auth type --------------------------------------


def test_none_adds_no_header(tmp_path) -> None:
    resolver = SecretResolver(settings=_settings(tmp_path))
    applier = build_auth(AuthConfig(type="none"), resolver)
    headers: dict[str, str] = {}
    applier.apply(headers)
    assert headers == {}


def test_bearer_header(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "s", "bearer-secret-abcdef")
    applier = build_auth(AuthConfig(type="bearer", secret_ref="file:s"), resolver)
    headers: dict[str, str] = {}
    applier.apply(headers)
    assert headers["authorization"] == "Bearer bearer-secret-abcdef"


def test_bearer_requires_secret_ref(tmp_path) -> None:
    resolver = SecretResolver(settings=_settings(tmp_path))
    with pytest.raises(AuthConfigurationError):
        build_auth(AuthConfig(type="bearer"), resolver)


def test_custom_header(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "s", "custom-secret-abcdef")
    applier = build_auth(
        AuthConfig(type="custom_header", header_name="X-Api-Token", secret_ref="file:s"),
        resolver,
    )
    headers: dict[str, str] = {}
    applier.apply(headers)
    assert headers["X-Api-Token"] == "custom-secret-abcdef"


def test_custom_header_requires_name(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "s", "custom-secret-abcdef")
    with pytest.raises(AuthConfigurationError):
        build_auth(AuthConfig(type="custom_header", secret_ref="file:s"), resolver)


@pytest.mark.parametrize("name", ["host", "Content-Length", "cookie", "transfer-encoding"])
def test_custom_header_rejects_reserved_names(tmp_path, name) -> None:
    resolver = _resolver_with_secret(tmp_path, "s", "custom-secret-abcdef")
    with pytest.raises(AuthConfigurationError):
        build_auth(
            AuthConfig(type="custom_header", header_name=name, secret_ref="file:s"),
            resolver,
        )


def test_custom_header_rejects_injection(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "s", "custom-secret-abcdef")
    with pytest.raises(AuthConfigurationError):
        build_auth(
            AuthConfig(type="custom_header", header_name="X-Bad\r\nHost", secret_ref="file:s"),
            resolver,
        )


def test_azure_api_key_header(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "s", "azure-secret-abcdef")
    applier = build_auth(AuthConfig(type="azure_api_key", secret_ref="file:s"), resolver)
    headers: dict[str, str] = {}
    applier.apply(headers)
    assert headers["api-key"] == "azure-secret-abcdef"


def test_unknown_auth_type(tmp_path) -> None:
    resolver = SecretResolver(settings=_settings(tmp_path))
    with pytest.raises(AuthConfigurationError):
        build_auth(AuthConfig(type="made_up"), resolver)


# -- OAuth2 client credentials ----------------------------------------------


class _FakeTokenEndpoint:
    """Programmable OAuth token endpoint over httpx.MockTransport."""

    def __init__(self, *, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {"access_token": "tok-1", "expires_in": 3600}
        self.calls: list[dict] = []
        self.call_count = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.call_count += 1
            self.calls.append(dict(httpx.QueryParams(request.content.decode())))
            if self.status >= 400:
                return httpx.Response(self.status, json={"error": "nope"}, request=request)
            return httpx.Response(self.status, json=self.body, request=request)

        return httpx.MockTransport(handler)


def _oauth_config() -> AuthConfig:
    return AuthConfig(
        type="oauth2_client_credentials",
        token_url="https://token.invalid/oauth/token",
        client_id="client-123",
        client_secret_ref="file:cs",
        scope="inference.read",
    )


def test_oauth_fetches_and_sets_bearer(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    endpoint = _FakeTokenEndpoint(body={"access_token": "access-tok-abcdef", "expires_in": 3600})
    applier = build_auth(_oauth_config(), resolver, transport=endpoint.transport())
    headers: dict[str, str] = {}
    applier.apply(headers)
    assert headers["authorization"] == "Bearer access-tok-abcdef"
    assert endpoint.call_count == 1
    # Client id and secret and scope are posted.
    assert endpoint.calls[0]["client_id"] == "client-123"
    assert endpoint.calls[0]["client_secret"] == "client-secret-abcdef"
    assert endpoint.calls[0]["scope"] == "inference.read"
    assert endpoint.calls[0]["grant_type"] == "client_credentials"


def test_oauth_caches_until_expiry(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    endpoint = _FakeTokenEndpoint(body={"access_token": "access-tok-abcdef", "expires_in": 3600})
    now = [1000.0]
    applier = build_auth(
        _oauth_config(), resolver, transport=endpoint.transport(), clock=lambda: now[0]
    )
    applier.apply({})
    applier.apply({})
    now[0] += 100.0  # still well within lifetime
    applier.apply({})
    assert endpoint.call_count == 1


def test_oauth_refreshes_after_expiry(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    endpoint = _FakeTokenEndpoint(body={"access_token": "access-tok-abcdef", "expires_in": 100})
    now = [1000.0]
    applier = build_auth(
        _oauth_config(), resolver, transport=endpoint.transport(), clock=lambda: now[0]
    )
    applier.apply({})
    assert endpoint.call_count == 1
    now[0] += 200.0  # past expiry (100s minus margin)
    applier.apply({})
    assert endpoint.call_count == 2


def test_oauth_401_maps_to_auth_error(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    endpoint = _FakeTokenEndpoint(status=401)
    applier = build_auth(_oauth_config(), resolver, transport=endpoint.transport())
    with pytest.raises(ProviderCallFailure) as exc:
        applier.apply({})
    assert exc.value.error_info.code == "provider_auth_failed"


def test_oauth_500_maps_to_unavailable(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    endpoint = _FakeTokenEndpoint(status=503)
    applier = build_auth(_oauth_config(), resolver, transport=endpoint.transport())
    with pytest.raises(ProviderCallFailure) as exc:
        applier.apply({})
    assert exc.value.error_info.code == "provider_unavailable"


def test_oauth_malformed_body(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    endpoint = _FakeTokenEndpoint(body={"no_token": "here"})
    applier = build_auth(_oauth_config(), resolver, transport=endpoint.transport())
    with pytest.raises(ProviderCallFailure) as exc:
        applier.apply({})
    assert exc.value.error_info.code == "provider_malformed_response"


def test_oauth_token_url_must_be_https(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    cfg = AuthConfig(
        type="oauth2_client_credentials",
        token_url="http://token.invalid/oauth/token",
        client_id="c",
        client_secret_ref="file:cs",
    )
    with pytest.raises(AuthConfigurationError):
        build_auth(cfg, resolver)


def test_oauth_token_url_rejects_query(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    cfg = AuthConfig(
        type="oauth2_client_credentials",
        token_url="https://token.invalid/oauth/token?x=1",
        client_id="c",
        client_secret_ref="file:cs",
    )
    with pytest.raises(AuthConfigurationError):
        build_auth(cfg, resolver)


def test_oauth_missing_fields(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    with pytest.raises(AuthConfigurationError):
        build_auth(
            AuthConfig(
                type="oauth2_client_credentials",
                client_id="c",
                client_secret_ref="file:cs",
            ),
            resolver,
        )


def test_oauth_concurrent_callers_fetch_once(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")

    barrier = threading.Barrier(8)

    class _SlowEndpoint(_FakeTokenEndpoint):
        def transport(self) -> httpx.MockTransport:
            def handler(request: httpx.Request) -> httpx.Response:
                self.call_count += 1
                return httpx.Response(
                    200,
                    json={"access_token": "access-tok-abcdef", "expires_in": 3600},
                    request=request,
                )

            return httpx.MockTransport(handler)

    endpoint = _SlowEndpoint()
    applier = build_auth(_oauth_config(), resolver, transport=endpoint.transport())

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            applier.apply({})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert endpoint.call_count == 1


def test_oauth_access_token_registered_with_redactor(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    endpoint = _FakeTokenEndpoint(
        body={"access_token": "access-tok-register-me-123456", "expires_in": 3600}
    )
    applier = build_auth(_oauth_config(), resolver, transport=endpoint.transport())
    applier.apply({})
    assert "access-tok-register-me-123456" in registered_secret_values()


# -- redaction of auth objects' repr ----------------------------------------


@pytest.mark.parametrize(
    ("cfg", "name", "value"),
    [
        (AuthConfig(type="bearer", secret_ref="file:s"), "s", "bearer-secret-abcdef"),
        (
            AuthConfig(type="custom_header", header_name="X-Token", secret_ref="file:s"),
            "s",
            "custom-secret-abcdef",
        ),
        (AuthConfig(type="azure_api_key", secret_ref="file:s"), "s", "azure-secret-abcdef"),
    ],
)
def test_auth_object_repr_hides_secret(tmp_path, cfg, name, value) -> None:
    resolver = _resolver_with_secret(tmp_path, name, value)
    applier = build_auth(cfg, resolver)
    assert value not in repr(applier)


def test_oauth_repr_hides_token(tmp_path) -> None:
    resolver = _resolver_with_secret(tmp_path, "cs", "client-secret-abcdef")
    endpoint = _FakeTokenEndpoint(body={"access_token": "access-tok-abcdef", "expires_in": 3600})
    applier = build_auth(_oauth_config(), resolver, transport=endpoint.transport())
    applier.apply({})
    assert "access-tok-abcdef" not in repr(applier)


# -- plan acceptance: a secret never appears in any log ----------------------


def test_injected_secret_absent_from_logs_and_errors(tmp_path, caplog) -> None:
    secret = "injected-acceptance-secret-9f3a2b1c"
    register_secret_value(secret)

    formatter = RedactingJsonFormatter("%(message)s")

    # message, args, extra and exception text must all be scrubbed.
    try:
        raise RuntimeError(f"upstream rejected {secret}")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname="t.py",
            lineno=1,
            msg="auth failure for token=%s",
            args=(secret,),
            exc_info=sys.exc_info(),
        )
        record.header_value = secret  # extra field
    output = formatter.format(record)
    assert secret not in output
    parsed = json.loads(output)
    # The value is gone; a redaction marker stands in its place. (A shape rule
    # such as the URL ``token=`` rule may further rewrite the exact-value marker,
    # which is fine: the secret itself never survives.)
    assert "[REDACTED" in parsed["message"]


def test_shapeless_secret_redacted_in_message_args_extra_exception(tmp_path) -> None:
    # A value with no credential shape and no key-like surrounding context: only
    # exact-value registration can catch it, and the marker survives unchanged.
    secret = "shapeless-operator-value-9f3a2b1c"
    resolver = _resolver_with_secret(tmp_path, "s", secret)
    resolver.resolve("file:s")  # registers the exact value

    formatter = RedactingJsonFormatter("%(message)s %(detail)s")
    try:
        raise RuntimeError(f"boom with {secret}")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname="t.py",
            lineno=1,
            msg="value was %s",
            args=(secret,),
            exc_info=sys.exc_info(),
        )
        record.detail = secret  # extra field
    output = formatter.format(record)
    assert secret not in output
    assert "[REDACTED_SECRET]" in output


def test_resolver_and_auth_reprs_do_not_leak(tmp_path) -> None:
    secret = "repr-secret-value-abcdef123456"
    resolver = _resolver_with_secret(tmp_path, "s", secret)
    applier = build_auth(AuthConfig(type="bearer", secret_ref="file:s"), resolver)
    # Resolving once registers the value; its repr and the resolver repr must
    # not contain it, and neither must the redactor let it through.
    from app.core.logging import redact_string

    assert secret not in repr(resolver)
    assert secret not in repr(applier)
    assert secret not in redact_string(f"resolver={resolver!r} value={secret}")


# -- plan acceptance: end-to-end through the fake provider server ------------


def test_injected_secret_reaches_only_the_fake_request(tmp_path, caplog, monkeypatch) -> None:
    """The plan's acceptance criterion.

    A bearer secret is injected into an OpenAI-compatible adapter via the Task 5
    auth strategy. It must appear only in the in-memory request header the fake
    server records — never in captured logs, error text, or serialized route
    metadata (ModelRoute / RouteRecord reprs).
    """
    from app.services.model_adapters import OpenAICompatibleAdapter
    from app.services.model_gateway import (
        ChatMessage,
        ModelCapability,
        ModelRoute,
        PlanningRequest,
    )
    from tests.support.fake_provider_server import CHAT_COMPLETIONS, FakeProviderServer

    secret = "end-to-end-bearer-secret-9f3a2b1c"
    resolver = _resolver_with_secret(tmp_path, "route-secret", secret)
    auth = build_auth(AuthConfig(type="bearer", secret_ref="file:route-secret"), resolver)

    server = FakeProviderServer()
    server.program(CHAT_COMPLETIONS)
    server.install(monkeypatch)  # routes the adapter's own client through the fake

    adapter = OpenAICompatibleAdapter(base_url="https://endpoint.invalid/v1", auth=auth)
    route = ModelRoute(
        route_id="r1",
        capability=ModelCapability.PLANNING,
        adapter="openai_compatible",
        provider="operator-endpoint",
        model="m1",
        model_revision="m1-2026-01-01",
    )
    request = PlanningRequest(messages=(ChatMessage(role="user", content="plan"),))

    with caplog.at_level(logging.DEBUG):
        adapter.invoke(route=route, request=request)

    # The secret is present only in the in-memory recorded request header.
    assert len(server.requests) == 1
    recorded = server.requests[0]
    assert recorded.headers.get("authorization") == f"Bearer {secret}"

    # Absent from all captured logs.
    assert all(secret not in record.getMessage() for record in caplog.records)

    # Absent from serialized route metadata and from the auth/resolver reprs.
    assert secret not in repr(route)
    assert secret not in repr(auth)
    assert secret not in repr(resolver)
    # Metadata holds only the secret *reference*, never the value.
    cfg = AuthConfig(type="bearer", secret_ref="file:route-secret")
    assert secret not in repr(cfg)
