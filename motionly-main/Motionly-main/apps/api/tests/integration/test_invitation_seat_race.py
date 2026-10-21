"""Concurrent seat-allocation contract for invite acceptance (Task 20).

Gated by RUN_SUPABASE_CONTRACTS=1 and run against the isolated local Supabase
stack. This proves the per-workspace advisory lock inside
``motionly_accept_workspace_invitation`` serializes concurrent accepts so the
plan's seat limit is never over-allocated: when many users race for the last
free seat, exactly one wins and the rest are refused with SEAT_LIMIT_REACHED.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.errors import ApiError
from app.repositories.team import SupabaseTeamRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SUPABASE_CONTRACTS") != "1",
    reason="Set RUN_SUPABASE_CONTRACTS=1 against an isolated local Supabase stack.",
)


def _admin_headers(key: str) -> dict[str, str]:
    return {"apikey": key, "authorization": f"Bearer {key}", "content-type": "application/json"}


def _create_user(url: str, key: str) -> UUID:
    created = httpx.post(
        f"{url}/auth/v1/admin/users",
        headers=_admin_headers(key),
        json={"email": f"seat-{uuid4()}@motionly.test", "password": f"Seat-{uuid4()}!",
              "email_confirm": True},
        timeout=20,
    )
    created.raise_for_status()
    return UUID(created.json()["id"])


def _delete_user(url: str, key: str, user_id: UUID) -> None:
    httpx.delete(f"{url}/auth/v1/admin/users/{user_id}", headers=_admin_headers(key), timeout=20)


def _owner_workspace(url: str, key: str, user_id: UUID) -> UUID:
    membership = httpx.get(
        f"{url}/rest/v1/workspace_members",
        headers={"apikey": key, "authorization": f"Bearer {key}"},
        params={"user_id": f"eq.{user_id}", "select": "workspace_id", "limit": "1"},
        timeout=20,
    )
    membership.raise_for_status()
    return UUID(membership.json()[0]["workspace_id"])


def test_concurrent_accepts_never_over_allocate_the_last_seat() -> None:
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    repo = SupabaseTeamRepository(supabase_url=url, service_role_key=key)

    owner = _create_user(url, key)
    workspace_id = _owner_workspace(url, key, owner)

    # A 3-seat workspace already has the owner active -> exactly 2 seats remain.
    seat_limit = 3
    racers = 6
    contenders = [_create_user(url, key) for _ in range(racers)]

    # Mint one live invite per contender (all seat_limit permitting at mint time
    # would fail; create invites directly so the race is purely on acceptance).
    tokens: list[str] = []
    for _ in range(racers):
        token = f"race-{uuid4()}"
        # Insert the invite row straight through the service role so minting
        # order does not itself consume the budget we are racing on.
        digest = sha256(token.encode()).hexdigest()
        inserted = httpx.post(
            f"{url}/rest/v1/workspace_invitations",
            headers={**_admin_headers(key), "prefer": "return=representation"},
            json={
                "workspace_id": str(workspace_id),
                "token_digest": digest,
                "role": "member",
                "created_by": str(owner),
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
            timeout=20,
        )
        inserted.raise_for_status()
        tokens.append(token)

    try:
        def _accept(args):
            token, user_id = args
            try:
                repo.accept_invitation(token=token, user_id=user_id, seat_limit=seat_limit)
                return True
            except ApiError as exc:
                assert exc.code == "seat_limit_reached", exc.code
                return False

        with ThreadPoolExecutor(max_workers=racers) as pool:
            results = list(pool.map(_accept, zip(tokens, contenders, strict=True)))

        # Owner occupies 1 seat; exactly (seat_limit - 1) accepts may win.
        assert sum(results) == seat_limit - 1, results

        # The database agrees: active members never exceed the seat limit.
        active = httpx.get(
            f"{url}/rest/v1/workspace_members",
            headers={"apikey": key, "authorization": f"Bearer {key}"},
            params={"workspace_id": f"eq.{workspace_id}", "status": "eq.active",
                    "select": "user_id"},
            timeout=20,
        )
        active.raise_for_status()
        assert len(active.json()) == seat_limit
    finally:
        _delete_user(url, key, owner)
        for user_id in contenders:
            _delete_user(url, key, user_id)
