from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.api.errors import ApiError
from app.services.supabase import SupabaseRestClient


class WorkflowRunState(StrEnum):
    UPLOADED = "uploaded"
    ANALYZING = "analyzing"
    PROPOSAL_READY = "proposal_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    ASSEMBLING = "assembling"
    DRAFT_RENDERING = "draft_rendering"
    DRAFT_READY = "draft_ready"
    REFINING = "refining"
    EXPORTING = "exporting"
    EXPORT_READY = "export_ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class WorkflowStepState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class OutboxState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    DISPATCHED = "dispatched"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class WorkflowArtifactType(StrEnum):
    TRANSCRIPT = "transcript"
    VISUAL_MANIFEST = "visual_manifest"
    PROPOSAL = "proposal"
    DECISION = "decision"
    TIMELINE_REVISION = "timeline_revision"
    DRAFT_RENDER_JOB = "draft_render_job"
    DRAFT_EXPORT = "draft_export"


TERMINAL_RUN_STATES = frozenset(
    {
        WorkflowRunState.EXPORT_READY,
        WorkflowRunState.FAILED,
        WorkflowRunState.CANCELLED,
        WorkflowRunState.SUPERSEDED,
    }
)

LEGAL_RUN_TRANSITIONS: dict[WorkflowRunState, frozenset[WorkflowRunState]] = {
    WorkflowRunState.UPLOADED: frozenset(
        {
            WorkflowRunState.ANALYZING,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
    WorkflowRunState.ANALYZING: frozenset(
        {
            WorkflowRunState.PROPOSAL_READY,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
    WorkflowRunState.PROPOSAL_READY: frozenset(
        {
            WorkflowRunState.AWAITING_APPROVAL,
            WorkflowRunState.ASSEMBLING,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
    WorkflowRunState.AWAITING_APPROVAL: frozenset(
        {
            WorkflowRunState.ASSEMBLING,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
    WorkflowRunState.ASSEMBLING: frozenset(
        {
            WorkflowRunState.DRAFT_RENDERING,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
    WorkflowRunState.DRAFT_RENDERING: frozenset(
        {
            WorkflowRunState.DRAFT_READY,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
    WorkflowRunState.DRAFT_READY: frozenset(
        {
            WorkflowRunState.REFINING,
            WorkflowRunState.EXPORTING,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
    WorkflowRunState.REFINING: frozenset(
        {
            WorkflowRunState.DRAFT_RENDERING,
            WorkflowRunState.DRAFT_READY,
            WorkflowRunState.EXPORTING,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
    WorkflowRunState.EXPORTING: frozenset(
        {
            WorkflowRunState.EXPORT_READY,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
            WorkflowRunState.SUPERSEDED,
        }
    ),
}

_WORKFLOW_RUN_SELECT = ",".join(
    (
        "id",
        "workspace_id",
        "project_id",
        "uploaded_video_id",
        "source_media_revision",
        "source_object_reference_id",
        "workflow_type",
        "workflow_version",
        "idempotency_key",
        "request_hash",
        "temporal_workflow_id",
        "state",
        "revision",
        "temporal_run_id",
        "current_step_key",
        "input",
        "started_by",
        "active_plan_id",
        "active_plan_revision",
        "retry_of_workflow_run_id",
        "root_workflow_run_id",
        "failure_code",
        "failure_message",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    )
)


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def normalize_workflow_error_code(value: object) -> str:
    """Normalize worker failures to the identifier-only Temporal error-code grammar."""
    text = str(value or "worker_error").strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_.-").lower()
    if not text or not text[0].isalnum():
        return "worker_error"
    return text[:120]


@dataclass
class WorkflowRunRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    uploaded_video_id: UUID
    source_media_revision: int
    source_object_reference_id: UUID
    workflow_type: str
    workflow_version: str
    idempotency_key: str
    request_hash: str
    temporal_workflow_id: str
    state: WorkflowRunState = WorkflowRunState.UPLOADED
    revision: int = 1
    temporal_run_id: str | None = None
    current_step_key: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    started_by: UUID | None = None
    active_plan_id: UUID | None = None
    active_plan_revision: int | None = None
    retry_of_workflow_run_id: UUID | None = None
    root_workflow_run_id: UUID | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> WorkflowRunRecord:
        return cls(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            uploaded_video_id=UUID(row["uploaded_video_id"]),
            source_media_revision=int(row["source_media_revision"]),
            source_object_reference_id=UUID(row["source_object_reference_id"]),
            workflow_type=row["workflow_type"],
            workflow_version=row["workflow_version"],
            idempotency_key=row["idempotency_key"],
            request_hash=row["request_hash"],
            temporal_workflow_id=row["temporal_workflow_id"],
            temporal_run_id=row.get("temporal_run_id"),
            state=WorkflowRunState(row["state"]),
            revision=int(row.get("revision") or 1),
            current_step_key=row.get("current_step_key"),
            input=row.get("input") or {},
            started_by=UUID(row["started_by"]) if row.get("started_by") else None,
            active_plan_id=UUID(row["active_plan_id"]) if row.get("active_plan_id") else None,
            active_plan_revision=row.get("active_plan_revision"),
            retry_of_workflow_run_id=(
                UUID(row["retry_of_workflow_run_id"])
                if row.get("retry_of_workflow_run_id")
                else None
            ),
            root_workflow_run_id=(
                UUID(row["root_workflow_run_id"])
                if row.get("root_workflow_run_id")
                else UUID(row["id"])
            ),
            failure_code=row.get("failure_code"),
            failure_message=row.get("failure_message"),
            started_at=_datetime(row.get("started_at")),
            completed_at=_optional_datetime(row.get("completed_at")),
            created_at=_datetime(row.get("created_at")),
            updated_at=_datetime(row.get("updated_at")),
        )


@dataclass
class WorkflowStepRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    workflow_run_id: UUID
    step_key: str
    step_kind: str
    state: WorkflowStepState = WorkflowStepState.QUEUED
    revision: int = 1
    attempt_count: int = 0
    max_attempts: int = 3
    bullmq_job_id: str | None = None
    resource_id: UUID | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    output: dict[str, Any] | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> WorkflowStepRecord:
        return cls(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            workflow_run_id=UUID(row["workflow_run_id"]),
            step_key=row["step_key"],
            step_kind=row["step_kind"],
            state=WorkflowStepState(row["state"]),
            revision=int(row.get("revision") or 1),
            attempt_count=int(row.get("attempt_count") or 0),
            max_attempts=int(row.get("max_attempts") or 3),
            bullmq_job_id=row.get("bullmq_job_id"),
            resource_id=UUID(row["resource_id"]) if row.get("resource_id") else None,
            input_hash=row.get("input_hash"),
            output_hash=row.get("output_hash"),
            output=row.get("output"),
            last_error_code=row.get("last_error_code"),
            last_error_message=row.get("last_error_message"),
            started_at=_optional_datetime(row.get("started_at")),
            completed_at=_optional_datetime(row.get("completed_at")),
            created_at=_datetime(row.get("created_at")),
            updated_at=_datetime(row.get("updated_at")),
        )


@dataclass
class WorkflowAttemptRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    workflow_run_id: UUID
    workflow_step_id: UUID
    attempt_number: int
    attempt_key: str
    claim_token: UUID
    lease_owner: str
    lease_expires_at: datetime
    state: str = "active"
    result_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> WorkflowAttemptRecord:
        return cls(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            workflow_run_id=UUID(row["workflow_run_id"]),
            workflow_step_id=UUID(row["workflow_step_id"]),
            attempt_number=int(row["attempt_number"]),
            attempt_key=row["attempt_key"],
            claim_token=UUID(row["claim_token"]),
            lease_owner=row["lease_owner"],
            lease_expires_at=_datetime(row["lease_expires_at"]),
            state=row["state"],
            result_hash=row.get("result_hash"),
            error_code=row.get("error_code"),
            error_message=row.get("error_message"),
            started_at=_datetime(row.get("started_at")),
            ended_at=_optional_datetime(row.get("ended_at")),
        )


@dataclass(frozen=True)
class WorkflowArtifactRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    workflow_run_id: UUID
    artifact_type: WorkflowArtifactType
    artifact_id: UUID
    artifact_revision: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> WorkflowArtifactRecord:
        artifact_type = WorkflowArtifactType(row["artifact_type"])
        id_column = {
            WorkflowArtifactType.TRANSCRIPT: "transcript_id",
            WorkflowArtifactType.VISUAL_MANIFEST: "visual_manifest_id",
            WorkflowArtifactType.PROPOSAL: "proposal_id",
            WorkflowArtifactType.DECISION: "decision_id",
            WorkflowArtifactType.TIMELINE_REVISION: "timeline_revision_id",
            WorkflowArtifactType.DRAFT_RENDER_JOB: "render_job_id",
            WorkflowArtifactType.DRAFT_EXPORT: "export_id",
        }[artifact_type]
        return cls(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            workflow_run_id=UUID(row["workflow_run_id"]),
            artifact_type=artifact_type,
            artifact_id=UUID(row[id_column]),
            artifact_revision=(
                int(row["artifact_revision"]) if row.get("artifact_revision") is not None else None
            ),
            created_at=_datetime(row.get("created_at")),
        )


@dataclass
class WorkflowEventRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    workflow_run_id: UUID
    sequence_number: int
    event_type: str
    event_key: str
    payload: dict[str, Any]
    workflow_step_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> WorkflowEventRecord:
        return cls(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            workflow_run_id=UUID(row["workflow_run_id"]),
            workflow_step_id=(
                UUID(row["workflow_step_id"]) if row.get("workflow_step_id") else None
            ),
            sequence_number=int(row["sequence_number"]),
            event_type=row["event_type"],
            event_key=row["event_key"],
            payload=row.get("payload") or {},
            created_at=_datetime(row.get("created_at")),
        )


@dataclass
class OutboxRecord:
    id: UUID
    workspace_id: UUID
    project_id: UUID
    workflow_run_id: UUID
    workflow_event_id: UUID
    destination: str
    event_type: str
    deduplication_key: str
    payload_hash: str
    payload: dict[str, Any]
    workflow_step_id: UUID | None = None
    state: OutboxState = OutboxState.PENDING
    available_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    lease_owner: str | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    claim_generation: int = 0
    attempt_count: int = 0
    max_attempts: int = 20
    transport_message_id: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    dispatched_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> OutboxRecord:
        return cls(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            project_id=UUID(row["project_id"]),
            workflow_run_id=UUID(row["workflow_run_id"]),
            workflow_event_id=UUID(row["workflow_event_id"]),
            workflow_step_id=(
                UUID(row["workflow_step_id"]) if row.get("workflow_step_id") else None
            ),
            destination=row["destination"],
            event_type=row["event_type"],
            deduplication_key=row["deduplication_key"],
            payload_hash=row["payload_hash"],
            payload=row.get("payload") or {},
            state=OutboxState(row["state"]),
            available_at=_datetime(row.get("available_at")),
            lease_owner=row.get("lease_owner"),
            lease_token=UUID(row["lease_token"]) if row.get("lease_token") else None,
            lease_expires_at=_optional_datetime(row.get("lease_expires_at")),
            claim_generation=int(row.get("claim_generation") or 0),
            attempt_count=int(row.get("attempt_count") or 0),
            max_attempts=int(row.get("max_attempts") or 20),
            transport_message_id=row.get("transport_message_id"),
            last_error_code=row.get("last_error_code"),
            last_error_message=row.get("last_error_message"),
            dispatched_at=_optional_datetime(row.get("dispatched_at")),
            dead_lettered_at=_optional_datetime(row.get("dead_lettered_at")),
            created_at=_datetime(row.get("created_at")),
            updated_at=_datetime(row.get("updated_at")),
        )


@dataclass(frozen=True)
class BeginStepResult:
    step: WorkflowStepRecord
    attempt: WorkflowAttemptRecord | None
    deduplicated: bool
    exhausted: bool = False


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, WorkflowRunRecord] = {}
        self.steps: dict[UUID, WorkflowStepRecord] = {}
        self.attempts: dict[UUID, WorkflowAttemptRecord] = {}
        self.events: dict[UUID, WorkflowEventRecord] = {}
        self.artifacts: dict[UUID, WorkflowArtifactRecord] = {}
        self.outbox: dict[UUID, OutboxRecord] = {}
        self._run_keys: dict[tuple[UUID, UUID, str, str], UUID] = {}
        self._step_keys: dict[tuple[UUID, str], UUID] = {}
        self._attempt_keys: dict[tuple[UUID, str], UUID] = {}
        self._artifact_keys: dict[tuple[UUID, WorkflowArtifactType], UUID] = {}
        self._command_keys: dict[tuple[UUID, str, str], tuple[str, UUID | None]] = {}
        self._outbox_keys: dict[tuple[str, str], UUID] = {}
        self._lock = RLock()

    def start_workflow(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        source_media_revision: int,
        source_object_reference_id: UUID,
        workflow_type: str,
        workflow_version: str,
        idempotency_key: str,
        request_hash: str,
        started_by: UUID | None,
        input: dict[str, Any] | None = None,
    ) -> tuple[WorkflowRunRecord, bool]:
        key = (workspace_id, project_id, workflow_type, idempotency_key)
        with self._lock:
            existing_id = self._run_keys.get(key)
            if existing_id is not None:
                existing = self.runs[existing_id]
                if existing.request_hash != request_hash:
                    raise ApiError(
                        409,
                        "workflow_idempotency_conflict",
                        "The workflow key was already used for different input.",
                    )
                return existing, True
            run = WorkflowRunRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                uploaded_video_id=uploaded_video_id,
                source_media_revision=source_media_revision,
                source_object_reference_id=source_object_reference_id,
                workflow_type=workflow_type,
                workflow_version=workflow_version,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                temporal_workflow_id=(
                    f"motionly:{workflow_type}:{workspace_id}:{project_id}:"
                    f"{uploaded_video_id}:{source_media_revision}:"
                    f"{hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]}"
                ),
                input=input or {},
                started_by=started_by,
            )
            run.root_workflow_run_id = run.id
            self.runs[run.id] = run
            self._run_keys[key] = run.id
            payload = {
                "workflow_run_id": str(run.id),
                "workflow_type": workflow_type,
                "uploaded_video_id": str(uploaded_video_id),
                "source_media_revision": source_media_revision,
            }
            event = self._append_event(
                run=run,
                event_type="workflow.requested",
                event_key=f"workflow-requested:{run.id}",
                payload=payload,
            )
            self._append_outbox(
                run=run,
                event=event,
                destination="temporal",
                event_type="workflow.start",
                deduplication_key=f"workflow-start:{run.id}",
                payload=payload,
            )
            return run, False

    def prepare_step(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        workflow_run_id: UUID,
        step_key: str,
        step_kind: str,
        input_hash: str,
        max_attempts: int,
        bullmq_job_id: str,
        resource_id: UUID | None = None,
    ) -> WorkflowStepRecord:
        if not 8 <= len(bullmq_job_id) <= 240:
            raise ApiError(422, "workflow_bullmq_id_invalid", "BullMQ identity is invalid.")
        with self._lock:
            run = self.get_workflow(
                workflow_run_id=workflow_run_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if run.state in TERMINAL_RUN_STATES:
                raise ApiError(409, "workflow_terminal", "Workflow is terminal.")
            key = (workflow_run_id, step_key)
            step_id = self._step_keys.get(key)
            step = self.steps.get(step_id) if step_id else None
            if step is None:
                step = WorkflowStepRecord(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    project_id=project_id,
                    workflow_run_id=workflow_run_id,
                    step_key=step_key,
                    step_kind=step_kind,
                    input_hash=input_hash,
                    max_attempts=max_attempts,
                    bullmq_job_id=bullmq_job_id,
                    resource_id=resource_id,
                )
                self.steps[step.id] = step
                self._step_keys[key] = step.id
            expected = (
                step_kind,
                input_hash,
                max_attempts,
                bullmq_job_id,
                resource_id,
            )
            actual = (
                step.step_kind,
                step.input_hash,
                step.max_attempts,
                step.bullmq_job_id,
                step.resource_id,
            )
            if actual != expected:
                raise ApiError(
                    409,
                    "workflow_step_transport_conflict",
                    "Workflow step transport identity conflicts.",
                )
            return step

    def link_artifact(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        workflow_run_id: UUID,
        artifact_type: WorkflowArtifactType | str,
        artifact_id: UUID,
        artifact_revision: int | None = None,
    ) -> WorkflowArtifactRecord:
        kind = WorkflowArtifactType(artifact_type)
        with self._lock:
            run = self.get_workflow(
                workflow_run_id=workflow_run_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            key = (workflow_run_id, kind)
            existing_id = self._artifact_keys.get(key)
            if existing_id is not None:
                existing = self.artifacts[existing_id]
                if (
                    existing.artifact_id != artifact_id
                    or existing.artifact_revision != artifact_revision
                ):
                    raise ApiError(
                        409,
                        "workflow_artifact_conflict",
                        "Workflow artifact lineage conflicts.",
                    )
                return existing
            if run.state in TERMINAL_RUN_STATES:
                raise ApiError(409, "workflow_terminal", "Workflow is terminal.")
            record = WorkflowArtifactRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                artifact_type=kind,
                artifact_id=artifact_id,
                artifact_revision=artifact_revision,
            )
            self.artifacts[record.id] = record
            self._artifact_keys[key] = record.id
            run.revision += 1
            run.updated_at = datetime.now(UTC)
            self._append_event(
                run=run,
                event_type="workflow.artifact.linked",
                event_key=f"artifact-linked:{run.id}:{kind.value}",
                payload={
                    "workflow_run_id": str(run.id),
                    "artifact_type": kind.value,
                    "artifact_id": str(artifact_id),
                    "artifact_revision": artifact_revision,
                },
            )
            return record

    def bind_draft_render(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        workflow_run_id: UUID,
        render_job_id: UUID,
        edit_plan_id: object,
        source_plan_revision: object,
        timeline_revision_id: object,
        source_media_revision: object,
    ) -> WorkflowRunRecord:
        run = self.get_workflow(
            workflow_run_id=workflow_run_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if run.workflow_type != "first_draft" or run.workflow_version != "first-draft-v2":
            raise ApiError(
                409,
                "workflow_render_version_invalid",
                "Workflow cannot render a draft.",
            )
        if run.state not in {WorkflowRunState.ASSEMBLING, WorkflowRunState.DRAFT_RENDERING}:
            raise ApiError(409, "workflow_not_assembling", "Workflow is not assembling a draft.")
        if (
            run.active_plan_id != edit_plan_id
            or run.active_plan_revision != source_plan_revision
            or run.source_media_revision != source_media_revision
            or not isinstance(timeline_revision_id, UUID)
        ):
            raise ApiError(
                409,
                "workflow_render_lineage_invalid",
                "Draft render lineage does not match the approved workflow.",
            )
        timeline = next(
            (
                item
                for item in self.artifacts.values()
                if item.workflow_run_id == workflow_run_id
                and item.artifact_type == WorkflowArtifactType.TIMELINE_REVISION
            ),
            None,
        )
        if (
            timeline is None
            or timeline.artifact_id != timeline_revision_id
            or timeline.artifact_revision != source_plan_revision
        ):
            raise ApiError(
                409,
                "workflow_approval_timeline_invalid",
                "The immutable approval timeline is missing or stale.",
            )
        existing = next(
            (
                item
                for item in self.artifacts.values()
                if item.workflow_run_id == workflow_run_id
                and item.artifact_type == WorkflowArtifactType.DRAFT_RENDER_JOB
            ),
            None,
        )
        if existing is not None:
            if existing.artifact_id != render_job_id:
                raise ApiError(
                    409,
                    "workflow_render_identity_conflict",
                    "Workflow is already bound to another draft render.",
                )
            return run
        self.link_artifact(
            workspace_id=workspace_id,
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            artifact_type=WorkflowArtifactType.DRAFT_RENDER_JOB,
            artifact_id=render_job_id,
            artifact_revision=int(source_plan_revision),
        )
        self._transition_run(run, WorkflowRunState.DRAFT_RENDERING)
        run.current_step_key = "render-first-draft-v2"
        run.revision += 1
        run.updated_at = datetime.now(UTC)
        self._append_event(
            run=run,
            event_type="workflow.draft_render.ensured",
            event_key=f"draft-render-ensured:{run.id}:{render_job_id}",
            payload={
                "workflow_run_id": str(run.id),
                "render_job_id": str(render_job_id),
                "edit_plan_id": str(edit_plan_id),
                "source_plan_revision": int(source_plan_revision),
                "timeline_revision_id": str(timeline_revision_id),
                "source_media_revision": int(source_media_revision),
                "state": WorkflowRunState.DRAFT_RENDERING.value,
            },
        )
        return run

    def list_steps(
        self, *, workflow_run_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[WorkflowStepRecord]:
        self.get_workflow(
            workflow_run_id=workflow_run_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        return sorted(
            [step for step in self.steps.values() if step.workflow_run_id == workflow_run_id],
            key=lambda step: (step.created_at, step.step_key),
        )

    def list_events(
        self, *, workflow_run_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[WorkflowEventRecord]:
        self.get_workflow(
            workflow_run_id=workflow_run_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        return sorted(
            [event for event in self.events.values() if event.workflow_run_id == workflow_run_id],
            key=lambda event: event.sequence_number,
        )

    def list_artifacts(
        self, *, workflow_run_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[WorkflowArtifactRecord]:
        self.get_workflow(
            workflow_run_id=workflow_run_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        return sorted(
            [
                artifact
                for artifact in self.artifacts.values()
                if artifact.workflow_run_id == workflow_run_id
            ],
            key=lambda artifact: artifact.created_at,
        )

    def cancel_workflow(
        self,
        *,
        workflow_run_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        expected_revision: int,
        idempotency_key: str,
        requested_by: UUID,
    ) -> tuple[WorkflowRunRecord, bool]:
        request_hash = canonical_payload_hash(
            {
                "command": "cancel",
                "workflow_run_id": str(workflow_run_id),
                "expected_revision": expected_revision,
            }
        )
        command_key = (workflow_run_id, "cancel", idempotency_key)
        with self._lock:
            existing = self._command_keys.get(command_key)
            if existing is not None:
                if existing[0] != request_hash:
                    raise ApiError(
                        409,
                        "workflow_command_idempotency_conflict",
                        "Workflow command idempotency conflicts.",
                    )
                return self.runs[workflow_run_id], True
            run = self.get_workflow(
                workflow_run_id=workflow_run_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if run.revision != expected_revision:
                raise ApiError(409, "workflow_revision_stale", "Workflow revision is stale.")
            if run.state in TERMINAL_RUN_STATES:
                raise ApiError(409, "workflow_not_cancelable", "Workflow cannot be canceled.")
            self._transition_run(run, WorkflowRunState.CANCELLED)
            run.current_step_key = None
            run.revision += 1
            run.updated_at = datetime.now(UTC)
            payload = {"workflow_run_id": str(run.id), "state": "cancelled"}
            event = self._append_event(
                run=run,
                event_type="workflow.cancelled",
                event_key=f"workflow-cancelled:{run.id}:{idempotency_key}",
                payload=payload,
            )
            self._append_outbox(
                run=run,
                event=event,
                destination="temporal",
                event_type="workflow.cancelled",
                deduplication_key=f"workflow-cancelled:{run.id}:{idempotency_key}",
                payload=payload,
            )
            self._command_keys[command_key] = (request_hash, None)
            return run, False

    def retry_workflow(
        self,
        *,
        workflow_run_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        expected_revision: int,
        idempotency_key: str,
        requested_by: UUID,
    ) -> tuple[WorkflowRunRecord, bool]:
        request_hash = canonical_payload_hash(
            {
                "command": "retry",
                "workflow_run_id": str(workflow_run_id),
                "expected_revision": expected_revision,
            }
        )
        command_key = (workflow_run_id, "retry", idempotency_key)
        with self._lock:
            existing = self._command_keys.get(command_key)
            if existing is not None:
                if existing[0] != request_hash:
                    raise ApiError(
                        409,
                        "workflow_command_idempotency_conflict",
                        "Workflow command idempotency conflicts.",
                    )
                assert existing[1] is not None
                return self.runs[existing[1]], True
            source = self.get_workflow(
                workflow_run_id=workflow_run_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if source.revision != expected_revision:
                raise ApiError(409, "workflow_revision_stale", "Workflow revision is stale.")
            if source.state not in {WorkflowRunState.FAILED, WorkflowRunState.CANCELLED}:
                raise ApiError(409, "workflow_not_retryable", "Workflow cannot be retried.")
            retry_key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]
            retried, _ = self.start_workflow(
                workspace_id=workspace_id,
                project_id=project_id,
                uploaded_video_id=source.uploaded_video_id,
                source_media_revision=source.source_media_revision,
                source_object_reference_id=source.source_object_reference_id,
                workflow_type=source.workflow_type,
                workflow_version=source.workflow_version,
                idempotency_key=f"retry:{source.id}:{retry_key_hash}",
                request_hash=canonical_payload_hash(
                    {"source_request_hash": source.request_hash, "command_hash": request_hash}
                ),
                started_by=requested_by,
                input={**source.input, "retry_of_workflow_run_id": str(source.id)},
            )
            retried.retry_of_workflow_run_id = source.id
            retried.root_workflow_run_id = source.root_workflow_run_id or source.id
            self._append_event(
                run=source,
                event_type="workflow.retry.requested",
                event_key=f"workflow-retry:{source.id}:{idempotency_key}",
                payload={
                    "workflow_run_id": str(source.id),
                    "retry_workflow_run_id": str(retried.id),
                },
            )
            self._command_keys[command_key] = (request_hash, retried.id)
            return retried, False

    def ensure_first_draft_v2(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        uploaded_video_id: UUID,
        source_media_revision: int,
        source_object_reference_id: UUID,
        started_by: UUID | None,
    ) -> tuple[WorkflowRunRecord, bool]:
        idempotency_key = f"upload:first-draft-v2:{uploaded_video_id}:{source_media_revision}"
        request = {
            "workspace_id": str(workspace_id),
            "project_id": str(project_id),
            "uploaded_video_id": str(uploaded_video_id),
            "source_media_revision": source_media_revision,
            "source_object_reference_id": str(source_object_reference_id),
        }
        return self.start_workflow(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=uploaded_video_id,
            source_media_revision=source_media_revision,
            source_object_reference_id=source_object_reference_id,
            workflow_type="first_draft",
            workflow_version="first-draft-v2",
            idempotency_key=idempotency_key,
            request_hash=canonical_payload_hash(request),
            started_by=started_by,
            input={
                "upload_id": str(uploaded_video_id),
                "source_media_revision": source_media_revision,
            },
        )

    def get_workflow(
        self, *, workflow_run_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> WorkflowRunRecord:
        run = self.runs.get(workflow_run_id)
        if run is None or run.workspace_id != workspace_id or run.project_id != project_id:
            raise ApiError(404, "workflow_not_found", "Workflow run was not found.")
        return run

    def list_workflows(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        limit: int = 50,
        states: set[WorkflowRunState] | frozenset[WorkflowRunState] | None = None,
    ) -> list[WorkflowRunRecord]:
        rows = [
            run
            for run in self.runs.values()
            if run.workspace_id == workspace_id
            and run.project_id == project_id
            and (states is None or run.state in states)
        ]
        ordered = sorted(rows, key=lambda item: item.created_at, reverse=True)
        return ordered[: max(1, min(limit, 100))]

    def list_reconciliation_candidates(
        self,
        *,
        stale_before: datetime,
        limit: int = 50,
        after_updated_at: datetime | None = None,
        after_id: UUID | None = None,
    ) -> list[WorkflowRunRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("reconciliation limit must be between 1 and 100")
        if (after_updated_at is None) != (after_id is None):
            raise ValueError("reconciliation cursor fields must be provided together")
        candidates = [run for run in self.runs.values() if run.updated_at <= stale_before]
        ordered = sorted(candidates, key=lambda run: (run.updated_at, str(run.id)))
        if after_updated_at is not None and after_id is not None:
            cursor = (after_updated_at, str(after_id))
            ordered = [run for run in ordered if (run.updated_at, str(run.id)) > cursor]
        return ordered[:limit]

    def claim_outbox(
        self,
        *,
        lease_owner: str,
        limit: int = 25,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> list[OutboxRecord]:
        now = now or datetime.now(UTC)
        if not lease_owner or len(lease_owner) > 160:
            raise ApiError(422, "outbox_lease_owner_invalid", "Lease owner is invalid.")
        if not 1 <= limit <= 100 or not 5 <= lease_seconds <= 900:
            raise ApiError(422, "outbox_claim_bounds_invalid", "Claim bounds are invalid.")
        with self._lock:
            for item in self.outbox.values():
                if (
                    item.state == OutboxState.LEASED
                    and item.lease_expires_at is not None
                    and item.lease_expires_at <= now
                    and item.attempt_count >= item.max_attempts
                ):
                    item.state = OutboxState.DEAD_LETTER
                    item.dead_lettered_at = now
                    item.last_error_code = "lease_expired_attempts_exhausted"
                    item.last_error_message = (
                        "Final dispatcher lease expired before acknowledgment."
                    )
                    item.lease_owner = None
                    item.lease_token = None
                    item.lease_expires_at = None
                    item.updated_at = now
            candidates = sorted(
                self.outbox.values(),
                key=lambda item: (item.available_at, item.created_at),
            )
            claimed = []
            for item in candidates:
                eligible = (
                    item.state in {OutboxState.PENDING, OutboxState.FAILED}
                    and item.available_at <= now
                ) or (
                    item.state == OutboxState.LEASED
                    and item.lease_expires_at is not None
                    and item.lease_expires_at <= now
                )
                if not eligible or item.attempt_count >= item.max_attempts:
                    continue
                item.state = OutboxState.LEASED
                item.lease_owner = lease_owner
                item.lease_token = uuid4()
                item.lease_expires_at = now + timedelta(seconds=lease_seconds)
                item.claim_generation += 1
                item.attempt_count += 1
                item.updated_at = now
                claimed.append(item)
                if len(claimed) >= limit:
                    break
            return claimed

    def ack_outbox(
        self,
        *,
        outbox_id: UUID,
        lease_token: UUID,
        transport_message_id: str,
        temporal_run_id: str | None = None,
        now: datetime | None = None,
    ) -> OutboxRecord:
        now = now or datetime.now(UTC)
        with self._lock:
            item = self._outbox(outbox_id)
            run = self.runs[item.workflow_run_id]
            if item.event_type == "workflow.start":
                if not temporal_run_id:
                    raise ApiError(
                        422,
                        "outbox_temporal_run_id_required",
                        "Temporal start acknowledgment requires a run ID.",
                    )
                if run.temporal_run_id not in {None, temporal_run_id}:
                    raise ApiError(
                        409,
                        "outbox_temporal_run_id_conflict",
                        "Temporal run identity conflicts with the accepted start.",
                    )
            elif temporal_run_id is not None:
                raise ApiError(
                    422,
                    "outbox_temporal_run_id_unexpected",
                    "Only workflow start can bind a Temporal run ID.",
                )
            if item.state == OutboxState.DISPATCHED:
                if item.transport_message_id != transport_message_id:
                    raise ApiError(
                        409,
                        "outbox_ack_conflict",
                        "Outbox acknowledgment conflicts.",
                    )
                if item.event_type == "workflow.start" and run.temporal_run_id is None:
                    run.temporal_run_id = temporal_run_id
                    run.updated_at = now
                return item
            if (
                item.state != OutboxState.LEASED
                or item.lease_token != lease_token
                or item.lease_expires_at is None
                or item.lease_expires_at <= now
            ):
                raise ApiError(409, "outbox_stale_claim", "Outbox claim is stale.")
            item.state = OutboxState.DISPATCHED
            item.transport_message_id = transport_message_id
            item.dispatched_at = now
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = None
            item.updated_at = now
            if item.event_type == "workflow.start" and run.temporal_run_id is None:
                run.temporal_run_id = temporal_run_id
                run.updated_at = now
            return item

    def fail_outbox(
        self,
        *,
        outbox_id: UUID,
        lease_token: UUID,
        error_code: str,
        error_message: str,
        retry_at: datetime,
        now: datetime | None = None,
    ) -> OutboxRecord:
        now = now or datetime.now(UTC)
        with self._lock:
            item = self._outbox(outbox_id)
            if (
                item.state != OutboxState.LEASED
                or item.lease_token != lease_token
                or item.lease_expires_at is None
                or item.lease_expires_at <= now
            ):
                raise ApiError(409, "outbox_stale_claim", "Outbox claim is stale.")
            terminal = item.attempt_count >= item.max_attempts
            item.state = OutboxState.DEAD_LETTER if terminal else OutboxState.FAILED
            item.available_at = retry_at
            item.last_error_code = error_code[:120]
            item.last_error_message = error_message[:500]
            item.dead_lettered_at = now if terminal else None
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = None
            item.updated_at = now
            return item

    def begin_step(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        workflow_run_id: UUID,
        step_key: str,
        step_kind: str,
        attempt_key: str,
        lease_owner: str,
        lease_seconds: int,
        input_hash: str | None = None,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> BeginStepResult:
        now = now or datetime.now(UTC)
        if (
            not step_key
            or not step_kind
            or not attempt_key
            or not lease_owner
            or max_attempts is None
            or lease_seconds is None
            or len(lease_owner) > 160
            or not 5 <= lease_seconds <= 3600
            or not 1 <= max_attempts <= 20
        ):
            raise ApiError(
                422,
                "workflow_step_lease_bounds_invalid",
                "Step lease bounds are invalid.",
            )
        with self._lock:
            run = self.get_workflow(
                workflow_run_id=workflow_run_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            key = (workflow_run_id, step_key)
            step_id = self._step_keys.get(key)
            step = self.steps.get(step_id) if step_id is not None else None
            if step is not None:
                if (
                    step.step_kind != step_kind
                    or step.input_hash != input_hash
                    or step.max_attempts != max_attempts
                ):
                    raise ApiError(
                        409,
                        "workflow_step_idempotency_conflict",
                        "Step identity was reused for different input.",
                    )
                attempt_id = self._attempt_keys.get((step.id, attempt_key))
                if attempt_id is not None:
                    attempt = self.attempts[attempt_id]
                    return BeginStepResult(
                        step=step,
                        attempt=attempt,
                        deduplicated=True,
                        exhausted=step.state == WorkflowStepState.FAILED,
                    )
            if run.state in TERMINAL_RUN_STATES:
                raise ApiError(409, "workflow_terminal", "Workflow is terminal.")
            if step is None:
                step = WorkflowStepRecord(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    project_id=project_id,
                    workflow_run_id=workflow_run_id,
                    step_key=step_key,
                    step_kind=step_kind,
                    input_hash=input_hash,
                    max_attempts=max_attempts,
                )
                self.steps[step.id] = step
                self._step_keys[key] = step.id
            if step.state == WorkflowStepState.SUCCEEDED:
                return BeginStepResult(step=step, attempt=None, deduplicated=True)
            active = next(
                (
                    attempt
                    for attempt in self.attempts.values()
                    if attempt.workflow_step_id == step.id and attempt.state == "active"
                ),
                None,
            )
            if active and active.lease_expires_at > now:
                raise ApiError(409, "workflow_step_already_claimed", "Step is already claimed.")
            if active:
                active.state = "abandoned"
                active.error_code = "lease_expired"
                active.error_message = "Worker lease expired before terminal result."
                active.ended_at = now
            if step.attempt_count >= step.max_attempts:
                step.state = WorkflowStepState.FAILED
                step.revision += 1
                step.completed_at = now
                step.last_error_code = "attempts_exhausted"
                step.last_error_message = "All worker attempts expired before terminal result."
                step.updated_at = now
                self._transition_run(run, WorkflowRunState.FAILED)
                run.failure_code = "step_attempts_exhausted"
                run.failure_message = "A workflow step exhausted all attempts."
                run.current_step_key = step.step_key
                run.revision += 1
                run.updated_at = now
                payload = {
                    "workflow_run_id": str(run.id),
                    "step_id": str(step.id),
                    "step_key": step.step_key,
                    "attempt_number": active.attempt_number if active else step.attempt_count,
                    "error_code": "attempts_exhausted",
                    "terminal": True,
                }
                event = self._append_event(
                    run=run,
                    step=step,
                    event_type="workflow.step.exhausted",
                    event_key=f"step-exhausted:{step.id}",
                    payload=payload,
                )
                self._append_outbox(
                    run=run,
                    step=step,
                    event=event,
                    destination="temporal",
                    event_type="workflow.step.exhausted",
                    deduplication_key=f"step-exhausted:{step.id}",
                    payload=payload,
                )
                return BeginStepResult(
                    step=step,
                    attempt=active,
                    deduplicated=False,
                    exhausted=True,
                )
            step.state = WorkflowStepState.RUNNING
            step.attempt_count += 1
            step.revision += 1
            step.started_at = step.started_at or now
            step.completed_at = None
            step.updated_at = now
            attempt = WorkflowAttemptRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                workflow_run_id=workflow_run_id,
                workflow_step_id=step.id,
                attempt_number=step.attempt_count,
                attempt_key=attempt_key,
                claim_token=uuid4(),
                lease_owner=lease_owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=now,
            )
            self.attempts[attempt.id] = attempt
            self._attempt_keys[(step.id, attempt_key)] = attempt.id
            if run.state == WorkflowRunState.UPLOADED and step_kind == "analyze_source":
                self._transition_run(run, WorkflowRunState.ANALYZING)
            run.current_step_key = step_key
            run.revision += 1
            run.updated_at = now
            return BeginStepResult(step=step, attempt=attempt, deduplicated=False)

    def complete_step(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        workflow_run_id: UUID,
        step_key: str,
        claim_token: UUID,
        output_hash: str,
        output: dict[str, Any],
        next_run_state: WorkflowRunState | None = None,
        event_type: str = "workflow.step.completed",
        now: datetime | None = None,
    ) -> tuple[WorkflowRunRecord, WorkflowStepRecord, bool]:
        now = now or datetime.now(UTC)
        with self._lock:
            run = self.get_workflow(
                workflow_run_id=workflow_run_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            step = self.steps[self._step_keys[(workflow_run_id, step_key)]]
            if step.state == WorkflowStepState.SUCCEEDED:
                if step.output_hash != output_hash:
                    raise ApiError(
                        409,
                        "workflow_step_result_conflict",
                        "Step already completed with another result.",
                    )
                return run, step, True
            if run.state in TERMINAL_RUN_STATES:
                raise ApiError(409, "workflow_terminal", "Workflow is terminal.")
            attempt = self._active_attempt(step.id, claim_token)
            if attempt.lease_expires_at <= now:
                raise ApiError(409, "workflow_step_stale_claim", "Step claim expired.")
            if step.step_kind == "render_draft":
                target_state = (
                    next_run_state.value
                    if isinstance(next_run_state, WorkflowRunState)
                    else next_run_state
                )
                render_repository = getattr(self, "render_repository", None)
                render = (
                    render_repository.get_workflow_draft_render(
                        workflow_run_id=workflow_run_id,
                        workspace_id=workspace_id,
                        project_id=project_id,
                    )
                    if render_repository is not None
                    else None
                )
                exports = (
                    [
                        item
                        for item in render_repository.exports.values()
                        if render is not None
                        and item.render_job_id == render.id
                        and item.restored_from_export_id is None
                    ]
                    if render_repository is not None
                    else []
                )
                timeline = next(
                    (
                        item
                        for item in self.artifacts.values()
                        if item.workflow_run_id == workflow_run_id
                        and item.artifact_type == WorkflowArtifactType.TIMELINE_REVISION
                    ),
                    None,
                )
                if (
                    run.state
                    not in {WorkflowRunState.DRAFT_RENDERING, WorkflowRunState.DRAFT_READY}
                    or target_state != WorkflowRunState.DRAFT_READY.value
                    or event_type != "workflow.step.completed"
                    or set(output) != {"render_job_id", "render_status", "failed"}
                    or render is None
                    or step.resource_id != render.id
                    or output.get("render_job_id") != str(render.id)
                    or output.get("render_status") != "complete"
                    or output.get("failed") is not False
                    or getattr(render.status, "value", render.status) != "complete"
                    or render.workflow_run_id != run.id
                    or render.edit_plan_id != run.active_plan_id
                    or render.source_plan_revision != run.active_plan_revision
                    or render.source_media_revision != run.source_media_revision
                    or timeline is None
                    or render.timeline_revision_id != timeline.artifact_id
                    or timeline.artifact_revision != run.active_plan_revision
                    or len(exports) != 1
                    or exports[0].workflow_run_id != run.id
                ):
                    raise ApiError(
                        409,
                        "workflow_draft_render_not_ready",
                        "Canonical draft render and export are not ready.",
                    )
                self.link_artifact(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    workflow_run_id=workflow_run_id,
                    artifact_type=WorkflowArtifactType.DRAFT_EXPORT,
                    artifact_id=exports[0].id,
                    artifact_revision=exports[0].version,
                )
            attempt.state = "succeeded"
            attempt.result_hash = output_hash
            attempt.ended_at = now
            step.state = WorkflowStepState.SUCCEEDED
            step.output_hash = output_hash
            step.output = output
            step.revision += 1
            step.completed_at = now
            step.last_error_code = None
            step.last_error_message = None
            step.updated_at = now
            if next_run_state is not None:
                self._transition_run(run, next_run_state)
            run.current_step_key = step_key
            run.revision += 1
            run.updated_at = now
            payload = {
                "workflow_run_id": str(run.id),
                "step_id": str(step.id),
                "step_key": step.step_key,
                "output_hash": output_hash,
                "state": run.state.value,
            }
            event = self._append_event(
                run=run,
                step=step,
                event_type=event_type,
                event_key=f"step-completed:{step.id}:{output_hash}",
                payload=payload,
            )
            self._append_outbox(
                run=run,
                step=step,
                event=event,
                destination="temporal",
                event_type=event_type,
                deduplication_key=f"step-completed:{step.id}:{output_hash}",
                payload=payload,
            )
            return run, step, False

    def fail_step(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        workflow_run_id: UUID,
        step_key: str,
        claim_token: UUID,
        error_code: str,
        error_message: str,
        retryable: bool = True,
        now: datetime | None = None,
    ) -> tuple[WorkflowRunRecord, WorkflowStepRecord, bool]:
        now = now or datetime.now(UTC)
        bounded_code = normalize_workflow_error_code(error_code)
        bounded_message = error_message[:500]
        with self._lock:
            run = self.get_workflow(
                workflow_run_id=workflow_run_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            step = self.steps[self._step_keys[(workflow_run_id, step_key)]]
            attempt = next(
                (
                    item
                    for item in self.attempts.values()
                    if item.workflow_step_id == step.id and item.claim_token == claim_token
                ),
                None,
            )
            if attempt is not None and attempt.state == "failed":
                if attempt.error_code != bounded_code or attempt.error_message != bounded_message:
                    raise ApiError(
                        409,
                        "workflow_step_failure_conflict",
                        "Step already failed with another result.",
                    )
                return run, step, step.state == WorkflowStepState.FAILED
            if run.state in TERMINAL_RUN_STATES:
                raise ApiError(409, "workflow_terminal", "Workflow is terminal.")
            if attempt is None or attempt.state != "active" or attempt.lease_expires_at <= now:
                raise ApiError(409, "workflow_step_stale_claim", "Step claim is stale.")
            attempt.state = "failed"
            attempt.error_code = bounded_code
            attempt.error_message = bounded_message
            attempt.ended_at = now
            terminal = not retryable or step.attempt_count >= step.max_attempts
            step.state = WorkflowStepState.FAILED if terminal else WorkflowStepState.QUEUED
            step.completed_at = now if terminal else None
            step.last_error_code = bounded_code
            step.last_error_message = bounded_message
            step.revision += 1
            step.updated_at = now
            if terminal:
                self._transition_run(run, WorkflowRunState.FAILED)
                run.failure_code = bounded_code
                run.failure_message = bounded_message
                run.current_step_key = step.step_key
            run.revision += 1
            run.updated_at = now
            payload = {
                "workflow_run_id": str(run.id),
                "step_id": str(step.id),
                "step_key": step.step_key,
                "attempt_number": attempt.attempt_number,
                "error_code": bounded_code,
                "terminal": terminal,
            }
            event = self._append_event(
                run=run,
                step=step,
                event_type="workflow.step.failed",
                event_key=f"step-failed:{attempt.id}",
                payload=payload,
            )
            self._append_outbox(
                run=run,
                step=step,
                event=event,
                destination="temporal",
                event_type="workflow.step.failed",
                deduplication_key=f"step-failed:{attempt.id}",
                payload=payload,
            )
            return run, step, terminal

    def _transition_run(
        self,
        run: WorkflowRunRecord,
        target: WorkflowRunState,
    ) -> None:
        if target == run.state:
            return
        if run.state in TERMINAL_RUN_STATES:
            raise ApiError(409, "workflow_terminal", "Workflow is terminal.")
        if target not in LEGAL_RUN_TRANSITIONS.get(run.state, frozenset()):
            raise ApiError(
                409,
                "workflow_transition_invalid",
                f"Workflow transition {run.state.value} -> {target.value} is invalid.",
            )
        run.state = target
        run.completed_at = (
            datetime.now(UTC)
            if target
            in {
                WorkflowRunState.EXPORT_READY,
                WorkflowRunState.CANCELLED,
                WorkflowRunState.SUPERSEDED,
            }
            else None
        )
        if target not in TERMINAL_RUN_STATES:
            return
        ended_at = datetime.now(UTC)
        cancelled_attempts = 0
        for attempt in self.attempts.values():
            if attempt.workflow_run_id == run.id and attempt.state == "active":
                attempt.state = "cancelled"
                attempt.error_code = "workflow_terminal"
                attempt.error_message = "Attempt closed because the workflow became terminal."
                attempt.ended_at = ended_at
                cancelled_attempts += 1
        for step in self.steps.values():
            if step.workflow_run_id == run.id and step.state in {
                WorkflowStepState.QUEUED,
                WorkflowStepState.RUNNING,
                WorkflowStepState.WAITING,
            }:
                step.state = WorkflowStepState.CANCELLED
                step.revision += 1
                step.completed_at = ended_at
                step.last_error_code = "workflow_terminal"
                step.last_error_message = "Step closed because the workflow became terminal."
                step.updated_at = ended_at
        if cancelled_attempts:
            payload = {
                "workflow_run_id": str(run.id),
                "state": target.value,
                "cancelled_attempt_count": cancelled_attempts,
            }
            event_key = f"terminal-siblings:{run.id}:{run.revision + 1}"
            event = self._append_event(
                run=run,
                event_type="workflow.siblings.cancelled",
                event_key=event_key,
                payload=payload,
            )
            self._append_outbox(
                run=run,
                event=event,
                destination="temporal",
                event_type="workflow.siblings.cancelled",
                deduplication_key=event_key,
                payload=payload,
            )

    def _active_attempt(self, step_id: UUID, claim_token: UUID) -> WorkflowAttemptRecord:
        attempt = next(
            (
                item
                for item in self.attempts.values()
                if item.workflow_step_id == step_id
                and item.claim_token == claim_token
                and item.state == "active"
            ),
            None,
        )
        if attempt is None:
            raise ApiError(409, "workflow_step_stale_claim", "Step claim is stale.")
        return attempt

    def _append_event(
        self,
        *,
        run: WorkflowRunRecord,
        event_type: str,
        event_key: str,
        payload: dict[str, Any],
        step: WorkflowStepRecord | None = None,
    ) -> WorkflowEventRecord:
        existing = next(
            (
                item
                for item in self.events.values()
                if item.workflow_run_id == run.id and item.event_key == event_key
            ),
            None,
        )
        if existing:
            return existing
        sequence = 1 + max(
            (
                item.sequence_number
                for item in self.events.values()
                if item.workflow_run_id == run.id
            ),
            default=0,
        )
        event = WorkflowEventRecord(
            id=uuid4(),
            workspace_id=run.workspace_id,
            project_id=run.project_id,
            workflow_run_id=run.id,
            workflow_step_id=step.id if step else None,
            sequence_number=sequence,
            event_type=event_type,
            event_key=event_key,
            payload=payload,
        )
        self.events[event.id] = event
        return event

    def _append_outbox(
        self,
        *,
        run: WorkflowRunRecord,
        event: WorkflowEventRecord,
        destination: str,
        event_type: str,
        deduplication_key: str,
        payload: dict[str, Any],
        step: WorkflowStepRecord | None = None,
    ) -> OutboxRecord:
        key = (destination, deduplication_key)
        existing_id = self._outbox_keys.get(key)
        if existing_id is not None:
            existing = self.outbox[existing_id]
            if existing.payload_hash != canonical_payload_hash(payload):
                raise ApiError(409, "outbox_idempotency_conflict", "Outbox payload conflicts.")
            return existing
        record = OutboxRecord(
            id=uuid4(),
            workspace_id=run.workspace_id,
            project_id=run.project_id,
            workflow_run_id=run.id,
            workflow_step_id=step.id if step else None,
            workflow_event_id=event.id,
            destination=destination,
            event_type=event_type,
            deduplication_key=deduplication_key,
            payload_hash=canonical_payload_hash(payload),
            payload=payload,
        )
        self.outbox[record.id] = record
        self._outbox_keys[key] = record.id
        return record

    def _outbox(self, outbox_id: UUID) -> OutboxRecord:
        item = self.outbox.get(outbox_id)
        if item is None:
            raise ApiError(404, "outbox_not_found", "Outbox message was not found.")
        return item

    async def prepare_step_async(self, **values: Any) -> WorkflowStepRecord:
        return self.prepare_step(**values)

    async def link_artifact_async(self, **values: Any) -> WorkflowArtifactRecord:
        return self.link_artifact(**values)

    async def list_steps_async(self, **values: Any) -> list[WorkflowStepRecord]:
        return self.list_steps(**values)

    async def list_events_async(self, **values: Any) -> list[WorkflowEventRecord]:
        return self.list_events(**values)

    async def list_artifacts_async(self, **values: Any) -> list[WorkflowArtifactRecord]:
        return self.list_artifacts(**values)

    async def cancel_workflow_async(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        return self.cancel_workflow(**values)

    async def retry_workflow_async(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        return self.retry_workflow(**values)

    async def ensure_first_draft_v2_async(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        return self.ensure_first_draft_v2(**values)

    async def reconcile_finalized_uploads_async(
        self, *, limit: int = 50
    ) -> list[WorkflowRunRecord]:
        _ = limit
        return []

    async def get_workflow_async(self, **values: Any) -> WorkflowRunRecord:
        return self.get_workflow(**values)

    async def list_workflows_async(self, **values: Any) -> list[WorkflowRunRecord]:
        return self.list_workflows(**values)

    async def list_reconciliation_candidates_async(self, **values: Any) -> list[WorkflowRunRecord]:
        return self.list_reconciliation_candidates(**values)

    async def claim_outbox_async(self, **values: Any) -> list[OutboxRecord]:
        return self.claim_outbox(**values)

    async def ack_outbox_async(self, **values: Any) -> OutboxRecord:
        return self.ack_outbox(**values)

    async def fail_outbox_async(self, **values: Any) -> OutboxRecord:
        return self.fail_outbox(**values)


class SupabaseWorkflowRepository:
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
            repository="workflows",
            metrics_registry=metrics_registry,
        )

    def start_workflow(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        response = self.client.request(
            "POST",
            "/rpc/motionly_start_workflow_run",
            json=_start_payload(values),
        )
        rows = self._rows(response)
        row = rows[0]
        return WorkflowRunRecord.from_row(row), bool(row.get("deduplicated"))

    async def start_workflow_async(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_start_workflow_run",
            json=_start_payload(values),
        )
        rows = self._rows(response)
        row = rows[0]
        return WorkflowRunRecord.from_row(row), bool(row.get("deduplicated"))

    def prepare_step(self, **values: Any) -> WorkflowStepRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_prepare_workflow_step",
            json=_prepare_step_payload(values),
        )
        return WorkflowStepRecord.from_row(self._rows(response)[0])

    async def prepare_step_async(self, **values: Any) -> WorkflowStepRecord:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_prepare_workflow_step",
            json=_prepare_step_payload(values),
        )
        return WorkflowStepRecord.from_row(self._rows(response)[0])

    def link_artifact(self, **values: Any) -> WorkflowArtifactRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_link_workflow_artifact",
            json=_artifact_payload(values),
        )
        return WorkflowArtifactRecord.from_row(self._rows(response)[0])

    async def link_artifact_async(self, **values: Any) -> WorkflowArtifactRecord:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_link_workflow_artifact",
            json=_artifact_payload(values),
        )
        return WorkflowArtifactRecord.from_row(self._rows(response)[0])

    def list_steps(
        self, *, workflow_run_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[WorkflowStepRecord]:
        response = self.client.request(
            "GET",
            "/workflow_steps",
            params={
                "workflow_run_id": f"eq.{workflow_run_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "created_at.asc,step_key.asc",
                "limit": "100",
            },
        )
        return [WorkflowStepRecord.from_row(row) for row in self._query_rows(response)]

    async def list_steps_async(self, **values: Any) -> list[WorkflowStepRecord]:
        response = await self.client.async_request(
            "GET",
            "/workflow_steps",
            params={
                "workflow_run_id": f"eq.{values['workflow_run_id']}",
                "workspace_id": f"eq.{values['workspace_id']}",
                "project_id": f"eq.{values['project_id']}",
                "select": "*",
                "order": "created_at.asc,step_key.asc",
                "limit": "100",
            },
        )
        return [WorkflowStepRecord.from_row(row) for row in self._query_rows(response)]

    def list_events(
        self, *, workflow_run_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[WorkflowEventRecord]:
        response = self.client.request(
            "GET",
            "/workflow_events",
            params={
                "workflow_run_id": f"eq.{workflow_run_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "sequence_number.asc",
                "limit": "500",
            },
        )
        return [WorkflowEventRecord.from_row(row) for row in self._query_rows(response)]

    async def list_events_async(self, **values: Any) -> list[WorkflowEventRecord]:
        response = await self.client.async_request(
            "GET",
            "/workflow_events",
            params={
                "workflow_run_id": f"eq.{values['workflow_run_id']}",
                "workspace_id": f"eq.{values['workspace_id']}",
                "project_id": f"eq.{values['project_id']}",
                "select": "*",
                "order": "sequence_number.asc",
                "limit": "500",
            },
        )
        return [WorkflowEventRecord.from_row(row) for row in self._query_rows(response)]

    def list_artifacts(
        self, *, workflow_run_id: UUID, workspace_id: UUID, project_id: UUID
    ) -> list[WorkflowArtifactRecord]:
        response = self.client.request(
            "GET",
            "/workflow_run_artifacts",
            params={
                "workflow_run_id": f"eq.{workflow_run_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "order": "created_at.asc",
                "limit": "20",
            },
        )
        return [WorkflowArtifactRecord.from_row(row) for row in self._query_rows(response)]

    async def list_artifacts_async(self, **values: Any) -> list[WorkflowArtifactRecord]:
        response = await self.client.async_request(
            "GET",
            "/workflow_run_artifacts",
            params={
                "workflow_run_id": f"eq.{values['workflow_run_id']}",
                "workspace_id": f"eq.{values['workspace_id']}",
                "project_id": f"eq.{values['project_id']}",
                "select": "*",
                "order": "created_at.asc",
                "limit": "20",
            },
        )
        return [WorkflowArtifactRecord.from_row(row) for row in self._query_rows(response)]

    def cancel_workflow(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        response = self.client.request(
            "POST",
            "/rpc/motionly_cancel_workflow_run",
            json=_command_payload(values),
        )
        row = self._rows(response)[0]
        return WorkflowRunRecord.from_row(row), bool(row.get("deduplicated"))

    async def cancel_workflow_async(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_cancel_workflow_run",
            json=_command_payload(values),
        )
        row = self._rows(response)[0]
        return WorkflowRunRecord.from_row(row), bool(row.get("deduplicated"))

    def retry_workflow(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        response = self.client.request(
            "POST",
            "/rpc/motionly_retry_workflow_run",
            json=_command_payload(values),
        )
        row = self._rows(response)[0]
        return WorkflowRunRecord.from_row(row), bool(row.get("deduplicated"))

    async def retry_workflow_async(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_retry_workflow_run",
            json=_command_payload(values),
        )
        row = self._rows(response)[0]
        return WorkflowRunRecord.from_row(row), bool(row.get("deduplicated"))

    def ensure_first_draft_v2(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        response = self.client.request(
            "POST",
            "/rpc/motionly_ensure_first_draft_v2",
            json={
                "p_workspace_id": str(values["workspace_id"]),
                "p_project_id": str(values["project_id"]),
                "p_uploaded_video_id": str(values["uploaded_video_id"]),
                "p_started_by": (str(values["started_by"]) if values.get("started_by") else None),
            },
        )
        row = self._rows(response)[0]
        return WorkflowRunRecord.from_row(row), bool(row.get("deduplicated"))

    async def ensure_first_draft_v2_async(self, **values: Any) -> tuple[WorkflowRunRecord, bool]:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_ensure_first_draft_v2",
            json={
                "p_workspace_id": str(values["workspace_id"]),
                "p_project_id": str(values["project_id"]),
                "p_uploaded_video_id": str(values["uploaded_video_id"]),
                "p_started_by": (str(values["started_by"]) if values.get("started_by") else None),
            },
        )
        row = self._rows(response)[0]
        return WorkflowRunRecord.from_row(row), bool(row.get("deduplicated"))

    async def reconcile_finalized_uploads_async(
        self, *, limit: int = 50
    ) -> list[WorkflowRunRecord]:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_reconcile_first_draft_v2",
            json={"p_limit": limit},
        )
        if response.status_code < 400 and response.json() == []:
            return []
        return [WorkflowRunRecord.from_row(row) for row in self._rows(response)]

    def get_workflow(
        self,
        *,
        workflow_run_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> WorkflowRunRecord:
        response = self.client.request(
            "GET",
            "/workflow_runs",
            params={
                "id": f"eq.{workflow_run_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "limit": "1",
            },
        )
        rows = self._query_rows(response)
        if not rows:
            raise ApiError(404, "workflow_not_found", "Workflow run was not found.")
        return WorkflowRunRecord.from_row(rows[0])

    async def get_workflow_async(
        self,
        *,
        workflow_run_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> WorkflowRunRecord:
        response = await self.client.async_request(
            "GET",
            "/workflow_runs",
            params={
                "id": f"eq.{workflow_run_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": "*",
                "limit": "1",
            },
        )
        rows = self._query_rows(response)
        if not rows:
            raise ApiError(404, "workflow_not_found", "Workflow run was not found.")
        return WorkflowRunRecord.from_row(rows[0])

    def list_workflows(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        limit: int = 50,
        states: set[WorkflowRunState] | frozenset[WorkflowRunState] | None = None,
    ) -> list[WorkflowRunRecord]:
        response = self.client.request(
            "GET",
            "/workflow_runs",
            params=self._list_workflow_params(
                workspace_id=workspace_id,
                project_id=project_id,
                limit=limit,
                states=states,
            ),
        )
        return [WorkflowRunRecord.from_row(row) for row in self._query_rows(response)]

    async def list_workflows_async(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        limit: int = 50,
        states: set[WorkflowRunState] | frozenset[WorkflowRunState] | None = None,
    ) -> list[WorkflowRunRecord]:
        response = await self.client.async_request(
            "GET",
            "/workflow_runs",
            params=self._list_workflow_params(
                workspace_id=workspace_id,
                project_id=project_id,
                limit=limit,
                states=states,
            ),
        )
        return [WorkflowRunRecord.from_row(row) for row in self._query_rows(response)]

    def list_reconciliation_candidates(
        self,
        *,
        stale_before: datetime,
        limit: int = 50,
        after_updated_at: datetime | None = None,
        after_id: UUID | None = None,
    ) -> list[WorkflowRunRecord]:
        params = self._reconciliation_candidate_params(
            stale_before=stale_before,
            limit=limit,
            after_updated_at=after_updated_at,
            after_id=after_id,
        )
        response = self.client.request("GET", "/workflow_runs", params=params)
        return [WorkflowRunRecord.from_row(row) for row in self._query_rows(response)]

    async def list_reconciliation_candidates_async(
        self,
        *,
        stale_before: datetime,
        limit: int = 50,
        after_updated_at: datetime | None = None,
        after_id: UUID | None = None,
    ) -> list[WorkflowRunRecord]:
        params = self._reconciliation_candidate_params(
            stale_before=stale_before,
            limit=limit,
            after_updated_at=after_updated_at,
            after_id=after_id,
        )
        response = await self.client.async_request("GET", "/workflow_runs", params=params)
        return [WorkflowRunRecord.from_row(row) for row in self._query_rows(response)]

    def claim_outbox(
        self, *, lease_owner: str, limit: int = 25, lease_seconds: int = 60
    ) -> list[OutboxRecord]:
        response = self.client.request(
            "POST",
            "/rpc/motionly_claim_workflow_outbox",
            json={
                "p_lease_owner": lease_owner,
                "p_limit": limit,
                "p_lease_seconds": lease_seconds,
            },
        )
        return [OutboxRecord.from_row(row) for row in self._rows(response)]

    async def claim_outbox_async(
        self, *, lease_owner: str, limit: int = 25, lease_seconds: int = 60
    ) -> list[OutboxRecord]:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_claim_workflow_outbox",
            json={
                "p_lease_owner": lease_owner,
                "p_limit": limit,
                "p_lease_seconds": lease_seconds,
            },
        )
        return [OutboxRecord.from_row(row) for row in self._rows(response)]

    def ack_outbox(
        self,
        *,
        outbox_id: UUID,
        lease_token: UUID,
        transport_message_id: str,
        temporal_run_id: str | None = None,
    ) -> OutboxRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_ack_workflow_outbox",
            json={
                "p_outbox_id": str(outbox_id),
                "p_lease_token": str(lease_token),
                "p_transport_message_id": transport_message_id,
                "p_temporal_run_id": temporal_run_id,
            },
        )
        return OutboxRecord.from_row(self._rows(response)[0])

    async def ack_outbox_async(self, **values: Any) -> OutboxRecord:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_ack_workflow_outbox",
            json={
                "p_outbox_id": str(values["outbox_id"]),
                "p_lease_token": str(values["lease_token"]),
                "p_transport_message_id": values["transport_message_id"],
                "p_temporal_run_id": values.get("temporal_run_id"),
            },
        )
        return OutboxRecord.from_row(self._rows(response)[0])

    def fail_outbox(
        self,
        *,
        outbox_id: UUID,
        lease_token: UUID,
        error_code: str,
        error_message: str,
        retry_at: datetime,
    ) -> OutboxRecord:
        response = self.client.request(
            "POST",
            "/rpc/motionly_fail_workflow_outbox",
            json={
                "p_outbox_id": str(outbox_id),
                "p_lease_token": str(lease_token),
                "p_error_code": error_code,
                "p_error_message": error_message,
                "p_retry_at": retry_at.isoformat(),
            },
        )
        return OutboxRecord.from_row(self._rows(response)[0])

    async def fail_outbox_async(
        self,
        *,
        outbox_id: UUID,
        lease_token: UUID,
        error_code: str,
        error_message: str,
        retry_at: datetime,
    ) -> OutboxRecord:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_fail_workflow_outbox",
            json={
                "p_outbox_id": str(outbox_id),
                "p_lease_token": str(lease_token),
                "p_error_code": error_code,
                "p_error_message": error_message,
                "p_retry_at": retry_at.isoformat(),
            },
        )
        return OutboxRecord.from_row(self._rows(response)[0])

    def begin_step(self, **values: Any) -> BeginStepResult:
        response = self.client.request(
            "POST",
            "/rpc/motionly_begin_workflow_step",
            json=_begin_payload(values),
        )
        row = self._rows(response)[0]
        return BeginStepResult(
            step=WorkflowStepRecord.from_row(row["step"]),
            attempt=(
                WorkflowAttemptRecord.from_row(row["attempt"]) if row.get("attempt") else None
            ),
            deduplicated=bool(row.get("deduplicated")),
            exhausted=bool(row.get("exhausted")),
        )

    async def begin_step_async(self, **values: Any) -> BeginStepResult:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_begin_workflow_step",
            json=_begin_payload(values),
        )
        row = self._rows(response)[0]
        return BeginStepResult(
            step=WorkflowStepRecord.from_row(row["step"]),
            attempt=(
                WorkflowAttemptRecord.from_row(row["attempt"]) if row.get("attempt") else None
            ),
            deduplicated=bool(row.get("deduplicated")),
            exhausted=bool(row.get("exhausted")),
        )

    def complete_step(self, **values: Any) -> tuple[WorkflowRunRecord, WorkflowStepRecord, bool]:
        response = self.client.request(
            "POST",
            "/rpc/motionly_complete_workflow_step",
            json=_complete_payload(values),
        )
        row = self._rows(response)[0]
        return (
            WorkflowRunRecord.from_row(row["run"]),
            WorkflowStepRecord.from_row(row["step"]),
            bool(row.get("deduplicated")),
        )

    async def complete_step_async(
        self, **values: Any
    ) -> tuple[WorkflowRunRecord, WorkflowStepRecord, bool]:
        response = await self.client.async_request(
            "POST",
            "/rpc/motionly_complete_workflow_step",
            json=_complete_payload(values),
        )
        row = self._rows(response)[0]
        return (
            WorkflowRunRecord.from_row(row["run"]),
            WorkflowStepRecord.from_row(row["step"]),
            bool(row.get("deduplicated")),
        )

    def fail_step(self, **values: Any) -> tuple[WorkflowRunRecord, WorkflowStepRecord, bool]:
        response = self.client.request(
            "POST",
            "/rpc/motionly_fail_workflow_step",
            json={
                "p_workspace_id": str(values["workspace_id"]),
                "p_project_id": str(values["project_id"]),
                "p_workflow_run_id": str(values["workflow_run_id"]),
                "p_step_key": values["step_key"],
                "p_claim_token": str(values["claim_token"]),
                "p_error_code": normalize_workflow_error_code(values["error_code"]),
                "p_error_message": values["error_message"],
                "p_retryable": values.get("retryable", True),
            },
        )
        row = self._rows(response)[0]
        return (
            WorkflowRunRecord.from_row(row["run"]),
            WorkflowStepRecord.from_row(row["step"]),
            bool(row["terminal"]),
        )

    @staticmethod
    def _reconciliation_candidate_params(
        *,
        stale_before: datetime,
        limit: int,
        after_updated_at: datetime | None = None,
        after_id: UUID | None = None,
    ) -> dict[str, str]:
        if not 1 <= limit <= 100:
            raise ValueError("reconciliation limit must be between 1 and 100")
        if (after_updated_at is None) != (after_id is None):
            raise ValueError("reconciliation cursor fields must be provided together")
        params = {
            "updated_at": f"lte.{stale_before.isoformat()}",
            "select": _WORKFLOW_RUN_SELECT,
            "order": "updated_at.asc,id.asc",
            "limit": str(limit),
        }
        if after_updated_at is not None and after_id is not None:
            cursor_time = after_updated_at.isoformat()
            params["or"] = (
                f"(updated_at.gt.{cursor_time},and(updated_at.eq.{cursor_time},id.gt.{after_id}))"
            )
        return params

    @staticmethod
    def _list_workflow_params(
        *,
        workspace_id: UUID,
        project_id: UUID,
        limit: int,
        states: set[WorkflowRunState] | frozenset[WorkflowRunState] | None,
    ) -> dict[str, str]:
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "project_id": f"eq.{project_id}",
            "select": "*",
            "order": "created_at.desc",
            "limit": str(max(1, min(limit, 100))),
        }
        if states:
            params["state"] = "in.(" + ",".join(sorted(state.value for state in states)) + ")"
        return params

    @classmethod
    def _query_rows(cls, response: httpx.Response) -> list[dict[str, Any]]:
        if response.status_code >= 400:
            cls._rows(response)
        rows = response.json()
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ApiError(502, "workflow_response_invalid", "Workflow query was invalid.")
        return rows

    @staticmethod
    def _rows(response: httpx.Response) -> list[dict[str, Any]]:
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            message = str(body.get("message") or "Workflow persistence failed.")
            code = str(body.get("code") or "workflow_persistence_failed")
            status = 409 if code in {"P0001", "23505"} else 404 if code == "P0002" else 502
            raise ApiError(status, code.lower(), message)
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise ApiError(502, "workflow_response_invalid", "Workflow RPC returned no rows.")
        return rows


def _start_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "p_workspace_id": str(values["workspace_id"]),
        "p_project_id": str(values["project_id"]),
        "p_uploaded_video_id": str(values["uploaded_video_id"]),
        "p_source_media_revision": values["source_media_revision"],
        "p_source_object_reference_id": str(values["source_object_reference_id"]),
        "p_workflow_type": values["workflow_type"],
        "p_workflow_version": values["workflow_version"],
        "p_idempotency_key": values["idempotency_key"],
        "p_request_hash": values["request_hash"],
        "p_started_by": str(values["started_by"]) if values.get("started_by") else None,
        "p_input": values.get("input") or {},
    }


def _prepare_step_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "p_workspace_id": str(values["workspace_id"]),
        "p_project_id": str(values["project_id"]),
        "p_workflow_run_id": str(values["workflow_run_id"]),
        "p_step_key": values["step_key"],
        "p_step_kind": values["step_kind"],
        "p_input_hash": values["input_hash"],
        "p_max_attempts": values["max_attempts"],
        "p_bullmq_job_id": values["bullmq_job_id"],
        "p_resource_id": str(values["resource_id"]) if values.get("resource_id") else None,
    }


def _artifact_payload(values: dict[str, Any]) -> dict[str, Any]:
    artifact_type = values["artifact_type"]
    return {
        "p_workspace_id": str(values["workspace_id"]),
        "p_project_id": str(values["project_id"]),
        "p_workflow_run_id": str(values["workflow_run_id"]),
        "p_artifact_type": (
            artifact_type.value
            if isinstance(artifact_type, WorkflowArtifactType)
            else artifact_type
        ),
        "p_artifact_id": str(values["artifact_id"]),
        "p_artifact_revision": values.get("artifact_revision"),
    }


def _command_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "p_workspace_id": str(values["workspace_id"]),
        "p_project_id": str(values["project_id"]),
        "p_workflow_run_id": str(values["workflow_run_id"]),
        "p_expected_revision": values["expected_revision"],
        "p_idempotency_key": values["idempotency_key"],
        "p_requested_by": str(values["requested_by"]),
    }


def _begin_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "p_workspace_id": str(values["workspace_id"]),
        "p_project_id": str(values["project_id"]),
        "p_workflow_run_id": str(values["workflow_run_id"]),
        "p_step_key": values["step_key"],
        "p_step_kind": values["step_kind"],
        "p_attempt_key": values["attempt_key"],
        "p_lease_owner": values["lease_owner"],
        "p_lease_seconds": values["lease_seconds"],
        "p_input_hash": values.get("input_hash"),
        "p_max_attempts": values.get("max_attempts", 3),
    }


def _complete_payload(values: dict[str, Any]) -> dict[str, Any]:
    state = values.get("next_run_state")
    return {
        "p_workspace_id": str(values["workspace_id"]),
        "p_project_id": str(values["project_id"]),
        "p_workflow_run_id": str(values["workflow_run_id"]),
        "p_step_key": values["step_key"],
        "p_claim_token": str(values["claim_token"]),
        "p_output_hash": values["output_hash"],
        "p_output": values["output"],
        "p_next_run_state": state.value if isinstance(state, WorkflowRunState) else state,
        "p_next_event_type": values.get("event_type", "workflow.step.completed"),
    }


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _optional_datetime(value: Any) -> datetime | None:
    return _datetime(value) if value else None
