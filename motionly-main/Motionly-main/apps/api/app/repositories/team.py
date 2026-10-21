"""Repository for team management: invite links, seats, and membership (Task 20).

All seat accounting and membership mutation goes through SECURITY DEFINER RPCs
that take a per-workspace advisory lock, so concurrent requests can never
over-allocate the last seat. The seat limit is passed in by the API from the
single plan source (``app.services.plan_limits.plan_seat_limit``); the database
never second-guesses the plan.

Only a SHA-256 fingerprint of each invite token is stored (like Task 16
export_share_links / Task 24 workspace_api_keys). The raw token is returned once
at creation and never persisted or logged.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.services.supabase import SupabaseRestClient

INVITATION_COLUMNS = (
    "id,workspace_id,token_digest,role,created_by,expires_at,revoked_at,"
    "accepted_by,accepted_at,created_at"
)


def token_digest(token: str) -> str:
    """SHA-256 fingerprint of an invite token. Only this is ever persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


# Map the RPC's raised exception messages (RAISE EXCEPTION '<CODE>') to the
# API's stable (status, code, message) triples. Unknown errors fall through to a
# 502 so a tenant can never learn about internal failures.
_RPC_ERRORS: dict[str, tuple[int, str, str]] = {
    "SEAT_LIMIT_REACHED": (
        402,
        "seat_limit_reached",
        "Your plan's seat limit is reached. Upgrade your plan to add more members.",
    ),
    "INVITE_ROLE_INVALID": (422, "invitation_role_invalid", "That invite role is not allowed."),
    "ROLE_CHANGE_INVALID": (422, "member_role_invalid", "That role change is not allowed."),
    "INVITE_NOT_FOUND": (404, "invitation_not_found", "Invitation was not found."),
    "INVITE_REVOKED": (404, "invitation_not_found", "Invitation was not found."),
    "INVITE_EXPIRED": (404, "invitation_not_found", "Invitation was not found."),
    "INVITE_ALREADY_USED": (409, "invitation_already_used", "This invitation was already used."),
    "MEMBER_NOT_FOUND": (404, "member_not_found", "Member was not found."),
    "LAST_OWNER_PROTECTED": (
        409,
        "last_owner_protected",
        "The last owner cannot be removed; transfer ownership first.",
    ),
    "NOT_WORKSPACE_OWNER": (403, "not_workspace_owner", "Only the owner can transfer ownership."),
    "OWNERSHIP_TRANSFER_INVALID": (
        422,
        "ownership_transfer_invalid",
        "Ownership cannot be transferred to the current owner.",
    ),
    "OWNERSHIP_TARGET_INACTIVE": (
        422,
        "ownership_target_inactive",
        "Ownership can only be transferred to an active member.",
    ),
}


@dataclass
class InvitationRecord:
    id: UUID
    workspace_id: UUID
    token_digest: str
    role: str
    created_by: UUID
    expires_at: datetime
    revoked_at: datetime | None = None
    accepted_by: UUID | None = None
    accepted_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MemberRecord:
    workspace_id: UUID
    user_id: UUID
    role: str
    status: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class MemberName:
    """A member's id, display name, and role for the online indicator (Task 26)."""

    user_id: UUID
    name: str
    role: str


