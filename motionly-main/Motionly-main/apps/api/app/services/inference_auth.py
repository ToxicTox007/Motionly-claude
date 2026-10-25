"""Authentication strategies for the declarative inference registry.

Plan Task 5 and Section 2.2 ("Auth types", Tier 1):

* ``none`` — private endpoints only; adds nothing.
* ``bearer`` — ``Authorization: Bearer <secret>``.
* ``custom_header`` — a configured header name carrying the secret.
* ``azure_api_key`` — the ``api-key`` header, matching the existing
  ``AzureOpenAIAdapter``.
* ``oauth2_client_credentials`` — a client-credentials token exchange against a
  token URL, caching the access token until just before it expires.

Every strategy implements :class:`app.services.inference_http.AuthApplier`:
``apply(headers)`` injects credentials into outgoing request headers and nothing
else. Secrets are resolved through a :class:`SecretResolver`, so each resolved
value — and every OAuth access token — is registered with the generic log
redactor. No strategy puts a secret in an exception message, ``repr`` or log.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, MutableMapping
from urllib.parse import urlsplit

import httpx

from app.core.logging import register_secret_value
from app.services.inference_http import build_client, error_info_for_class
from app.services.inference_registry import AuthConfig
from app.services.inference_secrets import SecretResolver
from app.services.provider_resilience import ProviderCallFailure

# Header names a template/auth strategy must never set: hop-by-hop headers and
# headers whose value the HTTP layer, not an auth strategy, must own.
_FORBIDDEN_HEADER_NAMES = frozenset(
    {
        "host",
        "content-length",
        "content-type",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "cookie",
        "set-cookie",
    }
)

# A safety margin: refresh an OAuth token this many seconds before it expires so
# an in-flight request never carries a just-expired token.
_OAUTH_EXPIRY_MARGIN_S = 30.0
# Fallback lifetime when a token endpoint omits ``expires_in``.
_OAUTH_DEFAULT_LIFETIME_S = 3600.0


class AuthConfigurationError(ValueError):
    """An auth configuration is invalid (bad header name, missing fields).

    Raised at build time, never carries a secret value.
    """


def _require_secret(resolver: SecretResolver, ref: str | None, *, field: str) -> str:
    if not ref:
        raise AuthConfigurationError(f"auth is missing {field}")
    return resolver.resolve(ref)


class _NoAuth:
    """``auth: none`` — adds no header."""

    def apply(self, headers: MutableMapping[str, str]) -> None:
        return None

    def __repr__(self) -> str:
        return "NoAuth()"


class _BearerAuth:
    """``Authorization: Bearer <secret>``."""

    __slots__ = ("_value",)

    def __init__(self, secret: str) -> None:
        self._value = f"Bearer {secret}"

    def apply(self, headers: MutableMapping[str, str]) -> None:
        headers["authorization"] = self._value

    def __repr__(self) -> str:
        return "BearerAuth(authorization=[REDACTED_SECRET])"


class _CustomHeaderAuth:
    """A configured header name carrying the secret value."""

    __slots__ = ("_name", "_value")

    def __init__(self, header_name: str, secret: str) -> None:
        self._name = header_name
        self._value = secret

    def apply(self, headers: MutableMapping[str, str]) -> None:
        headers[self._name] = self._value

    def __repr__(self) -> str:
        return f"CustomHeaderAuth(header={self._name!r}, value=[REDACTED_SECRET])"


class _AzureApiKeyAuth:
    """The ``api-key`` header, matching ``AzureOpenAIAdapter``."""

    __slots__ = ("_value",)

    def __init__(self, secret: str) -> None:
        self._value = secret

    def apply(self, headers: MutableMapping[str, str]) -> None:
        headers["api-key"] = self._value

    def __repr__(self) -> str:
        return "AzureApiKeyAuth(api-key=[REDACTED_SECRET])"


def _validate_token_url(token_url: str) -> None:
    """Apply the registry's URL rules to an OAuth token URL.

    HTTPS only, a host, no userinfo, no query/fragment. Never echoes a secret.
    """
    try:
        parsed = urlsplit(token_url)
    except ValueError as exc:
        raise AuthConfigurationError("oauth2 token_url could not be parsed") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise AuthConfigurationError("oauth2 token_url must be an absolute https URL with a host")
    if parsed.username or parsed.password:
        raise AuthConfigurationError("oauth2 token_url must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise AuthConfigurationError("oauth2 token_url must not contain a query or fragment")


class _OAuth2ClientCredentialsAuth:
    """OAuth2 client-credentials grant with a cached, auto-refreshing token.

    Thread-safe: concurrent callers that find the token missing or expired fetch
    it once (double-checked under a lock). A 401 from the token endpoint maps to
    the provider auth error class. The access token is registered with the log
    redactor.
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str | None,
        provider: str,
        transport: httpx.BaseTransport | None,
        clock: Callable[[], float],
        timeout_s: float,
    ) -> None:
        _validate_token_url(token_url)
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._provider = provider
        self._transport = transport
        self._clock = clock
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0

    def apply(self, headers: MutableMapping[str, str]) -> None:
        headers["authorization"] = f"Bearer {self._access_token()}"

    def _access_token(self) -> str:
        now = self._clock()
        token = self._token
        if token is not None and now < self._expires_at:
            return token
        with self._lock:
            now = self._clock()
            if self._token is not None and now < self._expires_at:
                return self._token
            token, lifetime = self._fetch()
            register_secret_value(token)
            self._token = token
            self._expires_at = now + max(0.0, lifetime - _OAUTH_EXPIRY_MARGIN_S)
            return token

    def _fetch(self) -> tuple[str, float]:
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scope:
            data["scope"] = self._scope
        with build_client(timeout_s=self._timeout_s, transport=self._transport) as client:
            try:
                response = client.post(
                    self._token_url,
                    data=data,
                    headers={"accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise ProviderCallFailure(
                    provider=self._provider,
                    error_info=error_info_for_class("unavailable"),
                ) from exc
        if response.status_code in (401, 403):
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=error_info_for_class("auth", response.status_code),
            )
        if response.status_code >= 400:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=error_info_for_class("unavailable", response.status_code),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=error_info_for_class("malformed"),
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=error_info_for_class("malformed"),
            )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderCallFailure(
                provider=self._provider,
                error_info=error_info_for_class("malformed"),
            )
        expires_in = payload.get("expires_in")
        lifetime = (
            float(expires_in)
            if isinstance(expires_in, (int, float)) and expires_in > 0
            else _OAUTH_DEFAULT_LIFETIME_S
        )
        return token, lifetime

    def __repr__(self) -> str:
        return "OAuth2ClientCredentialsAuth(token=[REDACTED_SECRET])"


