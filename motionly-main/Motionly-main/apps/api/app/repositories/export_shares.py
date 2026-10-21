"""Repository for export share links (Task 16).

Share links are stored with only a SHA-256 fingerprint of the secret token.
The public resolve path is enumeration-safe: unknown, revoked, and expired
links all raise the same 404 so a holder cannot distinguish link states.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.renders import ExportRecord, InMemoryRenderRepository
from app.schemas.export_shares import ExportShareLinkResponse
from app.services.supabase import SupabaseRestClient

SHARE_LINK_COLUMNS = (
    "id,workspace_id,project_id,export_id,token_digest,allow_download,expires_at,"
    "revoked_at,created_by,last_accessed_at,created_at"
)


def token_digest(token: str) -> str:
    """SHA-256 fingerprint of a share-link token. Only this is ever persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class ExportShareLinkRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    export_id: UUID
    token_digest: str
    allow_download: bool
    expires_at: datetime
    created_by: UUID
    revoked_at: datetime | None = None
    last_accessed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> ExportShareLinkResponse:
        return ExportShareLinkResponse(
            id=self.id,
            export_id=self.export_id,
            allow_download=self.allow_download,
            expires_at=self.expires_at,
            revoked_at=self.revoked_at,
            last_accessed_at=self.last_accessed_at,
            created_at=self.created_at,
        )


@dataclass(frozen=True)
class ResolvedExportShareLink:
    link: ExportShareLinkRecord
    export: ExportRecord


class InMemoryExportShareRepository:
    def __init__(self, render_repository: InMemoryRenderRepository) -> None:
        self.render_repository = render_repository
        self.share_links: dict[UUID, ExportShareLinkRecord] = {}
        self._lock = RLock()

    def _get_export_for_workspace(self, *, export_id: UUID, workspace_id: UUID) -> ExportRecord:
        return self.render_repository.get_export(export_id=export_id, workspace_id=workspace_id)

    def create_share_link(
        self,
        *,
        export_id: UUID,
        workspace_id: UUID,
        token: str,
        expires_at: datetime,
        allow_download: bool,
        created_by: UUID,
    ) -> ExportShareLinkRecord:
        export = self._get_export_for_workspace(export_id=export_id, workspace_id=workspace_id)
        link = ExportShareLinkRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=export.project_id,
            export_id=export_id,
            token_digest=token_digest(token),
            allow_download=allow_download,
            expires_at=expires_at,
            created_by=created_by,
        )
        with self._lock:
            self.share_links[link.id] = link
        return link

    def list_share_links(
        self, *, export_id: UUID, workspace_id: UUID
    ) -> list[ExportShareLinkRecord]:
        self._get_export_for_workspace(export_id=export_id, workspace_id=workspace_id)
        return sorted(
            [
                link
                for link in self.share_links.values()
                if link.export_id == export_id and link.workspace_id == workspace_id
            ],
            key=lambda link: link.created_at,
            reverse=True,
        )

    def revoke_share_link(
        self, *, share_link_id: UUID, workspace_id: UUID
    ) -> ExportShareLinkRecord:
        with self._lock:
            link = self.share_links.get(share_link_id)
            if link is None or link.workspace_id != workspace_id:
                raise ApiError(404, "export_share_link_not_found", "Share link was not found.")
            if link.revoked_at is None:
                link.revoked_at = datetime.now(UTC)
            return link

    def resolve_share_link(self, *, token: str) -> ResolvedExportShareLink:
        """Resolve a viewer token. Unknown/revoked/expired links are indistinguishable."""
        digest = token_digest(token)
        now = datetime.now(UTC)
        link = next(
            (
                candidate
                for candidate in self.share_links.values()
                if candidate.token_digest == digest
            ),
            None,
        )
        if link is None or link.revoked_at is not None or link.expires_at <= now:
            raise ApiError(404, "export_share_link_not_found", "Share link was not found.")
        export = self.render_repository.get_export(
            export_id=link.export_id, workspace_id=link.workspace_id
        )
        link.last_accessed_at = now
        return ResolvedExportShareLink(link=link, export=export)


