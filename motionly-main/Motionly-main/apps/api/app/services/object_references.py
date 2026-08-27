"""Durable, tenant-scoped storage references with on-demand URL grants."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.core.logging import get_logger
from app.services.offload import run_blocking
from app.services.storage import StorageSigner

logger = get_logger(__name__)

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
_OWNER_TYPES = {
    "uploaded_video",
    "export",
    "stock_asset",
    "music_track",
    "segmentation_matte",
    "video_manifest",
    "project",
    "style_profile",
}
_OBJECT_TYPES = {
    "source_video",
    "thumbnail",
    "export",
    "broll_asset",
    "music_asset",
    "matte",
    "temp_preview",
    "audio_stem",
    "style_reference",
}
_BROWSER_PURPOSES = {"browser_preview", "browser_download", "export_download"}


@dataclass(frozen=True)
class ObjectReference:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    storage_bucket: str
    storage_path: str
    owner_type: str
    owner_id: UUID
    object_type: str
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None
    status: str = "active"

    @property
    def bucket(self) -> str:
        return self.storage_bucket

    @property
    def path(self) -> str:
        return self.storage_path


@dataclass(frozen=True)
class SignedUrlResult:
    url: str
    expires_at: datetime
    ttl_seconds: int
    reference_id: UUID


@dataclass(frozen=True)
class RefreshedUrl:
    reference_id: UUID
    object_type: str
    url: str
    expires_at: datetime


class ObjectReferenceService:
    BROWSER_TTL = 300
    WORKER_TTL = 7200

    def __init__(
        self,
        *,
        storage_signer: StorageSigner,
        supabase_url: str | None = None,
        service_role_key: str | None = None,
        in_memory: bool = False,
        timeout_seconds: float = 10,
    ) -> None:
        self._storage_signer = storage_signer
        self._supabase_url = supabase_url.rstrip("/") if supabase_url else None
        self._service_role_key = service_role_key
        self._in_memory = in_memory
        self._timeout = timeout_seconds
        if not in_memory and not (self._supabase_url and service_role_key):
            raise ValueError("ObjectReferenceService requires Supabase or in_memory=True")
        self._references: dict[UUID, ObjectReference] = {}
        self._locations: dict[tuple[str, str], UUID] = {}
        self._grants: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def grants(self) -> tuple[dict[str, Any], ...]:
        """Read-only audit view used by local tests."""
        return tuple(dict(grant) for grant in self._grants)

    async def create_reference(
        self,
        *,
        workspace_id: UUID | str,
        project_id: UUID | str,
        storage_bucket: str | None = None,
        storage_path: str | None = None,
        bucket: str | None = None,
        path: str | None = None,
        owner_type: str | None = None,
        owner_id: UUID | str,
        object_type: str,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        file_size_bytes: int | None = None,
        sha256: str | None = None,
    ) -> ObjectReference:
        workspace_uuid = UUID(str(workspace_id))
        project_uuid = UUID(str(project_id))
        owner_uuid = UUID(str(owner_id))
        canonical_bucket = storage_bucket or bucket or ""
        canonical_path = storage_path or path or ""
        owner_kind = owner_type or "uploaded_video"
        canonical_size = size_bytes if size_bytes is not None else file_size_bytes
        self._validate_reference_values(
            workspace_id=workspace_uuid,
            project_id=project_uuid,
            storage_bucket=canonical_bucket,
            storage_path=canonical_path,
            owner_type=owner_kind,
            object_type=object_type,
            size_bytes=canonical_size,
            sha256=sha256,
        )
        row = {
            "workspace_id": str(workspace_uuid),
            "project_id": str(project_uuid),
            "storage_bucket": canonical_bucket,
            "storage_path": canonical_path,
            "owner_type": owner_kind,
            "owner_id": str(owner_uuid),
            "object_type": object_type,
            "mime_type": mime_type,
            "size_bytes": canonical_size,
            "sha256": sha256,
            "status": "active",
            "deleted_at": None,
        }
        if self._in_memory:
            async with self._lock:
                location = (canonical_bucket, canonical_path)
                existing_id = self._locations.get(location)
                reference_id = existing_id or uuid4()
                existing = self._references.get(reference_id)
                if existing is not None and (
                    existing.workspace_id != workspace_uuid or existing.project_id != project_uuid
                ):
                    raise ApiError(
                        409,
                        "object_reference_conflict",
                        "Storage object is already owned by another project.",
                    )
                reference = ObjectReference(
                    id=reference_id,
                    workspace_id=workspace_uuid,
                    project_id=project_uuid,
                    storage_bucket=canonical_bucket,
                    storage_path=canonical_path,
                    owner_type=owner_kind,
                    owner_id=owner_uuid,
                    object_type=object_type,
                    mime_type=mime_type,
                    size_bytes=canonical_size,
                    sha256=sha256,
                )
                self._references[reference_id] = reference
                self._locations[location] = reference_id
                return reference

        response = await self._request(
            "POST",
            "/object_references",
            params={"on_conflict": "storage_bucket,storage_path", "select": "*"},
            json=row,
            headers={"prefer": "resolution=merge-duplicates,return=representation"},
        )
        rows = response.json()
        if not rows:
            raise ApiError(503, "object_reference_unavailable", "Object reference was not saved.")
        reference = self._from_row(rows[0])
        if reference.workspace_id != workspace_uuid or reference.project_id != project_uuid:
            raise ApiError(
                409,
                "object_reference_conflict",
                "Storage object is already owned by another project.",
            )
        return reference

    async def get_signed_url(
        self,
        *,
        workspace_id: UUID | str,
        project_id: UUID | str,
        reference_id: UUID | str,
        purpose: str = "browser_preview",
        principal_id: UUID | str | None = None,
        user_id: UUID | str | None = None,
    ) -> SignedUrlResult:
        if purpose not in _BROWSER_PURPOSES:
            raise ApiError(422, "signed_url_purpose_invalid", "Browser URL purpose is invalid.")
        workspace_uuid = UUID(str(workspace_id))
        project_uuid = UUID(str(project_id))
        reference_uuid = UUID(str(reference_id))
        actor_uuid = UUID(str(principal_id or user_id)) if principal_id or user_id else None
        if actor_uuid is None:
            raise ApiError(422, "signed_url_principal_required", "Browser principal is required.")
        reference = await self._get_reference(
            workspace_id=workspace_uuid,
            project_id=project_uuid,
            reference_id=reference_uuid,
        )
        issued_at = datetime.now(UTC)
        signed = await run_blocking(
            self._storage_signer.create_signed_download,
            bucket=reference.storage_bucket,
            path=reference.storage_path,
            expires_in_seconds=self.BROWSER_TTL,
        )
        expires_at = issued_at + timedelta(seconds=self.BROWSER_TTL)
        await self._record_grant(
            reference=reference,
            purpose=purpose,
            principal_type="user",
            principal_id=actor_uuid,
            issued_at=issued_at,
            expires_at=expires_at,
            ttl_seconds=self.BROWSER_TTL,
            request_context={},
        )
        return SignedUrlResult(
            url=signed.url,
            expires_at=expires_at,
            ttl_seconds=self.BROWSER_TTL,
            reference_id=reference.id,
        )

    async def get_share_link_signed_url(
        self,
        *,
        workspace_id: UUID | str,
        project_id: UUID | str,
        reference_id: UUID | str,
        share_link_id: UUID | str,
        link_expires_at: datetime,
    ) -> SignedUrlResult:
        """Issue a preview URL bounded by both browser and link expiration."""
        workspace_uuid = UUID(str(workspace_id))
        project_uuid = UUID(str(project_id))
        reference_uuid = UUID(str(reference_id))
        link_uuid = UUID(str(share_link_id))
        issued_at = datetime.now(UTC)
        remaining_seconds = int((link_expires_at - issued_at).total_seconds())
        if remaining_seconds <= 0:
            raise ApiError(404, "review_link_not_found", "Review link was not found.")
        ttl_seconds = min(self.BROWSER_TTL, remaining_seconds)
        reference = await self._get_reference(
            workspace_id=workspace_uuid,
            project_id=project_uuid,
            reference_id=reference_uuid,
        )
        signed = await run_blocking(
            self._storage_signer.create_signed_download,
            bucket=reference.storage_bucket,
            path=reference.storage_path,
            expires_in_seconds=ttl_seconds,
        )
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
        await self._record_grant(
            reference=reference,
            purpose="browser_preview",
            principal_type="share_link",
            principal_id=link_uuid,
            issued_at=issued_at,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            request_context={"capability": "client_review"},
        )
        return SignedUrlResult(
            url=signed.url,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            reference_id=reference.id,
        )

    async def refresh_urls(
        self,
        *,
        workspace_id: UUID | str,
        project_id: UUID | str,
        reference_ids: list[UUID] | tuple[UUID, ...],
        principal_id: UUID | str | None = None,
        user_id: UUID | str | None = None,
    ) -> list[RefreshedUrl]:
        if not 1 <= len(reference_ids) <= 100 or len(set(reference_ids)) != len(reference_ids):
            raise ApiError(
                422,
                "reference_ids_invalid",
                "Provide between 1 and 100 unique object reference IDs.",
            )
        workspace_uuid = UUID(str(workspace_id))
        project_uuid = UUID(str(project_id))
        actor_uuid = UUID(str(principal_id or user_id)) if principal_id or user_id else None
        if actor_uuid is None:
            raise ApiError(422, "signed_url_principal_required", "Browser principal is required.")
        references = await self._get_references(
            workspace_id=workspace_uuid,
            project_id=project_uuid,
            reference_ids=[UUID(str(item)) for item in reference_ids],
        )
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=self.BROWSER_TTL)
        signed_values = []
        for reference in references:
            signed = await run_blocking(
                self._storage_signer.create_signed_download,
                bucket=reference.storage_bucket,
                path=reference.storage_path,
                expires_in_seconds=self.BROWSER_TTL,
            )
            signed_values.append((reference, signed.url))
        for reference, _url in signed_values:
            await self._record_grant(
                reference=reference,
                purpose="browser_preview",
                principal_type="user",
                principal_id=actor_uuid,
                issued_at=issued_at,
                expires_at=expires_at,
                ttl_seconds=self.BROWSER_TTL,
                request_context={"batch_size": len(references)},
            )
        return [
            RefreshedUrl(
                reference_id=reference.id,
                object_type=reference.object_type,
                url=url,
                expires_at=expires_at,
            )
            for reference, url in signed_values
        ]

    async def get_worker_urls(
        self,
        *,
        workspace_id: UUID | str,
        project_id: UUID | str,
        reference_ids: list[UUID] | tuple[UUID, ...] | None = None,
        media_references: list[str] | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> dict[UUID, SignedUrlResult]:
        raw_ids = reference_ids or [UUID(value) for value in media_references or []]
        if not 1 <= len(raw_ids) <= 100 or len(set(raw_ids)) != len(raw_ids):
            raise ApiError(
                422,
                "reference_ids_invalid",
                "Provide between 1 and 100 unique object reference IDs.",
            )
        workspace_uuid = UUID(str(workspace_id))
        project_uuid = UUID(str(project_id))
        references = await self._get_references(
            workspace_id=workspace_uuid,
            project_id=project_uuid,
            reference_ids=[UUID(str(item)) for item in raw_ids],
        )
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=self.WORKER_TTL)
        signed_values = []
        for reference in references:
            signed = await run_blocking(
                self._storage_signer.create_signed_worker_download,
                bucket=reference.storage_bucket,
                path=reference.storage_path,
                expires_in_seconds=self.WORKER_TTL,
            )
            signed_values.append((reference, signed.url))
        for reference, _url in signed_values:
            await self._record_grant(
                reference=reference,
                purpose="worker_processing",
                principal_type="worker",
                principal_id=None,
                issued_at=issued_at,
                expires_at=expires_at,
                ttl_seconds=self.WORKER_TTL,
                request_context=request_context or {"batch_size": len(references)},
            )
        return {
            reference.id: SignedUrlResult(
                url=url,
                expires_at=expires_at,
                ttl_seconds=self.WORKER_TTL,
                reference_id=reference.id,
            )
            for reference, url in signed_values
        }

    async def get_active_reference(
        self,
        *,
        workspace_id: UUID | str,
        project_id: UUID | str,
        reference_id: UUID | str,
    ) -> ObjectReference:
        """Resolve one active reference for a tenant-scoped domain operation.

        Callers that impose a narrower media policy (for example, the workspace
        asset library) must additionally validate owner, bucket, and path.
        """
        return await self._get_reference(
            workspace_id=UUID(str(workspace_id)),
            project_id=UUID(str(project_id)),
            reference_id=UUID(str(reference_id)),
        )

    def get_active_reference_sync(
        self,
        *,
        workspace_id: UUID | str,
        project_id: UUID | str,
        reference_id: UUID | str,
    ) -> ObjectReference:
        """Synchronous worker resolver for a tenant-scoped durable reference.

        Worker execution is intentionally synchronous.  This is the same
        service-role, active-only query as ``get_active_reference``; it exists
        solely so media workers never fall back to profile JSON bucket/path.
        """
        workspace_uuid = UUID(str(workspace_id))
        project_uuid = UUID(str(project_id))
        reference_uuid = UUID(str(reference_id))
        if self._in_memory:
            reference = self._references.get(reference_uuid)
            if (
                reference is None
                or reference.status != "active"
                or reference.workspace_id != workspace_uuid
                or reference.project_id != project_uuid
            ):
                raise ApiError(
                    404,
                    "object_reference_not_found",
                    "An active object reference was not found for this project.",
                )
            return reference
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(
                    f"{self._supabase_url}/rest/v1/object_references",
                    headers=self._headers(),
                    params={
                        "id": f"eq.{reference_uuid}",
                        "workspace_id": f"eq.{workspace_uuid}",
                        "project_id": f"eq.{project_uuid}",
                        "status": "eq.active",
                        "select": "*",
                        "limit": "1",
                    },
                )
            if response.status_code >= 400:
                raise RuntimeError(f"object reference query failed: {response.status_code}")
            rows = response.json()
        except Exception as exc:
            logger.warning(
                "object_reference_sync_query_failed",
                extra={"reference_id": str(reference_uuid), "error": type(exc).__name__},
            )
            raise ApiError(
                503,
                "object_reference_unavailable",
                "Media references are temporarily unavailable.",
            ) from exc
        if not rows:
            raise ApiError(
                404,
                "object_reference_not_found",
                "An active object reference was not found for this project.",
            )
        return self._from_row(rows[0])

    async def mark_deleted(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        reference_id: UUID,
    ) -> None:
        if self._in_memory:
            reference = self._references.get(reference_id)
            if (
                reference is None
                or reference.workspace_id != workspace_id
                or reference.project_id != project_id
            ):
                raise ApiError(
                    404,
                    "object_reference_not_found",
                    "An object reference was not found for this project.",
                )
            if reference.status == "deleted":
                return
            self._references[reference.id] = ObjectReference(
                **{**reference.__dict__, "status": "deleted"}
            )
            return
        response = await self._request(
            "PATCH",
            "/object_references",
            params={
                "id": f"eq.{reference_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "id,status",
            },
            json={"status": "deleted", "deleted_at": datetime.now(UTC).isoformat()},
            headers={"prefer": "return=representation"},
        )
        if not response.json():
            raise ApiError(
                404,
                "object_reference_not_found",
                "An object reference was not found for this project.",
            )

    async def _get_reference(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        reference_id: UUID,
    ) -> ObjectReference:
        references = await self._get_references(
            workspace_id=workspace_id,
            project_id=project_id,
            reference_ids=[reference_id],
        )
        return references[0]

    async def _get_references(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        reference_ids: list[UUID],
    ) -> list[ObjectReference]:
        if self._in_memory:
            by_id = {
                reference_id: self._references.get(reference_id) for reference_id in reference_ids
            }
        else:
            joined = ",".join(str(reference_id) for reference_id in reference_ids)
            response = await self._request(
                "GET",
                "/object_references",
                params={
                    "id": f"in.({joined})",
                    "workspace_id": f"eq.{workspace_id}",
                    "project_id": f"eq.{project_id}",
                    "status": "eq.active",
                    "select": "*",
                },
            )
            by_id = {reference.id: reference for reference in map(self._from_row, response.json())}
        ordered = []
        for reference_id in reference_ids:
            reference = by_id.get(reference_id)
            if (
                reference is None
                or reference.status != "active"
                or reference.workspace_id != workspace_id
                or reference.project_id != project_id
            ):
                raise ApiError(
                    404,
                    "object_reference_not_found",
                    "An active object reference was not found for this project.",
                )
            ordered.append(reference)
        return ordered

    async def _record_grant(
        self,
        *,
        reference: ObjectReference,
        purpose: str,
        principal_type: str,
        principal_id: UUID | None,
        issued_at: datetime,
        expires_at: datetime,
        ttl_seconds: int,
        request_context: dict[str, Any],
    ) -> None:
        grant = {
            "id": str(uuid4()),
            "workspace_id": str(reference.workspace_id),
            "project_id": str(reference.project_id),
            "object_reference_id": str(reference.id),
            "purpose": purpose,
            "principal_type": principal_type,
            "principal_id": str(principal_id) if principal_id else None,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "ttl_seconds": ttl_seconds,
            "request_context": request_context,
        }
        if self._in_memory:
            self._grants.append(grant)
            return
        await self._request("POST", "/signed_url_grants", json=grant)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    f"{self._supabase_url}/rest/v1/{path.lstrip('/')}",
                    headers={**self._headers(), **(headers or {})},
                    params=params,
                    json=json,
                )
            if response.status_code >= 400:
                raise RuntimeError(f"object reference request failed: {response.status_code}")
            return response
        except ApiError:
            raise
        except Exception as exc:
            logger.warning(
                "object_reference_request_failed",
                extra={"method": method, "path": path, "error": type(exc).__name__},
            )
            raise ApiError(
                503,
                "object_reference_unavailable",
                "Media references are temporarily unavailable.",
            ) from exc

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key or "",
            "authorization": f"Bearer {self._service_role_key or ''}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ObjectReference:
        return ObjectReference(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            storage_bucket=str(row["storage_bucket"]),
            storage_path=str(row["storage_path"]),
            owner_type=str(row["owner_type"]),
            owner_id=UUID(str(row["owner_id"])),
            object_type=str(row["object_type"]),
            mime_type=str(row["mime_type"]) if row.get("mime_type") else None,
            size_bytes=int(row["size_bytes"]) if row.get("size_bytes") is not None else None,
            sha256=str(row["sha256"]) if row.get("sha256") else None,
            status=str(row.get("status", "active")),
        )

    @staticmethod
    def _validate_reference_values(
        *,
        workspace_id: UUID,
        project_id: UUID,
        storage_bucket: str,
        storage_path: str,
        owner_type: str,
        object_type: str,
        size_bytes: int | None,
        sha256: str | None,
    ) -> None:
        if not _BUCKET_RE.fullmatch(storage_bucket):
            raise ApiError(422, "storage_bucket_invalid", "Storage bucket is invalid.")
        decoded = unquote(storage_path)
        segments = storage_path.split("/")
        expected_prefix = f"workspaces/{workspace_id}/projects/{project_id}/"
        if (
            decoded != storage_path
            or not storage_path.startswith(expected_prefix)
            or storage_path.startswith("/")
            or "\\" in storage_path
            or "" in segments
            or any(segment in {".", ".."} for segment in segments)
        ):
            raise ApiError(422, "storage_path_invalid", "Storage path is outside this project.")
        if owner_type not in _OWNER_TYPES:
            raise ApiError(422, "owner_type_invalid", "Object owner type is invalid.")
        if object_type not in _OBJECT_TYPES:
            raise ApiError(422, "object_type_invalid", "Object type is invalid.")
        if size_bytes is not None and size_bytes <= 0:
            raise ApiError(422, "object_size_invalid", "Object size must be positive.")
        if sha256 is not None and (
            len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise ApiError(422, "object_hash_invalid", "Object SHA256 is invalid.")
