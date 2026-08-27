from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.repositories.renders import ExportRecord, RenderJobRecord
from app.repositories.workflows import (
    WorkflowArtifactRecord,
    WorkflowArtifactType,
    WorkflowEventRecord,
    WorkflowRunRecord,
    WorkflowRunState,
    WorkflowStepRecord,
    WorkflowStepState,
)


class WorkflowAllowedAction(StrEnum):
    CANCEL = "cancel"
    RETRY = "retry"
    APPROVE_PROPOSAL = "approve_proposal"
    REJECT_PROPOSAL = "reject_proposal"


class WorkflowArtifactResponse(BaseModel):
    type: WorkflowArtifactType
    id: UUID
    revision: int | None = None
    created_at: datetime

    @classmethod
    def from_record(cls, record: WorkflowArtifactRecord) -> WorkflowArtifactResponse:
        return cls(
            type=record.artifact_type,
            id=record.artifact_id,
            revision=record.artifact_revision,
            created_at=record.created_at,
        )


class WorkflowStepResponse(BaseModel):
    id: UUID
    step_key: str
    step_kind: str
    state: WorkflowStepState
    revision: int = Field(ge=1)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    bullmq_job_id: str | None = None
    resource_id: UUID | None = None
    last_error_code: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: WorkflowStepRecord) -> WorkflowStepResponse:
        return cls(
            id=record.id,
            step_key=record.step_key,
            step_kind=record.step_kind,
            state=record.state,
            revision=record.revision,
            attempt_count=record.attempt_count,
            max_attempts=record.max_attempts,
            bullmq_job_id=record.bullmq_job_id,
            resource_id=record.resource_id,
            last_error_code=record.last_error_code,
            started_at=record.started_at,
            completed_at=record.completed_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class WorkflowEventResponse(BaseModel):
    id: UUID
    sequence_number: int = Field(ge=1)
    event_type: str
    step_id: UUID | None = None
    payload: dict[str, object]
    created_at: datetime

    @classmethod
    def from_record(cls, record: WorkflowEventRecord) -> WorkflowEventResponse:
        return cls(
            id=record.id,
            sequence_number=record.sequence_number,
            event_type=record.event_type,
            step_id=record.workflow_step_id,
            payload=record.payload,
            created_at=record.created_at,
        )


class WorkflowSummaryResponse(BaseModel):
    id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    workflow_type: str
    workflow_version: str
    state: WorkflowRunState
    revision: int = Field(ge=1)
    source_media_revision: int = Field(ge=1)
    current_step_key: str | None = None
    retry_of_workflow_run_id: UUID | None = None
    root_workflow_run_id: UUID
    failure_code: str | None = None
    allowed_actions: list[WorkflowAllowedAction]
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: WorkflowRunRecord) -> WorkflowSummaryResponse:
        return cls(
            id=record.id,
            project_id=record.project_id,
            uploaded_video_id=record.uploaded_video_id,
            workflow_type=record.workflow_type,
            workflow_version=record.workflow_version,
            state=record.state,
            revision=record.revision,
            source_media_revision=record.source_media_revision,
            current_step_key=record.current_step_key,
            retry_of_workflow_run_id=record.retry_of_workflow_run_id,
            root_workflow_run_id=record.root_workflow_run_id or record.id,
            failure_code=record.failure_code,
            allowed_actions=allowed_actions(record.state),
            started_at=record.started_at,
            completed_at=record.completed_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class WorkflowDraftRenderResponse(BaseModel):
    id: UUID
    status: str
    progress_pct: int = Field(ge=0, le=100)
    error_code: str | None = None
    error_message: str | None = None
    render_engine: str | None = None
    props_hash: str | None = None
    edit_plan_id: UUID | None = None
    source_plan_revision: int | None = None
    timeline_revision_id: UUID | None = None
    source_media_revision: int | None = None

    @classmethod
    def from_record(cls, record: RenderJobRecord) -> WorkflowDraftRenderResponse:
        return cls(
            id=record.id,
            status=record.status.value,
            progress_pct=record.progress_pct,
            error_code=record.error_code,
            error_message=record.error_message,
            render_engine=record.render_engine.value if record.render_engine else None,
            props_hash=record.props_hash,
            edit_plan_id=record.edit_plan_id,
            source_plan_revision=record.source_plan_revision,
            timeline_revision_id=record.timeline_revision_id,
            source_media_revision=record.source_media_revision,
        )


class WorkflowDraftExportResponse(BaseModel):
    id: UUID
    render_job_id: UUID
    version: int = Field(ge=1)
    format: str
    width: int
    height: int
    fps: int
    duration_ms: int | None = None
    file_size_bytes: int | None = None
    source_plan_revision: int | None = None

    @classmethod
    def from_record(cls, record: ExportRecord) -> WorkflowDraftExportResponse:
        return cls(
            id=record.id,
            render_job_id=record.render_job_id,
            version=record.version,
            format=getattr(record.format, "value", str(record.format)),
            width=record.width,
            height=record.height,
            fps=record.fps,
            duration_ms=record.duration_ms,
            file_size_bytes=record.file_size_bytes,
            source_plan_revision=record.source_plan_revision,
        )


class WorkflowDetailResponse(WorkflowSummaryResponse):
    steps: list[WorkflowStepResponse] = Field(default_factory=list)
    events: list[WorkflowEventResponse] = Field(default_factory=list)
    artifacts: list[WorkflowArtifactResponse] = Field(default_factory=list)
    draft_render: WorkflowDraftRenderResponse | None = None
    draft_export: WorkflowDraftExportResponse | None = None


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowSummaryResponse]


class WorkflowCommandRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class WorkflowCommandResponse(BaseModel):
    workflow: WorkflowDetailResponse
    idempotent_replay: bool = False


def allowed_actions(state: WorkflowRunState) -> list[WorkflowAllowedAction]:
    if state in {WorkflowRunState.FAILED, WorkflowRunState.CANCELLED}:
        return [WorkflowAllowedAction.RETRY]
    if state == WorkflowRunState.AWAITING_APPROVAL:
        return [
            WorkflowAllowedAction.CANCEL,
            WorkflowAllowedAction.APPROVE_PROPOSAL,
            WorkflowAllowedAction.REJECT_PROPOSAL,
        ]
    if state in {
        WorkflowRunState.EXPORT_READY,
        WorkflowRunState.SUPERSEDED,
    }:
        return []
    return [WorkflowAllowedAction.CANCEL]
