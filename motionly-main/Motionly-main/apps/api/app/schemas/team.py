"""Schemas for team management: invite links, seats, and membership (Task 20).

Owners and admins mint invite links bounded by the plan seat limit; members
accept, leave, change roles, and transfer ownership. The raw invite token is
returned exactly once at creation (inside the invite URL fragment) and is never
persisted or echoed again.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InvitableRole(StrEnum):
    """Roles an invite may grant. Owners are made only via transfer, never invite."""

    ADMIN = "admin"
    MEMBER = "member"


class AssignableRole(StrEnum):
    """Roles a member's role can be changed to (owner is reserved for transfer)."""

    ADMIN = "admin"
    MEMBER = "member"


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: InvitableRole = InvitableRole.MEMBER
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationResponse(BaseModel):
    id: UUID
    role: InvitableRole
    created_by: UUID
    expires_at: datetime
    revoked_at: datetime | None = None
    accepted_by: UUID | None = None
    accepted_at: datetime | None = None
    created_at: datetime


class InvitationCreateResponse(BaseModel):
    invitation: InvitationResponse
    # Returned once; the client builds/sends the invite URL and we never store
    # the raw token again.
    url: str
    token: str


class InvitationListResponse(BaseModel):
    invitations: list[InvitationResponse]


class InvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)


class MemberResponse(BaseModel):
    user_id: UUID
    role: str
    status: str
    created_at: datetime


class AcceptInvitationResponse(BaseModel):
    workspace_id: UUID
    member: MemberResponse


class WorkspaceMemberName(BaseModel):
    """A member's identity for the live-collaboration online indicator (Task 26).

    Presence over Realtime carries only the user id; the browser maps those ids
    to names through this workspace-scoped directory. No email or other PII is
    exposed — just the id, a display name, and the role.
    """

    user_id: UUID
    name: str
    role: str


class WorkspaceMemberNamesResponse(BaseModel):
    members: list[WorkspaceMemberName]


class ChangeRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AssignableRole


class TransferOwnershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
