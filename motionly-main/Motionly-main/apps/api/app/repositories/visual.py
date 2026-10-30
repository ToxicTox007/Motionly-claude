from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.uploads import InMemoryUploadRepository, UploadRecord
from app.schemas.visual import (
    VISUAL_MANIFEST_SCHEMA_VERSION,
    MatteStatus,
    SegmentationMatte,
    VideoManifest,
    VideoManifestResponse,
    VisualManifestStatus,
)
from app.services.supabase import SupabaseRestClient

VIDEO_MANIFEST_COLUMNS = (
    "id,workspace_id,project_id,uploaded_video_id,version,schema_version,status,provider,model,"
    "cache_key,manifest,error_code,error_message,created_at,updated_at"
)
SEGMENTATION_MATTE_COLUMNS = (
    "id,workspace_id,project_id,uploaded_video_id,status,cache_key,source_file_hash,"
    "segmentation_settings,model_version,matte,error_code,error_message,retry_count,"
    "created_at,updated_at"
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
    cache_key: str | None
    manifest: VideoManifest | None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> VideoManifestResponse:
        return VideoManifestResponse(**self.__dict__)


@dataclass
class MatteRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    status: MatteStatus
    cache_key: str
    source_file_hash: str | None
    segmentation_settings: dict[str, object]
    model_version: str
    matte: SegmentationMatte | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryVisualRepository:
    def __init__(self, upload_repository: InMemoryUploadRepository) -> None:
        self.upload_repository = upload_repository
        self.manifests: dict[UUID, VideoManifestRecord] = {}
        self.mattes: dict[UUID, MatteRecord] = {}

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

    def find_manifest_by_cache_key(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        cache_key: str,
    ) -> VideoManifestRecord | None:
        matches = [
            manifest
            for manifest in self.manifests.values()
            if manifest.workspace_id == workspace_id
            and manifest.project_id == project_id
            and manifest.uploaded_video_id == uploaded_video_id
            and manifest.cache_key == cache_key
        ]
        return (
            sorted(matches, key=lambda manifest: manifest.updated_at, reverse=True)[0]
            if matches
            else None
        )

    def latest_for_upload(
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
        ]
        return (
            sorted(matches, key=lambda manifest: manifest.updated_at, reverse=True)[0]
            if matches
            else None
        )

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
        cache_key: str | None = None,
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
            cache_key=cache_key,
            manifest=manifest,
            error_code=error_code,
            error_message=error_message,
        )
        self.manifests[record.id] = record
        return record

    def transition_manifest(
        self,
        *,
        record: VideoManifestRecord,
        status: VisualManifestStatus,
        manifest: VideoManifest | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VideoManifestRecord:
        record.status = status
        record.manifest = manifest
        record.error_code = error_code
        record.error_message = error_message
        record.updated_at = datetime.now(UTC)
        return record

    def find_matte_by_cache_key(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        cache_key: str,
    ) -> MatteRecord | None:
        matches = [
            matte
            for matte in self.mattes.values()
            if matte.workspace_id == workspace_id
            and matte.project_id == project_id
            and matte.uploaded_video_id == uploaded_video_id
            and matte.cache_key == cache_key
        ]
        return (
            sorted(matches, key=lambda item: item.updated_at, reverse=True)[0]
            if matches
            else None
        )

    def latest_matte_for_upload(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
    ) -> MatteRecord | None:
        matches = [
            matte
            for matte in self.mattes.values()
            if matte.workspace_id == workspace_id
            and matte.project_id == project_id
            and matte.uploaded_video_id == uploaded_video_id
        ]
        return (
            sorted(matches, key=lambda item: item.updated_at, reverse=True)[0]
            if matches
            else None
        )

    def get_matte(
        self,
        *,
        matte_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> MatteRecord | None:
        """The matte row a job was enqueued for, scoped to its tenant."""
        record = self.mattes.get(matte_id)
        if record is None or record.workspace_id != workspace_id or record.project_id != project_id:
            return None
        return record

    def create_matte(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        status: MatteStatus,
        cache_key: str,
        source_file_hash: str | None,
        segmentation_settings: dict[str, object],
        model_version: str,
        matte: SegmentationMatte | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> MatteRecord:
        existing = self.find_matte_by_cache_key(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=uploaded_video_id,
            cache_key=cache_key,
        )
        if existing and existing.status in {
            MatteStatus.PENDING,
            MatteStatus.PROCESSING,
            MatteStatus.COMPLETED,
        }:
            return existing
        if existing and existing.status == MatteStatus.FAILED and status == MatteStatus.PENDING:
            return self.transition_matte(
                record=existing,
                status=MatteStatus.PENDING,
                error_code=None,
                error_message=None,
            )
        record = MatteRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=uploaded_video_id,
            status=status,
            cache_key=cache_key,
            source_file_hash=source_file_hash,
            segmentation_settings=segmentation_settings,
            model_version=model_version,
            matte=matte,
            error_code=error_code,
            error_message=error_message,
        )
        self.mattes[record.id] = record
        return record

    def transition_matte(
        self,
        *,
        record: MatteRecord,
        status: MatteStatus,
        cache_key: str | None = None,
        source_file_hash: str | None = None,
        matte: SegmentationMatte | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        increment_retry: bool = False,
    ) -> MatteRecord:
        record.status = status
        if cache_key is not None:
            record.cache_key = cache_key
        if source_file_hash is not None:
            record.source_file_hash = source_file_hash
        if matte is not None or status != MatteStatus.FAILED:
            record.matte = matte
        record.error_code = error_code
        record.error_message = error_message
        if increment_retry:
            record.retry_count += 1
        record.updated_at = datetime.now(UTC)
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
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__(upload_repository)
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="visual",
            metrics_registry=metrics_registry,
        )

    def latest_manifest(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        response = self.client.request(
            "GET",
            "/video_manifests",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._record_from_row(rows[0]) if rows else None

    async def latest_manifest_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        response = await self.client.async_request(
            "GET",
            "/video_manifests",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
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
        response = self.client.request(
            "GET",
            "/video_manifests",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "status": f"eq.{VisualManifestStatus.READY.value}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._record_from_row(rows[0]) if rows else None

    async def latest_ready_for_upload_async(
        self,
        *,
        uploaded_video_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        response = await self.client.async_request(
            "GET",
            "/video_manifests",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "status": f"eq.{VisualManifestStatus.READY.value}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._record_from_row(rows[0]) if rows else None

    def find_manifest_by_cache_key(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        cache_key: str,
    ) -> VideoManifestRecord | None:
        response = self.client.request(
            "GET",
            "/video_manifests",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "cache_key": f"eq.{cache_key}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._record_from_row(rows[0]) if rows else None

    async def find_manifest_by_cache_key_async(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        cache_key: str,
    ) -> VideoManifestRecord | None:
        response = await self.client.async_request(
            "GET",
            "/video_manifests",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "cache_key": f"eq.{cache_key}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._record_from_row(rows[0]) if rows else None

    def latest_for_upload(
        self,
        *,
        uploaded_video_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        response = self.client.request(
            "GET",
            "/video_manifests",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._record_from_row(rows[0]) if rows else None

    async def latest_for_upload_async(
        self,
        *,
        uploaded_video_id: UUID,
        project_id: UUID,
        workspace_id: UUID,
    ) -> VideoManifestRecord | None:
        response = await self.client.async_request(
            "GET",
            "/video_manifests",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "select": VIDEO_MANIFEST_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
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
        cache_key: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VideoManifestRecord:
        version = self._next_supabase_version(project_id=project_id, workspace_id=workspace_id)
        response = self.client.request(
            "POST",
            "/video_manifests",
            params={"select": VIDEO_MANIFEST_COLUMNS},
            json={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "uploaded_video_id": str(uploaded_video_id),
                "version": version,
                "schema_version": VISUAL_MANIFEST_SCHEMA_VERSION,
                "status": status.value,
                "provider": provider,
                "model": model,
                "cache_key": cache_key,
                "manifest": manifest.model_dump(mode="json") if manifest else None,
                "error_code": error_code,
                "error_message": error_message,
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._record_from_row(response.json()[0])

    async def create_manifest_async(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        status: VisualManifestStatus,
        provider: str,
        model: str,
        manifest: VideoManifest | None,
        cache_key: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VideoManifestRecord:
        version = await self._next_supabase_version_async(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        response = await self.client.async_request(
            "POST",
            "/video_manifests",
            params={"select": VIDEO_MANIFEST_COLUMNS},
            json={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "uploaded_video_id": str(uploaded_video_id),
                "version": version,
                "schema_version": VISUAL_MANIFEST_SCHEMA_VERSION,
                "status": status.value,
                "provider": provider,
                "model": model,
                "cache_key": cache_key,
                "manifest": manifest.model_dump(mode="json") if manifest else None,
                "error_code": error_code,
                "error_message": error_message,
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._record_from_row(response.json()[0])

    def find_matte_by_cache_key(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        cache_key: str,
    ) -> MatteRecord | None:
        response = self.client.request(
            "GET",
            "/segmentation_mattes",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "cache_key": f"eq.{cache_key}",
                "select": SEGMENTATION_MATTE_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._matte_from_row(rows[0]) if rows else None

    async def find_matte_by_cache_key_async(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        cache_key: str,
    ) -> MatteRecord | None:
        response = await self.client.async_request(
            "GET",
            "/segmentation_mattes",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "cache_key": f"eq.{cache_key}",
                "select": SEGMENTATION_MATTE_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._matte_from_row(rows[0]) if rows else None

    def latest_matte_for_upload(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
    ) -> MatteRecord | None:
        response = self.client.request(
            "GET",
            "/segmentation_mattes",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "select": SEGMENTATION_MATTE_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._matte_from_row(rows[0]) if rows else None

    def get_matte(
        self,
        *,
        matte_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> MatteRecord | None:
        """Load the exact matte row a job was enqueued for (audit WJ-08).

        The worker process has no in-memory cache of API-created rows, so the
        id must be resolved against the database, scoped to the job's tenant.
        """
        response = self.client.request(
            "GET",
            "/segmentation_mattes",
            params={
                "id": f"eq.{matte_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": SEGMENTATION_MATTE_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._matte_from_row(rows[0]) if rows else None

    async def latest_matte_for_upload_async(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
    ) -> MatteRecord | None:
        response = await self.client.async_request(
            "GET",
            "/segmentation_mattes",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "uploaded_video_id": f"eq.{uploaded_video_id}",
                "select": SEGMENTATION_MATTE_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._matte_from_row(rows[0]) if rows else None

    def create_matte(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        status: MatteStatus,
        cache_key: str,
        source_file_hash: str | None,
        segmentation_settings: dict[str, object],
        model_version: str,
        matte: SegmentationMatte | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> MatteRecord:
        existing = self.find_matte_by_cache_key(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=uploaded_video_id,
            cache_key=cache_key,
        )
        if existing and existing.status in {
            MatteStatus.PENDING,
            MatteStatus.PROCESSING,
            MatteStatus.COMPLETED,
        }:
            return existing
        if existing and existing.status == MatteStatus.FAILED and status == MatteStatus.PENDING:
            return self.transition_matte(
                record=existing,
                status=MatteStatus.PENDING,
                error_code=None,
                error_message=None,
            )
        response = self.client.request(
            "POST",
            "/segmentation_mattes",
            params={"select": SEGMENTATION_MATTE_COLUMNS},
            json={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "uploaded_video_id": str(uploaded_video_id),
                "status": status.value,
                "cache_key": cache_key,
                "source_file_hash": source_file_hash,
                "segmentation_settings": segmentation_settings,
                "model_version": model_version,
                "matte": matte.model_dump(mode="json") if matte else None,
                "error_code": error_code,
                "error_message": error_message,
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._matte_from_row(response.json()[0])

    async def create_matte_async(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        status: MatteStatus,
        cache_key: str,
        source_file_hash: str | None,
        segmentation_settings: dict[str, object],
        model_version: str,
        matte: SegmentationMatte | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> MatteRecord:
        existing = await self.find_matte_by_cache_key_async(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=uploaded_video_id,
            cache_key=cache_key,
        )
        if existing and existing.status in {
            MatteStatus.PENDING,
            MatteStatus.PROCESSING,
            MatteStatus.COMPLETED,
        }:
            return existing
        if existing and existing.status == MatteStatus.FAILED and status == MatteStatus.PENDING:
            return await self.transition_matte_async(
                record=existing,
                status=MatteStatus.PENDING,
                error_code=None,
                error_message=None,
            )
        response = await self.client.async_request(
            "POST",
            "/segmentation_mattes",
            params={"select": SEGMENTATION_MATTE_COLUMNS},
            json={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "uploaded_video_id": str(uploaded_video_id),
                "status": status.value,
                "cache_key": cache_key,
                "source_file_hash": source_file_hash,
                "segmentation_settings": segmentation_settings,
                "model_version": model_version,
                "matte": matte.model_dump(mode="json") if matte else None,
                "error_code": error_code,
                "error_message": error_message,
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._matte_from_row(response.json()[0])

    def transition_matte(
        self,
        *,
        record: MatteRecord,
        status: MatteStatus,
        cache_key: str | None = None,
        source_file_hash: str | None = None,
        matte: SegmentationMatte | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        increment_retry: bool = False,
    ) -> MatteRecord:
        response = self.client.request(
            "PATCH",
            "/segmentation_mattes",
            params=self._matte_transition_params(record),
            json={
                "status": status.value,
                "cache_key": cache_key or record.cache_key,
                "source_file_hash": source_file_hash or record.source_file_hash,
                "matte": (
                    matte.model_dump(mode="json")
                    if matte
                    else record.matte.model_dump(mode="json")
                    if record.matte
                    else None
                ),
                "error_code": error_code,
                "error_message": error_message,
                "retry_count": record.retry_count + 1 if increment_retry else record.retry_count,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._transitioned_matte(response)

    async def transition_matte_async(
        self,
        *,
        record: MatteRecord,
        status: MatteStatus,
        cache_key: str | None = None,
        source_file_hash: str | None = None,
        matte: SegmentationMatte | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        increment_retry: bool = False,
    ) -> MatteRecord:
        response = await self.client.async_request(
            "PATCH",
            "/segmentation_mattes",
            params=self._matte_transition_params(record),
            json={
                "status": status.value,
                "cache_key": cache_key or record.cache_key,
                "source_file_hash": source_file_hash or record.source_file_hash,
                "matte": (
                    matte.model_dump(mode="json")
                    if matte
                    else record.matte.model_dump(mode="json")
                    if record.matte
                    else None
                ),
                "error_code": error_code,
                "error_message": error_message,
                "retry_count": record.retry_count + 1 if increment_retry else record.retry_count,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._transitioned_matte(response)

    def transition_manifest(
        self,
        *,
        record: VideoManifestRecord,
        status: VisualManifestStatus,
        manifest: VideoManifest | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VideoManifestRecord:
        response = self.client.request(
            "PATCH",
            "/video_manifests",
            params={"id": f"eq.{record.id}", "select": VIDEO_MANIFEST_COLUMNS},
            json={
                "status": status.value,
                "manifest": manifest.model_dump(mode="json") if manifest else None,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._record_from_row(response.json()[0])

    async def transition_manifest_async(
        self,
        *,
        record: VideoManifestRecord,
        status: VisualManifestStatus,
        manifest: VideoManifest | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> VideoManifestRecord:
        response = await self.client.async_request(
            "PATCH",
            "/video_manifests",
            params={"id": f"eq.{record.id}", "select": VIDEO_MANIFEST_COLUMNS},
            json={
                "status": status.value,
                "manifest": manifest.model_dump(mode="json") if manifest else None,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            timeout=20,
        )
        self._raise_for_supabase(response)
        return self._record_from_row(response.json()[0])

    def _next_supabase_version(self, *, project_id: UUID, workspace_id: UUID) -> int:
        latest = self.latest_manifest(project_id=project_id, workspace_id=workspace_id)
        return (latest.version if latest else 0) + 1

    async def _next_supabase_version_async(self, *, project_id: UUID, workspace_id: UUID) -> int:
        latest = await self.latest_manifest_async(project_id=project_id, workspace_id=workspace_id)
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
            cache_key=str(row["cache_key"]) if row.get("cache_key") else None,
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

    @staticmethod
    def _matte_transition_params(record: MatteRecord) -> dict[str, str]:
        # Compare-and-set on the status this caller read (audit WJ-19): a second
        # execution of the same matte job cannot overwrite a transition the
        # first one already made.
        return {
            "id": f"eq.{record.id}",
            "status": f"eq.{record.status.value}",
            "select": SEGMENTATION_MATTE_COLUMNS,
        }

    def _transitioned_matte(self, response) -> MatteRecord:
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise ApiError(
                409,
                "matte_transition_conflict",
                "The matte changed state concurrently; this transition was not applied.",
            )
        return self._matte_from_row(rows[0])

    def _matte_from_row(self, row: dict[str, object]) -> MatteRecord:
        raw_matte = row.get("matte")
        return MatteRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            uploaded_video_id=UUID(str(row["uploaded_video_id"])),
            status=MatteStatus(str(row["status"])),
            cache_key=str(row["cache_key"]),
            source_file_hash=(
                str(row["source_file_hash"]) if row.get("source_file_hash") else None
            ),
            segmentation_settings=row["segmentation_settings"]
            if isinstance(row.get("segmentation_settings"), dict)
            else {},
            model_version=str(row["model_version"]),
            matte=(
                SegmentationMatte.model_validate(raw_matte)
                if isinstance(raw_matte, dict)
                else None
            ),
            error_code=str(row["error_code"]) if row.get("error_code") else None,
            error_message=str(row["error_message"]) if row.get("error_message") else None,
            retry_count=int(row.get("retry_count") or 0),
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
