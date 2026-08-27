"""Atomic byte-and-object storage quota reservations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_MAX_BYTES = 5 * 1024**3
_DEFAULT_MAX_OBJECTS = 100


@dataclass(frozen=True)
class QuotaCheckResult:
    allowed: bool
    reservation_id: UUID
    used_bytes: int
    reserved_bytes: int
    max_bytes: int
    used_object_count: int
    reserved_object_count: int
    max_objects: int
    remaining_bytes: int
    remaining_objects: int
    reason: str | None = None


@dataclass(frozen=True)
class QuotaUsage:
    used_bytes: int
    reserved_bytes: int
    max_bytes: int
    used_object_count: int
    reserved_object_count: int
    max_objects: int
    percentage: float


@dataclass
class _MemoryReservation:
    workspace_id: UUID
    byte_amount: int
    object_amount: int
    expires_at: datetime
    state: str = "reserved"


class StorageQuotaService:
    """Quota service backed either by service-role RPCs or explicit local memory."""

    def __init__(
        self,
        *,
        supabase_url: str | None = None,
        service_role_key: str | None = None,
        in_memory: bool = False,
        timeout_seconds: float = 10,
    ) -> None:
        self._supabase_url = supabase_url.rstrip("/") if supabase_url else None
        self._service_role_key = service_role_key
        self._in_memory = in_memory
        self._timeout = timeout_seconds
        if not in_memory and not (self._supabase_url and service_role_key):
            raise ValueError("StorageQuotaService requires Supabase or in_memory=True")
        self._usage: dict[UUID, QuotaUsage] = {}
        self._reservations: dict[UUID, _MemoryReservation] = {}
        self._lock = asyncio.Lock()

    async def reserve(
        self,
        *,
        reservation_id: UUID,
        workspace_id: UUID,
        byte_amount: int,
        object_amount: int = 1,
        expires_at: datetime,
    ) -> QuotaCheckResult:
        if byte_amount <= 0 or object_amount <= 0:
            raise ApiError(422, "quota_amount_invalid", "Quota amounts must be positive.")
        if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
            raise ApiError(422, "quota_expiry_invalid", "Quota expiry must be in the future.")
        if self._in_memory:
            return await self._reserve_memory(
                reservation_id=reservation_id,
                workspace_id=workspace_id,
                byte_amount=byte_amount,
                object_amount=object_amount,
                expires_at=expires_at,
            )

        result = await self._rpc(
            "motionly_reserve_storage_quota",
            {
                "p_reservation_id": str(reservation_id),
                "p_workspace_id": str(workspace_id),
                "p_byte_amount": byte_amount,
                "p_object_amount": object_amount,
                "p_expires_at": expires_at.isoformat(),
            },
        )
        allowed = bool(self._extract_rpc_scalar(result))
        usage = await self.get_usage(workspace_id)
        return self._result(
            allowed=allowed,
            reservation_id=reservation_id,
            usage=usage,
            reason=None if allowed else "Storage byte or object quota would be exceeded.",
        )

    async def check_and_reserve(
        self,
        workspace_id: UUID | str,
        file_size_bytes: int,
        *,
        reservation_id: UUID | None = None,
        object_count: int = 1,
        expires_at: datetime | None = None,
    ) -> QuotaCheckResult:
        """Compatibility entry point with an explicit/idempotent reservation result."""
        return await self.reserve(
            reservation_id=reservation_id or uuid4(),
            workspace_id=UUID(str(workspace_id)),
            byte_amount=file_size_bytes,
            object_amount=object_count,
            expires_at=expires_at or datetime.now(UTC) + timedelta(hours=6),
        )

    async def commit(self, reservation_id: UUID | str) -> bool:
        reservation_uuid = UUID(str(reservation_id))
        if self._in_memory:
            async with self._lock:
                reservation = self._reservations.get(reservation_uuid)
                if reservation is None or reservation.state == "released":
                    return False
                if reservation.state == "committed":
                    return True
                usage = self._memory_usage(reservation.workspace_id)
                self._usage[reservation.workspace_id] = QuotaUsage(
                    used_bytes=usage.used_bytes + reservation.byte_amount,
                    reserved_bytes=usage.reserved_bytes - reservation.byte_amount,
                    max_bytes=usage.max_bytes,
                    used_object_count=usage.used_object_count + reservation.object_amount,
                    reserved_object_count=(usage.reserved_object_count - reservation.object_amount),
                    max_objects=usage.max_objects,
                    percentage=self._percentage(
                        usage.used_bytes + reservation.byte_amount,
                        usage.reserved_bytes - reservation.byte_amount,
                        usage.max_bytes,
                    ),
                )
                reservation.state = "committed"
                return True
        result = await self._rpc(
            "motionly_commit_storage_quota",
            {"p_reservation_id": str(reservation_uuid)},
        )
        return bool(self._extract_rpc_scalar(result))

    async def release_reservation(self, reservation_id: UUID | str) -> bool:
        reservation_uuid = UUID(str(reservation_id))
        if self._in_memory:
            async with self._lock:
                reservation = self._reservations.get(reservation_uuid)
                if reservation is None:
                    return False
                if reservation.state == "released":
                    return True
                if reservation.state == "committed":
                    return False
                usage = self._memory_usage(reservation.workspace_id)
                self._usage[reservation.workspace_id] = QuotaUsage(
                    used_bytes=usage.used_bytes,
                    reserved_bytes=usage.reserved_bytes - reservation.byte_amount,
                    max_bytes=usage.max_bytes,
                    used_object_count=usage.used_object_count,
                    reserved_object_count=(usage.reserved_object_count - reservation.object_amount),
                    max_objects=usage.max_objects,
                    percentage=self._percentage(
                        usage.used_bytes,
                        usage.reserved_bytes - reservation.byte_amount,
                        usage.max_bytes,
                    ),
                )
                reservation.state = "released"
                return True
        result = await self._rpc(
            "motionly_release_storage_quota",
            {"p_reservation_id": str(reservation_uuid)},
        )
        return bool(self._extract_rpc_scalar(result))

    async def delete_committed(self, reservation_id: UUID | str) -> bool:
        """Account for permanent deletion of an already committed object exactly once."""
        reservation_uuid = UUID(str(reservation_id))
        if self._in_memory:
            async with self._lock:
                reservation = self._reservations.get(reservation_uuid)
                if reservation is None:
                    return False
                if reservation.state == "deleted":
                    return True
                if reservation.state != "committed":
                    return False
                usage = self._memory_usage(reservation.workspace_id)
                self._usage[reservation.workspace_id] = QuotaUsage(
                    used_bytes=usage.used_bytes - reservation.byte_amount,
                    reserved_bytes=usage.reserved_bytes,
                    max_bytes=usage.max_bytes,
                    used_object_count=usage.used_object_count - reservation.object_amount,
                    reserved_object_count=usage.reserved_object_count,
                    max_objects=usage.max_objects,
                    percentage=self._percentage(
                        usage.used_bytes - reservation.byte_amount,
                        usage.reserved_bytes,
                        usage.max_bytes,
                    ),
                )
                reservation.state = "deleted"
                return True
        result = await self._rpc(
            "motionly_delete_committed_storage_quota",
            {"p_reservation_id": str(reservation_uuid)},
        )
        return bool(self._extract_rpc_scalar(result))

    async def get_reservation_state(self, reservation_id: UUID | str) -> str | None:
        reservation_uuid = UUID(str(reservation_id))
        if self._in_memory:
            async with self._lock:
                reservation = self._reservations.get(reservation_uuid)
                return reservation.state if reservation is not None else None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._supabase_url}/rest/v1/storage_quota_reservations",
                    headers=self._headers(),
                    params={
                        "id": f"eq.{reservation_uuid}",
                        "select": "state",
                        "limit": "1",
                    },
                )
            if response.status_code >= 400:
                raise RuntimeError(f"quota reservation query failed: {response.status_code}")
            rows = response.json()
        except Exception as exc:
            logger.warning(
                "storage_quota_reservation_query_failed",
                extra={
                    "reservation_id": str(reservation_uuid),
                    "error": type(exc).__name__,
                },
            )
            raise ApiError(
                503,
                "storage_quota_unavailable",
                "Storage quota accounting is temporarily unavailable.",
            ) from exc
        return str(rows[0]["state"]) if rows else None

    async def get_usage(self, workspace_id: UUID | str) -> QuotaUsage:
        workspace_uuid = UUID(str(workspace_id))
        if self._in_memory:
            return self._memory_usage(workspace_uuid)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._supabase_url}/rest/v1/workspace_storage_quotas",
                    headers=self._headers(),
                    params={
                        "workspace_id": f"eq.{workspace_uuid}",
                        "select": (
                            "used_bytes,reserved_bytes,max_bytes,used_object_count,"
                            "reserved_object_count,max_objects"
                        ),
                        "limit": "1",
                    },
                )
            if response.status_code >= 400:
                raise RuntimeError(f"quota usage query failed: {response.status_code}")
            rows = response.json()
        except ApiError:
            raise
        except Exception as exc:
            logger.warning(
                "storage_quota_usage_failed",
                extra={"workspace_id": str(workspace_uuid), "error": type(exc).__name__},
            )
            raise ApiError(
                503,
                "storage_quota_unavailable",
                "Storage quota accounting is temporarily unavailable.",
            ) from exc
        if not rows:
            return self._empty_usage()
        row = rows[0]
        used = int(row["used_bytes"])
        reserved = int(row["reserved_bytes"])
        maximum = int(row["max_bytes"])
        return QuotaUsage(
            used_bytes=used,
            reserved_bytes=reserved,
            max_bytes=maximum,
            used_object_count=int(row["used_object_count"]),
            reserved_object_count=int(row["reserved_object_count"]),
            max_objects=int(row["max_objects"]),
            percentage=self._percentage(used, reserved, maximum),
        )

    def set_in_memory_limits(
        self,
        *,
        workspace_id: UUID,
        max_bytes: int,
        max_objects: int,
    ) -> None:
        """Test/local configuration hook; unavailable for RPC-backed instances."""
        if not self._in_memory:
            raise RuntimeError("in-memory limits are available only in in-memory mode")
        existing = self._memory_usage(workspace_id)
        if existing.used_bytes + existing.reserved_bytes > max_bytes:
            raise ValueError("new byte limit is below current accounting")
        if existing.used_object_count + existing.reserved_object_count > max_objects:
            raise ValueError("new object limit is below current accounting")
        self._usage[workspace_id] = QuotaUsage(
            used_bytes=existing.used_bytes,
            reserved_bytes=existing.reserved_bytes,
            max_bytes=max_bytes,
            used_object_count=existing.used_object_count,
            reserved_object_count=existing.reserved_object_count,
            max_objects=max_objects,
            percentage=self._percentage(existing.used_bytes, existing.reserved_bytes, max_bytes),
        )

    async def _reserve_memory(
        self,
        *,
        reservation_id: UUID,
        workspace_id: UUID,
        byte_amount: int,
        object_amount: int,
        expires_at: datetime,
    ) -> QuotaCheckResult:
        async with self._lock:
            existing = self._reservations.get(reservation_id)
            if existing is not None:
                if (
                    existing.workspace_id != workspace_id
                    or existing.byte_amount != byte_amount
                    or existing.object_amount != object_amount
                    or existing.expires_at != expires_at
                ):
                    raise ApiError(
                        409,
                        "quota_reservation_conflict",
                        "Reservation ID was reused with different values.",
                    )
                allowed = existing.state in {"reserved", "committed"}
                return self._result(
                    allowed=allowed,
                    reservation_id=reservation_id,
                    usage=self._memory_usage(workspace_id),
                    reason=None if allowed else "Storage reservation was already released.",
                )

            usage = self._memory_usage(workspace_id)
            allowed = (
                usage.used_bytes + usage.reserved_bytes + byte_amount <= usage.max_bytes
                and usage.used_object_count + usage.reserved_object_count + object_amount
                <= usage.max_objects
            )
            if not allowed:
                return self._result(
                    allowed=False,
                    reservation_id=reservation_id,
                    usage=usage,
                    reason="Storage byte or object quota would be exceeded.",
                )
            self._reservations[reservation_id] = _MemoryReservation(
                workspace_id=workspace_id,
                byte_amount=byte_amount,
                object_amount=object_amount,
                expires_at=expires_at,
            )
            updated = QuotaUsage(
                used_bytes=usage.used_bytes,
                reserved_bytes=usage.reserved_bytes + byte_amount,
                max_bytes=usage.max_bytes,
                used_object_count=usage.used_object_count,
                reserved_object_count=usage.reserved_object_count + object_amount,
                max_objects=usage.max_objects,
                percentage=self._percentage(
                    usage.used_bytes, usage.reserved_bytes + byte_amount, usage.max_bytes
                ),
            )
            self._usage[workspace_id] = updated
            return self._result(
                allowed=True,
                reservation_id=reservation_id,
                usage=updated,
                reason=None,
            )

    async def _rpc(self, function_name: str, params: dict[str, Any]) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._supabase_url}/rest/v1/rpc/{function_name}",
                    headers=self._headers(),
                    json=params,
                )
            if response.status_code >= 400:
                raise RuntimeError(f"{function_name} failed: {response.status_code}")
            return response.json()
        except ApiError:
            raise
        except Exception as exc:
            logger.warning(
                "storage_quota_rpc_failed",
                extra={"function": function_name, "error": type(exc).__name__},
            )
            raise ApiError(
                503,
                "storage_quota_unavailable",
                "Storage quota accounting is temporarily unavailable.",
            ) from exc

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key or "",
            "authorization": f"Bearer {self._service_role_key or ''}",
            "content-type": "application/json",
        }

    def _memory_usage(self, workspace_id: UUID) -> QuotaUsage:
        return self._usage.get(workspace_id, self._empty_usage())

    @staticmethod
    def _empty_usage() -> QuotaUsage:
        return QuotaUsage(
            used_bytes=0,
            reserved_bytes=0,
            max_bytes=_DEFAULT_MAX_BYTES,
            used_object_count=0,
            reserved_object_count=0,
            max_objects=_DEFAULT_MAX_OBJECTS,
            percentage=0.0,
        )

    @classmethod
    def _result(
        cls,
        *,
        allowed: bool,
        reservation_id: UUID,
        usage: QuotaUsage,
        reason: str | None,
    ) -> QuotaCheckResult:
        return QuotaCheckResult(
            allowed=allowed,
            reservation_id=reservation_id,
            used_bytes=usage.used_bytes,
            reserved_bytes=usage.reserved_bytes,
            max_bytes=usage.max_bytes,
            used_object_count=usage.used_object_count,
            reserved_object_count=usage.reserved_object_count,
            max_objects=usage.max_objects,
            remaining_bytes=max(0, usage.max_bytes - usage.used_bytes - usage.reserved_bytes),
            remaining_objects=max(
                0,
                usage.max_objects - usage.used_object_count - usage.reserved_object_count,
            ),
            reason=reason,
        )

    @staticmethod
    def _percentage(used: int, reserved: int, maximum: int) -> float:
        return round(((used + reserved) / maximum * 100) if maximum else 0.0, 2)

    @staticmethod
    def _extract_rpc_scalar(result: Any) -> Any:
        if isinstance(result, list) and len(result) == 1:
            row = result[0]
            if isinstance(row, dict) and len(row) == 1:
                return next(iter(row.values()))
            return row
        return result
