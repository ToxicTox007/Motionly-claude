from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.uploads import UploadReadResponse, UploadStatus, VideoMetadata
from app.services.upload_state import assert_upload_transition


@dataclass
class ProjectRecord:
    id: UUID
    workspace_id: UUID
    title: str
    active_video_id: UUID | None = None


@dataclass
class UploadRecord:
    id: UUID
    project_id: UUID
    workspace_id: UUID
    storage_bucket: str
    storage_path: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    status: UploadStatus = UploadStatus.CREATED
    thumbnail_path: str | None = None
    metadata: VideoMetadata = field(default_factory=VideoMetadata)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> UploadReadResponse:
        return UploadReadResponse(
            id=self.id,
            project_id=self.project_id,
            workspace_id=self.workspace_id,
            status=self.status,
            storage_bucket=self.storage_bucket,
            storage_path=self.storage_path,
            original_filename=self.original_filename,
            mime_type=self.mime_type,
            file_size_bytes=self.file_size_bytes,
            thumbnail_path=self.thumbnail_path,
            metadata=self.metadata,
            error_code=self.error_code,
            error_message=self.error_message,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class InMemoryUploadRepository:
    def __init__(self) -> None:
        self.projects: dict[UUID, ProjectRecord] = {}
        self.uploads: dict[UUID, UploadRecord] = {}

    def seed_project(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        title: str = "Untitled reel",
    ) -> None:
        self.projects[project_id] = ProjectRecord(
            id=project_id,
            workspace_id=workspace_id,
            title=title,
        )

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> ProjectRecord:
        project = self.projects.get(project_id)
        if project is None:
            raise ApiError(404, "project_not_found", "Project was not found.")
        if project.workspace_id != workspace_id:
            raise ApiError(
                403,
                "workspace_access_denied",
                "You do not have access to this project.",
            )
        return project

    def count_active_uploads(self, *, workspace_id: UUID) -> int:
        active_statuses = {UploadStatus.CREATED, UploadStatus.UPLOADING, UploadStatus.UPLOADED}
        return sum(
            1
            for upload in self.uploads.values()
            if upload.workspace_id == workspace_id and upload.status in active_statuses
        )

    def create_upload(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        storage_bucket: str,
        storage_path: str,
        original_filename: str,
        mime_type: str,
        file_size_bytes: int,
    ) -> UploadRecord:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        upload = UploadRecord(
            id=uuid4(),
            project_id=project_id,
            workspace_id=workspace_id,
            storage_bucket=storage_bucket,
            storage_path=storage_path,
            original_filename=original_filename,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
        )
        self.uploads[upload.id] = upload
        return upload

    def get_upload_for_project(
        self,
        *,
        project_id: UUID,
        video_id: UUID,
        workspace_id: UUID,
    ) -> UploadRecord:
        upload = self.uploads.get(video_id)
        if upload is None or upload.project_id != project_id:
            raise ApiError(404, "upload_not_found", "Upload was not found.")
        if upload.workspace_id != workspace_id:
            raise ApiError(403, "workspace_access_denied", "You do not have access to this upload.")
        return upload

    def transition_upload(self, *, upload: UploadRecord, status: UploadStatus) -> UploadRecord:
        assert_upload_transition(upload.status, status)
        upload.status = status
        upload.updated_at = datetime.now(UTC)
        return upload

    def mark_processed(
        self,
        *,
        upload: UploadRecord,
        metadata: VideoMetadata,
        thumbnail_path: str | None,
    ) -> UploadRecord:
        assert_upload_transition(upload.status, UploadStatus.READY)
        upload.status = UploadStatus.READY
        upload.metadata = metadata
        upload.thumbnail_path = thumbnail_path
        upload.error_code = None
        upload.error_message = None
        upload.updated_at = datetime.now(UTC)
        return upload

    def mark_failed(
        self,
        *,
        upload: UploadRecord,
        error_code: str,
        error_message: str,
    ) -> UploadRecord:
        assert_upload_transition(upload.status, UploadStatus.FAILED)
        upload.status = UploadStatus.FAILED
        upload.error_code = error_code
        upload.error_message = error_message
        upload.updated_at = datetime.now(UTC)
        return upload

    def attach_active_video(self, *, project_id: UUID, video_id: UUID) -> None:
        if project := self.projects.get(project_id):
            project.active_video_id = video_id


class SupabaseUploadRepository:
    def __init__(self, *, supabase_url: str, service_role_key: str) -> None:
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> ProjectRecord:
        response = httpx.get(
            f"{self.rest_url}/projects",
            headers=self.headers,
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "id,workspace_id,title,active_video_id",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "project_not_found", "Project was not found.")
        row = rows[0]
        return ProjectRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            title=row["title"],
            active_video_id=UUID(row["active_video_id"]) if row.get("active_video_id") else None,
        )

    def count_active_uploads(self, *, workspace_id: UUID) -> int:
        response = httpx.get(
            f"{self.rest_url}/uploaded_videos",
            headers={**self.headers, "prefer": "count=exact"},
            params={
                "workspace_id": f"eq.{workspace_id}",
                "status": "in.(created,uploading,uploaded)",
                "select": "id",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        content_range = response.headers.get("content-range", "0-0/0")
        return int(content_range.rsplit("/", 1)[-1])

    def create_upload(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        storage_bucket: str,
        storage_path: str,
        original_filename: str,
        mime_type: str,
        file_size_bytes: int,
    ) -> UploadRecord:
        response = httpx.post(
            f"{self.rest_url}/uploaded_videos",
            headers=self.headers,
            json={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "storage_bucket": storage_bucket,
                "storage_path": storage_path,
                "original_filename": original_filename,
                "mime_type": mime_type,
                "byte_size": file_size_bytes,
                "file_size_bytes": file_size_bytes,
                "status": UploadStatus.CREATED,
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def get_upload_for_project(
        self,
        *,
        project_id: UUID,
        video_id: UUID,
        workspace_id: UUID,
    ) -> UploadRecord:
        response = httpx.get(
            f"{self.rest_url}/uploaded_videos",
            headers=self.headers,
            params={
                "id": f"eq.{video_id}",
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "upload_not_found", "Upload was not found.")
        return self._upload_from_row(rows[0])

    def transition_upload(self, *, upload: UploadRecord, status: UploadStatus) -> UploadRecord:
        assert_upload_transition(upload.status, status)
        response = httpx.patch(
            f"{self.rest_url}/uploaded_videos",
            headers=self.headers,
            params={"id": f"eq.{upload.id}", "select": "*"},
            json={"status": status, "updated_at": datetime.now(UTC).isoformat()},
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def mark_processed(
        self,
        *,
        upload: UploadRecord,
        metadata: VideoMetadata,
        thumbnail_path: str | None,
    ) -> UploadRecord:
        assert_upload_transition(upload.status, UploadStatus.READY)
        response = httpx.patch(
            f"{self.rest_url}/uploaded_videos",
            headers=self.headers,
            params={"id": f"eq.{upload.id}", "select": "*"},
            json={
                "status": UploadStatus.READY.value,
                "metadata": metadata.model_dump(mode="json"),
                "duration_ms": metadata.duration_ms,
                "width": metadata.width,
                "height": metadata.height,
                "thumbnail_path": thumbnail_path,
                "error_code": None,
                "error_message": None,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def mark_failed(
        self,
        *,
        upload: UploadRecord,
        error_code: str,
        error_message: str,
    ) -> UploadRecord:
        assert_upload_transition(upload.status, UploadStatus.FAILED)
        response = httpx.patch(
            f"{self.rest_url}/uploaded_videos",
            headers=self.headers,
            params={"id": f"eq.{upload.id}", "select": "*"},
            json={
                "status": UploadStatus.FAILED.value,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def attach_active_video(self, *, project_id: UUID, video_id: UUID) -> None:
        response = httpx.patch(
            f"{self.rest_url}/projects",
            headers=self.headers,
            params={"id": f"eq.{project_id}"},
            json={"active_video_id": str(video_id), "updated_at": datetime.now(UTC).isoformat()},
            timeout=10,
        )
        self._raise_for_supabase(response)

    def update_storage_path(self, *, upload: UploadRecord, storage_path: str) -> UploadRecord:
        response = httpx.patch(
            f"{self.rest_url}/uploaded_videos",
            headers=self.headers,
            params={"id": f"eq.{upload.id}", "select": "*"},
            json={"storage_path": storage_path, "updated_at": datetime.now(UTC).isoformat()},
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def _upload_from_row(self, row: dict[str, object]) -> UploadRecord:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return UploadRecord(
            id=UUID(str(row["id"])),
            project_id=UUID(str(row["project_id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            storage_bucket=str(row["storage_bucket"]),
            storage_path=str(row["storage_path"]),
            original_filename=str(row["original_filename"]),
            mime_type=str(row["mime_type"]),
            file_size_bytes=int(row.get("file_size_bytes") or row.get("byte_size") or 0),
            status=UploadStatus(str(row.get("status", UploadStatus.CREATED))),
            thumbnail_path=str(row["thumbnail_path"]) if row.get("thumbnail_path") else None,
            metadata=VideoMetadata.model_validate(metadata),
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
            "The upload service could not reach storage data.",
        )
