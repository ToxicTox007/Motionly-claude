from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ProviderErrorInfo:
    code: str
    retryable: bool
    status_code: int | None = None


class ProviderCircuitOpen(RuntimeError):
    def __init__(self, provider: str, retry_after_s: int) -> None:
        super().__init__(f"{provider} provider temporarily unavailable")
        self.provider = provider
        self.retry_after_s = retry_after_s
        self.error_info = ProviderErrorInfo("provider_circuit_open", True)


class ProviderCallFailure(RuntimeError):
    def __init__(
        self,
        *,
        provider: str,
        error_info: ProviderErrorInfo,
        message: str | None = None,
    ) -> None:
        super().__init__(message or error_info.code)
        self.provider = provider
        self.error_info = error_info
        self.safe_message = message or _safe_provider_message(error_info.code)


class ProviderCircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
        clock=time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self.clock = clock
        self._failures: dict[str, int] = {}
        self._opened_until: dict[str, float] = {}

    def check(self, provider: str) -> None:
        opened_until = self._opened_until.get(provider)
        if opened_until is None:
            return
        now = self.clock()
        if now >= opened_until:
            self._opened_until.pop(provider, None)
            self._failures[provider] = 0
            return
        raise ProviderCircuitOpen(provider, retry_after_s=max(1, round(opened_until - now)))

    def record_success(self, provider: str) -> None:
        self._failures[provider] = 0
        self._opened_until.pop(provider, None)

    def record_failure(self, provider: str, *, retryable: bool) -> bool:
        if not retryable:
            return False
        failures = self._failures.get(provider, 0) + 1
        self._failures[provider] = failures
        if failures < self.failure_threshold:
            return False
        self._opened_until[provider] = self.clock() + self.cooldown_seconds
        return True

    def reset(self) -> None:
        self._failures.clear()
        self._opened_until.clear()


provider_circuit_breaker = ProviderCircuitBreaker()


def classify_provider_exception(exc: BaseException) -> ProviderErrorInfo:
    if isinstance(exc, ProviderCallFailure):
        return exc.error_info
    if isinstance(exc, ProviderCircuitOpen):
        return exc.error_info
    if isinstance(exc, httpx.TimeoutException):
        return ProviderErrorInfo("provider_timeout", True)
    if isinstance(exc, httpx.NetworkError):
        return ProviderErrorInfo("provider_network_error", True)
    if isinstance(exc, httpx.HTTPStatusError):
        return classify_provider_status(exc.response.status_code)
    return ProviderErrorInfo("provider_error", True)


def classify_provider_status(status_code: int) -> ProviderErrorInfo:
    if status_code == 429:
        return ProviderErrorInfo("provider_rate_limited", True, status_code)
    if status_code in {401, 403}:
        return ProviderErrorInfo("provider_auth_failed", False, status_code)
    if status_code == 402:
        return ProviderErrorInfo("provider_quota_exceeded", False, status_code)
    if 500 <= status_code < 600:
        return ProviderErrorInfo("provider_unavailable", True, status_code)
    if 400 <= status_code < 500:
        return ProviderErrorInfo("provider_request_rejected", False, status_code)
    return ProviderErrorInfo("provider_error", True, status_code)


def bounded_backoff_seconds(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    return min(cap, base * (2 ** max(0, attempt - 1)))


def _safe_provider_message(code: str) -> str:
    messages = {
        "provider_timeout": "The AI provider timed out. This job can be retried.",
        "provider_rate_limited": (
            "The AI provider is rate limiting requests. This job can be retried."
        ),
        "provider_auth_failed": "The AI provider credentials are invalid.",
        "provider_quota_exceeded": "The AI provider quota is exhausted.",
        "provider_malformed_response": "The AI provider returned an invalid response.",
        "provider_unavailable": (
            "The AI provider is temporarily unavailable. This job can be retried."
        ),
        "provider_network_error": "The AI provider could not be reached. This job can be retried.",
        "provider_circuit_open": "The AI provider is temporarily paused after repeated failures.",
    }
    return messages.get(code, "The AI provider request failed.")
