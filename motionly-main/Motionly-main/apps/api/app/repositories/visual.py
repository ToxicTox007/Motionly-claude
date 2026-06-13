from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.uploads import InMemoryUploadRepository, UploadRecord
from app.schemas.visual import (
    VISUAL_MANIFEST_SCHEMA_VERSION,
    VideoManifest,
    VideoManifestResponse,
    VisualManifestStatus,
)


@dataclass
class VideoManifestRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    version: int
    schema_version: int
    status: VisualManifestStatus
    provider: str
    model: str
    manifest: VideoManifest | None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> VideoManifestResponse:
        return VideoManifestResponse(**self.__dict__)


class InMemoryVisualRepository:
    def __init__(self, upload_repository: InMemoryUploadRepository) -> None:
        self.upload_repository = upload_repository
        self.manifests: dict[UUID, VideoManifestRecord] = {}

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> None:
        self.upload_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def get_active_upload(self, *, project_id: UUID, workspace_id: UUID) -> UploadRecord:
        project = self.upload_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        if not project.active_video_id:
            raise ApiError(404, "active_video_missing", "Upload a source video first.")
        return self.upload_repository.get_upload_for_project(
            project_id=project_id,
            video_id=project.active_video_id,
            workspace_id=workspace_id,
        )

    def get_upload(self, *, project_id: UUID, video_id: UUID, workspace_id: UUID) -> UploadRecord:
        return self.upload_repository.get_upload_for_project(
            project_id=project_id,
            video_id=video_id,
            workspace_id=workspace_id,
        )

    def latest_manifest(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        matches = [
            manifest
            for manifest in self.manifests.values()
            if manifest.project_id == project_id and manifest.workspace_id == workspace_id
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda manifest: manifest.updated_at, reverse=True)[0]

    def latest_ready_for_upload(
        self,
        *,
        uploaded_video_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        matches = [
            manifest
            for manifest in self.manifests.values()
            if manifest.uploaded_video_id == uploaded_video_id
            and manifest.project_id == project_id
            and manifest.workspace_id == workspace_id
            and manifest.status == VisualManifestStatus.READY
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda manifest: manifest.updated_at, reverse=True)[0]

    def create_manifest(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        status: VisualManifestStatus,
        provider: str,
        model: str,
        manifest: VideoManifest | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VideoManifestRecord:
        version = self._next_version(project_id=project_id, workspace_id=workspace_id)
        record = VideoManifestRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=uploaded_video_id,
            version=version,
            schema_version=VISUAL_MANIFEST_SCHEMA_VERSION,
            status=status,
            provider=provider,
            model=model,
            manifest=manifest,
            error_code=error_code,
            error_message=error_message,
        )
        self.manifests[record.id] = record
        return record

    def _next_version(self, *, project_id: UUID, workspace_id: UUID) -> int:
        versions = [
            manifest.version
            for manifest in self.manifests.values()
            if manifest.project_id == project_id and manifest.workspace_id == workspace_id
        ]
        return max(versions, default=0) + 1


class SupabaseVisualRepository(InMemoryVisualRepository):
    def __init__(
        self,
        *,
        upload_repository: InMemoryUploadRepository,
        supabase_url: str,
        service_role_key: str,
    ) -> None:
        super().__init__(upload_repository)
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def latest_manifest(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        response = httpx.get(
            f"{self.rest_url}/video_manifests",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "updated_at.desc",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._record_from_row(rows[0]) if rows else None

    def latest_ready_for_upload(
        self,
        *,
        uploaded_video_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        response = httpx.get(
            f"{self.rest_url}/video_manifests",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "status": f"eq.{VisualManifestStatus.READY.value}",
                "select": "*",
                "order": "updated_at.desc",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._record_from_row(rows[0]) if rows else None

    def create_manifest(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        status: VisualManifestStatus,
        provider: str,
        model: str,
        manifest: VideoManifest | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VideoManifestRecord:
        version = self._next_supabase_version(project_id=project_id, workspace_id=workspace_id)
        response = httpx.post(
            f"{self.rest_url}/video_manifests",
            headers=self.headers,
            json={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "uploaded_video_id": str(uploaded_video_id),
                "version": version,
                "schema_version": VISUAL_MANIFEST_SCHEMA_VERSION,
                "status": status.value,
                "provider": provider,
                "model": model,
                "manifest": manifest.model_dump(mode="json") if manifest else None,
                "error_code": error_code,
                "error_message": error_message,
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._record_from_row(response.json()[0])

    def _next_supabase_version(self, *, project_id: UUID, workspace_id: UUID) -> int:
        latest = self.latest_manifest(project_id=project_id, workspace_id=workspace_id)
        return (latest.version if latest else 0) + 1

    def _record_from_row(self, row: dict[str, object]) -> VideoManifestRecord:
        raw_manifest = row.get("manifest")
        return VideoManifestRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            uploaded_video_id=UUID(str(row["uploaded_video_id"])),
            version=int(row["version"]),
            schema_version=int(row.get("schema_version") or VISUAL_MANIFEST_SCHEMA_VERSION),
            status=VisualManifestStatus(str(row["status"])),
            provider=str(row["provider"]),
            model=str(row["model"]),
            manifest=(
                VideoManifest.model_validate(raw_manifest)
                if isinstance(raw_manifest, dict)
                else None
            ),
            error_code=str(row["error_code"]) if row.get("error_code") else None,
            error_message=str(row["error_message"]) if row.get("error_message") else None,
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(
            502,
            "supabase_request_failed",
            "The visual manifest repository could not reach Supabase.",
        )
