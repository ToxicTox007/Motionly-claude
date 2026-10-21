"""Persistence for password-reset requests and the admin audit log (Task 12).

Both tables are service-role only. When Supabase is configured the rows live in
``public.password_reset_requests`` and ``public.admin_audit_events``; without it
(unit tests) an in-memory store keeps the same shape so route behaviour, audit
writes, and ``details`` redaction can be asserted without a database.

``details`` never holds secrets: no reset tokens, links, or passwords are ever
passed in, and the service drops anything that looks token-shaped defensively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.logging import get_logger
from app.services.offload import run_blocking
from app.services.supabase import SupabaseRestClient

logger = get_logger(__name__)

# Admin actions recorded in the audit log.
ACTION_RESET_REQUESTED = "password_reset_requested"
ACTION_LINK_CREATED = "password_reset_link_created"
ACTION_REQUEST_DISMISSED = "password_reset_request_dismissed"
ACTION_USER_BANNED = "user_banned"
ACTION_USER_UNBANNED = "user_unbanned"

# Keys that must never be stored in an audit detail, whatever the caller sends.
_SECRET_DETAIL_KEYS = frozenset(
    {"token", "token_hash", "hashed_token", "link", "action_link", "password", "otp", "email_otp"}
)


@dataclass(frozen=True)
class ResetRequest:
    id: str
    user_id: str | None
    email: str
    status: str
    viewer_hash: str
    created_at: str
    handled_by: str | None = None
    handled_at: str | None = None


@dataclass
class _InMemoryStore:
    requests: list[dict[str, Any]] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)


def _redact(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    return {key: value for key, value in details.items() if key not in _SECRET_DETAIL_KEYS}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AdminUserDirectory:
    def __init__(
        self,
        *,
        supabase_url: str | None = None,
        service_role_key: str | None = None,
        metrics_registry: Any | None = None,
    ) -> None:
        self._in_memory = not (supabase_url and service_role_key)
        self._store = _InMemoryStore() if self._in_memory else None
        self._requests_client = (
            None
            if self._in_memory
            else SupabaseRestClient(
                supabase_url=str(supabase_url),
                service_role_key=str(service_role_key),
                repository="password_reset_requests",
                metrics_registry=metrics_registry,
            )
        )
        self._audit_client = (
            None
            if self._in_memory
            else SupabaseRestClient(
                supabase_url=str(supabase_url),
                service_role_key=str(service_role_key),
                repository="admin_audit_events",
                metrics_registry=metrics_registry,
            )
        )

    # -- password reset requests --------------------------------------------

    async def record_reset_request(
        self, *, email: str, user_id: str | None, viewer_hash: str
    ) -> None:
        """Record a help request. Unknown emails are recorded too (no leak)."""
        row = {
            "id": str(uuid4()),
            "user_id": user_id,
            "email": email,
            "status": "pending",
            "viewer_hash": viewer_hash,
            "created_at": _now(),
        }
        if self._in_memory:
            assert self._store is not None
            self._store.requests.append(row)
            return
        assert self._requests_client is not None
        await run_blocking(
            lambda: self._requests_client.request("POST", "", json=row)
        )

    async def list_pending_requests(self, *, limit: int = 50) -> list[ResetRequest]:
        if self._in_memory:
            assert self._store is not None
            rows = [row for row in self._store.requests if row["status"] == "pending"]
            rows = sorted(rows, key=lambda row: row["created_at"], reverse=True)[:limit]
            return [_reset_request(row) for row in rows]
        assert self._requests_client is not None
        response = await run_blocking(
            lambda: self._requests_client.request(
                "GET",
                "",
                params={
                    "status": "eq.pending",
                    "order": "created_at.desc",
                    "limit": limit,
                },
            )
        )
        response.raise_for_status()
        return [_reset_request(row) for row in response.json()]

    async def dismiss_request(self, *, request_id: str, handled_by: str) -> bool:
        return await self._set_request_status(
            request_id=request_id, status="dismissed", handled_by=handled_by
        )

    async def mark_request_link_issued(self, *, request_id: str, handled_by: str) -> bool:
        return await self._set_request_status(
            request_id=request_id, status="link_issued", handled_by=handled_by
        )

    async def _set_request_status(
        self, *, request_id: str, status: str, handled_by: str
    ) -> bool:
        patch = {"status": status, "handled_by": handled_by, "handled_at": _now()}
        if self._in_memory:
            assert self._store is not None
            for row in self._store.requests:
                if row["id"] == request_id and row["status"] == "pending":
                    row.update(patch)
                    return True
            return False
        assert self._requests_client is not None
        response = await run_blocking(
            lambda: self._requests_client.request(
                "PATCH",
                "",
                params={"id": f"eq.{request_id}", "status": "eq.pending"},
                json=patch,
            )
        )
        response.raise_for_status()
        return bool(response.json())

    # -- audit log ----------------------------------------------------------

    async def record_audit_event(
        self,
        *,
        actor_user_id: str,
        action: str,
        target_user_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = _redact(details)
        row = {
            "id": str(uuid4()),
            "actor_user_id": actor_user_id,
            "action": action,
            "target_user_id": target_user_id,
            "details": safe_details,
            "created_at": _now(),
        }
        logger.info(
            "admin_audit_event",
            extra={
                "action": action,
                "actor_user_id": actor_user_id,
                "target_user_id": target_user_id,
            },
        )
        if self._in_memory:
            assert self._store is not None
            self._store.audit.append(row)
            return
        assert self._audit_client is not None
        await run_blocking(lambda: self._audit_client.request("POST", "", json=row))

    # -- test helpers -------------------------------------------------------

    @property
    def recorded_audit_events(self) -> list[dict[str, Any]]:
        if self._store is None:
            raise RuntimeError("audit events are only inspectable with the in-memory store")
        return list(self._store.audit)

    @property
    def recorded_requests(self) -> list[dict[str, Any]]:
        if self._store is None:
            raise RuntimeError("reset requests are only inspectable with the in-memory store")
        return list(self._store.requests)


def _reset_request(row: dict[str, Any]) -> ResetRequest:
    return ResetRequest(
        id=str(row["id"]),
        user_id=(str(row["user_id"]) if row.get("user_id") else None),
        email=str(row["email"]),
        status=str(row["status"]),
        viewer_hash=str(row.get("viewer_hash") or ""),
        created_at=str(row.get("created_at") or ""),
        handled_by=(str(row["handled_by"]) if row.get("handled_by") else None),
        handled_at=(str(row["handled_at"]) if row.get("handled_at") else None),
    )
