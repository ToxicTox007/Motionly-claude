"""Shared HTTP plumbing for the ``generic_http`` and ``async_job`` adapters.

This module owns the parts both template adapters share: the auth-application
protocol, the httpx client factory (``trust_env=False``, a connect-capped
timeout, an optional injected transport for tests), bounded JSON response
reading, and the ``error_map`` -> ``ProviderErrorInfo`` translation.

Design notes tied to the plan:

* Section 2.10: "Auth is applied by the adapter, never by a template." The
  :class:`AuthApplier` protocol is how the adapter injects credentials into the
  request headers. Task 5 implements the concrete strategies (bearer, custom
  header, Azure api-key, OAuth2) against this protocol; Tier 1 ships only
  :class:`NoAuth`.
* Section 2.10 "error_map ... maps an HTTP status to one Motionly error class":
  :func:`error_info_for_class` maps each class onto a
  :class:`ProviderErrorInfo` consistently with ``classify_provider_status``:
  ``rejected`` is not retryable, not fallback-eligible and not billable;
  ``unavailable`` is retryable and not billable; ``auth`` and ``quota`` reuse
  the existing classification codes; ``malformed`` is the standard malformed
  response.
* Every adapter-auth request sends ``accept: application/json`` (Section 2.10
  "Concrete key layout", fixed rules).
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Protocol

import httpx

from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
    classify_provider_status,
)

# Default cap on a JSON response body the adapters will read into memory.
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class AuthApplier(Protocol):
    """Injects adapter credentials into outgoing request headers.

    Implementations never touch the template or the request body; they only add
    or replace headers. Task 5 provides the concrete strategies.
    """

    def apply(self, headers: MutableMapping[str, str]) -> None: ...


class NoAuth:
    """An :class:`AuthApplier` that adds nothing (private endpoints, or ``auth: none``)."""

    def apply(self, headers: MutableMapping[str, str]) -> None:  # noqa: D401 - protocol impl
        return None


def build_client(
    *,
    timeout_s: float,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """An httpx client matching the archived adapters' construction.

    ``trust_env=False`` (ignore proxy/cert env), a connect timeout capped at 10s,
    and an optional injected transport for tests (``httpx.MockTransport``).
    """
    return httpx.Client(
        timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
        transport=transport,
        trust_env=False,
    )


def base_headers(extra_headers: Mapping[str, str] | None = None) -> dict[str, str]:
    """Headers for an adapter-auth request: ``accept: application/json`` plus extras."""
    headers: dict[str, str] = {"accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return headers


def read_json_response(
    response: httpx.Response,
    *,
    provider: str,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    error_map: Mapping[str, str] | None = None,
) -> object:
    """Validate status, enforce a size cap, and parse a JSON body.

    HTTP status >= 400 is translated through ``error_map`` when one is given,
    else through ``classify_provider_status``. A body larger than ``max_bytes``
    or a non-JSON body is a malformed response.
    """
    if response.status_code >= 400:
        raise ProviderCallFailure(
            provider=provider,
            error_info=status_error_info(response.status_code, error_map),
        )
    content = response.content
    if len(content) > max_bytes:
        raise malformed_failure(provider)
    try:
        return response.json()
    except ValueError as exc:
        raise malformed_failure(provider) from exc


def status_error_info(
    status_code: int, error_map: Mapping[str, str] | None
) -> ProviderErrorInfo:
    """Translate an HTTP status into a :class:`ProviderErrorInfo`.

    When ``error_map`` lists the exact status, its Motionly class wins; otherwise
    the shared ``classify_provider_status`` rule applies (as ``async_job`` always
    does — it has no ``error_map``).
    """
    if error_map is not None:
        klass = error_map.get(str(status_code))
        if klass is not None:
            return error_info_for_class(klass, status_code)
    return classify_provider_status(status_code)


def error_info_for_class(klass: str, status_code: int | None = None) -> ProviderErrorInfo:
    """Map a Motionly ``error_map`` class to a :class:`ProviderErrorInfo`."""
    if klass == "rejected":
        return ProviderErrorInfo(
            "provider_request_rejected",
            False,
            status_code,
            fallback_eligible=False,
            circuit_worthy=False,
            billable=False,
        )
    if klass == "unavailable":
        return ProviderErrorInfo(
            "provider_unavailable",
            True,
            status_code,
            billable=False,
        )
    if klass == "auth":
        # Parity with classify_provider_status(401/403).
        return ProviderErrorInfo(
            "provider_auth_failed",
            False,
            status_code,
            fallback_eligible=True,
            circuit_worthy=True,
        )
    if klass == "quota":
        # Parity with classify_provider_status(402).
        return ProviderErrorInfo(
            "provider_quota_exceeded",
            False,
            status_code,
            fallback_eligible=True,
            circuit_worthy=True,
        )
    if klass == "malformed":
        return ProviderErrorInfo(
            "provider_malformed_response",
            False,
            status_code,
            fallback_eligible=True,
            circuit_worthy=True,
        )
    raise ValueError(f"unknown error_map class: {klass!r}")


def malformed_failure(provider: str) -> ProviderCallFailure:
    """The standard malformed-response failure (fallback-eligible, circuit-worthy)."""
    return ProviderCallFailure(
        provider=provider,
        error_info=error_info_for_class("malformed"),
    )


def exception_failure(provider: str, exc: BaseException) -> ProviderCallFailure:
    """Wrap a transport exception via ``classify_provider_exception``."""
    return ProviderCallFailure(
        provider=provider,
        error_info=classify_provider_exception(exc),
    )
