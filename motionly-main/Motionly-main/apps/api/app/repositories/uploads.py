import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.schemas.uploads import UploadReadResponse, UploadStatus, VideoMetadata
from app.services.supabase import SupabaseRestClient
from app.services.upload_state import assert_upload_transition

UPLOAD_RESPONSE_COLUMNS = (
    "id,project_id,workspace_id,storage_bucket,storage_path,original_filename,mime_type,"
    "file_size_bytes,byte_size,status,media_revision,thumbnail_path,metadata,error_code,error_message,"
    "source_object_reference_id,verified_size_bytes,verified_sha256,workflow_version,"
    "storage_quota_reservation_id,legacy_quota_reservation_id,"
    "legacy_upload_expires_at,created_at,updated_at"
)


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
    media_revision: int = 1
    source_object_reference_id: UUID | None = None
    verified_size_bytes: int | None = None
    verified_sha256: str | None = None
    workflow_version: str | None = None
    storage_quota_reservation_id: UUID | None = None
    legacy_quota_reservation_id: UUID | None = None
    legacy_upload_expires_at: datetime | None = None
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
            media_revision=self.media_revision,
            source_object_reference_id=self.source_object_reference_id,
            verified_size_bytes=self.verified_size_bytes,
            verified_sha256=self.verified_sha256,
            workflow_version=self.workflow_version,
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
        self._admission_lock = asyncio.Lock()

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

    def initialize_upload(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        storage_bucket: str,
        storage_extension: str,
        original_filename: str,
        mime_type: str,
        file_size_bytes: int,
        max_active_uploads: int,
    ) -> UploadRecord:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        if self.count_active_uploads(workspace_id=workspace_id) >= max_active_uploads:
            raise ApiError(
                429,
                "upload_concurrency_limit",
                "Your workspace has too many active uploads. Try again when one finishes.",
            )
        upload_id = uuid4()
        upload = UploadRecord(
            id=upload_id,
            project_id=project_id,
            workspace_id=workspace_id,
            storage_bucket=storage_bucket,
            storage_path=(
                f"workspaces/{workspace_id}/projects/{project_id}/raw/"
                f"{upload_id}.{storage_extension}"
            ),
            original_filename=original_filename,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            status=UploadStatus.UPLOADING,
        )
        self.uploads[upload.id] = upload
        return upload

    async def initialize_upload_async(self, **kwargs) -> UploadRecord:
        async with self._admission_lock:
            return self.initialize_upload(**kwargs)

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

    def get_active_upload(self, *, project_id: UUID, workspace_id: UUID) -> UploadRecord:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        uploads = [
            upload
            for upload in self.uploads.values()
            if upload.project_id == project_id and upload.workspace_id == workspace_id
        ]
        if not uploads:
            raise ApiError(404, "upload_not_found", "Upload was not found.")
        return sorted(uploads, key=lambda upload: upload.updated_at, reverse=True)[0]

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

    def attach_source_reference(
        self,
        *,
        upload: UploadRecord,
        source_object_reference_id: UUID,
    ) -> UploadRecord:
        if (
            upload.source_object_reference_id is not None
            and upload.source_object_reference_id != source_object_reference_id
        ):
            raise ApiError(
                409,
                "source_reference_conflict",
                "Upload source reference changed unexpectedly.",
            )
        upload.source_object_reference_id = source_object_reference_id
        upload.updated_at = datetime.now(UTC)
        return upload

    async def attach_source_reference_async(self, **kwargs) -> UploadRecord:
        return self.attach_source_reference(**kwargs)

    def attach_active_video(self, *, project_id: UUID, video_id: UUID) -> None:
        if project := self.projects.get(project_id):
            project.active_video_id = video_id

    def record_verified_source(
        self,
        *,
        upload: UploadRecord,
        source_object_reference_id: UUID,
        verified_size_bytes: int,
        verified_sha256: str,
    ) -> UploadRecord:
        upload.source_object_reference_id = source_object_reference_id
        upload.verified_size_bytes = verified_size_bytes
        upload.verified_sha256 = verified_sha256
        upload.updated_at = datetime.now(UTC)
        return upload

    async def record_verified_source_async(self, **kwargs) -> UploadRecord:
        return self.record_verified_source(**kwargs)

    def mark_workflow_owned(
        self, *, upload: UploadRecord, workflow_version: str
    ) -> UploadRecord:
        if workflow_version != "first-draft-v2":
            raise ApiError(422, "upload_workflow_version_invalid", "Workflow version is invalid.")
        if upload.workflow_version not in {None, workflow_version}:
            raise ApiError(
                409,
                "upload_workflow_ownership_conflict",
                "Upload workflow ownership conflicts.",
            )
        upload.workflow_version = workflow_version
        upload.updated_at = datetime.now(UTC)
        return upload

    async def mark_workflow_owned_async(self, **kwargs) -> UploadRecord:
        return self.mark_workflow_owned(**kwargs)

    def complete_upload_for_processing(
        self,
        *,
        project_id: UUID,
        video_id: UUID,
        workspace_id: UUID,
        observed_file_size_bytes: int | None = None,
    ) -> UploadRecord:
        upload = self.get_upload_for_project(
            project_id=project_id,
            video_id=video_id,
            workspace_id=workspace_id,
        )
        if observed_file_size_bytes and observed_file_size_bytes != upload.file_size_bytes:
            raise ApiError(
                409,
                "upload_size_mismatch",
                "The uploaded object size did not match preflight.",
            )
        if upload.status == UploadStatus.PROCESSING:
            return upload
        if upload.status == UploadStatus.UPLOADING:
            assert_upload_transition(upload.status, UploadStatus.UPLOADED)
            assert_upload_transition(UploadStatus.UPLOADED, UploadStatus.PROCESSING)
        elif upload.status == UploadStatus.UPLOADED:
            assert_upload_transition(upload.status, UploadStatus.PROCESSING)
        else:
            assert_upload_transition(upload.status, UploadStatus.UPLOADED)
        upload.status = UploadStatus.PROCESSING
        upload.updated_at = datetime.now(UTC)
        self.attach_active_video(project_id=project_id, video_id=video_id)
        return upload


