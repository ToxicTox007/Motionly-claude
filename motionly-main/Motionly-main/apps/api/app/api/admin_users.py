"""Admin user administration and password-reset help (Task 12).

Every route is behind :func:`app.api.admin.require_admin`. An admin can search
users by email, see pending reset requests, issue a one-time recovery link,
ban/unban a user, and dismiss a request. Each state-changing action writes an
append-only audit event (actor, action, target, redacted details).

Reset links point at the public ``/auth/confirm`` page and carry the GoTrue
recovery token in the URL *fragment* (``#token_hash=...&type=recovery``). The
fragment never leaves the browser, so the token stays out of proxy and server
access logs; the web app reads it client-side and verifies it server-side
through the Supabase client. Links expire after one hour
(GOTRUE_MAILER_OTP_EXP), and issuing a new link invalidates the previous one.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.admin import require_admin
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.core.logging import get_logger
from app.schemas.auth import (
    AdminBanRequest,
    AdminBanResponse,
    AdminCreateResetLinkRequest,
    AdminDismissRequestRequest,
    AdminResetLinkResponse,
    AdminResetRequestListResponse,
    AdminResetRequestRecord,
    AdminUserRecord,
    AdminUserSearchResponse,
)
from app.services import membership_cache
from app.services.admin_user_directory import (
    ACTION_LINK_CREATED,
    ACTION_REQUEST_DISMISSED,
    ACTION_USER_BANNED,
    ACTION_USER_UNBANNED,
    AdminUserDirectory,
)
from app.services.auth_admin import AuthAdminClient, AuthAdminError, AuthUserRecord
from app.services.offload import run_blocking
from app.services.signup_policy import normalize_email

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/users", tags=["admin"])

AdminActorDep = Annotated[Actor, Depends(require_admin)]

# Reset links live for one hour (GOTRUE_MAILER_OTP_EXP=3600).
RESET_LINK_EXPIRES_IN_SECONDS = 3600


def _auth_admin(request: Request) -> AuthAdminClient:
    client: AuthAdminClient | None = getattr(request.app.state, "auth_admin", None)
    if client is None:
        raise ApiError(503, "user_admin_unavailable", "User administration is not available.")
    return client


def _directory(request: Request) -> AdminUserDirectory:
    directory: AdminUserDirectory | None = getattr(
        request.app.state, "admin_user_directory", None
    )
    if directory is None:
        raise ApiError(503, "user_admin_unavailable", "User administration is not available.")
    return directory


def _public_origin(request: Request) -> str:
    settings = request.app.state.settings
    origin = getattr(settings, "public_origin", None)
    if not origin:
        raise ApiError(503, "reset_link_unavailable", "Reset links are not configured.")
    return str(origin).rstrip("/")


def _to_record(user: AuthUserRecord) -> AdminUserRecord:
    return AdminUserRecord(
        id=user.id,
        email=user.email,
        banned=bool(user.banned_until),
        banned_until=user.banned_until,
        last_sign_in_at=user.last_sign_in_at,
        created_at=user.created_at,
    )


@router.get("", response_model=AdminUserSearchResponse)
async def search_users(
    request: Request,
    _actor: AdminActorDep,
    email: str = "",
) -> AdminUserSearchResponse:
    query = email.strip()
    if not query:
        return AdminUserSearchResponse(users=[])
    client = _auth_admin(request)
    try:
        users = await client.list_users_by_email(email=query)
    except AuthAdminError as exc:
        raise ApiError(502, "user_search_failed", "Could not search users right now.") from exc
    return AdminUserSearchResponse(users=[_to_record(user) for user in users])


@router.get("/reset-requests", response_model=AdminResetRequestListResponse)
async def list_reset_requests(
    request: Request,
    _actor: AdminActorDep,
) -> AdminResetRequestListResponse:
    directory = _directory(request)
    pending = await directory.list_pending_requests()
    return AdminResetRequestListResponse(
        requests=[
            AdminResetRequestRecord(
                id=item.id, email=item.email, status=item.status, created_at=item.created_at
            )
            for item in pending
        ]
    )


@router.post("/reset-links", response_model=AdminResetLinkResponse)
async def create_reset_link(
    payload: AdminCreateResetLinkRequest,
    request: Request,
    actor: AdminActorDep,
) -> AdminResetLinkResponse:
    email = normalize_email(payload.email)
    if email is None:
        raise ApiError(422, "email_invalid", "Enter a valid email address.")
    client = _auth_admin(request)
    directory = _directory(request)
    origin = _public_origin(request)
    try:
        recovery = await client.generate_recovery_link(email=email)
    except AuthAdminError as exc:
        if exc.code == "user_not_found":
            raise ApiError(404, "user_not_found", "No account uses that email.") from exc
        raise ApiError(502, "reset_link_failed", "Could not create a reset link.") from exc

    # The token rides in the URL fragment so it never reaches access logs.
    link = f"{origin}/auth/confirm#token_hash={recovery.hashed_token}&type=recovery"

    target = await _resolve_user_id(client, email)
    if payload.request_id is not None:
        await directory.mark_request_link_issued(
            request_id=str(payload.request_id), handled_by=str(actor.user_id)
        )
    await directory.record_audit_event(
        actor_user_id=str(actor.user_id),
        action=ACTION_LINK_CREATED,
        target_user_id=target,
        # Email only; the token and link are never recorded.
        details={
            "email": email,
            "request_id": str(payload.request_id) if payload.request_id else None,
        },
    )
    return AdminResetLinkResponse(
        link=link, expires_in_seconds=RESET_LINK_EXPIRES_IN_SECONDS
    )


@router.post("/ban", response_model=AdminBanResponse)
async def ban_user(
    payload: AdminBanRequest,
    request: Request,
    actor: AdminActorDep,
) -> AdminBanResponse:
    return await _set_ban(request, actor, user_id=str(payload.user_id), banned=True)


@router.post("/unban", response_model=AdminBanResponse)
async def unban_user(
    payload: AdminBanRequest,
    request: Request,
    actor: AdminActorDep,
) -> AdminBanResponse:
    return await _set_ban(request, actor, user_id=str(payload.user_id), banned=False)


@router.post("/reset-requests/dismiss", status_code=204)
async def dismiss_reset_request(
    payload: AdminDismissRequestRequest,
    request: Request,
    actor: AdminActorDep,
) -> None:
    directory = _directory(request)
    dismissed = await directory.dismiss_request(
        request_id=str(payload.request_id), handled_by=str(actor.user_id)
    )
    if not dismissed:
        raise ApiError(404, "reset_request_not_found", "That request is no longer pending.")
    await directory.record_audit_event(
        actor_user_id=str(actor.user_id),
        action=ACTION_REQUEST_DISMISSED,
        details={"request_id": str(payload.request_id)},
    )


async def _set_ban(
    request: Request, actor: Actor, *, user_id: str, banned: bool
) -> AdminBanResponse:
    client = _auth_admin(request)
    directory = _directory(request)
    try:
        record = await client.set_user_banned(user_id=user_id, banned=banned)
    except AuthAdminError as exc:
        if exc.code == "user_not_found":
            raise ApiError(404, "user_not_found", "No such user.") from exc
        raise ApiError(502, "ban_failed", "Could not update that user.") from exc
    # The membership check refuses banned users (motionly_workspace_member_role),
    # but a role cached before the ban would keep admitting them for up to the
    # cache TTL. Drop the user's cached roles in every workspace now.
    try:
        await run_blocking(membership_cache.invalidate_user, UUID(user_id))
    except Exception:  # noqa: BLE001 - the ban itself succeeded; the TTL bounds staleness
        logger.warning("membership_cache_invalidation_failed", extra={"user_id": user_id})
    await directory.record_audit_event(
        actor_user_id=str(actor.user_id),
        action=ACTION_USER_BANNED if banned else ACTION_USER_UNBANNED,
        target_user_id=user_id,
    )
    return AdminBanResponse(user_id=record.id or user_id, banned=bool(record.banned_until))


async def _resolve_user_id(client: AuthAdminClient, email: str) -> str | None:
    try:
        found = await client.find_user_by_email(email=email)
    except AuthAdminError:
        return None
    return found.id if found else None
