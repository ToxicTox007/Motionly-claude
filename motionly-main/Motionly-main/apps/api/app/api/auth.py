"""Accounts without email (Task 11).

``POST /auth/signup`` creates an already-confirmed user through Auth's admin
API. Auth's own sign-up endpoint is disabled (GOTRUE_DISABLE_SIGNUP), so this
rate-limited route is the only way to create an account.
"""

from __future__ import annotations

import hashlib
import ipaddress

from fastapi import APIRouter, Request

from app.api.errors import ApiError
from app.core.logging import get_logger
from app.schemas.auth import (
    PasswordResetRequestRequest,
    PasswordResetRequestResponse,
    SignupRequest,
    SignupResponse,
)
from app.services.admin_user_directory import ACTION_RESET_REQUESTED, AdminUserDirectory
from app.services.auth_admin import AuthAdminClient, AuthAdminError
from app.services.signup_policy import (
    PASSWORD_PROBLEM_MESSAGES,
    normalize_email,
    normalize_full_name,
    password_problem,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Set by the ingress from CloudFront-Viewer-Address after the origin guard
# (infra/Caddyfile.origin-guard), and passed on by the web server.
VIEWER_IP_HEADER = "x-motionly-viewer-ip"
SIGNUP_WINDOW_SECONDS = 3600


def viewer_address(request: Request) -> str:
    """The viewer's address, or the connecting peer's when none was set."""
    value = request.headers.get(VIEWER_IP_HEADER, "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return request.client.host if request.client else "unknown"


def viewer_hash(address: str) -> str:
    return hashlib.sha256(address.encode("utf-8")).hexdigest()[:12]


async def enforce_hourly_limit(
    request: Request, *, action: str, per_viewer: int, overall: int
) -> None:
    """Refuse once a viewer, or all viewers together, used up the hour's quota.

    Every attempt counts, including refused ones, so the limit also bounds
    probing (for taken emails, or weak passwords).
    """
    limiter = request.app.state.rate_limiter
    address = viewer_address(request)
    allowed, _ = await limiter.allow(
        key=f"{action}:viewer:{address}", limit=per_viewer, window_seconds=SIGNUP_WINDOW_SECONDS
    )
    if allowed:
        allowed, _ = await limiter.allow(
            key=f"{action}:all", limit=overall, window_seconds=SIGNUP_WINDOW_SECONDS
        )
    if not allowed:
        logger.warning(
            f"{action}_rate_limited",
            extra={"viewer_hash": viewer_hash(address), "request_id": request.state.request_id},
        )
        raise ApiError(
            429,
            f"{action}_rate_limited",
            "Too many attempts from your network. Wait an hour, then try again.",
        )


@router.post("/signup", response_model=SignupResponse, status_code=201)
async def signup(payload: SignupRequest, request: Request) -> SignupResponse:
    settings = request.app.state.settings
    auth_admin: AuthAdminClient | None = getattr(request.app.state, "auth_admin", None)
    if auth_admin is None:
        raise ApiError(503, "signup_unavailable", "Sign-up is not available right now.")

    await enforce_hourly_limit(
        request,
        action="signup",
        per_viewer=settings.signup_rate_limit_per_hour,
        overall=settings.signup_global_rate_limit_per_hour,
    )

    full_name = normalize_full_name(payload.full_name)
    if full_name is None:
        raise ApiError(422, "name_required", "Enter your name (up to 120 characters).")
    email = normalize_email(payload.email)
    if email is None:
        raise ApiError(422, "email_invalid", "Enter a valid email address.")
    problem = password_problem(payload.password, email=email)
    if problem is not None:
        raise ApiError(422, "password_weak", PASSWORD_PROBLEM_MESSAGES[problem])

    try:
        user = await auth_admin.create_confirmed_user(
            email=email, password=payload.password, full_name=full_name
        )
    except AuthAdminError as exc:
        if exc.code == "email_exists":
            raise ApiError(
                409,
                "email_taken",
                "An account with this email already exists. Log in instead.",
            ) from exc
        if exc.code == "weak_password":
            raise ApiError(
                422, "password_weak", PASSWORD_PROBLEM_MESSAGES["rejected_by_auth"]
            ) from exc
        if exc.code == "email_invalid":
            raise ApiError(422, "email_invalid", "Enter a valid email address.") from exc
        raise ApiError(502, "signup_unavailable", "Sign-up is not available right now.") from exc

    logger.info(
        "account_signup_created",
        extra={"user_id": user.id, "request_id": request.state.request_id},
    )
    return SignupResponse(user_id=user.id, email=user.email)


@router.post(
    "/password-reset-requests",
    response_model=PasswordResetRequestResponse,
    status_code=202,
)
async def request_password_reset(
    payload: PasswordResetRequestRequest, request: Request
) -> PasswordResetRequestResponse:
    """Record a "forgot my password" request without revealing anything.

    The answer is always the same 202 whether or not the email has an account,
    and the account lookup runs either way so the timing matches. An admin
    later issues a one-time recovery link from the Users page.
    """
    settings = request.app.state.settings

    await enforce_hourly_limit(
        request,
        action="password_reset_request",
        per_viewer=settings.password_reset_request_rate_limit_per_hour,
        overall=settings.password_reset_request_global_rate_limit_per_hour,
    )

    directory: AdminUserDirectory | None = getattr(request.app.state, "admin_user_directory", None)
    auth_admin: AuthAdminClient | None = getattr(request.app.state, "auth_admin", None)

    email = normalize_email(payload.email)
    # An invalid email is still accepted with the same 202: the form never
    # tells a visitor whether their address is one we would recognise.
    if email is not None and directory is not None:
        user_id: str | None = None
        if auth_admin is not None:
            try:
                found = await auth_admin.find_user_by_email(email=email)
                user_id = found.id if found else None
            except AuthAdminError:
                # A lookup failure must not change the answer or leak timing.
                user_id = None
        try:
            await directory.record_reset_request(
                email=email, user_id=user_id, viewer_hash=viewer_hash(viewer_address(request))
            )
            await directory.record_audit_event(
                actor_user_id="system",
                action=ACTION_RESET_REQUESTED,
                target_user_id=user_id,
                details={"account_exists": user_id is not None},
            )
        except Exception:  # noqa: BLE001 - recording must never 500 the visitor
            logger.warning(
                "password_reset_request_not_recorded",
                extra={"request_id": request.state.request_id},
            )

    return PasswordResetRequestResponse()
