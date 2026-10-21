"""Tests for the workspace-membership role cache (Task 19).

Covers cache hit, invalidation (per-member and per-workspace), the stable Redis
key format, the in-memory fallback when REDIS_URL is unset, and that a negative
(not-a-member) result is not cached by default.
"""

from uuid import uuid4

import pytest

from app.services import membership_cache
from app.services.auth import require_workspace_membership


@pytest.fixture(autouse=True)
def fresh_in_memory_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AUTH_MEMBERSHIP_CACHE_TTL_SECONDS", "30")
    monkeypatch.setenv("AUTH_MEMBERSHIP_NEGATIVE_CACHE_TTL_SECONDS", "0")
    membership_cache.set_membership_cache_for_test(membership_cache.InMemoryMembershipCache())
    yield
    membership_cache.set_membership_cache_for_test(None)


def test_redis_key_format_is_stable():
    user_id = uuid4()
    workspace_id = uuid4()
    assert (
        membership_cache.membership_key(user_id, workspace_id)
        == f"motionly:workspace-membership:{workspace_id}:{user_id}"
    )
    assert membership_cache.workspace_scan_pattern(workspace_id) == (
        f"motionly:workspace-membership:{workspace_id}:*"
    )


def test_unset_redis_url_uses_in_memory_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    membership_cache.set_membership_cache_for_test(None)
    assert isinstance(
        membership_cache.get_membership_cache(), membership_cache.InMemoryMembershipCache
    )


def test_second_check_is_a_cache_hit_without_a_supabase_call(monkeypatch: pytest.MonkeyPatch):
    user_id = uuid4()
    workspace_id = uuid4()
    calls = {"count": 0}

    def fake_get(url, headers, params, timeout):  # noqa: ANN001
        calls["count"] += 1
        return _FakeResponse(200, [{"role": "admin"}])

    monkeypatch.setattr("app.services.auth.httpx.get", fake_get)

    first = require_workspace_membership(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    second = require_workspace_membership(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
    )

    assert first == "admin"
    assert second == "admin"
    assert calls["count"] == 1  # second resolved from cache


def test_invalidate_membership_forces_a_refresh(monkeypatch: pytest.MonkeyPatch):
    user_id = uuid4()
    workspace_id = uuid4()
    roles = iter(["member", "owner"])

    def fake_get(url, headers, params, timeout):  # noqa: ANN001
        return _FakeResponse(200, [{"role": next(roles)}])

    monkeypatch.setattr("app.services.auth.httpx.get", fake_get)

    kwargs = dict(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    assert require_workspace_membership(**kwargs) == "member"
    membership_cache.invalidate_membership(user_id, workspace_id)
    assert require_workspace_membership(**kwargs) == "owner"


def test_invalidate_workspace_drops_every_member(monkeypatch: pytest.MonkeyPatch):
    workspace_id = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    membership_cache.store_role(user_a, workspace_id, "owner")
    membership_cache.store_role(user_b, workspace_id, "member")

    membership_cache.invalidate_workspace(workspace_id)

    assert membership_cache.cached_role(user_a, workspace_id) is None
    assert membership_cache.cached_role(user_b, workspace_id) is None


def test_negative_result_is_not_cached_by_default(monkeypatch: pytest.MonkeyPatch):
    user_id = uuid4()
    workspace_id = uuid4()
    calls = {"count": 0}

    def fake_get(url, headers, params, timeout):  # noqa: ANN001
        calls["count"] += 1
        return _FakeResponse(200, [])

    monkeypatch.setattr("app.services.auth.httpx.get", fake_get)

    kwargs = dict(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
        user_id=user_id,
        workspace_id=workspace_id,
    )
    from app.api.errors import ApiError

    for _ in range(2):
        with pytest.raises(ApiError) as exc:
            require_workspace_membership(**kwargs)
        assert exc.value.code == "workspace_access_denied"
    # Each denial re-checked Supabase; nothing stale was cached.
    assert calls["count"] == 2


class _FakeResponse:
    def __init__(self, status_code: int, payload):  # noqa: ANN001
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload
