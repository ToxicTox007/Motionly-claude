import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from uuid import UUID

import httpx
import os

from app.api.errors import ApiError


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
    # Local bypass: accept a fixed mock token when SKIP_API_AUTH is enabled
    if os.getenv("SKIP_API_AUTH") == "true" and token == "mock-local-bearer":
        return VerifiedToken(user_id=UUID("00000000-0000-4000-8000-000000000001"), claims={})
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
) -> None:
    response = httpx.get(
        f"{supabase_url.rstrip('/')}/rest/v1/workspace_members",
        headers={
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
        },
        params={
            "workspace_id": f"eq.{workspace_id}",
            "user_id": f"eq.{user_id}",
            "status": "eq.active",
            "select": "workspace_id",
            "limit": "1",
        },
        timeout=10,
    )
    if response.status_code >= 400:
        raise ApiError(
            502,
            "auth_membership_check_failed",
            "Workspace access could not be verified.",
        )
    if not response.json():
        raise ApiError(403, "workspace_access_denied", "You do not have access to this workspace.")


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
