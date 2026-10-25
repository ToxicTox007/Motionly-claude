"""Team management endpoints: invite links, seats, and membership (Task 20).

Owners and admins mint invite links bounded by the plan's seat limit; the raw
invite token is returned exactly once inside the invite URL fragment
(``<PUBLIC_ORIGIN>/invite#token=...``) and never persisted or logged. Accepting
and leaving are self-service for the signed-in user. Ownership transfer is
owner-only. Every membership change invalidates the Task 19 role cache.

Role enforcement (proven by tests/test_role_route_inventory.py):
- create / list / revoke invites, change role, remove member: owner or admin.
- transfer ownership: owner only.
- accept invite, leave: any active member (self-service).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    Actor,
    get_actor,
    get_billing_repository,
    require_role,
)
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.team import (
    InMemoryTeamRepository,
    InvitationRecord,
    MemberName,
    MemberRecord,
)
from app.schemas.team import (
    AcceptInvitationResponse,
    ChangeRoleRequest,
    InvitationAcceptRequest,
    InvitationCreateRequest,
    InvitationCreateResponse,
    InvitationListResponse,
    InvitationResponse,
    MemberResponse,
    TransferOwnershipRequest,
    WorkspaceMemberName,
    WorkspaceMemberNamesResponse,
)
from app.services.membership_cache import invalidate_membership, invalidate_workspace
from app.services.offload import run_blocking
from app.services.plan_limits import plan_seat_limit

router = APIRouter(tags=["team"])

ActorDep = Annotated[Actor, Depends(get_actor)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]

# Owner/admin may manage invites and membership.
OwnerAdminDep = Depends(require_role("owner", "admin"))
# Ownership transfer is owner-only.
OwnerDep = Depends(require_role("owner"))

INVITE_PATH = "/invite"


def _get_team_repository(request: Request) -> InMemoryTeamRepository:
    return request.app.state.team_repository


TeamRepositoryDep = Annotated[InMemoryTeamRepository, Depends(_get_team_repository)]


async def _call(repository: Any, name: str, **kwargs: Any) -> Any:
    return await run_blocking(getattr(repository, name), **kwargs)


async def _seat_limit(billing_repository: Any, workspace_id: UUID) -> int:
    """Resolve the workspace's seat limit from the single plan source (Task 23)."""
    get_async = getattr(billing_repository, "get_subscription_async", None)
    if get_async is not None:
        subscription = await get_async(workspace_id=workspace_id)
    else:
        subscription = await run_blocking(
            billing_repository.get_subscription, workspace_id=workspace_id
        )
    # An inactive (or past-due beyond its grace period) subscription counts as Free.
    from app.services.billing import effective_plan_key

    effective = effective_plan_key(subscription)
    return plan_seat_limit(effective)


def _invitation_response(record: InvitationRecord) -> InvitationResponse:
    return InvitationResponse(
        id=record.id,
        role=record.role,
        created_by=record.created_by,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
        accepted_by=record.accepted_by,
        accepted_at=record.accepted_at,
        created_at=record.created_at,
    )


def _member_response(record: MemberRecord) -> MemberResponse:
    return MemberResponse(
        user_id=record.user_id,
        role=record.role,
        status=record.status,
        created_at=record.created_at,
    )


def _invite_url(request: Request, token: str) -> str:
    settings = request.app.state.settings
    origin = getattr(settings, "public_origin", None)
    base = str(origin).rstrip("/") if origin else ""
    # The token travels in the URL fragment so it never reaches server logs.
    return f"{base}{INVITE_PATH}#token={token}"


def _member_name_response(record: MemberName) -> WorkspaceMemberName:
    return WorkspaceMemberName(
        user_id=record.user_id,
        name=record.name,
        role=record.role,
    )


@router.get("/team/members", response_model=WorkspaceMemberNamesResponse)
async def list_workspace_member_names(
    actor: ActorDep,
    repository: TeamRepositoryDep,
) -> WorkspaceMemberNamesResponse:
    """The acting workspace's members, with display names (Task 26).

    Powers the live-collaboration online indicator: Realtime presence carries
    only user ids, so the browser maps those ids to names through this
    workspace-scoped endpoint. ``get_actor`` already requires an active
    membership of the workspace, so a non-member can never reach it — the names
    of one workspace never leak to another. Only ids, names, and roles are
    returned (no email or other PII).
    """
    members = await _call(
        repository, "list_members_with_names", workspace_id=actor.workspace_id
    )
    return WorkspaceMemberNamesResponse(
        members=[_member_name_response(member) for member in members]
    )


