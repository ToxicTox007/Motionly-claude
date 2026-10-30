import asyncio
import threading
import weakref
from time import perf_counter
from typing import Any

import httpx

from app.core.logging import get_logger
from app.services.request_counters import increment_supabase_calls

logger = get_logger(__name__)

_RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "PATCH"})

# Shared connection pools (database audit DB-20). Building an httpx client
# loads the CA bundle into a new SSL context: measured 27 ms (async) and 47 ms
# (sync) per client, which every PostgREST call used to pay. Clients are now
# reused: per process for sync calls, per event loop for async calls (an
# AsyncClient is bound to the loop it first runs on), and per timeout. httpx's
# default pool limits apply (100 connections, 20 kept alive for 5 s, well
# inside the gateway's keep-alive). The client class is part of the key so a
# test that substitutes httpx.Client/AsyncClient gets its own instance.
_sync_pools: dict[tuple[object, float], httpx.Client] = {}
_sync_pool_lock = threading.Lock()
_async_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[object, float], httpx.AsyncClient]]" = weakref.WeakKeyDictionary()  # noqa: E501


def _sync_client(timeout: float) -> httpx.Client:
    key = (httpx.Client, timeout)
    client = _sync_pools.get(key)
    if client is None or getattr(client, "is_closed", False):
        with _sync_pool_lock:
            client = _sync_pools.get(key)
            if client is None or getattr(client, "is_closed", False):
                client = httpx.Client(timeout=timeout)
                _sync_pools[key] = client
    return client


def _async_client(timeout: float) -> httpx.AsyncClient:
    pools = _async_pools.setdefault(asyncio.get_running_loop(), {})
    key = (httpx.AsyncClient, timeout)
    client = pools.get(key)
    if client is None or getattr(client, "is_closed", False):
        client = httpx.AsyncClient(timeout=timeout)
        pools[key] = client
    return client


def _can_retry(method: str) -> bool:
    """Avoid replaying non-idempotent creates after an ambiguous commit."""
    return method.upper() in _RETRYABLE_METHODS


class SupabaseRestClient:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        repository: str,
        metrics_registry: Any | None = None,
        timeout_seconds: float = 10,
        max_retries: int = 1,
    ) -> None:
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.repository = repository
        self.metrics_registry = metrics_registry
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Any | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        increment_supabase_calls()
        table = _table_label(path)
        attempt = 0
        while True:
            attempt += 1
            started_at = perf_counter()
            try:
                response = _sync_client(timeout or self.timeout_seconds).request(
                    method,
                    f"{self.rest_url}/{path.lstrip('/')}",
                    headers={**self.headers, **(headers or {})},
                    params=params,
                    json=json,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                latency_ms = round((perf_counter() - started_at) * 1000)
                self._observe(
                    method=method,
                    table=table,
                    status="exception",
                    latency_ms=latency_ms,
                    error=type(exc).__name__,
                )
                logger.warning(
                    "supabase_request_failed",
                    extra={
                        "repository": self.repository,
                        "method": method.upper(),
                        "table": table,
                        "status": "exception",
                        "latency_ms": latency_ms,
                        "attempt": attempt,
                        "error": type(exc).__name__,
                    },
                )
                if _can_retry(method) and attempt <= self.max_retries:
                    continue
                raise

            latency_ms = round((perf_counter() - started_at) * 1000)
            status = str(response.status_code)
            error = "http_status" if response.status_code >= 400 else None
            self._observe(
                method=method,
                table=table,
                status=status,
                latency_ms=latency_ms,
                error=error,
            )
            logger.info(
                "supabase_request_completed",
                extra={
                    "repository": self.repository,
                    "method": method.upper(),
                    "table": table,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "attempt": attempt,
                },
            )
            if (
                response.status_code >= 500
                and _can_retry(method)
                and attempt <= self.max_retries
            ):
                continue
            return response

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        params: Any | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        increment_supabase_calls()
        table = _table_label(path)
        attempt = 0
        while True:
            attempt += 1
            started_at = perf_counter()
            try:
                response = await _async_client(timeout or self.timeout_seconds).request(
                    method,
                    f"{self.rest_url}/{path.lstrip('/')}",
                    headers={**self.headers, **(headers or {})},
                    params=params,
                    json=json,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                latency_ms = round((perf_counter() - started_at) * 1000)
                self._observe(
                    method=method,
                    table=table,
                    status="exception",
                    latency_ms=latency_ms,
                    error=type(exc).__name__,
                )
                logger.warning(
                    "supabase_request_failed",
                    extra={
                        "repository": self.repository,
                        "method": method.upper(),
                        "table": table,
                        "status": "exception",
                        "latency_ms": latency_ms,
                        "attempt": attempt,
                        "error": type(exc).__name__,
                        "async_client": True,
                    },
                )
                if _can_retry(method) and attempt <= self.max_retries:
                    continue
                raise

            latency_ms = round((perf_counter() - started_at) * 1000)
            status = str(response.status_code)
            error = "http_status" if response.status_code >= 400 else None
            self._observe(
                method=method,
                table=table,
                status=status,
                latency_ms=latency_ms,
                error=error,
            )
            logger.info(
                "supabase_request_completed",
                extra={
                    "repository": self.repository,
                    "method": method.upper(),
                    "table": table,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "attempt": attempt,
                    "async_client": True,
                },
            )
            if (
                response.status_code >= 500
                and _can_retry(method)
                and attempt <= self.max_retries
            ):
                continue
            return response

    def _observe(
        self,
        *,
        method: str,
        table: str,
        status: str,
        latency_ms: int,
        error: str | None,
    ) -> None:
        if self.metrics_registry is None:
            return
        labels = {
            "repository": self.repository,
            "method": method.upper(),
            "table": table,
            "status": status,
        }
        self.metrics_registry.observe("supabase_request_duration_ms", latency_ms, **labels)
        self.metrics_registry.observe("supabase_request_count", 1, **labels)
        if error is not None:
            self.metrics_registry.observe("supabase_error_count", 1, error=error, **labels)


def _table_label(path: str) -> str:
    clean_path = path.lstrip("/").split("?", 1)[0]
    return clean_path.split("/", 1)[0] or "unknown"