class SupabaseUploadRepository:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        metrics_registry: object | None = None,
    ) -> None:
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="uploads",
            metrics_registry=metrics_registry,
        )

    def get_project_for_workspace(self, *, project_id: UUID, workspace_id: UUID) -> ProjectRecord:
        response = self.client.request(
            "GET",
            "/projects",
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "id,workspace_id,title,active_video_id",
                "limit": "1",
            },
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

    async def get_project_for_workspace_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> ProjectRecord:
        response = await self.client.async_request(
            "GET",
            "/projects",
            params={
                "id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "id,workspace_id,title,active_video_id",
                "limit": "1",
            },
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
        response = self.client.request(
            "GET",
            "/uploaded_videos",
            headers={"prefer": "count=exact"},
            params={
                "workspace_id": f"eq.{workspace_id}",
                "status": "in.(created,uploading,uploaded)",
                "select": "id",
            },
        )
        self._raise_for_supabase(response)
        content_range = response.headers.get("content-range", "0-0/0")
        return int(content_range.rsplit("/", 1)[-1])

    async def count_active_uploads_async(self, *, workspace_id: UUID) -> int:
        response = await self.client.async_request(
            "GET",
            "/uploaded_videos",
            headers={"prefer": "count=exact"},
            params={
                "workspace_id": f"eq.{workspace_id}",
                "status": "in.(created,uploading,uploaded)",
                "select": "id",
            },
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
        response = self.client.request(
            "POST",
            "/uploaded_videos",
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
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def initialize_upload(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        storage_bucket: str,
        storage_extension: str,
        original_filename: str,
        mime_type: str,
        file_size_bytes: int,
        max_active_uploads: int,
    ) -> UploadRecord:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        upload_id = uuid4()
        storage_path = (
            f"workspaces/{workspace_id}/projects/{project_id}/raw/{upload_id}.{storage_extension}"
        )
        response = self.client.request(
            "POST",
            "/rpc/motionly_initialize_upload",
            json={
                "p_upload_id": str(upload_id),
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_storage_bucket": storage_bucket,
                "p_storage_path": storage_path,
                "p_original_filename": original_filename,
                "p_mime_type": mime_type,
                "p_file_size_bytes": file_size_bytes,
                "p_max_active_uploads": max_active_uploads,
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(
                429,
                "upload_concurrency_limit",
                "Your workspace has too many active uploads. Try again when one finishes.",
            )
        return self._upload_from_row(rows[0])

    async def initialize_upload_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        storage_bucket: str,
        storage_extension: str,
        original_filename: str,
        mime_type: str,
        file_size_bytes: int,
        max_active_uploads: int,
    ) -> UploadRecord:
        await self.get_project_for_workspace_async(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        upload_id = uuid4()
        storage_path = (
            f"workspaces/{workspace_id}/projects/{project_id}/raw/{upload_id}.{storage_extension}"
        )
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_initialize_upload",
            json={
                "p_upload_id": str(upload_id),
                "p_workspace_id": str(workspace_id),
                "p_project_id": str(project_id),
                "p_storage_bucket": storage_bucket,
                "p_storage_path": storage_path,
                "p_original_filename": original_filename,
                "p_mime_type": mime_type,
                "p_file_size_bytes": file_size_bytes,
                "p_max_active_uploads": max_active_uploads,
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(
                429,
                "upload_concurrency_limit",
                "Your workspace has too many active uploads. Try again when one finishes.",
            )
        return self._upload_from_row(rows[0])

    def get_upload_for_project(
        self,
        *,
        project_id: UUID,
        video_id: UUID,
        workspace_id: UUID,
    ) -> UploadRecord:
        response = self.client.request(
            "GET",
            "/uploaded_videos",
            params={
                "id": f"eq.{video_id}",
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": UPLOAD_RESPONSE_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "upload_not_found", "Upload was not found.")
        return self._upload_from_row(rows[0])

    async def get_upload_for_project_async(
        self,
        *,
        project_id: UUID,
        video_id: UUID,
        workspace_id: UUID,
    ) -> UploadRecord:
        response = await self.client.async_request(
            "GET",
            "/uploaded_videos",
            params={
                "id": f"eq.{video_id}",
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": UPLOAD_RESPONSE_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "upload_not_found", "Upload was not found.")
        return self._upload_from_row(rows[0])

    def get_active_upload(self, *, project_id: UUID, workspace_id: UUID) -> UploadRecord:
        self.get_project_for_workspace(project_id=project_id, workspace_id=workspace_id)
        response = self.client.request(
            "GET",
            "/uploaded_videos",
            params={
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": UPLOAD_RESPONSE_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "upload_not_found", "Upload was not found.")
        return self._upload_from_row(rows[0])

    async def get_active_upload_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> UploadRecord:
        await self.get_project_for_workspace_async(project_id=project_id, workspace_id=workspace_id)
        response = await self.client.async_request(
            "GET",
            "/uploaded_videos",
            params={
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": UPLOAD_RESPONSE_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "upload_not_found", "Upload was not found.")
        return self._upload_from_row(rows[0])

    def transition_upload(self, *, upload: UploadRecord, status: UploadStatus) -> UploadRecord:
        assert_upload_transition(upload.status, status)
        response = self.client.request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
            json={"status": status, "updated_at": datetime.now(UTC).isoformat()},
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
        response = self.client.request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
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
        response = self.client.request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
            json={
                "status": UploadStatus.FAILED.value,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    async def mark_failed_async(
        self,
        *,
        upload: UploadRecord,
        error_code: str,
        error_message: str,
    ) -> UploadRecord:
        assert_upload_transition(upload.status, UploadStatus.FAILED)
        response = await self.client.async_request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
            json={
                "status": UploadStatus.FAILED.value,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def attach_active_video(self, *, project_id: UUID, video_id: UUID) -> None:
        response = self.client.request(
            "PATCH",
            "/projects",
            params={"id": f"eq.{project_id}"},
            json={"active_video_id": str(video_id), "updated_at": datetime.now(UTC).isoformat()},
        )
        self._raise_for_supabase(response)

    async def attach_active_video_async(self, *, project_id: UUID, video_id: UUID) -> None:
        response = await self.client.async_request(
            "PATCH",
            "/projects",
            params={"id": f"eq.{project_id}"},
            json={"active_video_id": str(video_id), "updated_at": datetime.now(UTC).isoformat()},
        )
        self._raise_for_supabase(response)

    def update_storage_path(self, *, upload: UploadRecord, storage_path: str) -> UploadRecord:
        response = self.client.request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
            json={"storage_path": storage_path, "updated_at": datetime.now(UTC).isoformat()},
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def attach_source_reference(
        self,
        *,
        upload: UploadRecord,
        source_object_reference_id: UUID,
    ) -> UploadRecord:
        response = self.client.request(
            "PATCH",
            "/uploaded_videos",
            params={
                "id": f"eq.{upload.id}",
                "workspace_id": f"eq.{upload.workspace_id}",
                "project_id": f"eq.{upload.project_id}",
                "source_object_reference_id": "is.null",
                "select": UPLOAD_RESPONSE_COLUMNS,
            },
            json={
                "source_object_reference_id": str(source_object_reference_id),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if rows:
            return self._upload_from_row(rows[0])
        current = self.get_upload_for_project(
            project_id=upload.project_id,
            video_id=upload.id,
            workspace_id=upload.workspace_id,
        )
        if current.source_object_reference_id != source_object_reference_id:
            raise ApiError(
                409,
                "source_reference_conflict",
                "Upload source reference changed unexpectedly.",
            )
        return current

    async def attach_source_reference_async(
        self,
        *,
        upload: UploadRecord,
        source_object_reference_id: UUID,
    ) -> UploadRecord:
        response = await self.client.async_request(
            "PATCH",
            "/uploaded_videos",
            params={
                "id": f"eq.{upload.id}",
                "workspace_id": f"eq.{upload.workspace_id}",
                "project_id": f"eq.{upload.project_id}",
                "source_object_reference_id": "is.null",
                "select": UPLOAD_RESPONSE_COLUMNS,
            },
            json={
                "source_object_reference_id": str(source_object_reference_id),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if rows:
            return self._upload_from_row(rows[0])
        current = await self.get_upload_for_project_async(
            project_id=upload.project_id,
            video_id=upload.id,
            workspace_id=upload.workspace_id,
        )
        if current.source_object_reference_id != source_object_reference_id:
            raise ApiError(
                409,
                "source_reference_conflict",
                "Upload source reference changed unexpectedly.",
            )
        return current

    def record_verified_source(
        self,
        *,
        upload: UploadRecord,
        source_object_reference_id: UUID,
        verified_size_bytes: int,
        verified_sha256: str,
    ) -> UploadRecord:
        response = self.client.request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
            json={
                "source_object_reference_id": str(source_object_reference_id),
                "verified_size_bytes": verified_size_bytes,
                "verified_sha256": verified_sha256,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    async def record_verified_source_async(
        self,
        *,
        upload: UploadRecord,
        source_object_reference_id: UUID,
        verified_size_bytes: int,
        verified_sha256: str,
    ) -> UploadRecord:
        response = await self.client.async_request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
            json={
                "source_object_reference_id": str(source_object_reference_id),
                "verified_size_bytes": verified_size_bytes,
                "verified_sha256": verified_sha256,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise_for_supabase(response)
        return self._upload_from_row(response.json()[0])

    def mark_workflow_owned(
        self, *, upload: UploadRecord, workflow_version: str
    ) -> UploadRecord:
        response = self.client.request(
            "PATCH",
            "/uploaded_videos",
            params={
                "id": f"eq.{upload.id}",
                "workspace_id": f"eq.{upload.workspace_id}",
                "project_id": f"eq.{upload.project_id}",
                "or": f"(workflow_version.is.null,workflow_version.eq.{workflow_version})",
                "select": UPLOAD_RESPONSE_COLUMNS,
            },
            json={"workflow_version": workflow_version},
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(
                409,
                "upload_workflow_ownership_conflict",
                "Upload workflow ownership conflicts.",
            )
        return self._upload_from_row(rows[0])

    async def mark_workflow_owned_async(
        self, *, upload: UploadRecord, workflow_version: str
    ) -> UploadRecord:
        response = await self.client.async_request(
            "PATCH",
            "/uploaded_videos",
            params={
                "id": f"eq.{upload.id}",
                "workspace_id": f"eq.{upload.workspace_id}",
                "project_id": f"eq.{upload.project_id}",
                "or": f"(workflow_version.is.null,workflow_version.eq.{workflow_version})",
                "select": UPLOAD_RESPONSE_COLUMNS,
            },
            json={"workflow_version": workflow_version},
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(
                409,
                "upload_workflow_ownership_conflict",
                "Upload workflow ownership conflicts.",
            )
        return self._upload_from_row(rows[0])

    def cancel_legacy_upload_atomic(
        self,
        *,
        upload: UploadRecord,
        reservation_id: UUID,
    ) -> bool:
        response = self.client.request(
            "POST",
            "/rpc/motionly_cancel_legacy_upload",
            json={
                "p_upload_id": str(upload.id),
                "p_workspace_id": str(upload.workspace_id),
                "p_project_id": str(upload.project_id),
                "p_reservation_id": str(reservation_id),
            },
        )
        self._raise_for_supabase(response)
        return response.json() is True

    async def cancel_legacy_upload_atomic_async(self, **kwargs) -> bool:
        upload = kwargs["upload"]
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_cancel_legacy_upload",
            json={
                "p_upload_id": str(upload.id),
                "p_workspace_id": str(upload.workspace_id),
                "p_project_id": str(upload.project_id),
                "p_reservation_id": str(kwargs["reservation_id"]),
            },
        )
        self._raise_for_supabase(response)
        return response.json() is True

    def complete_upload_for_processing(
        self,
        *,
        project_id: UUID,
        video_id: UUID,
        workspace_id: UUID,
        observed_file_size_bytes: int | None = None,
    ) -> UploadRecord:
        upload = self.get_upload_for_project(
            project_id=project_id,
            video_id=video_id,
            workspace_id=workspace_id,
        )
        if observed_file_size_bytes and observed_file_size_bytes != upload.file_size_bytes:
            raise ApiError(
                409,
                "upload_size_mismatch",
                "The uploaded object size did not match preflight.",
            )
        if upload.status == UploadStatus.PROCESSING:
            return upload
        if upload.status == UploadStatus.UPLOADING:
            assert_upload_transition(upload.status, UploadStatus.UPLOADED)
            assert_upload_transition(UploadStatus.UPLOADED, UploadStatus.PROCESSING)
        elif upload.status == UploadStatus.UPLOADED:
            assert_upload_transition(upload.status, UploadStatus.PROCESSING)
        else:
            assert_upload_transition(upload.status, UploadStatus.UPLOADED)
        response = self.client.request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
            json={
                "status": UploadStatus.PROCESSING.value,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise_for_supabase(response)
        updated = self._upload_from_row(response.json()[0])
        self.attach_active_video(project_id=project_id, video_id=video_id)
        return updated

    async def complete_upload_for_processing_async(
        self,
        *,
        project_id: UUID,
        video_id: UUID,
        workspace_id: UUID,
        observed_file_size_bytes: int | None = None,
    ) -> UploadRecord:
        upload = await self.get_upload_for_project_async(
            project_id=project_id,
            video_id=video_id,
            workspace_id=workspace_id,
        )
        if observed_file_size_bytes and observed_file_size_bytes != upload.file_size_bytes:
            raise ApiError(
                409,
                "upload_size_mismatch",
                "The uploaded object size did not match preflight.",
            )
        if upload.status == UploadStatus.PROCESSING:
            return upload
        if upload.status == UploadStatus.UPLOADING:
            assert_upload_transition(upload.status, UploadStatus.UPLOADED)
            assert_upload_transition(UploadStatus.UPLOADED, UploadStatus.PROCESSING)
        elif upload.status == UploadStatus.UPLOADED:
            assert_upload_transition(upload.status, UploadStatus.PROCESSING)
        else:
            assert_upload_transition(upload.status, UploadStatus.UPLOADED)
        response = await self.client.async_request(
            "PATCH",
            "/uploaded_videos",
            params={"id": f"eq.{upload.id}", "select": UPLOAD_RESPONSE_COLUMNS},
            json={
                "status": UploadStatus.PROCESSING.value,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        self._raise_for_supabase(response)
        updated = self._upload_from_row(response.json()[0])
        await self.attach_active_video_async(project_id=project_id, video_id=video_id)
        return updated

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
            media_revision=int(row.get("media_revision") or 1),
            source_object_reference_id=(
                UUID(str(row["source_object_reference_id"]))
                if row.get("source_object_reference_id")
                else None
            ),
            verified_size_bytes=(
                int(row["verified_size_bytes"])
                if row.get("verified_size_bytes") is not None
                else None
            ),
            verified_sha256=(str(row["verified_sha256"]) if row.get("verified_sha256") else None),
            workflow_version=(
                str(row["workflow_version"]) if row.get("workflow_version") else None
            ),
            storage_quota_reservation_id=(
                UUID(str(row["storage_quota_reservation_id"]))
                if row.get("storage_quota_reservation_id")
                else None
            ),
            legacy_quota_reservation_id=(
                UUID(str(row["legacy_quota_reservation_id"]))
                if row.get("legacy_quota_reservation_id")
                else None
            ),
            legacy_upload_expires_at=(
                datetime.fromisoformat(str(row["legacy_upload_expires_at"]).replace("Z", "+00:00"))
                if row.get("legacy_upload_expires_at")
                else None
            ),
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
