"""Workspace-membership role cache (Task 19).

``require_workspace_membership`` resolves the caller's ``workspace_role`` once
per short TTL window and caches it here so every subsequent request on the same
``(supabase_url, user_id, workspace_id)`` triple avoids a Supabase round-trip.

The cache is backed by Redis when ``REDIS_URL`` is configured and falls back to
an in-process dict otherwise (single-process dev / tests). Task 20 clears a
member's entry whenever their membership changes (invite accepted, role edited,
removed) by calling :func:`invalidate_membership` / :func:`invalidate_workspace`.

A *negative* result (the caller is not a member) is never cached for longer than
``AUTH_MEMBERSHIP_NEGATIVE_CACHE_TTL_SECONDS`` (default 0 — not cached) so a
freshly added member is not locked out by a stale "denied" entry.
"""

from __future__ import annotations

import os
import threading
from time import monotonic
from typing import Protocol
from uuid import UUID

# Sentinel stored for "checked and the caller is not a member".
_NEGATIVE = "\x00not-a-member"

_KEY_PREFIX = "motionly:workspace-membership"


def _positive_ttl_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("AUTH_MEMBERSHIP_CACHE_TTL_SECONDS", "30")))
    except ValueError:
        return 30.0


def _negative_ttl_seconds() -> float:
    """Negative entries are short-lived (default: not cached) so a brand-new
    member is never locked out by a stale denial."""
    try:
        return max(0.0, float(os.getenv("AUTH_MEMBERSHIP_NEGATIVE_CACHE_TTL_SECONDS", "0")))
    except ValueError:
        return 0.0


def membership_key(user_id: UUID, workspace_id: UUID) -> str:
    """Redis key for a single membership entry (stable, documented format)."""
    return f"{_KEY_PREFIX}:{workspace_id}:{user_id}"


def workspace_scan_pattern(workspace_id: UUID) -> str:
    """Redis SCAN pattern matching every member entry for a workspace."""
    return f"{_KEY_PREFIX}:{workspace_id}:*"


class MembershipCacheBackend(Protocol):
    def get(self, user_id: UUID, workspace_id: UUID) -> str | None: ...

    def set(self, user_id: UUID, workspace_id: UUID, value: str, ttl_seconds: float) -> None: ...

    def invalidate_membership(self, user_id: UUID, workspace_id: UUID) -> None: ...

    def invalidate_workspace(self, workspace_id: UUID) -> None: ...

    def clear(self) -> None: ...


class InMemoryMembershipCache:
    """Process-local backend used when ``REDIS_URL`` is unset (dev / tests)."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def get(self, user_id: UUID, workspace_id: UUID) -> str | None:
        key = membership_key(user_id, workspace_id)
        now = monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at is not None and expires_at <= now:
                self._entries.pop(key, None)
                return None
            return value

    def set(self, user_id: UUID, workspace_id: UUID, value: str, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        key = membership_key(user_id, workspace_id)
        with self._lock:
            self._entries[key] = (value, monotonic() + ttl_seconds)

    def invalidate_membership(self, user_id: UUID, workspace_id: UUID) -> None:
        key = membership_key(user_id, workspace_id)
        with self._lock:
            self._entries.pop(key, None)

    def invalidate_workspace(self, workspace_id: UUID) -> None:
        prefix = f"{_KEY_PREFIX}:{workspace_id}:"
        with self._lock:
            for key in [key for key in self._entries if key.startswith(prefix)]:
                self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class RedisMembershipCache:
    """Redis-backed backend shared across API processes."""

    def __init__(self, redis_url: str) -> None:
        from redis import Redis

        self.redis = Redis.from_url(redis_url, decode_responses=True)

    def get(self, user_id: UUID, workspace_id: UUID) -> str | None:
        return self.redis.get(membership_key(user_id, workspace_id))

    def set(self, user_id: UUID, workspace_id: UUID, value: str, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        # px keeps sub-second precision for the short negative TTL.
        self.redis.set(membership_key(user_id, workspace_id), value, px=int(ttl_seconds * 1000))

    def invalidate_membership(self, user_id: UUID, workspace_id: UUID) -> None:
        self.redis.delete(membership_key(user_id, workspace_id))

    def invalidate_workspace(self, workspace_id: UUID) -> None:
        pattern = workspace_scan_pattern(workspace_id)
        keys = list(self.redis.scan_iter(match=pattern))
        if keys:
            self.redis.delete(*keys)

    def clear(self) -> None:
        for key in list(self.redis.scan_iter(match=f"{_KEY_PREFIX}:*")):
            self.redis.delete(key)


_backend_lock = threading.Lock()
_backend: MembershipCacheBackend | None = None


def _build_backend() -> MembershipCacheBackend:
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            return RedisMembershipCache(redis_url)
        except Exception:  # pragma: no cover - fall back if redis import/conn fails
            return InMemoryMembershipCache()
    return InMemoryMembershipCache()


def get_membership_cache() -> MembershipCacheBackend:
    """Return the process-wide membership cache backend (lazily built)."""
    global _backend
    with _backend_lock:
        if _backend is None:
            _backend = _build_backend()
        return _backend


def set_membership_cache_for_test(backend: MembershipCacheBackend | None) -> None:
    """Swap the backend (tests). ``None`` forces a rebuild from the environment."""
    global _backend
    with _backend_lock:
        _backend = backend


def cached_role(user_id: UUID, workspace_id: UUID) -> str | None:
    """Return the cached role, the ``_NEGATIVE`` sentinel, or ``None`` (miss)."""
    return get_membership_cache().get(user_id, workspace_id)


def store_role(user_id: UUID, workspace_id: UUID, role: str) -> None:
    get_membership_cache().set(user_id, workspace_id, role, _positive_ttl_seconds())


def store_negative(user_id: UUID, workspace_id: UUID) -> None:
    ttl = _negative_ttl_seconds()
    if ttl > 0:
        get_membership_cache().set(user_id, workspace_id, _NEGATIVE, ttl)


def invalidate_membership(user_id: UUID, workspace_id: UUID) -> None:
    """Drop one member's cached role. Call after their membership changes."""
    get_membership_cache().invalidate_membership(user_id, workspace_id)


def invalidate_workspace(workspace_id: UUID) -> None:
    """Drop every cached role for a workspace. Call after bulk membership changes."""
    get_membership_cache().invalidate_workspace(workspace_id)


def clear_membership_cache_for_test() -> None:
    get_membership_cache().clear()
