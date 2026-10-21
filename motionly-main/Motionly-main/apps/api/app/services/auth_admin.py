"""Auth's admin API (GoTrue), called with the service-role key.

Public sign-up is off (GOTRUE_DISABLE_SIGNUP); accounts are created here,
already confirmed, so nothing waits for an email.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


class AuthAdminError(Exception):
    """Auth refused or failed a request. ``code`` is stable for callers."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str


@dataclass(frozen=True)
class AuthUserRecord:
    """A user as the admin list and ban endpoints return it."""

    id: str
    email: str
    banned_until: str | None = None
    last_sign_in_at: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class RecoveryLink:
    """A one-time GoTrue recovery token (never the full action_link).

    Only the hashed token is kept: the API builds its own link to the public
    ``/auth/confirm`` page so the token never passes through GoTrue's own
    redirect host, and server logs record nothing token-shaped.
    """

    hashed_token: str
    verification_type: str


class AuthAdminClient:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        anon_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = f"{supabase_url.rstrip('/')}/auth/v1"
        self._service_role_key = service_role_key
        # Recovery tokens are verified with the anon key, like the browser does;
        # the service-role key defaults in only so tests may omit it.
        self._anon_key = anon_key or service_role_key
        self._transport = transport
        self._timeout = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key,
            "authorization": f"Bearer {self._service_role_key}",
        }

    def _anon_headers(self) -> dict[str, str]:
        return {"apikey": self._anon_key}

    async def _request(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                return await client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=headers or self._headers(),
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            logger.warning("auth_admin_unreachable", extra={"error_type": type(exc).__name__})
            raise AuthAdminError("auth_unavailable") from exc

    async def create_confirmed_user(self, *, email: str, password: str, full_name: str) -> AuthUser:
        response = await self._request(
            "POST",
            "/admin/users",
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name},
            },
        )
        if response.status_code < 300:
            payload = response.json()
            return AuthUser(id=str(payload["id"]), email=str(payload.get("email") or email))
        raise _error_from(response)

    async def generate_recovery_link(self, *, email: str) -> RecoveryLink:
        """A one-time recovery token for ``email``; raises ``user_not_found``.

        GoTrue invalidates any earlier recovery link for the same user when it
        issues a new one, so each link hands out is the only live one.
        """
        response = await self._request(
            "POST",
            "/admin/generate_link",
            json={"type": "recovery", "email": email},
        )
        if response.status_code < 300:
            payload = response.json()
            hashed_token = str(payload.get("hashed_token") or "")
            if not hashed_token:
                raise AuthAdminError("auth_unavailable", status_code=response.status_code)
            return RecoveryLink(
                hashed_token=hashed_token,
                verification_type=str(payload.get("verification_type") or "recovery"),
            )
        raise _error_from(response)

    async def find_user_by_email(self, *, email: str) -> AuthUserRecord | None:
        """The one user whose email matches exactly, or None."""
        for user in await self.list_users_by_email(email=email):
            if user.email.lower() == email.lower():
                return user
        return None

    async def list_users_by_email(
        self, *, email: str, per_page: int = 20
    ) -> list[AuthUserRecord]:
        """Users whose email contains ``email`` (GoTrue's substring filter)."""
        response = await self._request(
            "GET",
            "/admin/users",
            params={"filter": email, "page": 1, "per_page": per_page},
        )
        if response.status_code < 300:
            payload = response.json()
            users = payload.get("users") if isinstance(payload, dict) else None
            return [_user_record(item) for item in (users or []) if isinstance(item, dict)]
        raise _error_from(response)

    async def set_user_banned(self, *, user_id: str, banned: bool) -> AuthUserRecord:
        """Ban (effectively forever) or unban a user."""
        response = await self._request(
            "PUT",
            f"/admin/users/{user_id}",
            json={"ban_duration": "876000h" if banned else "none"},
        )
        if response.status_code < 300:
            return _user_record(response.json())
        raise _error_from(response)

    async def verify_recovery_token(self, *, token_hash: str) -> dict[str, Any]:
        """Exchange a recovery token for a session (browser-equivalent call).

        The web app verifies the token itself through the Supabase client so
        the recovery session cookie is set; this method exists so the API can
        exercise and document the exact call. A reused/expired token is
        ``otp_expired`` (403); an unknown one is ``otp_not_found`` (404).
        """
        response = await self._request(
            "POST",
            "/verify",
            headers=self._anon_headers(),
            json={"type": "recovery", "token_hash": token_hash},
        )
        if response.status_code < 300:
            return response.json()
        raise _error_from(response)


def _error_from(response: httpx.Response) -> AuthAdminError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    error_code = str(body.get("error_code") or "") if isinstance(body, dict) else ""
    message = str(body.get("msg") or body.get("message") or "") if isinstance(body, dict) else ""
    status = response.status_code
    if error_code in {"email_exists", "user_already_exists"} or (
        status == 422 and "already been registered" in message
    ):
        return AuthAdminError("email_exists", status_code=status)
    if error_code == "weak_password":
        return AuthAdminError("weak_password", status_code=status)
    if error_code in {"email_address_invalid", "validation_failed"}:
        return AuthAdminError("email_invalid", status_code=status)
    if error_code == "user_not_found" or (status == 404 and "not found" in message.lower()):
        return AuthAdminError("user_not_found", status_code=status)
    if error_code == "otp_expired":
        return AuthAdminError("otp_expired", status_code=status)
    if error_code in {"otp_not_found", "flow_state_not_found"}:
        return AuthAdminError("otp_not_found", status_code=status)
    logger.warning(
        "auth_admin_request_failed",
        extra={"status_code": status, "auth_error_code": error_code or None},
    )
    return AuthAdminError("auth_unavailable", status_code=status)


def _user_record(payload: Any) -> AuthUserRecord:
    data = payload if isinstance(payload, dict) else {}
    return AuthUserRecord(
        id=str(data.get("id") or ""),
        email=str(data.get("email") or ""),
        banned_until=(str(data["banned_until"]) if data.get("banned_until") else None),
        last_sign_in_at=(str(data["last_sign_in_at"]) if data.get("last_sign_in_at") else None),
        created_at=(str(data["created_at"]) if data.get("created_at") else None),
    )
