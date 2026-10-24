from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
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
from app.services.supabase import SupabaseRestClient

RENDER_JOB_COLUMNS = (
    "id,workspace_id,project_id,uploaded_video_id,caption_track_id,workflow_run_id,edit_plan_id,"
    "source_plan_revision,timeline_revision_id,source_media_revision,status,planner_version,"
    "manifest,keep_segments,specs,template_id,render_engine,renderer_version,props_hash,"
    "artifact_path,benchmark_ms,logs,progress_pct,error_code,error_message,created_at,updated_at"
)
RENDER_JOB_EVENT_COLUMNS = (
    "id,render_job_id,workspace_id,project_id,event_type,from_status,to_status,progress_pct,"
    "message,error_code,latency_ms,metadata,created_at"
)
EXPORT_COLUMNS = (
    "id,workspace_id,project_id,render_job_id,workflow_run_id,source_plan_revision,version,"
    "storage_bucket,storage_path,format,width,height,fps,duration_ms,file_size_bytes,thumbnail_url,"
    "settings_summary,restored_from_export_id,created_at"
)
# Terminal states that give a render's monthly slot back (nothing was delivered).
RENDER_SLOT_RELEASED_STATUSES = frozenset({RenderJobStatus.FAILED, RenderJobStatus.CANCELED})
# The same states as stored in render_jobs.status (the enum has no legacy aliases
# for these two).
_RENDER_SLOT_RELEASED_DB_STATUSES = "(failed,canceled)"
# A render in one of these states is finished; nothing may move it again
# except an explicit retry of a failed/canceled render.
RENDER_TERMINAL_STATUSES = frozenset(
    {RenderJobStatus.COMPLETE, RenderJobStatus.FAILED, RenderJobStatus.CANCELED}
)
# Terminal states as stored, including the legacy 'succeeded' alias of complete.
_RENDER_TERMINAL_DB_STATUSES = "(complete,succeeded,failed,canceled)"


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
    caption_track_id: UUID | None
    status: RenderJobStatus
    planner_version: str
    manifest: dict[str, object]
    keep_segments: list[CutSegment]
    workflow_run_id: UUID | None = None
    edit_plan_id: UUID | None = None
    source_plan_revision: int | None = None
    timeline_revision_id: UUID | None = None
    source_media_revision: int | None = None
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
            workflow_run_id=self.workflow_run_id,
            edit_plan_id=self.edit_plan_id,
            source_plan_revision=self.source_plan_revision,
            timeline_revision_id=self.timeline_revision_id,
            source_media_revision=self.source_media_revision,
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
    workflow_run_id: UUID | None = None
    source_plan_revision: int | None = None
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
            workflow_run_id=self.workflow_run_id,
            source_plan_revision=self.source_plan_revision,
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
        self.workflow_repository: object | None = None
        self._lock = RLock()

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

    def get_workflow_draft_render(
        self,
        *,
        workflow_run_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> RenderJobRecord | None:
        return next(
            (
                job
                for job in self.render_jobs.values()
                if job.workflow_run_id == workflow_run_id
                and job.workspace_id == workspace_id
                and job.project_id == project_id
            ),
            None,
        )

    def ensure_workflow_draft_render(
        self,
        *,
        workflow_repository: object,
        **values: object,
    ) -> RenderJobRecord:
        workflow_run_id = values.get("workflow_run_id")
        workspace_id = values.get("workspace_id")
        project_id = values.get("project_id")
        if not isinstance(workflow_run_id, UUID):
            raise ApiError(422, "workflow_render_lineage_invalid", "Workflow render ID is missing.")
        if not isinstance(workspace_id, UUID) or not isinstance(project_id, UUID):
            raise ApiError(
                422,
                "workflow_render_scope_invalid",
                "Workflow render scope is missing.",
            )
        workflow_lock = getattr(workflow_repository, "_lock", self._lock)
        workflow_repository.render_repository = self  # type: ignore[attr-defined]
        self.workflow_repository = workflow_repository
        with workflow_lock, self._lock:
            existing = self.get_workflow_draft_render(
                workflow_run_id=workflow_run_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if existing is not None:
                expected = (
                    values.get("edit_plan_id"),
                    values.get("source_plan_revision"),
                    values.get("timeline_revision_id"),
                    values.get("source_media_revision"),
                    values.get("props_hash"),
                    values.get("render_engine"),
                )
                actual = (
                    existing.edit_plan_id,
                    existing.source_plan_revision,
                    existing.timeline_revision_id,
                    existing.source_media_revision,
                    existing.props_hash,
                    existing.render_engine,
                )
                if actual != expected:
                    raise ApiError(
                        409,
                        "workflow_render_identity_conflict",
                        "Canonical draft render identity conflicts with the existing target.",
                    )
                return existing
            bind = getattr(workflow_repository, "bind_draft_render", None)
            if bind is None:
                raise ApiError(
                    500,
                    "workflow_render_repository_invalid",
                    "Workflow render persistence is unavailable.",
                )
            render_id = uuid4()
            bind(
                workspace_id=workspace_id,
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                render_job_id=render_id,
                edit_plan_id=values.get("edit_plan_id"),
                source_plan_revision=values.get("source_plan_revision"),
                timeline_revision_id=values.get("timeline_revision_id"),
                source_media_revision=values.get("source_media_revision"),
            )
            job = RenderJobRecord(id=render_id, **values)
            self.render_jobs[job.id] = job
            self.add_event(
                job=job,
                event_type="queued",
                to_status=job.status,
                progress_pct=job.progress_pct,
                message="Workflow draft render queued.",
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
        require_active: bool = False,
    ) -> RenderJobRecord:
        """Move ``job`` to ``status`` and record the event.

        ``require_active=True`` makes this a conditional write: it only applies
        while the stored job is still queued or running, and raises 409
        ``render_not_cancelable`` otherwise (e.g. the worker completed it
        concurrently). Callers that race the worker, like user cancel, use it.
        """
        if require_active:
            with self._lock:
                stored = self.render_jobs.get(job.id, job)
                if stored.status in RENDER_TERMINAL_STATUSES:
                    raise ApiError(
                        409,
                        "render_not_cancelable",
                        "This render can no longer be canceled.",
                    )
                return self.transition_job(
                    job=stored,
                    status=status,
                    progress_pct=progress_pct,
                    message=message,
                    event_type=event_type,
                    error_code=error_code,
                    error_message=error_message,
                    metadata=metadata,
                )
        if status == RenderJobStatus.COMPLETE and job.workflow_run_id is not None:
            workflow_repository = self.workflow_repository
            get_workflow = getattr(workflow_repository, "get_workflow", None)
            run = (
                get_workflow(
                    workflow_run_id=job.workflow_run_id,
                    workspace_id=job.workspace_id,
                    project_id=job.project_id,
                )
                if get_workflow is not None
                else None
            )
            base_exports = [
                exported
                for exported in self.exports.values()
                if exported.render_job_id == job.id
                and exported.workflow_run_id == job.workflow_run_id
                and exported.source_plan_revision == job.source_plan_revision
                and exported.restored_from_export_id is None
            ]
            if getattr(getattr(run, "state", None), "value", None) != "draft_rendering":
                raise ApiError(
                    409,
                    "workflow_render_run_not_active",
                    "Workflow render completion is no longer active.",
                )
            if len(base_exports) != 1:
                raise ApiError(
                    409,
                    "workflow_render_export_missing",
                    "Workflow render completion requires one canonical export.",
                )
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

    def list_render_jobs(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[RenderJobRecord]:
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
        )[:limit]

    def list_inflight_duplicate_jobs(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        props_hash: str,
        limit: int = 10,
    ) -> list[RenderJobRecord]:
        """Queued or running (non-workflow) renders of exactly these render props.

        Oldest first by ``(created_at, id)``, so concurrent requests agree on
        which of several identical renders is the one to keep.
        """
        with self._lock:
            jobs = [
                job
                for job in self.render_jobs.values()
                if job.workspace_id == workspace_id
                and job.project_id == project_id
                and job.props_hash == props_hash
                and job.workflow_run_id is None
                and job.status not in RENDER_TERMINAL_STATUSES
            ]
        jobs.sort(key=lambda job: (job.created_at, str(job.id)))
        return jobs[:limit]

    def list_render_slot_jobs(
        self,
        *,
        workspace_id: UUID,
        period_start: datetime,
        period_end: datetime,
        limit: int,
    ) -> list[tuple[UUID, RenderJobStatus]]:
        """Renders created in the period that still hold a monthly render slot.

        Every render that has not failed or been canceled holds a slot from the
        moment it is created, whichever engine completes it. Oldest first by
        ``(created_at, id)`` so concurrent requests agree on who came first.
        """
        with self._lock:
            jobs = [
                job
                for job in self.render_jobs.values()
                if job.workspace_id == workspace_id
                and period_start <= job.created_at < period_end
                and job.status not in RENDER_SLOT_RELEASED_STATUSES
            ]
        jobs.sort(key=lambda job: (job.created_at, str(job.id)))
        return [(job.id, job.status) for job in jobs[:limit]]

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
        with self._lock:
            job = self.get_render_job_by_id(render_job_id=render_job_id)
            if job.status in {
                RenderJobStatus.COMPLETE,
                RenderJobStatus.FAILED,
                RenderJobStatus.CANCELED,
            }:
                raise ApiError(
                    409, "render_not_cancelable", "This render can no longer be canceled."
                )
            return self.transition_job(
                job=job,
                status=RenderJobStatus.CANCELED,
                progress_pct=job.progress_pct,
                message="Render canceled by admin.",
                event_type="admin_cancel",
                # The in-process lock does not cover other API processes or the
                # worker; the conditional write does (Supabase inherits this).
                require_active=True,
            )

    def events_for_job(
        self,
        render_job_id: UUID,
        *,
        limit: int = 100,
    ) -> list[RenderJobEventRecord]:
        return sorted(
            [
                event
                for event in self.render_job_events.values()
                if event.render_job_id == render_job_id
            ],
            key=lambda event: event.created_at,
        )[-limit:]

    def create_export(self, **values: object) -> ExportRecord:
        render_job_id = values.get("render_job_id")
        render = self.render_jobs.get(render_job_id) if isinstance(render_job_id, UUID) else None
        if render is not None:
            if render.workspace_id != values.get("workspace_id") or render.project_id != values.get(
                "project_id"
            ):
                raise ApiError(
                    409,
                    "export_render_lineage_invalid",
                    "Export render scope conflicts.",
                )
            requested_run_id = values.get("workflow_run_id", render.workflow_run_id)
            if requested_run_id != render.workflow_run_id:
                raise ApiError(
                    409,
                    "export_workflow_lineage_invalid",
                    "Export workflow lineage conflicts with its render.",
                )
            values["workflow_run_id"] = render.workflow_run_id
            values["source_plan_revision"] = render.source_plan_revision
            if render.workflow_run_id is not None and values.get("restored_from_export_id") is None:
                workflow_repository = self.workflow_repository
                get_workflow = getattr(workflow_repository, "get_workflow", None)
                run = (
                    get_workflow(
                        workflow_run_id=render.workflow_run_id,
                        workspace_id=render.workspace_id,
                        project_id=render.project_id,
                    )
                    if get_workflow is not None
                    else None
                )
                if getattr(getattr(run, "state", None), "value", None) != "draft_rendering":
                    raise ApiError(
                        409,
                        "workflow_draft_export_run_not_active",
                        "Workflow draft export persistence is no longer active.",
                    )
        else:
            values.setdefault("workflow_run_id", None)
            values.setdefault("source_plan_revision", None)
        if values.get("restored_from_export_id") is None:
            existing = next(
                (
                    export
                    for export in self.exports.values()
                    if export.render_job_id == values["render_job_id"]
                    and export.project_id == values["project_id"]
                    and export.workspace_id == values["workspace_id"]
                    and export.restored_from_export_id is None
                ),
                None,
            )
            if existing is not None:
                existing.storage_bucket = values["storage_bucket"]
                existing.storage_path = values["storage_path"]
                existing.format = values["format"]
                existing.width = values["width"]
                existing.height = values["height"]
                existing.fps = values["fps"]
                existing.duration_ms = values.get("duration_ms")
                existing.file_size_bytes = values.get("file_size_bytes")
                existing.settings_summary = values.get(
                    "settings_summary",
                    existing.settings_summary,
                )
                return existing
        version = (
            len(
                [
                    export
                    for export in self.exports.values()
                    if export.project_id == values["project_id"]
                    and export.workspace_id == values["workspace_id"]
                ]
            )
            + 1
        )
        export = ExportRecord(id=uuid4(), version=version, **values)
        self.exports[export.id] = export
        return export

    def complete_render(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        render_job_id: UUID,
        storage_bucket: str,
        storage_path: str,
        format: RenderOutputFormat | str,
        width: int,
        height: int,
        fps: int,
        duration_ms: int | None,
        file_size_bytes: int,
        thumbnail_url: str | None = None,
        settings_summary: dict[str, object] | None = None,
        benchmark_ms: int,
    ) -> tuple[RenderJobRecord, ExportRecord]:
        """Atomically publish a rendered artifact or return its exact prior result.

        Supabase delegates this compare-and-set to ``motionly_complete_render``;
        the in-memory branch mirrors terminal, specs, canonical-export, and
        exact-replay behavior for local execution and API tests.
        """
        format_value = format.value if isinstance(format, RenderOutputFormat) else str(format)
        summary = settings_summary or {}
        client = getattr(self, "client", None)
        if client is not None:
            response = client.request(
                "POST",
                "/rpc/motionly_complete_render",
                json={
                    "p_workspace_id": str(workspace_id),
                    "p_project_id": str(project_id),
                    "p_render_job_id": str(render_job_id),
                    "p_storage_bucket": storage_bucket,
                    "p_storage_path": storage_path,
                    "p_format": format_value,
                    "p_width": width,
                    "p_height": height,
                    "p_fps": fps,
                    "p_duration_ms": duration_ms,
                    "p_file_size_bytes": file_size_bytes,
                    "p_thumbnail_url": thumbnail_url,
                    "p_settings_summary": self._json_safe(summary),
                    "p_benchmark_ms": benchmark_ms,
                },
            )
            self._raise_for_supabase(response)
            rows = response.json()
            if not isinstance(rows, list) or len(rows) != 1 or not rows[0].get("export_id"):
                raise ApiError(
                    502,
                    "render_completion_response_invalid",
                    "Render completion returned no canonical export.",
                )
            completed = self.get_render_job(
                project_id=project_id,
                render_job_id=render_job_id,
                workspace_id=workspace_id,
            )
            exported = self.get_export(
                export_id=UUID(str(rows[0]["export_id"])),
                workspace_id=workspace_id,
            )
            return completed, exported

        with self._lock:
            job = self.get_render_job(
                project_id=project_id,
                render_job_id=render_job_id,
                workspace_id=workspace_id,
            )
            if (width, height, fps, format_value) != (
                job.specs.width,
                job.specs.height,
                job.specs.fps,
                job.specs.format.value,
            ):
                raise ApiError(
                    409,
                    "render_output_specs_mismatch",
                    "Rendered output does not match the requested output specifications.",
                )
            canonical = next(
                (
                    export
                    for export in self.exports.values()
                    if export.render_job_id == job.id
                    and export.workspace_id == workspace_id
                    and export.project_id == project_id
                    and export.restored_from_export_id is None
                ),
                None,
            )
            expected = (
                storage_bucket,
                storage_path,
                format_value,
                width,
                height,
                fps,
                duration_ms,
                file_size_bytes,
                thumbnail_url,
                summary,
                benchmark_ms,
            )
            if job.status == RenderJobStatus.COMPLETE:
                actual = (
                    canonical.storage_bucket if canonical else None,
                    canonical.storage_path if canonical else None,
                    canonical.format.value if canonical else None,
                    canonical.width if canonical else None,
                    canonical.height if canonical else None,
                    canonical.fps if canonical else None,
                    canonical.duration_ms if canonical else None,
                    canonical.file_size_bytes if canonical else None,
                    canonical.thumbnail_url if canonical else None,
                    canonical.settings_summary if canonical else None,
                    job.benchmark_ms,
                )
                if canonical is None or actual != expected:
                    raise ApiError(
                        409,
                        "render_completion_idempotency_conflict",
                        "Completed render output differs from the canonical artifact.",
                    )
                return job, canonical
            if job.status in {RenderJobStatus.CANCELED, RenderJobStatus.FAILED}:
                raise ApiError(
                    409,
                    "render_terminal_state",
                    "Canceled or failed renders cannot be completed.",
                )
            if job.status not in {
                RenderJobStatus.QUEUED,
                RenderJobStatus.PREPARING,
                RenderJobStatus.RENDERING,
                RenderJobStatus.UPLOADING,
            }:
                raise ApiError(
                    409,
                    "render_completion_status_invalid",
                    "Render is not in a completable state.",
                )
            if canonical is not None:
                actual = (
                    canonical.storage_bucket,
                    canonical.storage_path,
                    canonical.format.value,
                    canonical.width,
                    canonical.height,
                    canonical.fps,
                    canonical.duration_ms,
                    canonical.file_size_bytes,
                    canonical.thumbnail_url,
                    canonical.settings_summary,
                    benchmark_ms,
                )
                if actual != expected:
                    raise ApiError(
                        409,
                        "render_completion_idempotency_conflict",
                        "Canonical export conflicts with the completion output.",
                    )
            else:
                canonical = ExportRecord(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    project_id=project_id,
                    render_job_id=job.id,
                    workflow_run_id=job.workflow_run_id,
                    source_plan_revision=job.source_plan_revision,
                    version=(
                        len(
                            [
                                export
                                for export in self.exports.values()
                                if export.workspace_id == workspace_id
                                and export.project_id == project_id
                            ]
                        )
                        + 1
                    ),
                    storage_bucket=storage_bucket,
                    storage_path=storage_path,
                    format=RenderOutputFormat(format_value),
                    width=width,
                    height=height,
                    fps=fps,
                    duration_ms=duration_ms,
                    file_size_bytes=file_size_bytes,
                    thumbnail_url=thumbnail_url,
                    settings_summary=summary,
                )
                self.exports[canonical.id] = canonical

            job.artifact_path = storage_path
            job.benchmark_ms = benchmark_ms
            completed = self.transition_job(
                job=job,
                status=RenderJobStatus.COMPLETE,
                progress_pct=100,
                message="Export is ready to download.",
                event_type="complete",
                error_code=None,
                error_message=None,
                metadata={
                    "export_id": str(canonical.id),
                    "completion_rpc": "motionly_complete_render",
                },
            )
            return completed, canonical

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

    def list_exports(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[ExportRecord]:
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
        )[:limit]

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
        metrics_registry: object | None = None,
    ) -> None:
        super().__init__(caption_repository)
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="renders",
            metrics_registry=metrics_registry,
        )

    def create_render_job(self, **values: object) -> RenderJobRecord:
        payload = self._json_safe(values)
        response = self.client.request(
            "POST",
            "/render_jobs",
            json=payload,
        )
        if self._is_missing_column_error(response):
            response = self.client.request(
                "POST",
                "/render_jobs",
                json=self._compat_render_job_payload(payload),
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

    def get_workflow_draft_render(
        self,
        *,
        workflow_run_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> RenderJobRecord | None:
        response = self.client.request(
            "GET",
            "/render_jobs",
            params={
                "workflow_run_id": f"eq.{workflow_run_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": RENDER_JOB_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        return self._job_from_row(rows[0]) if rows else None

    def ensure_workflow_draft_render(
        self,
        *,
        workflow_repository: object,
        **values: object,
    ) -> RenderJobRecord:
        del workflow_repository
        payload = self._json_safe(values)
        response = self.client.request(
            "POST",
            "/rpc/motionly_ensure_workflow_draft_render",
            json={
                "p_workspace_id": payload["workspace_id"],
                "p_project_id": payload["project_id"],
                "p_workflow_run_id": payload["workflow_run_id"],
                "p_uploaded_video_id": payload["uploaded_video_id"],
                "p_edit_plan_id": payload["edit_plan_id"],
                "p_source_plan_revision": payload["source_plan_revision"],
                "p_timeline_revision_id": payload["timeline_revision_id"],
                "p_source_media_revision": payload["source_media_revision"],
                "p_caption_track_id": payload.get("caption_track_id"),
                "p_planner_version": payload["planner_version"],
                "p_manifest": payload["manifest"],
                "p_keep_segments": payload["keep_segments"],
                "p_specs": payload["specs"],
                "p_template_id": payload.get("template_id"),
                "p_render_engine": payload["render_engine"],
                "p_renderer_version": payload["renderer_version"],
                "p_props_hash": payload["props_hash"],
                "p_logs": payload.get("logs") or [],
                "p_progress_pct": payload.get("progress_pct") or 0,
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise ApiError(
                502,
                "workflow_render_response_invalid",
                "Canonical draft render persistence returned no target.",
            )
        return self._job_from_row(rows[0])

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
        response = self.client.request(
            "POST",
            "/render_job_events",
            json=self._json_safe(event_values),
        )
        self._raise_for_supabase(response)
        return self._event_from_row(response.json()[0])

    async def add_event_async(self, **values: object) -> RenderJobEventRecord:
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
        response = await self.client.async_request(
            "POST",
            "/render_job_events",
            json=self._json_safe(event_values),
        )
        self._raise_for_supabase(response)
        return self._event_from_row(response.json()[0])

    def _transition_request(
        self, values: dict[str, object]
    ) -> tuple[dict[str, object], dict[str, object]]:
        """PATCH ``(params, payload)`` for one transition.

        A conditional (``require_active``) transition only matches a row that is
        still queued or running, so a concurrent terminal write by the worker
        wins; it also leaves worker-owned columns (artifact, logs, timings)
        untouched instead of writing back this request's stale copy of them.
        """
        job = values["job"]
        status = values["status"]
        payload: dict[str, object] = {
            "status": status.value,
            "progress_pct": values["progress_pct"],
            "error_code": values.get("error_code"),
            "error_message": values.get("error_message"),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        params: dict[str, object] = {"id": f"eq.{job.id}", "select": RENDER_JOB_COLUMNS}
        if values.get("require_active"):
            params["status"] = f"not.in.{_RENDER_TERMINAL_DB_STATUSES}"
        else:
            payload["artifact_path"] = getattr(job, "artifact_path", None)
            payload["benchmark_ms"] = getattr(job, "benchmark_ms", None)
            payload["logs"] = getattr(job, "logs", [])
        return params, payload

    def _transition_compat_payload(
        self, *, job: RenderJobRecord, values: dict[str, object], payload: dict[str, object]
    ) -> dict[str, object]:
        compat = self._compat_render_job_update_payload(job=job, values=payload)
        if values.get("require_active"):
            # The legacy schema keeps logs (with the manifest) in one column;
            # a conditional transition must not overwrite it.
            compat.pop("logs", None)
        return compat

    def _transitioned_row(self, response: httpx.Response) -> RenderJobRecord:
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            # Conditional transition matched nothing: the job reached a
            # terminal state first (or was deleted).
            raise ApiError(
                409,
                "render_not_cancelable",
                "This render can no longer be canceled.",
            )
        return self._job_from_row(rows[0])

    def transition_job(self, **values: object) -> RenderJobRecord:
        job = values["job"]
        status = values["status"]
        params, payload = self._transition_request(values)
        response = self.client.request("PATCH", "/render_jobs", params=params, json=payload)
        if self._is_missing_column_error(response):
            response = self.client.request(
                "PATCH",
                "/render_jobs",
                params=params,
                json=self._transition_compat_payload(job=job, values=values, payload=payload),
            )
        updated = self._transitioned_row(response)
        self.add_event(
            job=updated,
            event_type=values.get("event_type") or status.value,
            from_status=job.status,
            to_status=status,
            progress_pct=values["progress_pct"],
            message=values["message"],
            error_code=values.get("error_code"),
            metadata=values.get("metadata"),
        )
        return updated

    async def transition_job_async(self, **values: object) -> RenderJobRecord:
        job = values["job"]
        status = values["status"]
        params, payload = self._transition_request(values)
        response = await self.client.async_request(
            "PATCH", "/render_jobs", params=params, json=payload
        )
        if self._is_missing_column_error(response):
            response = await self.client.async_request(
                "PATCH",
                "/render_jobs",
                params=params,
                json=self._transition_compat_payload(job=job, values=values, payload=payload),
            )
        updated = self._transitioned_row(response)
        await self.add_event_async(
            job=updated,
            event_type=values.get("event_type") or status.value,
            from_status=job.status,
            to_status=status,
            progress_pct=values["progress_pct"],
            message=values["message"],
            error_code=values.get("error_code"),
            metadata=values.get("metadata"),
        )
        return updated

    def create_export(self, **values: object) -> ExportRecord:
        payload = self._json_safe(values)
        if values.get("restored_from_export_id") is None:
            response = self.client.request(
                "POST",
                "/rpc/motionly_upsert_base_export",
                json={
                    "p_workspace_id": payload["workspace_id"],
                    "p_project_id": payload["project_id"],
                    "p_render_job_id": payload["render_job_id"],
                    "p_storage_bucket": payload["storage_bucket"],
                    "p_storage_path": payload["storage_path"],
                    "p_format": payload["format"],
                    "p_width": payload["width"],
                    "p_height": payload["height"],
                    "p_fps": payload["fps"],
                    "p_duration_ms": payload.get("duration_ms"),
                    "p_file_size_bytes": payload.get("file_size_bytes"),
                    "p_thumbnail_url": payload.get("thumbnail_url"),
                    "p_settings_summary": payload.get("settings_summary") or {},
                },
            )
            self._raise_for_supabase(response)
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                raise ApiError(
                    502,
                    "export_response_invalid",
                    "Export persistence returned no row.",
                )
            return self._export_from_row(rows[0])

        render = self.get_render_job(
            project_id=values["project_id"],
            render_job_id=values["render_job_id"],
            workspace_id=values["workspace_id"],
        )
        existing = self.list_exports(
            project_id=values["project_id"],
            workspace_id=values["workspace_id"],
        )
        payload.update(
            {
                "workflow_run_id": str(render.workflow_run_id) if render.workflow_run_id else None,
                "source_plan_revision": render.source_plan_revision,
                "version": len(existing) + 1,
            }
        )
        response = self.client.request("POST", "/exports", json=payload)
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

    def list_render_jobs(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[RenderJobRecord]:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        response = self.client.request(
            "GET",
            "/render_jobs",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": RENDER_JOB_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._job_from_row(row) for row in response.json()]

    async def list_render_jobs_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[RenderJobRecord]:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        response = await self.client.async_request(
            "GET",
            "/render_jobs",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": RENDER_JOB_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._job_from_row(row) for row in response.json()]

    def _inflight_duplicate_params(
        self, *, workspace_id: UUID, project_id: UUID, props_hash: str, limit: int
    ) -> dict[str, object]:
        return {
            "workspace_id": f"eq.{workspace_id}",
            "project_id": f"eq.{project_id}",
            "props_hash": f"eq.{props_hash}",
            "workflow_run_id": "is.null",
            "status": f"not.in.{_RENDER_TERMINAL_DB_STATUSES}",
            "select": RENDER_JOB_COLUMNS,
            "order": "created_at.asc,id.asc",
            "limit": str(max(1, limit)),
        }

    def list_inflight_duplicate_jobs(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        props_hash: str,
        limit: int = 10,
    ) -> list[RenderJobRecord]:
        response = self.client.request(
            "GET",
            "/render_jobs",
            params=self._inflight_duplicate_params(
                workspace_id=workspace_id,
                project_id=project_id,
                props_hash=props_hash,
                limit=limit,
            ),
        )
        if self._is_missing_column_error(response):
            return []  # legacy schema without props_hash: no de-duplication
        self._raise_for_supabase(response)
        return [self._job_from_row(row) for row in response.json()]

    async def list_inflight_duplicate_jobs_async(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        props_hash: str,
        limit: int = 10,
    ) -> list[RenderJobRecord]:
        response = await self.client.async_request(
            "GET",
            "/render_jobs",
            params=self._inflight_duplicate_params(
                workspace_id=workspace_id,
                project_id=project_id,
                props_hash=props_hash,
                limit=limit,
            ),
        )
        if self._is_missing_column_error(response):
            return []
        self._raise_for_supabase(response)
        return [self._job_from_row(row) for row in response.json()]

    def _render_slot_params(
        self,
        *,
        workspace_id: UUID,
        period_start: datetime,
        period_end: datetime,
        limit: int,
    ) -> dict[str, object]:
        return {
            "workspace_id": f"eq.{workspace_id}",
            "created_at": [f"gte.{period_start.isoformat()}", f"lt.{period_end.isoformat()}"],
            "status": f"not.in.{_RENDER_SLOT_RELEASED_DB_STATUSES}",
            "select": "id,status,progress_pct",
            "order": "created_at.asc,id.asc",
            "limit": str(max(1, limit)),
        }

    def _render_slot_rows(self, response: httpx.Response) -> list[tuple[UUID, RenderJobStatus]]:
        self._raise_for_supabase(response)
        return [
            (
                UUID(str(row["id"])),
                self._app_status(row["status"], progress_pct=row.get("progress_pct")),
            )
            for row in response.json()
        ]

    def list_render_slot_jobs(
        self,
        *,
        workspace_id: UUID,
        period_start: datetime,
        period_end: datetime,
        limit: int,
    ) -> list[tuple[UUID, RenderJobStatus]]:
        response = self.client.request(
            "GET",
            "/render_jobs",
            params=self._render_slot_params(
                workspace_id=workspace_id,
                period_start=period_start,
                period_end=period_end,
                limit=limit,
            ),
        )
        return self._render_slot_rows(response)

    async def list_render_slot_jobs_async(
        self,
        *,
        workspace_id: UUID,
        period_start: datetime,
        period_end: datetime,
        limit: int,
    ) -> list[tuple[UUID, RenderJobStatus]]:
        response = await self.client.async_request(
            "GET",
            "/render_jobs",
            params=self._render_slot_params(
                workspace_id=workspace_id,
                period_start=period_start,
                period_end=period_end,
                limit=limit,
            ),
        )
        return self._render_slot_rows(response)

    def get_render_job(
        self,
        *,
        project_id: UUID,
        render_job_id: UUID,
        workspace_id: UUID,
    ) -> RenderJobRecord:
        response = self.client.request(
            "GET",
            "/render_jobs",
            params={
                "id": f"eq.{render_job_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": RENDER_JOB_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "render_job_not_found", "Render job was not found.")
        return self._job_from_row(rows[0])

    async def get_render_job_async(
        self,
        *,
        project_id: UUID,
        render_job_id: UUID,
        workspace_id: UUID,
    ) -> RenderJobRecord:
        response = await self.client.async_request(
            "GET",
            "/render_jobs",
            params={
                "id": f"eq.{render_job_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": RENDER_JOB_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "render_job_not_found", "Render job was not found.")
        return self._job_from_row(rows[0])

    def list_recent_render_jobs(self, *, limit: int = 25) -> list[RenderJobRecord]:
        response = self.client.request(
            "GET",
            "/render_jobs",
            params={
                "select": RENDER_JOB_COLUMNS,
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._job_from_row(row) for row in response.json()]

    def get_render_job_by_id(self, *, render_job_id: UUID) -> RenderJobRecord:
        response = self.client.request(
            "GET",
            "/render_jobs",
            params={
                "id": f"eq.{render_job_id}",
                "select": RENDER_JOB_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "render_job_not_found", "Render job was not found.")
        return self._job_from_row(rows[0])

    def events_for_job(
        self,
        render_job_id: UUID,
        *,
        limit: int = 100,
    ) -> list[RenderJobEventRecord]:
        response = self.client.request(
            "GET",
            "/render_job_events",
            params={
                "render_job_id": f"eq.{render_job_id}",
                "select": RENDER_JOB_EVENT_COLUMNS,
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._event_from_row(row) for row in response.json()]

    async def events_for_job_async(
        self,
        render_job_id: UUID,
        *,
        limit: int = 100,
    ) -> list[RenderJobEventRecord]:
        response = await self.client.async_request(
            "GET",
            "/render_job_events",
            params={
                "render_job_id": f"eq.{render_job_id}",
                "select": RENDER_JOB_EVENT_COLUMNS,
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._event_from_row(row) for row in response.json()]

    def list_exports(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[ExportRecord]:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        response = self.client.request(
            "GET",
            "/exports",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EXPORT_COLUMNS,
                "order": "version.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._export_from_row(row) for row in response.json()]

    async def list_exports_async(
        self,
        *,
        project_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[ExportRecord]:
        self.caption_repository.get_project_for_workspace(
            project_id=project_id,
            workspace_id=workspace_id,
        )
        response = await self.client.async_request(
            "GET",
            "/exports",
            params={
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": EXPORT_COLUMNS,
                "order": "version.desc",
                "limit": str(limit),
            },
        )
        self._raise_for_supabase(response)
        return [self._export_from_row(row) for row in response.json()]

    def get_export(self, *, export_id: UUID, workspace_id: UUID) -> ExportRecord:
        response = self.client.request(
            "GET",
            "/exports",
            params={
                "id": f"eq.{export_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": EXPORT_COLUMNS,
                "limit": "1",
            },
        )
        self._raise_for_supabase(response)
        rows = response.json()
        if not rows:
            raise ApiError(404, "export_not_found", "Export was not found.")
        return self._export_from_row(rows[0])

    async def get_export_async(self, *, export_id: UUID, workspace_id: UUID) -> ExportRecord:
        response = await self.client.async_request(
            "GET",
            "/exports",
            params={
                "id": f"eq.{export_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": EXPORT_COLUMNS,
                "limit": "1",
            },
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
            caption_track_id=(
                UUID(str(row.get("caption_track_id") or compat.get("caption_track_id")))
                if row.get("caption_track_id") or compat.get("caption_track_id")
                else None
            ),
            workflow_run_id=(
                UUID(str(row["workflow_run_id"])) if row.get("workflow_run_id") else None
            ),
            edit_plan_id=UUID(str(row["edit_plan_id"])) if row.get("edit_plan_id") else None,
            source_plan_revision=(
                int(row["source_plan_revision"])
                if row.get("source_plan_revision") is not None
                else None
            ),
            timeline_revision_id=(
                UUID(str(row["timeline_revision_id"])) if row.get("timeline_revision_id") else None
            ),
            source_media_revision=(
                int(row["source_media_revision"])
                if row.get("source_media_revision") is not None
                else None
            ),
            status=self._app_status(row.get("status"), progress_pct=compat.get("progress_pct")),
            planner_version=str(
                row.get("planner_version") or compat.get("planner_version") or "unknown",
            ),
            manifest=manifest,
            keep_segments=[
                CutSegment(**segment) for segment in keep_segments if isinstance(segment, dict)
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
            workflow_run_id=(
                UUID(str(row["workflow_run_id"])) if row.get("workflow_run_id") else None
            ),
            source_plan_revision=(
                int(row["source_plan_revision"])
                if row.get("source_plan_revision") is not None
                else None
            ),
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
                        "caption_track_id": (
                            str(job.caption_track_id) if job.caption_track_id else None
                        ),
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
        return status.value if hasattr(status, "value") else str(status)

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
        if status_value == "processing":
            return RenderJobStatus.PREPARING
        return RenderJobStatus(status_value)

    def _raise_for_supabase(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raise ApiError(502, "supabase_request_failed", "Render data could not be saved.")
