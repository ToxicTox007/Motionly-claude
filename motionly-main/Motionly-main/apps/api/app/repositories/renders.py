from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.repositories.captions import CaptionTrackRecord, InMemoryCaptionRepository
from app.repositories.uploads import UploadRecord
from app.schemas.renders import (
    CutSegment,
    ExportResponse,
    RenderEngine,
    RenderJobEventResponse,
    RenderJobResponse,
    RenderJobStatus,
    RenderOutputFormat,
    RenderSpecs,
    RenderTemplateId,
)


@dataclass
class RenderJobEventRecord:
    id: UUID
    render_job_id: UUID
    workspace_id: UUID
    project_id: UUID
    event_type: str
    from_status: RenderJobStatus | None
    to_status: RenderJobStatus
    progress_pct: int
    message: str
    error_code: str | None = None
    latency_ms: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> RenderJobEventResponse:
        return RenderJobEventResponse(
            id=self.id,
            render_job_id=self.render_job_id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            event_type=self.event_type,
            from_status=self.from_status,
            to_status=self.to_status,
            progress_pct=self.progress_pct,
            message=self.message,
            error_code=self.error_code,
            latency_ms=self.latency_ms,
            metadata=self.metadata,
            created_at=self.created_at,
        )


@dataclass
class RenderJobRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    caption_track_id: UUID
    status: RenderJobStatus
    planner_version: str
    manifest: dict[str, object]
    keep_segments: list[CutSegment]
    specs: RenderSpecs = field(default_factory=RenderSpecs)
    template_id: RenderTemplateId | None = None
    render_engine: RenderEngine | None = None
    renderer_version: str | None = None
    props_hash: str | None = None
    artifact_path: str | None = None
    benchmark_ms: int | None = None
    logs: list[str] = field(default_factory=list)
    progress_pct: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self, events: list[RenderJobEventRecord] | None = None) -> RenderJobResponse:
        return RenderJobResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            uploaded_video_id=self.uploaded_video_id,
            caption_track_id=self.caption_track_id,
            status=self.status,
            planner_version=self.planner_version,
            manifest=self.manifest,
            keep_segments=self.keep_segments,
            specs=self.specs,
            template_id=self.template_id,
            render_engine=self.render_engine,
            renderer_version=self.renderer_version,
            props_hash=self.props_hash,
            artifact_path=self.artifact_path,
            benchmark_ms=self.benchmark_ms,
            logs=self.logs,
            progress_pct=self.progress_pct,
            error_code=self.error_code,
            error_message=self.error_message,
            created_at=self.created_at,
            updated_at=self.updated_at,
            events=[event.to_response() for event in (events or [])],
        )


@dataclass
class ExportRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    render_job_id: UUID
    version: int
    storage_bucket: str
    storage_path: str
    format: RenderOutputFormat
    width: int
    height: int
    fps: int
    duration_ms: int | None = None
    file_size_bytes: int | None = None
    thumbnail_url: str | None = None
    settings_summary: dict[str, object] = field(default_factory=dict)
    restored_from_export_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_response(self) -> ExportResponse:
        return ExportResponse(
            id=self.id,
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            render_job_id=self.render_job_id,
            version=self.version,
            storage_bucket=self.storage_bucket,
            storage_path=self.storage_path,
            format=self.format,
            width=self.width,
            height=self.height,
            fps=self.fps,
            duration_ms=self.duration_ms,
            file_size_bytes=self.file_size_bytes,
            thumbnail_url=self.thumbnail_url,
            settings_summary=self.settings_summary,
            restored_from_export_id=self.restored_from_export_id,
            created_at=self.created_at,
        )