def build_auth(
    auth: AuthConfig,
    resolver: SecretResolver,
    *,
    provider: str = "inference",
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
    timeout_s: float = 30.0,
):
    """Build an :class:`AuthApplier` for a registry :class:`AuthConfig`.

    ``resolver`` resolves (and redactor-registers) every secret. ``transport``
    and ``clock`` are injection points for OAuth tests. Raises
    :class:`AuthConfigurationError` for an invalid configuration; the message
    never carries a secret value.
    """
    kind = auth.type
    if kind == "none":
        return _NoAuth()
    if kind == "bearer":
        return _BearerAuth(_require_secret(resolver, auth.secret_ref, field="secret_ref"))
    if kind == "custom_header":
        name = (auth.header_name or "").strip()
        if not name:
            raise AuthConfigurationError("custom_header auth requires header_name")
        if name.lower() in _FORBIDDEN_HEADER_NAMES:
            raise AuthConfigurationError(f"custom_header auth may not set the {name!r} header")
        if "\n" in name or "\r" in name or ":" in name:
            raise AuthConfigurationError("custom_header header_name is not a valid header name")
        return _CustomHeaderAuth(
            name, _require_secret(resolver, auth.secret_ref, field="secret_ref")
        )
    if kind == "azure_api_key":
        return _AzureApiKeyAuth(_require_secret(resolver, auth.secret_ref, field="secret_ref"))
    if kind == "oauth2_client_credentials":
        if not auth.token_url:
            raise AuthConfigurationError("oauth2_client_credentials auth requires token_url")
        if not auth.client_id:
            raise AuthConfigurationError("oauth2_client_credentials auth requires client_id")
        client_secret = _require_secret(resolver, auth.client_secret_ref, field="client_secret_ref")
        return _OAuth2ClientCredentialsAuth(
            token_url=auth.token_url,
            client_id=auth.client_id,
            client_secret=client_secret,
            scope=auth.scope,
            provider=provider,
            transport=transport,
            clock=clock,
            timeout_s=timeout_s,
        )
    raise AuthConfigurationError(f"unknown auth type {kind!r}")
