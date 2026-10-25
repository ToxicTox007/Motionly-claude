import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from app.api.errors import ApiError
from app.services import membership_cache

# The three workspace_role enum values, most-privileged first.
WORKSPACE_ROLES: tuple[str, ...] = ("owner", "admin", "member")


@dataclass(frozen=True)
class VerifiedToken:
    user_id: UUID
    claims: dict[str, object]


def verify_supabase_jwt(
    *,
    token: str,
    jwt_secret: str,
    audience: str = "authenticated",
) -> VerifiedToken:
    parts = token.split(".")
    if len(parts) != 3:
        raise ApiError(401, "auth_token_invalid", "Authentication token is invalid.")

    header = _decode_json(parts[0])
    if header.get("alg") != "HS256":
        raise ApiError(401, "auth_token_invalid", "Authentication token algorithm is unsupported.")

    expected = hmac.new(
        jwt_secret.encode("utf-8"),
        f"{parts[0]}.{parts[1]}".encode(),
        hashlib.sha256,
    ).digest()
    provided = _decode_base64url(parts[2])
    if not hmac.compare_digest(expected, provided):
        raise ApiError(401, "auth_token_invalid", "Authentication token signature is invalid.")

    claims = _decode_json(parts[1])
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp <= int(time.time()):
        raise ApiError(401, "auth_token_expired", "Authentication token has expired.")

    aud = claims.get("aud")
    if audience and aud != audience:
        raise ApiError(401, "auth_token_invalid", "Authentication token audience is invalid.")

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise ApiError(401, "auth_token_invalid", "Authentication token subject is missing.")

    try:
        user_id = UUID(sub)
    except ValueError as exc:
        raise ApiError(
            401,
            "auth_token_invalid",
            "Authentication token subject is invalid.",
        ) from exc

    return VerifiedToken(user_id=user_id, claims=claims)


def verify_supabase_token_with_auth_api(
    *,
    token: str,
    supabase_url: str,
    service_role_key: str,
) -> VerifiedToken:
    response = httpx.get(
        f"{supabase_url.rstrip('/')}/auth/v1/user",
        headers={
            "apikey": service_role_key,
            "authorization": f"Bearer {token}",
        },
        timeout=10,
    )
    if response.status_code in {401, 403}:
        raise ApiError(401, "auth_token_invalid", "Authentication token is invalid.")
    if response.status_code >= 400:
        raise ApiError(502, "auth_verification_failed", "Authentication could not be verified.")

    payload = response.json()
    user_id_value = payload.get("id")
    if not isinstance(user_id_value, str):
        raise ApiError(401, "auth_token_invalid", "Authentication token subject is missing.")
    try:
        user_id = UUID(user_id_value)
    except ValueError as exc:
        raise ApiError(
            401,
            "auth_token_invalid",
            "Authentication token subject is invalid.",
        ) from exc
    return VerifiedToken(user_id=user_id, claims=payload)


def require_workspace_membership(
    *,
    supabase_url: str,
    service_role_key: str,
    user_id: UUID,
    workspace_id: UUID,
    metrics_registry: Any | None = None,
) -> str:
    """Verify active workspace membership and return the caller's role.

    Returns the ``workspace_role`` (``owner`` / ``admin`` / ``member``). Raises
    403 ``workspace_access_denied`` for a non-member. The resolved role is
    cached (Redis, or in-memory when ``REDIS_URL`` is unset) for a short TTL;
    ``membership_cache.invalidate_membership`` clears it when membership changes.
    """
    cached = membership_cache.cached_role(user_id, workspace_id)
    if cached is not None:
        if metrics_registry is not None:
            metrics_registry.observe("auth_membership_cache_count", 1, result="hit")
        if cached == membership_cache._NEGATIVE:
            raise ApiError(
                403, "workspace_access_denied", "You do not have access to this workspace."
            )
        return cached
    if metrics_registry is not None:
        metrics_registry.observe("auth_membership_cache_count", 1, result="miss")

    # motionly_workspace_member_role (migration 202610230201) returns the
    # active member's role, or no row for a non-member, an inactive member, or
    # a user GoTrue has banned or deleted: a token issued before a ban stays
    # valid until it expires, so the ban is enforced here too.
    response = httpx.get(
        f"{supabase_url.rstrip('/')}/rest/v1/rpc/motionly_workspace_member_role",
        headers={
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
        },
        params={
            "p_workspace_id": str(workspace_id),
            "p_user_id": str(user_id),
        },
        timeout=10,
    )
    if response.status_code >= 400:
        raise ApiError(
            502,
            "auth_membership_check_failed",
            "Workspace access could not be verified.",
        )
    rows = response.json()
    if not rows:
        membership_cache.store_negative(user_id, workspace_id)
        raise ApiError(403, "workspace_access_denied", "You do not have access to this workspace.")
    role = rows[0].get("role")
    if role not in WORKSPACE_ROLES:
        # A row with no / unexpected role still proves active membership; treat
        # it as the least-privileged role rather than failing closed on access.
        role = "member"
    membership_cache.store_role(user_id, workspace_id, role)
    return role


def clear_membership_cache_for_test() -> None:
    membership_cache.clear_membership_cache_for_test()


def _decode_json(value: str) -> dict[str, object]:
    try:
        decoded = _decode_base64url(value).decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ApiError(401, "auth_token_invalid", "Authentication token is malformed.") from exc
    if not isinstance(payload, dict):
        raise ApiError(401, "auth_token_invalid", "Authentication token is malformed.")
    return payload


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(f"{value}{padding}")
    except ValueError as exc:
        raise ApiError(401, "auth_token_invalid", "Authentication token is malformed.") from exc