class InMemoryTeamRepository:
    """In-memory mirror of the seat-safe RPCs for unit tests and local dev.

    A single process lock stands in for the per-workspace advisory lock so the
    seat invariants (no over-allocation, last owner protected) hold under the
    repository's concurrency model.
    """

    def __init__(self) -> None:
        self.invitations: dict[UUID, InvitationRecord] = {}
        self.members: dict[tuple[UUID, UUID], MemberRecord] = {}
        self.workspace_owner: dict[UUID, UUID] = {}
        # user_id -> display name (mirrors public.profiles.full_name).
        self.member_names: dict[UUID, str] = {}
        self._lock = threading.RLock()

    # -- test/bootstrap helpers --------------------------------------------
    def seed_member(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        role: str,
        status: str = "active",
        name: str | None = None,
    ) -> MemberRecord:
        with self._lock:
            record = MemberRecord(
                workspace_id=workspace_id, user_id=user_id, role=role, status=status
            )
            self.members[(workspace_id, user_id)] = record
            if name is not None:
                self.member_names[user_id] = name
            if role == "owner":
                self.workspace_owner[workspace_id] = user_id
            return record

    def list_members_with_names(self, *, workspace_id: UUID) -> list[MemberName]:
        """Active members of a workspace with display names (Task 26).

        Names come from the profile directory (``member_names`` here; the
        ``public.profiles`` table in the Supabase repository). A member without
        a recorded name falls back to a stable, non-PII label so the online
        indicator always has something to show.
        """
        with self._lock:
            members = sorted(
                (
                    m
                    for m in self.members.values()
                    if m.workspace_id == workspace_id and m.status == "active"
                ),
                key=lambda m: m.created_at,
            )
            return [
                MemberName(
                    user_id=m.user_id,
                    name=self.member_names.get(m.user_id) or _fallback_name(m.user_id),
                    role=m.role,
                )
                for m in members
            ]

    def _active_seats(self, workspace_id: UUID) -> int:
        return sum(
            1
            for m in self.members.values()
            if m.workspace_id == workspace_id and m.status == "active"
        )

    def _pending_invites(self, workspace_id: UUID, now: datetime) -> int:
        return sum(
            1
            for inv in self.invitations.values()
            if inv.workspace_id == workspace_id
            and inv.accepted_at is None
            and inv.revoked_at is None
            and inv.expires_at > now
        )

    def _owner_count(self, workspace_id: UUID) -> int:
        return sum(
            1
            for m in self.members.values()
            if m.workspace_id == workspace_id and m.role == "owner"
        )

    # -- mutations ----------------------------------------------------------
    def create_invitation(
        self,
        *,
        workspace_id: UUID,
        token: str,
        role: str,
        created_by: UUID,
        expires_at: datetime,
        seat_limit: int,
    ) -> InvitationRecord:
        if role not in {"admin", "member"}:
            raise _rpc_error("INVITE_ROLE_INVALID")
        with self._lock:
            now = datetime.now(UTC)
            used = self._active_seats(workspace_id) + self._pending_invites(workspace_id, now)
            if used >= seat_limit:
                raise _rpc_error("SEAT_LIMIT_REACHED")
            invite = InvitationRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                token_digest=token_digest(token),
                role=role,
                created_by=created_by,
                expires_at=expires_at,
            )
            self.invitations[invite.id] = invite
            return invite

    def list_invitations(self, *, workspace_id: UUID) -> list[InvitationRecord]:
        return sorted(
            (inv for inv in self.invitations.values() if inv.workspace_id == workspace_id),
            key=lambda inv: inv.created_at,
            reverse=True,
        )

    def accept_invitation(
        self, *, token: str, user_id: UUID, seat_limit: int
    ) -> MemberRecord:
        digest = token_digest(token)
        with self._lock:
            invite = next(
                (inv for inv in self.invitations.values() if inv.token_digest == digest),
                None,
            )
            if invite is None:
                raise _rpc_error("INVITE_NOT_FOUND")
            existing = self.members.get((invite.workspace_id, user_id))
            if invite.accepted_at is not None:
                if invite.accepted_by == user_id and existing is not None:
                    return existing
                raise _rpc_error("INVITE_ALREADY_USED")
            if invite.revoked_at is not None:
                raise _rpc_error("INVITE_REVOKED")
            if invite.expires_at <= datetime.now(UTC):
                raise _rpc_error("INVITE_EXPIRED")

            if existing is not None:
                existing.role = invite.role
                existing.status = "active"
                member = existing
            else:
                if self._active_seats(invite.workspace_id) >= seat_limit:
                    raise _rpc_error("SEAT_LIMIT_REACHED")
                member = MemberRecord(
                    workspace_id=invite.workspace_id,
                    user_id=user_id,
                    role=invite.role,
                    status="active",
                )
                self.members[(invite.workspace_id, user_id)] = member
            invite.accepted_by = user_id
            invite.accepted_at = datetime.now(UTC)
            return member

    def revoke_invitation(
        self, *, workspace_id: UUID, invitation_id: UUID
    ) -> InvitationRecord:
        with self._lock:
            invite = self.invitations.get(invitation_id)
            if invite is None or invite.workspace_id != workspace_id:
                raise _rpc_error("INVITE_NOT_FOUND")
            if invite.accepted_at is not None:
                raise _rpc_error("INVITE_ALREADY_USED")
            if invite.revoked_at is None:
                invite.revoked_at = datetime.now(UTC)
            return invite

    def change_member_role(
        self, *, workspace_id: UUID, user_id: UUID, role: str
    ) -> MemberRecord:
        if role not in {"admin", "member"}:
            raise _rpc_error("ROLE_CHANGE_INVALID")
        with self._lock:
            member = self.members.get((workspace_id, user_id))
            if member is None:
                raise _rpc_error("MEMBER_NOT_FOUND")
            if member.role == "owner" and self._owner_count(workspace_id) <= 1:
                raise _rpc_error("LAST_OWNER_PROTECTED")
            member.role = role
            return member

    def remove_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        with self._lock:
            member = self.members.get((workspace_id, user_id))
            if member is None:
                raise _rpc_error("MEMBER_NOT_FOUND")
            if member.role == "owner" and self._owner_count(workspace_id) <= 1:
                raise _rpc_error("LAST_OWNER_PROTECTED")
            del self.members[(workspace_id, user_id)]

    def transfer_ownership(
        self, *, workspace_id: UUID, from_user_id: UUID, to_user_id: UUID
    ) -> None:
        if from_user_id == to_user_id:
            raise _rpc_error("OWNERSHIP_TRANSFER_INVALID")
        with self._lock:
            owner = self.members.get((workspace_id, from_user_id))
            if owner is None or owner.role != "owner":
                raise _rpc_error("NOT_WORKSPACE_OWNER")
            target = self.members.get((workspace_id, to_user_id))
            if target is None:
                raise _rpc_error("MEMBER_NOT_FOUND")
            if target.status != "active":
                raise _rpc_error("OWNERSHIP_TARGET_INACTIVE")
            target.role = "owner"
            target.status = "active"
            owner.role = "admin"
            self.workspace_owner[workspace_id] = to_user_id

    def leave_workspace(self, *, workspace_id: UUID, user_id: UUID) -> None:
        with self._lock:
            member = self.members.get((workspace_id, user_id))
            if member is None:
                raise _rpc_error("MEMBER_NOT_FOUND")
            if member.role == "owner" and self._owner_count(workspace_id) <= 1:
                raise _rpc_error("LAST_OWNER_PROTECTED")
            del self.members[(workspace_id, user_id)]