class SupabaseExportShareRepository(InMemoryExportShareRepository):
    def __init__(
        self,
        *,
        render_repository: InMemoryRenderRepository,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__(render_repository)
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="export_shares",
            metrics_registry=metrics_registry,
        )

    def create_share_link(self, **values: Any) -> ExportShareLinkRecord:
        export = self._get_export_for_workspace(
            export_id=values["export_id"], workspace_id=values["workspace_id"]
        )
        response = self.client.request(
            "POST",
            "/export_share_links",
            params={"select": SHARE_LINK_COLUMNS},
            json={
                "workspace_id": str(values["workspace_id"]),
                "project_id": str(export.project_id),
                "export_id": str(values["export_id"]),
                "token_digest": token_digest(values["token"]),
                "allow_download": values["allow_download"],
                "expires_at": values["expires_at"].isoformat(),
                "created_by": str(values["created_by"]),
            },
        )
        self._raise(response)
        return self._link_from_row(response.json()[0])

    def list_share_links(
        self, *, export_id: UUID, workspace_id: UUID
    ) -> list[ExportShareLinkRecord]:
        self._get_export_for_workspace(export_id=export_id, workspace_id=workspace_id)
        response = self.client.request(
            "GET",
            "/export_share_links",
            params={
                "export_id": f"eq.{export_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": SHARE_LINK_COLUMNS,
                "order": "created_at.desc",
                "limit": "200",
            },
        )
        self._raise(response)
        return [self._link_from_row(row) for row in response.json()]

    def revoke_share_link(
        self, *, share_link_id: UUID, workspace_id: UUID
    ) -> ExportShareLinkRecord:
        now = datetime.now(UTC)
        response = self.client.request(
            "PATCH",
            "/export_share_links",
            params={
                "id": f"eq.{share_link_id}",
                "workspace_id": f"eq.{workspace_id}",
                "revoked_at": "is.null",
                "select": SHARE_LINK_COLUMNS,
            },
            json={"revoked_at": now.isoformat()},
        )
        self._raise(response)
        rows = response.json()
        if rows:
            return self._link_from_row(rows[0])
        # Either already revoked or not ours; fetch to distinguish not-found from
        # an idempotent re-revoke without leaking across the tenant boundary.
        existing = self.client.request(
            "GET",
            "/export_share_links",
            params={
                "id": f"eq.{share_link_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": SHARE_LINK_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(existing)
        existing_rows = existing.json()
        if not existing_rows:
            raise ApiError(404, "export_share_link_not_found", "Share link was not found.")
        return self._link_from_row(existing_rows[0])

    def resolve_share_link(self, *, token: str) -> ResolvedExportShareLink:
        now = datetime.now(UTC)
        response = self.client.request(
            "GET",
            "/export_share_links",
            params={
                "token_digest": f"eq.{token_digest(token)}",
                "revoked_at": "is.null",
                "expires_at": f"gt.{now.isoformat()}",
                "select": SHARE_LINK_COLUMNS,
                "limit": "1",
            },
        )
        self._raise(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "export_share_link_not_found", "Share link was not found.")
        link = self._link_from_row(rows[0])
        export = self.render_repository.get_export(
            export_id=link.export_id, workspace_id=link.workspace_id
        )
        access = self.client.request(
            "PATCH",
            "/export_share_links",
            params={"id": f"eq.{link.id}"},
            json={"last_accessed_at": now.isoformat()},
        )
        self._raise(access)
        return ResolvedExportShareLink(link=link, export=export)

    @staticmethod
    def _link_from_row(row: dict[str, Any]) -> ExportShareLinkRecord:
        return ExportShareLinkRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            export_id=UUID(str(row["export_id"])),
            token_digest=str(row["token_digest"]),
            allow_download=bool(row["allow_download"]),
            expires_at=_datetime(row["expires_at"]),
            revoked_at=_datetime(row["revoked_at"]) if row.get("revoked_at") else None,
            last_accessed_at=(
                _datetime(row["last_accessed_at"]) if row.get("last_accessed_at") else None
            ),
            created_by=UUID(str(row["created_by"])),
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", "The export service could not reach data.")
