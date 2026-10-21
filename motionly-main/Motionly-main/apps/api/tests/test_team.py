"""API unit tests for team management: invites, seats, membership (Task 20).

These drive the real app through a TestClient and override only ``get_actor``
so each request runs as a chosen workspace role; the ``require_role`` guards and
the in-memory seat-safe repository are exercised unchanged. Seat races are
proven separately against the live database in
``tests/integration/test_invitation_seat_race.py`` (RUN_SUPABASE_CONTRACTS=1).
"""

import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "local")

from app.api.dependencies import Actor, get_actor  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.billing import BillingPlanKey, SubscriptionStatus  # noqa: E402

_OWNER = UUID("00000000-0000-4000-8000-000000000001")
_WORKSPACE = UUID("00000000-0000-4000-8000-000000000002")
_INVITEE = UUID("00000000-0000-4000-8000-000000000011")
_SECOND = UUID("00000000-0000-4000-8000-000000000012")


def _make_client(role: str = "owner", *, actor_user: UUID = _OWNER, plan="agency"):
    app = create_app()

    def _actor() -> Actor:
        return Actor(user_id=actor_user, workspace_id=_WORKSPACE, role=role)

    app.dependency_overrides[get_actor] = _actor

    # Seed the workspace plan so the seat limit comes from the single plan source.
    billing = app.state.billing_repository
    billing.upsert_subscription(
        workspace_id=_WORKSPACE,
        plan=BillingPlanKey(plan),
        status=SubscriptionStatus.ACTIVE,
    )
    team = app.state.team_repository
    team.seed_member(workspace_id=_WORKSPACE, user_id=_OWNER, role="owner")

    client = TestClient(app, raise_server_exceptions=False)
    return client, app


def _headers() -> dict[str, str]:
    return {"x-motionly-workspace-id": str(_WORKSPACE)}