def _rpc_error(code: str) -> ApiError:
    status, api_code, message = _RPC_ERRORS[code]
    return ApiError(status, api_code, message)


def _fallback_name(user_id: UUID) -> str:
    """A stable, non-PII label for a member whose profile has no name yet."""
    return f"Member {str(user_id)[:8]}"


class SupabaseTeamRepository(InMemoryTeamRepository):
    """Supabase-backed repository calling the seat-safe SECURITY DEFINER RPCs."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__()
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="team",
            metrics_registry=metrics_registry,
        )

    def create_invitation(
        self,
        *,
        workspace_id: UUID,
        token: str,
        role: str,
        created_by: UUID,
        expires_at: datetime,
        seat_limit: int,
    ) -> InvitationRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_create_workspace_invitation",
            json={
                "p_workspace_id": str(workspace_id),
                "p_token_digest": token_digest(token),
                "p_role": role,
                "p_created_by": str(created_by),
                "p_expires_at": expires_at.isoformat(),
                "p_seat_limit": int(seat_limit),
            },
        )
        self._raise(response)
        return self._invite_from_row(_single(response.json()))

    def list_invitations(self, *, workspace_id: UUID) -> list[InvitationRecord]:
        response = self.client.request(
            "GET",
            "/workspace_invitations",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "select": INVITATION_COLUMNS,
                "order": "created_at.desc",
                "limit": "200",
            },
        )
        self._raise(response)
        return [self._invite_from_row(row) for row in response.json()]

    def accept_invitation(self, *, token: str, user_id: UUID, seat_limit: int) -> MemberRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_accept_workspace_invitation",
            json={
                "p_token_digest": token_digest(token),
                "p_user_id": str(user_id),
                "p_seat_limit": int(seat_limit),
            },
        )
        self._raise(response)
        return self._member_from_row(_single(response.json()))

    def revoke_invitation(
        self, *, workspace_id: UUID, invitation_id: UUID
    ) -> InvitationRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_revoke_workspace_invitation",
            json={
                "p_workspace_id": str(workspace_id),
                "p_invitation_id": str(invitation_id),
            },
        )
        self._raise(response)
        return self._invite_from_row(_single(response.json()))

    def change_member_role(
        self, *, workspace_id: UUID, user_id: UUID, role: str
    ) -> MemberRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_change_member_role",
            json={
                "p_workspace_id": str(workspace_id),
                "p_user_id": str(user_id),
                "p_role": role,
            },
        )
        self._raise(response)
        return self._member_from_row(_single(response.json()))

    def remove_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        response = self.client.request(
            "POST",
            "/rpc/motionly_remove_workspace_member",
            json={"p_workspace_id": str(workspace_id), "p_user_id": str(user_id)},
        )
        self._raise(response)

    def transfer_ownership(
        self, *, workspace_id: UUID, from_user_id: UUID, to_user_id: UUID
    ) -> None:
        response = self.client.request(
            "POST",
            "/rpc/motionly_transfer_workspace_ownership",
            json={
                "p_workspace_id": str(workspace_id),
                "p_from_user_id": str(from_user_id),
                "p_to_user_id": str(to_user_id),
            },
        )
        self._raise(response)

    def leave_workspace(self, *, workspace_id: UUID, user_id: UUID) -> None:
        response = self.client.request(
            "POST",
            "/rpc/motionly_leave_workspace",
            json={"p_workspace_id": str(workspace_id), "p_user_id": str(user_id)},
        )
        self._raise(response)

    def list_members_with_names(self, *, workspace_id: UUID) -> list[MemberName]:
        """Active members joined to their profile name (Task 26).

        Two service-role reads: the workspace's active memberships, then the
        matching profile rows for their display names. Names are the only extra
        field exposed; no email or other PII leaves the API.
        """
        members_response = self.client.request(
            "GET",
            "/workspace_members",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "status": "eq.active",
                "select": "user_id,role,created_at",
                "order": "created_at.asc",
                "limit": "500",
            },
        )
        self._raise(members_response)
        rows = members_response.json()
        if not rows:
            return []
        user_ids = [str(row["user_id"]) for row in rows]
        names: dict[str, str] = {}
        profiles_response = self.client.request(
            "GET",
            "/profiles",
            params={
                "id": f"in.({','.join(user_ids)})",
                "select": "id,full_name",
            },
        )
        self._raise(profiles_response)
        for profile in profiles_response.json():
            full_name = str(profile.get("full_name") or "").strip()
            if full_name:
                names[str(profile["id"])] = full_name
        result: list[MemberName] = []
        for row in rows:
            user_id = UUID(str(row["user_id"]))
            result.append(
                MemberName(
                    user_id=user_id,
                    name=names.get(str(user_id)) or _fallback_name(user_id),
                    role=str(row["role"]),
                )
            )
        return result

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        message = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                message = str(body.get("message") or "")
        except ValueError:
            message = response.text or ""
        for marker, (status, code, text) in _RPC_ERRORS.items():
            if marker in message:
                raise ApiError(status, code, text)
        raise ApiError(502, "team_request_failed", "The team service could not reach data.")

    @staticmethod
    def _invite_from_row(row: dict[str, Any]) -> InvitationRecord:
        return InvitationRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            token_digest=str(row["token_digest"]),
            role=str(row["role"]),
            created_by=UUID(str(row["created_by"])),
            expires_at=_datetime(row["expires_at"]),
            revoked_at=_datetime(row["revoked_at"]) if row.get("revoked_at") else None,
            accepted_by=UUID(str(row["accepted_by"])) if row.get("accepted_by") else None,
            accepted_at=_datetime(row["accepted_at"]) if row.get("accepted_at") else None,
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _member_from_row(row: dict[str, Any]) -> MemberRecord:
        return MemberRecord(
            workspace_id=UUID(str(row["workspace_id"])),
            user_id=UUID(str(row["user_id"])),
            role=str(row["role"]),
            status=str(row["status"]),
            created_at=_datetime(row["created_at"]),
        )


def _single(payload: Any) -> dict[str, Any]:
    """A SECURITY DEFINER RPC returning a composite type yields one row/object."""
    if isinstance(payload, list):
        if not payload:
            raise ApiError(502, "team_request_failed", "The team service returned no data.")
        return payload[0]
    if isinstance(payload, dict):
        return payload
    raise ApiError(502, "team_request_failed", "The team service returned invalid data.")