@router.post(
    "/team/invitations",
    response_model=InvitationCreateResponse,
    status_code=201,
    dependencies=[OwnerAdminDep],
)
async def create_invitation(
    payload: InvitationCreateRequest,
    request: Request,
    actor: ActorDep,
    repository: TeamRepositoryDep,
    billing_repository: BillingRepositoryDep,
) -> InvitationCreateResponse:
    seat_limit = await _seat_limit(billing_repository, actor.workspace_id)
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=int(payload.expires_in_days))
    invite = await _call(
        repository,
        "create_invitation",
        workspace_id=actor.workspace_id,
        token=token,
        role=payload.role.value,
        created_by=actor.user_id,
        expires_at=expires_at,
        seat_limit=seat_limit,
    )
    return InvitationCreateResponse(
        invitation=_invitation_response(invite),
        url=_invite_url(request, token),
        token=token,
    )


@router.get(
    "/team/invitations",
    response_model=InvitationListResponse,
    dependencies=[OwnerAdminDep],
)
async def list_invitations(
    actor: ActorDep,
    repository: TeamRepositoryDep,
) -> InvitationListResponse:
    invites = await _call(repository, "list_invitations", workspace_id=actor.workspace_id)
    return InvitationListResponse(
        invitations=[_invitation_response(invite) for invite in invites]
    )


@router.post(
    "/team/invitations/{invitation_id}/revoke",
    response_model=InvitationResponse,
    dependencies=[OwnerAdminDep],
)
async def revoke_invitation(
    invitation_id: UUID,
    actor: ActorDep,
    repository: TeamRepositoryDep,
) -> InvitationResponse:
    invite = await _call(
        repository,
        "revoke_invitation",
        workspace_id=actor.workspace_id,
        invitation_id=invitation_id,
    )
    return _invitation_response(invite)


@router.post("/team/invitations/accept", response_model=AcceptInvitationResponse)
async def accept_invitation(
    payload: InvitationAcceptRequest,
    actor: ActorDep,
    repository: TeamRepositoryDep,
    billing_repository: BillingRepositoryDep,
) -> AcceptInvitationResponse:
    # The invite carries the target workspace; the seat limit is re-checked in
    # the RPC under the per-workspace lock. We still pass the acting workspace's
    # limit for the in-memory path; the Supabase RPC ignores a mismatched
    # workspace because it resolves the invite's own workspace.
    seat_limit = await _seat_limit(billing_repository, actor.workspace_id)
    member = await _call(
        repository,
        "accept_invitation",
        token=payload.token,
        user_id=actor.user_id,
        seat_limit=seat_limit,
    )
    invalidate_membership(member.user_id, member.workspace_id)
    return AcceptInvitationResponse(
        workspace_id=member.workspace_id,
        member=_member_response(member),
    )


@router.patch(
    "/team/members/{user_id}/role",
    response_model=MemberResponse,
    dependencies=[OwnerAdminDep],
)
async def change_member_role(
    user_id: UUID,
    payload: ChangeRoleRequest,
    actor: ActorDep,
    repository: TeamRepositoryDep,
) -> MemberResponse:
    member = await _call(
        repository,
        "change_member_role",
        workspace_id=actor.workspace_id,
        user_id=user_id,
        role=payload.role.value,
    )
    invalidate_membership(user_id, actor.workspace_id)
    return _member_response(member)


@router.delete(
    "/team/members/{user_id}",
    status_code=204,
    dependencies=[OwnerAdminDep],
)
async def remove_member(
    user_id: UUID,
    actor: ActorDep,
    repository: TeamRepositoryDep,
) -> None:
    await _call(
        repository,
        "remove_member",
        workspace_id=actor.workspace_id,
        user_id=user_id,
    )
    invalidate_membership(user_id, actor.workspace_id)


@router.post(
    "/team/transfer-ownership",
    status_code=204,
    dependencies=[OwnerDep],
)
async def transfer_ownership(
    payload: TransferOwnershipRequest,
    actor: ActorDep,
    repository: TeamRepositoryDep,
) -> None:
    if payload.user_id == actor.user_id:
        raise ApiError(
            422,
            "ownership_transfer_invalid",
            "Ownership cannot be transferred to the current owner.",
        )
    await _call(
        repository,
        "transfer_ownership",
        workspace_id=actor.workspace_id,
        from_user_id=actor.user_id,
        to_user_id=payload.user_id,
    )
    # Both members' roles changed: drop every cached role for the workspace.
    invalidate_workspace(actor.workspace_id)


@router.post("/team/leave", status_code=204)
async def leave_workspace(
    actor: ActorDep,
    repository: TeamRepositoryDep,
) -> None:
    await _call(
        repository,
        "leave_workspace",
        workspace_id=actor.workspace_id,
        user_id=actor.user_id,
    )
    invalidate_membership(actor.user_id, actor.workspace_id)