class InMemoryRenderRepository:
    def __init__(self, caption_repository: InMemoryCaptionRepository) -> None:
        self.caption_repository = caption_repository
        self.render_jobs: dict[UUID, RenderJobRecord] = {}
        self.render_job_events: dict[UUID, RenderJobEventRecord] = {}
        self.exports: dict[UUID, ExportRecord] = {}

    def get_active_upload(self, *, project_id: UUID, workspace_id: UUID) -> UploadRecord:
        return self.caption_repository.get_active_upload(
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def get_caption_track(
        self,
        *,
        project_id: UUID,
        caption_track_id: UUID,
        workspace_id: UUID,
    ) -> CaptionTrackRecord:
        return self.caption_repository.get_caption_track(
            project_id=project_id,
            track_id=caption_track_id,
            workspace_id=workspace_id,
        )

    def latest_caption_track(self, *, project_id: UUID, workspace_id: UUID) -> CaptionTrackRecord:
        tracks = self.caption_repository.list_caption_tracks(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        if not tracks:
            raise ApiError(404, "caption_track_not_found", "Generate captions before rendering.")
        return tracks[0]

    def create_render_job(self, **values: object) -> RenderJobRecord:
        job = RenderJobRecord(id=uuid4(), **values)
        self.render_jobs[job.id] = job
        self.add_event(
            job=job,
            event_type="queued",
            to_status=job.status,
            progress_pct=job.progress_pct,
            message="Render queued.",
        )
        return job

    def add_event(
        self,
        *,
        job: RenderJobRecord,
        event_type: str,
        to_status: RenderJobStatus,
        progress_pct: int,
        message: str,
        from_status: RenderJobStatus | None = None,
        error_code: str | None = None,
        latency_ms: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RenderJobEventRecord:
        event = RenderJobEventRecord(
            id=uuid4(),
            render_job_id=job.id,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            progress_pct=progress_pct,
            message=message,
            error_code=error_code,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )
        self.render_job_events[event.id] = event
        return event

    def transition_job(
        self,
        *,
        job: RenderJobRecord,
        status: RenderJobStatus,
        progress_pct: int,
        message: str,
        event_type: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RenderJobRecord:
        from_status = job.status
        job.status = status
        job.progress_pct = progress_pct
        job.error_code = error_code
        job.error_message = error_message
        job.updated_at = datetime.now(UTC)
        self.add_event(
            job=job,
            event_type=event_type or status.value,
            from_status=from_status,
            to_status=status,
            progress_pct=progress_pct,
            message=message,
            error_code=error_code,
            metadata=metadata,
        )
        return job

    def get_render_job(
        self,
        *,
        project_id: UUID,
        render_job_id: UUID,
        workspace_id: UUID,
    ) -> RenderJobRecord:
        job = self.render_jobs.get(render_job_id)
        if job is None or job.project_id != project_id:
            raise ApiError(404, "render_job_not_found", "Render job was not found.")
        if job.workspace_id != workspace_id:
            raise ApiError(403, "workspace_access_denied", "You do not have access to this render.")
        return job

    def list_render_jobs(self, *, project_id: UUID, workspace_id: UUID) -> list[RenderJobRecord]:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        return sorted(
            [
                job
                for job in self.render_jobs.values()
                if job.project_id == project_id and job.workspace_id == workspace_id
            ],
            key=lambda job: job.created_at,
            reverse=True,
        )

    def list_recent_render_jobs(self, *, limit: int = 25) -> list[RenderJobRecord]:
        return sorted(
            self.render_jobs.values(),
            key=lambda job: job.created_at,
            reverse=True,
        )[:limit]

    def get_render_job_by_id(self, *, render_job_id: UUID) -> RenderJobRecord:
        job = self.render_jobs.get(render_job_id)
        if job is None:
            raise ApiError(404, "render_job_not_found", "Render job was not found.")
        return job

    def retry_render_job(self, *, render_job_id: UUID) -> RenderJobRecord:
        job = self.get_render_job_by_id(render_job_id=render_job_id)
        if job.status not in {RenderJobStatus.FAILED, RenderJobStatus.CANCELED}:
            raise ApiError(
                409,
                "render_not_retryable",
                "Only failed or canceled renders can be retried.",
            )
        return self.transition_job(
            job=job,
            status=RenderJobStatus.QUEUED,
            progress_pct=5,
            message="Render requeued by admin.",
            event_type="admin_retry",
            error_code=None,
            error_message=None,
        )

    def retry_project_render_job(
        self,
        *,
        project_id: UUID,
        render_job_id: UUID,
        workspace_id: UUID,
    ) -> RenderJobRecord:
        job = self.get_render_job(
            project_id=project_id,
            render_job_id=render_job_id,
            workspace_id=workspace_id,
        )
        if job.status not in {RenderJobStatus.FAILED, RenderJobStatus.CANCELED}:
            raise ApiError(
                409,
                "render_not_retryable",
                "Only failed or canceled renders can be retried.",
            )
        return self.transition_job(
            job=job,
            status=RenderJobStatus.QUEUED,
            progress_pct=5,
            message="Render requeued.",
            event_type="retry",
            error_code=None,
            error_message=None,
        )

    def cancel_render_job_by_id(self, *, render_job_id: UUID) -> RenderJobRecord:
        job = self.get_render_job_by_id(render_job_id=render_job_id)
        if job.status in {
            RenderJobStatus.COMPLETE,
            RenderJobStatus.FAILED,
            RenderJobStatus.CANCELED,
        }:
            raise ApiError(409, "render_not_cancelable", "This render can no longer be canceled.")
        return self.transition_job(
            job=job,
            status=RenderJobStatus.CANCELED,
            progress_pct=job.progress_pct,
            message="Render canceled by admin.",
            event_type="admin_cancel",
        )

    def events_for_job(self, render_job_id: UUID) -> list[RenderJobEventRecord]:
        return sorted(
            [
                event
                for event in self.render_job_events.values()
                if event.render_job_id == render_job_id
            ],
            key=lambda event: event.created_at,
        )

    def create_export(self, **values: object) -> ExportRecord:
        version = len(
            [
                export
                for export in self.exports.values()
                if export.project_id == values["project_id"]
                and export.workspace_id == values["workspace_id"]
            ]
        ) + 1
        export = ExportRecord(id=uuid4(), version=version, **values)
        self.exports[export.id] = export
        return export

    def restore_export(
        self,
        *,
        project_id: UUID,
        export_id: UUID,
        workspace_id: UUID,
    ) -> ExportRecord:
        source = self.get_export(export_id=export_id, workspace_id=workspace_id)
        if source.project_id != project_id:
            raise ApiError(404, "export_not_found", "Export was not found for this project.")
        return self.create_export(
            workspace_id=workspace_id,
            project_id=project_id,
            render_job_id=source.render_job_id,
            storage_bucket=source.storage_bucket,
            storage_path=source.storage_path,
            format=source.format,
            width=source.width,
            height=source.height,
            fps=source.fps,
            duration_ms=source.duration_ms,
            file_size_bytes=source.file_size_bytes,
            thumbnail_url=source.thumbnail_url,
            settings_summary={
                **source.settings_summary,
                "restored_from_version": source.version,
            },
            restored_from_export_id=source.id,
        )

    def list_exports(self, *, project_id: UUID, workspace_id: UUID) -> list[ExportRecord]:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        return sorted(
            [
                export
                for export in self.exports.values()
                if export.project_id == project_id and export.workspace_id == workspace_id
            ],
            key=lambda export: export.version,
            reverse=True,
        )

    def get_export(self, *, export_id: UUID, workspace_id: UUID) -> ExportRecord:
        export = self.exports.get(export_id)
        if export is None:
            raise ApiError(404, "export_not_found", "Export was not found.")
        if export.workspace_id != workspace_id:
            raise ApiError(403, "workspace_access_denied", "You do not have access to this export.")
        return export


class SupabaseRenderRepository(InMemoryRenderRepository):
    def __init__(
        self,
        *,
        caption_repository: InMemoryCaptionRepository,
        supabase_url: str,
        service_role_key: str,
    ) -> None:
        super().__init__(caption_repository)
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
            "prefer": "return=representation",
        }

    def create_render_job(self, **values: object) -> RenderJobRecord:
        payload = self._json_safe(values)
        response = httpx.post(
            f"{self.rest_url}/render_jobs",
            headers=self.headers,
            json=payload,
            timeout=10,
        )
        if self._is_missing_column_error(response):
            response = httpx.post(
                f"{self.rest_url}/render_jobs",
                headers=self.headers,
                json=self._compat_render_job_payload(payload),
                timeout=10,
            )
        self._raise_for_supabase(response)
        job = self._job_from_row(response.json()[0])
        self.add_event(
            job=job,
            event_type="queued",
            to_status=job.status,
            progress_pct=job.progress_pct,
            message="Render queued.",
        )
        return job

    def add_event(self, **values: object) -> RenderJobEventRecord:
        job = values.pop("job")
        from_status = values.get("from_status")
        to_status = values.get("to_status")
        event_values = {
            "render_job_id": job.id,
            "workspace_id": job.workspace_id,
            "project_id": job.project_id,
            **values,
            "from_status": self._db_status(from_status) if from_status else None,
            "to_status": self._db_status(to_status),
            "metadata": values.get("metadata") or {},
        }
        response = httpx.post(
            f"{self.rest_url}/render_job_events",
            headers=self.headers,
            json=self._json_safe(event_values),
            timeout=10,
        )
        self._raise_for_supabase(response)
        return self._event_from_row(response.json()[0])

    def transition_job(self, **values: object) -> RenderJobRecord:
        job = values["job"]
        status = values["status"]
        progress_pct = values["progress_pct"]
        payload = {
            "status": status.value,
            "progress_pct": progress_pct,
            "error_code": values.get("error_code"),
            "error_message": values.get("error_message"),
            "artifact_path": getattr(job, "artifact_path", None),
            "benchmark_ms": getattr(job, "benchmark_ms", None),
            "logs": getattr(job, "logs", []),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        response = httpx.patch(
            f"{self.rest_url}/render_jobs",
            headers=self.headers,
            params={"id": f"eq.{job.id}", "select": "*"},
            json=payload,
            timeout=10,
        )
        if self._is_missing_column_error(response):
            response = httpx.patch(
                f"{self.rest_url}/render_jobs",
                headers=self.headers,
                params={"id": f"eq.{job.id}", "select": "*"},
                json=self._compat_render_job_update_payload(job=job, values=payload),
                timeout=10,
            )
        self._raise_for_supabase(response)
        updated = self._job_from_row(response.json()[0])
        self.add_event(
            job=updated,
            event_type=values.get("event_type") or status.value,
            from_status=job.status,
            to_status=status,
            progress_pct=progress_pct,
            message=values["message"],
            error_code=values.get("error_code"),
            metadata=values.get("metadata"),
        )
        return updated

    def create_export(self, **values: object) -> ExportRecord:
        existing = self.list_exports(
            project_id=values["project_id"],
            workspace_id=values["workspace_id"],
        )
        payload = {**self._json_safe(values), "version": len(existing) + 1}
        response = httpx.post(
            f"{self.rest_url}/exports",
            headers=self.headers,
            json=payload,
            timeout=10,
        )
        if self._is_missing_column_error(response):
            response = httpx.post(
                f"{self.rest_url}/exports",
                headers=self.headers,
                json=self._compat_export_payload(payload),
                timeout=10,
            )
        self._raise_for_supabase(response)
        return self._export_from_row(response.json()[0])

    def restore_export(
        self,
        *,
        project_id: UUID,
        export_id: UUID,
        workspace_id: UUID,
    ) -> ExportRecord:
        source = self.get_export(export_id=export_id, workspace_id=workspace_id)
        if source.project_id != project_id:
            raise ApiError(404, "export_not_found", "Export was not found for this project.")
        return self.create_export(
            workspace_id=workspace_id,
            project_id=project_id,
            render_job_id=source.render_job_id,
            storage_bucket=source.storage_bucket,
            storage_path=source.storage_path,
            format=source.format,
            width=source.width,
            height=source.height,
            fps=source.fps,
            duration_ms=source.duration_ms,
            file_size_bytes=source.file_size_bytes,
            thumbnail_url=source.thumbnail_url,
            settings_summary={
                **source.settings_summary,
                "restored_from_version": source.version,
            },
            restored_from_export_id=source.id,
        )

    def list_render_jobs(self, *, project_id: UUID, workspace_id: UUID) -> list[RenderJobRecord]:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        response = httpx.get(
            f"{self.rest_url}/render_jobs",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "created_at.desc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._job_from_row(row) for row in response.json()]

    def get_render_job(
        self,
        *,
        project_id: UUID,
        render_job_id: UUID,
        workspace_id: UUID,
    ) -> RenderJobRecord:
        response = httpx.get(
            f"{self.rest_url}/render_jobs",
            headers=self.headers,
            params={
                "id": f"eq.{render_job_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "render_job_not_found", "Render job was not found.")
        return self._job_from_row(rows[0])

    def list_recent_render_jobs(self, *, limit: int = 25) -> list[RenderJobRecord]:
        response = httpx.get(
            f"{self.rest_url}/render_jobs",
            headers=self.headers,
            params={
                "select": "*",
                "order": "created_at.desc",
                "limit": str(limit),
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._job_from_row(row) for row in response.json()]

    def get_render_job_by_id(self, *, render_job_id: UUID) -> RenderJobRecord:
        response = httpx.get(
            f"{self.rest_url}/render_jobs",
            headers=self.headers,
            params={
                "id": f"eq.{render_job_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "render_job_not_found", "Render job was not found.")
        return self._job_from_row(rows[0])

    def events_for_job(self, render_job_id: UUID) -> list[RenderJobEventRecord]:
        response = httpx.get(
            f"{self.rest_url}/render_job_events",
            headers=self.headers,
            params={
                "render_job_id": f"eq.{render_job_id}",
                "select": "*",
                "order": "created_at.asc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._event_from_row(row) for row in response.json()]

    def list_exports(self, *, project_id: UUID, workspace_id: UUID) -> list[ExportRecord]:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        response = httpx.get(
            f"{self.rest_url}/exports",
            headers=self.headers,
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "version.desc",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        return [self._export_from_row(row) for row in response.json()]

    def get_export(self, *, export_id: UUID, workspace_id: UUID) -> ExportRecord:
        response = httpx.get(
            f"{self.rest_url}/exports",
            headers=self.headers,
            params={
                "id": f"eq.{export_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": "*",
                "limit": "1",
            },
            timeout=10,
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "export_not_found", "Export was not found.")
        return self._export_from_row(rows[0])

    def _job_from_row(self, row: dict[str, object]) -> RenderJobRecord:
        compat = self._compat_from_logs(row.get("logs"))
        manifest = self._dict_value(row.get("manifest")) or self._dict_value(
            compat.get("manifest"),
        )
        keep_segments = self._list_value(row.get("keep_segments")) or self._list_value(
            compat.get("keep_segments"),
        )
        specs = self._dict_value(row.get("specs")) or self._dict_value(compat.get("specs"))
        logs = self._messages_from_logs(row.get("logs"))
        return RenderJobRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            uploaded_video_id=UUID(
                str(row.get("uploaded_video_id") or compat.get("uploaded_video_id") or UUID(int=0)),
            ),
            caption_track_id=UUID(
                str(row.get("caption_track_id") or compat.get("caption_track_id") or UUID(int=0)),
            ),
            status=self._app_status(row.get("status"), progress_pct=compat.get("progress_pct")),
            planner_version=str(
                row.get("planner_version") or compat.get("planner_version") or "unknown",
            ),
            manifest=manifest,
            keep_segments=[
                CutSegment(**segment)
                for segment in keep_segments
                if isinstance(segment, dict)
            ],
            specs=RenderSpecs(**specs) if specs else RenderSpecs(),
            template_id=(
                RenderTemplateId(str(row["template_id"])) if row.get("template_id") else None
            ),
            render_engine=(
                RenderEngine(str(row["render_engine"])) if row.get("render_engine") else None
            ),
            renderer_version=(
                str(row["renderer_version"]) if row.get("renderer_version") else None
            ),
            props_hash=str(row["props_hash"]) if row.get("props_hash") else None,
            artifact_path=str(row["artifact_path"]) if row.get("artifact_path") else None,
            benchmark_ms=row.get("benchmark_ms"),
            logs=logs,
            progress_pct=int(row.get("progress_pct") or compat.get("progress_pct") or 0),
            error_code=str(row["error_code"]) if row.get("error_code") else None,
            error_message=str(row["error_message"]) if row.get("error_message") else None,
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    def _event_from_row(self, row: dict[str, object]) -> RenderJobEventRecord:
        return RenderJobEventRecord(
            id=UUID(str(row["id"])),
            render_job_id=UUID(str(row["render_job_id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            event_type=str(row["event_type"]),
            from_status=(
                self._app_status(row.get("from_status"), progress_pct=row.get("progress_pct"))
                if row.get("from_status")
                else None
            ),
            to_status=self._app_status(row.get("to_status"), progress_pct=row.get("progress_pct")),
            progress_pct=int(row["progress_pct"]),
            message=str(row["message"]),
            error_code=str(row["error_code"]) if row.get("error_code") else None,
            latency_ms=row.get("latency_ms"),
            metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

    def _export_from_row(self, row: dict[str, object]) -> ExportRecord:
        manifest = self._dict_value(row.get("manifest"))
        settings_summary = self._dict_value(row.get("settings_summary"))
        compat = self._dict_value(manifest.get("compat_export"))
        return ExportRecord(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            project_id=UUID(str(row["project_id"])),
            render_job_id=UUID(str(row["render_job_id"])),
            version=int(row["version"]),
            storage_bucket=str(row["storage_bucket"]),
            storage_path=str(row["storage_path"]),
            format=RenderOutputFormat(str(row.get("format") or compat.get("format") or "mp4")),
            width=int(row.get("width") or compat.get("width") or 1080),
            height=int(row.get("height") or compat.get("height") or 1920),
            fps=int(row.get("fps") or compat.get("fps") or 30),
            duration_ms=row.get("duration_ms") or compat.get("duration_ms"),
            file_size_bytes=row.get("file_size_bytes") or compat.get("file_size_bytes"),
            thumbnail_url=str(row["thumbnail_url"]) if row.get("thumbnail_url") else None,
            settings_summary=settings_summary,
            restored_from_export_id=(
                UUID(str(row["restored_from_export_id"]))
                if row.get("restored_from_export_id")
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

    def _compat_render_job_payload(self, payload: dict[str, object]) -> dict[str, object]:
        compat_keys = {
            "uploaded_video_id",
            "caption_track_id",
            "planner_version",
            "manifest",
            "keep_segments",
            "specs",
            "progress_pct",
        }
        return {
            key: value
            for key, value in {
                "workspace_id": payload.get("workspace_id"),
                "project_id": payload.get("project_id"),
                "status": payload.get("status"),
                "template_id": payload.get("template_id"),
                "render_engine": payload.get("render_engine"),
                "renderer_version": payload.get("renderer_version"),
                "props_hash": payload.get("props_hash"),
                "artifact_path": payload.get("artifact_path"),
                "benchmark_ms": payload.get("benchmark_ms"),
                "error_code": payload.get("error_code"),
                "error_message": payload.get("error_message"),
                "logs": {
                    "messages": (
                        payload.get("logs") if isinstance(payload.get("logs"), list) else []
                    ),
                    "compat": {
                        key: payload[key]
                        for key in compat_keys
                        if key in payload and payload[key] is not None
                    },
                },
            }.items()
            if value is not None
        }

    def _compat_render_job_update_payload(
        self,
        *,
        job: RenderJobRecord,
        values: dict[str, object],
    ) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "status": self._db_status(values.get("status")),
                "artifact_path": values.get("artifact_path"),
                "benchmark_ms": values.get("benchmark_ms"),
                "error_code": values.get("error_code"),
                "error_message": values.get("error_message"),
                "updated_at": values.get("updated_at"),
                "logs": {
                    "messages": values.get("logs") if isinstance(values.get("logs"), list) else [],
                    "compat": {
                        "uploaded_video_id": str(job.uploaded_video_id),
                        "caption_track_id": str(job.caption_track_id),
                        "planner_version": job.planner_version,
                        "manifest": job.manifest,
                        "keep_segments": [
                            segment.model_dump(mode="json") for segment in job.keep_segments
                        ],
                        "specs": job.specs.model_dump(mode="json"),
                        "progress_pct": values.get("progress_pct"),
                    },
                },
            }.items()
            if value is not None
        }

    def _compat_export_payload(self, payload: dict[str, object]) -> dict[str, object]:
        compat = {
            key: payload[key]
            for key in {
                "format",
                "width",
                "height",
                "fps",
                "duration_ms",
                "file_size_bytes",
            }
            if key in payload and payload[key] is not None
        }
        manifest = self._dict_value(payload.get("manifest"))
        return {
            key: value
            for key, value in {
                "workspace_id": payload.get("workspace_id"),
                "project_id": payload.get("project_id"),
                "render_job_id": payload.get("render_job_id"),
                "version": payload.get("version"),
                "storage_bucket": payload.get("storage_bucket"),
                "storage_path": payload.get("storage_path"),
                "manifest": {**manifest, "compat_export": compat},
                "thumbnail_url": payload.get("thumbnail_url"),
                "settings_summary": payload.get("settings_summary"),
                "restored_from_export_id": payload.get("restored_from_export_id"),
            }.items()
            if value is not None
        }

    def _json_safe(self, values: dict[str, object]) -> dict[str, object]:
        safe: dict[str, object] = {}
        for key, value in values.items():
            if isinstance(value, UUID):
                safe[key] = str(value)
            elif isinstance(value, list):
                safe[key] = [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in value
                ]
            elif hasattr(value, "value"):
                safe[key] = value.value
            elif hasattr(value, "model_dump"):
                safe[key] = value.model_dump(mode="json")
            else:
                safe[key] = value
        return safe

    def _compat_from_logs(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        compat = value.get("compat")
        return compat if isinstance(compat, dict) else {}

    def _messages_from_logs(self, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, dict) and isinstance(value.get("messages"), list):
            return [str(item) for item in value["messages"]]
        return []

    def _dict_value(self, value: object) -> dict[str, object]:
        return value if isinstance(value, dict) else {}

    def _list_value(self, value: object) -> list[object]:
        return value if isinstance(value, list) else []

    def _is_missing_column_error(self, response: httpx.Response) -> bool:
        if response.status_code != 400:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        return payload.get("code") in {"42703", "PGRST204"}

    def _db_status(self, status: object) -> str:
        status_value = status.value if hasattr(status, "value") else str(status)
        if status_value in {"preparing", "rendering", "uploading"}:
            return "running"
        if status_value == "complete":
            return "succeeded"
        return status_value

    def _app_status(self, status: object, *, progress_pct: object = None) -> RenderJobStatus:
        status_value = str(status)
        if status_value == "running":
            progress = int(progress_pct or 0)
            if progress >= 90:
                return RenderJobStatus.UPLOADING
            if progress >= 70:
                return RenderJobStatus.RENDERING
            return RenderJobStatus.PREPARING
        if status_value == "succeeded":
            return RenderJobStatus.COMPLETE
        return RenderJobStatus(status_value)

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", "Render data could not be saved.")