# ---------------------------------------------------------------------------
# create invite + seat limit
# ---------------------------------------------------------------------------
def test_owner_creates_invite_returns_token_once_in_fragment():
    client, app = _make_client("owner", plan="pro")  # pro = 2 seats
    response = client.post(
        "/team/invitations", json={"role": "member"}, headers=_headers()
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["token"]
    # Invite URL carries the token only in the fragment, never a query/path.
    assert "/invite#token=" in body["url"]
    assert body["token"] in body["url"].split("#", 1)[1]
    assert body["invitation"]["role"] == "member"
    # The fingerprint is stored, never the raw token.
    stored = list(app.state.team_repository.invitations.values())[0]
    assert stored.token_digest != body["token"]
    assert len(stored.token_digest) == 64


def test_admin_can_create_invite():
    client, _ = _make_client("admin", plan="agency")
    response = client.post(
        "/team/invitations", json={"role": "member"}, headers=_headers()
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("role", ["member"])
def test_member_cannot_create_invite(role):
    client, _ = _make_client(role)
    response = client.post(
        "/team/invitations", json={"role": "member"}, headers=_headers()
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "workspace_role_forbidden"


def test_seat_limit_blocks_over_allocation():
    # free plan = 1 seat; the owner already occupies it, so the first invite
    # (0 pending + 1 active >= 1) is refused.
    client, _ = _make_client("owner", plan="free")
    response = client.post(
        "/team/invitations", json={"role": "member"}, headers=_headers()
    )
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "seat_limit_reached"


def test_invite_cannot_grant_owner():
    client, _ = _make_client("owner", plan="agency")
    response = client.post(
        "/team/invitations", json={"role": "owner"}, headers=_headers()
    )
    # 'owner' is not an InvitableRole -> schema 422.
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# list + revoke
# ---------------------------------------------------------------------------
def test_list_and_revoke_invite():
    client, app = _make_client("owner", plan="agency")
    created = client.post(
        "/team/invitations", json={"role": "member"}, headers=_headers()
    ).json()
    invite_id = created["invitation"]["id"]

    listed = client.get("/team/invitations", headers=_headers())
    assert listed.status_code == 200
    assert any(i["id"] == invite_id for i in listed.json()["invitations"])

    revoked = client.post(
        f"/team/invitations/{invite_id}/revoke", headers=_headers()
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_member_cannot_list_or_revoke():
    client, _ = _make_client("member")
    assert client.get("/team/invitations", headers=_headers()).status_code == 403
    assert (
        client.post(
            f"/team/invitations/{uuid4()}/revoke", headers=_headers()
        ).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# accept (self-service)
# ---------------------------------------------------------------------------
def test_accept_invite_makes_member():
    client, app = _make_client("owner", plan="agency")
    created = client.post(
        "/team/invitations", json={"role": "member"}, headers=_headers()
    ).json()
    token = created["token"]

    # Now act as the invitee (any-member self-service).
    app.dependency_overrides[get_actor] = lambda: Actor(
        user_id=_INVITEE, workspace_id=_WORKSPACE, role="member"
    )
    response = client.post(
        "/team/invitations/accept", json={"token": token}, headers=_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["member"]["role"] == "member"
    assert body["member"]["status"] == "active"
    assert UUID(body["member"]["user_id"]) == _INVITEE


def test_accept_rejects_revoked_invite():
    client, app = _make_client("owner", plan="agency")
    created = client.post(
        "/team/invitations", json={"role": "member"}, headers=_headers()
    ).json()
    token = created["token"]
    invite_id = created["invitation"]["id"]
    client.post(f"/team/invitations/{invite_id}/revoke", headers=_headers())

    app.dependency_overrides[get_actor] = lambda: Actor(
        user_id=_INVITEE, workspace_id=_WORKSPACE, role="member"
    )
    response = client.post(
        "/team/invitations/accept", json={"token": token}, headers=_headers()
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invitation_not_found"


def test_accept_rejects_already_used_invite():
    client, app = _make_client("owner", plan="agency")
    token = client.post(
        "/team/invitations", json={"role": "member"}, headers=_headers()
    ).json()["token"]

    app.dependency_overrides[get_actor] = lambda: Actor(
        user_id=_INVITEE, workspace_id=_WORKSPACE, role="member"
    )
    assert (
        client.post(
            "/team/invitations/accept", json={"token": token}, headers=_headers()
        ).status_code
        == 200
    )
    # A different user replaying the same token is refused.
    app.dependency_overrides[get_actor] = lambda: Actor(
        user_id=_SECOND, workspace_id=_WORKSPACE, role="member"
    )
    replay = client.post(
        "/team/invitations/accept", json={"token": token}, headers=_headers()
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "invitation_already_used"


# ---------------------------------------------------------------------------
# change role / remove / last-owner protection
# ---------------------------------------------------------------------------
def test_change_member_role_and_remove():
    client, app = _make_client("owner", plan="agency")
    app.state.team_repository.seed_member(
        workspace_id=_WORKSPACE, user_id=_INVITEE, role="member"
    )
    changed = client.patch(
        f"/team/members/{_INVITEE}/role", json={"role": "admin"}, headers=_headers()
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "admin"

    removed = client.delete(f"/team/members/{_INVITEE}", headers=_headers())
    assert removed.status_code == 204


def test_last_owner_cannot_be_removed():
    client, _ = _make_client("owner", plan="agency")
    response = client.delete(f"/team/members/{_OWNER}", headers=_headers())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_owner_protected"


def test_member_cannot_change_roles_or_remove():
    client, app = _make_client("member")
    app.state.team_repository.seed_member(
        workspace_id=_WORKSPACE, user_id=_INVITEE, role="member"
    )
    assert (
        client.patch(
            f"/team/members/{_INVITEE}/role",
            json={"role": "admin"},
            headers=_headers(),
        ).status_code
        == 403
    )
    assert client.delete(f"/team/members/{_INVITEE}", headers=_headers()).status_code == 403


# ---------------------------------------------------------------------------
# transfer ownership (owner-only) + leave
# ---------------------------------------------------------------------------
def test_transfer_ownership_owner_only():
    client, app = _make_client("owner", plan="agency")
    app.state.team_repository.seed_member(
        workspace_id=_WORKSPACE, user_id=_INVITEE, role="admin"
    )
    response = client.post(
        "/team/transfer-ownership", json={"user_id": str(_INVITEE)}, headers=_headers()
    )
    assert response.status_code == 204, response.text
    team = app.state.team_repository
    assert team.members[(_WORKSPACE, _INVITEE)].role == "owner"
    assert team.members[(_WORKSPACE, _OWNER)].role == "admin"


def test_admin_cannot_transfer_ownership():
    client, app = _make_client("admin", plan="agency")
    app.state.team_repository.seed_member(
        workspace_id=_WORKSPACE, user_id=_INVITEE, role="admin"
    )
    response = client.post(
        "/team/transfer-ownership", json={"user_id": str(_INVITEE)}, headers=_headers()
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "workspace_role_forbidden"


def test_leave_last_owner_blocked_but_member_can_leave():
    client, app = _make_client("member", actor_user=_INVITEE, plan="agency")
    app.state.team_repository.seed_member(
        workspace_id=_WORKSPACE, user_id=_INVITEE, role="member"
    )
    # A member can leave.
    response = client.post("/team/leave", headers=_headers())
    assert response.status_code == 204, response.text

    # The sole owner cannot leave without transferring.
    owner_client, _ = _make_client("owner", plan="agency")
    blocked = owner_client.post("/team/leave", headers=_headers())
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "last_owner_protected"
