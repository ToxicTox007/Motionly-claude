from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]*$")

_WORKFLOW_TYPES = frozenset({"first_draft"})
_WORKFLOW_VERSIONS = frozenset({"first-draft-v2"})
_STEP_KINDS = frozenset(
    {
        "analyze_source",
        "preprocess_source",
        "transcribe_source",
        "generate_proposal",
        "render_draft",
    }
)
_JOB_KINDS = frozenset(
    {"visual_analysis", "video_preprocess", "transcription", "edit_plan", "render"}
)
_QUEUE_NAMES = frozenset(
    {
        "visual_analysis",
        "upload_preprocess",
        "transcription",
        "planning",
        "render",
        "render_gpu",
        "render_remotion",
    }
)
_PROPOSAL_STATES = frozenset(
    {
        "queued",
        "generating",
        "awaiting_approval",
        "approved",
        "partially_approved",
        "rejected",
        "failed",
    }
)
_APPROVAL_DECISIONS = frozenset({"approve", "partial_approve", "reject"})
_RUN_STATES = frozenset(
    {
        "uploaded",
        "analyzing",
        "proposal_ready",
        "awaiting_approval",
        "assembling",
        "draft_rendering",
        "draft_ready",
        "refining",
        "exporting",
        "export_ready",
        "failed",
        "cancelled",
        "superseded",
    }
)
_MAX_STEPS = 32


class HistoryPayloadRejected(ValueError):
    """Raised without echoing a rejected value that may contain sensitive data."""


class StepOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    CANCELLED = "cancelled"
    CANONICAL_NOTICE = "canonical_notice"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    PARTIAL_APPROVE = "partial_approve"
    REJECT = "reject"


class WorkflowExecutionState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class WorkflowStepSpec:
    step_key: str
    step_kind: str
    job_kind: str
    queue_name: str
    input_hash: str
    next_run_state: str | None = None
    resource_id: str | None = None
    max_attempts: int = 3

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class WorkflowStartInput:
    workflow_run_id: str
    temporal_workflow_id: str
    workspace_id: str
    project_id: str
    workflow_type: str
    workflow_version: str
    request_hash: str
    steps: list[WorkflowStepSpec]

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class EnqueueStepRequest:
    workflow_run_id: str
    workflow_step_id: str | None
    workspace_id: str
    project_id: str
    step_key: str
    step_kind: str
    job_kind: str
    queue_name: str
    attempt_number: int
    attempt_key: str
    input_hash: str
    next_run_state: str | None = None
    resource_id: str | None = None
    max_attempts: int = 3

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class EnqueueStepResult:
    job_id: str
    queue_name: str
    workflow_run_id: str
    step_key: str
    attempt_key: str
    input_hash: str

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class StepOutcomeSignal:
    event_id: str
    workflow_run_id: str
    outcome: StepOutcome
    step_key: str | None = None
    workflow_step_id: str | None = None
    attempt_number: int = 0
    output_hash: str | None = None
    next_run_state: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class EnsureProposalRequest:
    workflow_run_id: str
    workspace_id: str
    project_id: str
    request_hash: str

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class EnsureDraftRenderRequest:
    workflow_run_id: str
    workspace_id: str
    project_id: str
    request_hash: str

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class EnsureDraftRenderResult:
    workflow_run_id: str
    render_job_id: str
    props_hash: str
    render_engine: str
    render_queue: str
    source_plan_revision: int
    source_media_revision: int

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class EnsureProposalResult:
    workflow_run_id: str
    proposal_id: str
    proposal_revision: int
    request_id: str
    context_digest: str
    proposal_state: str

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class ApprovalGateSignal:
    event_id: str
    workflow_run_id: str
    proposal_id: str
    decision_id: str
    decision: ApprovalDecision
    proposal_revision: int
    workflow_revision: int
    edit_plan_id: str | None = None
    edit_plan_revision: int | None = None

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class WorkflowResult:
    workflow_run_id: str
    state: WorkflowExecutionState
    completed_step_count: int
    current_step_key: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class WorkflowStatus:
    workflow_run_id: str | None
    state: WorkflowExecutionState
    current_step_key: str | None
    current_attempt: int
    completed_step_count: int
    pending_signal_count: int
    ignored_signal_count: int
    error_code: str | None

    def __post_init__(self) -> None:
        assert_identifier_only_history(self)


@dataclass(frozen=True)
class TemporalStartResult:
    workflow_id: str
    run_id: str


@dataclass(frozen=True)
class TemporalExecutionDescription:
    workflow_id: str
    run_id: str | None
    status: str


def assert_identifier_only_history(value: object, *, path: str = "payload") -> None:
    """Accept only exact, known identifier-only Temporal and Task 7 payload schemas."""
    if isinstance(value, WorkflowStartInput):
        _validate_workflow_start(asdict(value), path)
        return
    if isinstance(value, WorkflowStepSpec):
        _validate_step_spec(asdict(value), path)
        return
    if isinstance(value, EnqueueStepRequest):
        _validate_enqueue_request(asdict(value), path)
        return
    if isinstance(value, EnqueueStepResult):
        _validate_enqueue_result(asdict(value), path)
        return
    if isinstance(value, StepOutcomeSignal):
        _validate_step_signal(asdict(value), path)
        return
    if isinstance(value, EnsureProposalRequest):
        _validate_ensure_proposal_request(asdict(value), path)
        return
    if isinstance(value, EnsureProposalResult):
        _validate_ensure_proposal_result(asdict(value), path)
        return
    if isinstance(value, EnsureDraftRenderRequest):
        _validate_ensure_draft_render_request(asdict(value), path)
        return
    if isinstance(value, EnsureDraftRenderResult):
        _validate_ensure_draft_render_result(asdict(value), path)
        return
    if isinstance(value, ApprovalGateSignal):
        _validate_approval_gate_signal(asdict(value), path)
        return
    if isinstance(value, WorkflowResult):
        _validate_workflow_result(asdict(value), path)
        return
    if isinstance(value, WorkflowStatus):
        _validate_workflow_status(asdict(value), path)
        return
    if not isinstance(value, Mapping):
        raise HistoryPayloadRejected(f"{path} is not a known history schema")
    _validate_known_mapping(value, path)


def _validate_known_mapping(value: Mapping[object, object], path: str) -> None:
    if any(not isinstance(key, str) for key in value):
        raise HistoryPayloadRejected(f"{path} contains a non-string field")
    mapping = {str(key): item for key, item in value.items()}
    keys = frozenset(mapping)

    if "temporal_workflow_id" in keys:
        _validate_workflow_start(mapping, path)
    elif "render_job_id" in keys:
        _validate_ensure_draft_render_result(mapping, path)
    elif "proposal_state" in keys:
        _validate_ensure_proposal_result(mapping, path)
    elif keys == frozenset({"workflow_run_id", "workspace_id", "project_id", "request_hash"}):
        _validate_ensure_proposal_request(mapping, path)
    elif "proposal_id" in keys and "decision_id" in keys and "decision" in keys:
        _validate_approval_gate_signal(mapping, path)
    elif "job_id" in keys:
        _validate_enqueue_result(mapping, path)
    elif "event_id" in keys:
        _validate_step_signal(mapping, path)
    elif "job_kind" in keys and "attempt_number" in keys:
        _validate_enqueue_request(mapping, path)
    elif "job_kind" in keys:
        _validate_step_spec(mapping, path)
    elif "pending_signal_count" in keys:
        _validate_workflow_status(mapping, path)
    elif "completed_step_count" in keys:
        _validate_workflow_result(mapping, path)
    elif keys == frozenset(
        {"workflow_run_id", "workflow_type", "uploaded_video_id", "source_media_revision"}
    ):
        _validate_start_outbox(mapping, path)
    elif keys == frozenset({"workflow_run_id", "step_id", "step_key", "output_hash", "state"}):
        _validate_completed_outbox(mapping, path)
    elif keys == frozenset(
        {
            "workflow_run_id",
            "step_id",
            "step_key",
            "attempt_number",
            "error_code",
            "terminal",
        }
    ):
        _validate_failed_outbox(mapping, path)
    elif keys == frozenset({"workflow_run_id", "state", "cancelled_attempt_count"}):
        _validate_siblings_outbox(mapping, path)
    elif keys == frozenset({"workflow_run_id", "state"}):
        _validate_cancelled_outbox(mapping, path)
    else:
        raise HistoryPayloadRejected(f"{path} does not match an allowed history schema")


def _validate_workflow_start(value: Mapping[str, object], path: str) -> None:
    required = {
        "workflow_run_id",
        "temporal_workflow_id",
        "workspace_id",
        "project_id",
        "workflow_type",
        "workflow_version",
        "request_hash",
        "steps",
    }
    _exact_fields(value, required, required, path)
    _uuid_field(value, "workflow_run_id", path)
    _opaque_field(value, "temporal_workflow_id", path, minimum=8, maximum=240)
    _uuid_field(value, "workspace_id", path)
    _uuid_field(value, "project_id", path)
    _enum_field(value, "workflow_type", _WORKFLOW_TYPES, path)
    _enum_field(value, "workflow_version", _WORKFLOW_VERSIONS, path)
    _hash_field(value, "request_hash", path)
    steps = value["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= _MAX_STEPS:
        raise HistoryPayloadRejected(f"{path}.steps has an invalid entry count")
    step_keys: set[str] = set()
    for index, step in enumerate(steps):
        if isinstance(step, WorkflowStepSpec):
            step = asdict(step)
        if not isinstance(step, Mapping):
            raise HistoryPayloadRejected(f"{path}.steps[{index}] is not a step schema")
        _validate_step_spec(step, f"{path}.steps[{index}]")
        step_key = step.get("step_key")
        assert isinstance(step_key, str)
        if step_key in step_keys:
            raise HistoryPayloadRejected(f"{path}.steps contains duplicate step keys")
        step_keys.add(step_key)


def _validate_step_spec(value: Mapping[str, object], path: str) -> None:
    required = {"step_key", "step_kind", "job_kind", "queue_name", "input_hash"}
    allowed = required | {"next_run_state", "resource_id", "max_attempts"}
    _exact_fields(value, required, allowed, path)
    _name_field(value, "step_key", path)
    _enum_field(value, "step_kind", _STEP_KINDS, path)
    _enum_field(value, "job_kind", _JOB_KINDS, path)
    _enum_field(value, "queue_name", _QUEUE_NAMES, path)
    _hash_field(value, "input_hash", path)
    _optional_enum_field(value, "next_run_state", _RUN_STATES, path)
    _optional_uuid_field(value, "resource_id", path)
    _int_field(value, "max_attempts", path, minimum=1, maximum=20, default=3)


def _validate_enqueue_request(value: Mapping[str, object], path: str) -> None:
    required = {
        "workflow_run_id",
        "workspace_id",
        "project_id",
        "step_key",
        "step_kind",
        "job_kind",
        "queue_name",
        "attempt_number",
        "attempt_key",
        "input_hash",
    }
    allowed = required | {
        "workflow_step_id",
        "next_run_state",
        "resource_id",
        "max_attempts",
    }
    _exact_fields(value, required, allowed, path)
    _uuid_field(value, "workflow_run_id", path)
    _optional_uuid_field(value, "workflow_step_id", path)
    _uuid_field(value, "workspace_id", path)
    _uuid_field(value, "project_id", path)
    _name_field(value, "step_key", path)
    _enum_field(value, "step_kind", _STEP_KINDS, path)
    _enum_field(value, "job_kind", _JOB_KINDS, path)
    _enum_field(value, "queue_name", _QUEUE_NAMES, path)
    max_attempts = _int_field(value, "max_attempts", path, minimum=1, maximum=20, default=3)
    attempt_number = _int_field(value, "attempt_number", path, minimum=1, maximum=20)
    if attempt_number > max_attempts:
        raise HistoryPayloadRejected(f"{path}.attempt_number exceeds max_attempts")
    _opaque_field(value, "attempt_key", path, minimum=8, maximum=200)
    _hash_field(value, "input_hash", path)
    _optional_enum_field(value, "next_run_state", _RUN_STATES, path)
    _optional_uuid_field(value, "resource_id", path)


def _validate_enqueue_result(value: Mapping[str, object], path: str) -> None:
    fields = {"job_id", "queue_name", "workflow_run_id", "step_key", "attempt_key", "input_hash"}
    _exact_fields(value, fields, fields, path)
    _opaque_field(value, "job_id", path, minimum=8, maximum=240)
    _enum_field(value, "queue_name", _QUEUE_NAMES, path)
    _uuid_field(value, "workflow_run_id", path)
    _name_field(value, "step_key", path)
    _opaque_field(value, "attempt_key", path, minimum=8, maximum=200)
    _hash_field(value, "input_hash", path)


def _validate_step_signal(value: Mapping[str, object], path: str) -> None:
    required = {"event_id", "workflow_run_id", "outcome"}
    allowed = required | {
        "step_key",
        "workflow_step_id",
        "attempt_number",
        "output_hash",
        "next_run_state",
        "error_code",
    }
    _exact_fields(value, required, allowed, path)
    _opaque_field(value, "event_id", path, minimum=8, maximum=240)
    _uuid_field(value, "workflow_run_id", path)
    outcome = _enum_field(value, "outcome", {item.value for item in StepOutcome}, path)
    _optional_name_field(value, "step_key", path)
    _optional_uuid_field(value, "workflow_step_id", path)
    _int_field(value, "attempt_number", path, minimum=0, maximum=20, default=0)
    _optional_hash_field(value, "output_hash", path)
    _optional_enum_field(value, "next_run_state", _RUN_STATES, path)
    _optional_name_field(value, "error_code", path, maximum=120)
    if outcome == StepOutcome.SUCCEEDED.value and value.get("output_hash") is None:
        raise HistoryPayloadRejected(f"{path} successful outcome requires output_hash")
    if value.get("step_key") is None and outcome not in {
        StepOutcome.CANCELLED.value,
        StepOutcome.TERMINAL_FAILURE.value,
        StepOutcome.CANONICAL_NOTICE.value,
    }:
        raise HistoryPayloadRejected(f"{path} workflow-level outcome must be terminal")


def _validate_ensure_proposal_request(value: Mapping[str, object], path: str) -> None:
    fields = {"workflow_run_id", "workspace_id", "project_id", "request_hash"}
    _exact_fields(value, fields, fields, path)
    _uuid_field(value, "workflow_run_id", path)
    _uuid_field(value, "workspace_id", path)
    _uuid_field(value, "project_id", path)
    _hash_field(value, "request_hash", path)


def _validate_ensure_proposal_result(value: Mapping[str, object], path: str) -> None:
    fields = {
        "workflow_run_id",
        "proposal_id",
        "proposal_revision",
        "request_id",
        "context_digest",
        "proposal_state",
    }
    _exact_fields(value, fields, fields, path)
    _uuid_field(value, "workflow_run_id", path)
    _uuid_field(value, "proposal_id", path)
    _int_field(value, "proposal_revision", path, minimum=1, maximum=2_147_483_647)
    _hash_field(value, "request_id", path)
    _hash_field(value, "context_digest", path)
    _enum_field(value, "proposal_state", _PROPOSAL_STATES, path)


def _validate_ensure_draft_render_request(value: Mapping[str, object], path: str) -> None:
    fields = {"workflow_run_id", "workspace_id", "project_id", "request_hash"}
    _exact_fields(value, fields, fields, path)
    _uuid_field(value, "workflow_run_id", path)
    _uuid_field(value, "workspace_id", path)
    _uuid_field(value, "project_id", path)
    _hash_field(value, "request_hash", path)


def _validate_ensure_draft_render_result(value: Mapping[str, object], path: str) -> None:
    fields = {
        "workflow_run_id",
        "render_job_id",
        "props_hash",
        "render_engine",
        "render_queue",
        "source_plan_revision",
        "source_media_revision",
    }
    _exact_fields(value, fields, fields, path)
    _uuid_field(value, "workflow_run_id", path)
    _uuid_field(value, "render_job_id", path)
    _hash_field(value, "props_hash", path)
    _enum_field(value, "render_engine", {"ffmpeg_basic", "remotion_premium"}, path)
    _enum_field(value, "render_queue", {"render", "render_gpu", "render_remotion"}, path)
    _int_field(value, "source_plan_revision", path, minimum=1, maximum=2_147_483_647)
    _int_field(value, "source_media_revision", path, minimum=1, maximum=2_147_483_647)


def _validate_approval_gate_signal(value: Mapping[str, object], path: str) -> None:
    required = {
        "workflow_run_id",
        "proposal_id",
        "decision_id",
        "decision",
        "proposal_revision",
        "workflow_revision",
    }
    allowed = required | {"event_id", "edit_plan_id", "edit_plan_revision"}
    _exact_fields(value, required, allowed, path)
    if "event_id" in value:
        _opaque_field(value, "event_id", path, minimum=8, maximum=240)
    _uuid_field(value, "workflow_run_id", path)
    _uuid_field(value, "proposal_id", path)
    _uuid_field(value, "decision_id", path)
    decision = _enum_field(value, "decision", _APPROVAL_DECISIONS, path)
    _int_field(value, "proposal_revision", path, minimum=1, maximum=2_147_483_647)
    _int_field(value, "workflow_revision", path, minimum=1, maximum=2_147_483_647)
    _optional_uuid_field(value, "edit_plan_id", path)
    plan_revision = value.get("edit_plan_revision")
    if plan_revision is not None:
        _int_field(value, "edit_plan_revision", path, minimum=1, maximum=2_147_483_647)
    if decision == ApprovalDecision.REJECT.value:
        if value.get("edit_plan_id") is not None or plan_revision is not None:
            raise HistoryPayloadRejected(f"{path} rejection must not contain a plan")
    elif value.get("edit_plan_id") is None or plan_revision is None:
        raise HistoryPayloadRejected(f"{path} approval requires an active plan revision")


def _validate_workflow_result(value: Mapping[str, object], path: str) -> None:
    required = {"workflow_run_id", "state", "completed_step_count"}
    allowed = required | {"current_step_key", "error_code"}
    _exact_fields(value, required, allowed, path)
    _uuid_field(value, "workflow_run_id", path)
    _enum_field(value, "state", {item.value for item in WorkflowExecutionState}, path)
    _int_field(value, "completed_step_count", path, minimum=0, maximum=_MAX_STEPS)
    _optional_name_field(value, "current_step_key", path)
    _optional_name_field(value, "error_code", path, maximum=120)


def _validate_workflow_status(value: Mapping[str, object], path: str) -> None:
    fields = {
        "workflow_run_id",
        "state",
        "current_step_key",
        "current_attempt",
        "completed_step_count",
        "pending_signal_count",
        "ignored_signal_count",
        "error_code",
    }
    _exact_fields(value, fields, fields, path)
    _optional_uuid_field(value, "workflow_run_id", path)
    _enum_field(value, "state", {item.value for item in WorkflowExecutionState}, path)
    _optional_name_field(value, "current_step_key", path)
    _int_field(value, "current_attempt", path, minimum=0, maximum=20)
    _int_field(value, "completed_step_count", path, minimum=0, maximum=_MAX_STEPS)
    _int_field(value, "pending_signal_count", path, minimum=0, maximum=10_000)
    _int_field(value, "ignored_signal_count", path, minimum=0, maximum=10_000)
    _optional_name_field(value, "error_code", path, maximum=120)


def _validate_start_outbox(value: Mapping[str, object], path: str) -> None:
    _uuid_field(value, "workflow_run_id", path)
    _enum_field(value, "workflow_type", _WORKFLOW_TYPES, path)
    _uuid_field(value, "uploaded_video_id", path)
    _int_field(value, "source_media_revision", path, minimum=1, maximum=2_147_483_647)


def _validate_completed_outbox(value: Mapping[str, object], path: str) -> None:
    _uuid_field(value, "workflow_run_id", path)
    _uuid_field(value, "step_id", path)
    _name_field(value, "step_key", path)
    _hash_field(value, "output_hash", path)
    _enum_field(value, "state", _RUN_STATES, path)


def _validate_failed_outbox(value: Mapping[str, object], path: str) -> None:
    _uuid_field(value, "workflow_run_id", path)
    _uuid_field(value, "step_id", path)
    _name_field(value, "step_key", path)
    _int_field(value, "attempt_number", path, minimum=1, maximum=20)
    _name_field(value, "error_code", path, maximum=120)
    if not isinstance(value.get("terminal"), bool):
        raise HistoryPayloadRejected(f"{path}.terminal must be a boolean")


def _validate_siblings_outbox(value: Mapping[str, object], path: str) -> None:
    _uuid_field(value, "workflow_run_id", path)
    _enum_field(value, "state", {"failed", "cancelled", "superseded", "export_ready"}, path)
    _int_field(value, "cancelled_attempt_count", path, minimum=1, maximum=10_000)


def _validate_cancelled_outbox(value: Mapping[str, object], path: str) -> None:
    _uuid_field(value, "workflow_run_id", path)
    _enum_field(value, "state", {"cancelled"}, path)


def workflow_start_for_run(run: Any) -> WorkflowStartInput:
    request_hash = str(run.request_hash)
    return WorkflowStartInput(
        workflow_run_id=str(run.id),
        temporal_workflow_id=str(run.temporal_workflow_id),
        workspace_id=str(run.workspace_id),
        project_id=str(run.project_id),
        workflow_type=str(run.workflow_type),
        workflow_version=str(run.workflow_version),
        request_hash=request_hash,
        steps=workflow_steps_for(
            workflow_type=str(run.workflow_type),
            workflow_version=str(run.workflow_version),
            request_hash=request_hash,
            uploaded_video_id=str(run.uploaded_video_id),
        ),
    )


def workflow_steps_for(
    *,
    workflow_type: str,
    workflow_version: str,
    request_hash: str,
    uploaded_video_id: str,
) -> list[WorkflowStepSpec]:
    _enum_value(workflow_type, _WORKFLOW_TYPES, "workflow_type")
    _enum_value(workflow_version, _WORKFLOW_VERSIONS, "workflow_version")
    _hash(request_hash, "request_hash")
    _uuid(uploaded_video_id, "uploaded_video_id")
    if workflow_type != "first_draft":
        raise HistoryPayloadRejected("workflow type or version is not executable")
    if workflow_version == "first-draft-v2":
        return [
            WorkflowStepSpec(
                step_key="preprocess-source-v2",
                step_kind="preprocess_source",
                job_kind="video_preprocess",
                queue_name="upload_preprocess",
                input_hash=request_hash,
                next_run_state="analyzing",
                resource_id=uploaded_video_id,
            ),
            WorkflowStepSpec(
                step_key="transcribe-source-v2",
                step_kind="transcribe_source",
                job_kind="transcription",
                queue_name="transcription",
                input_hash=request_hash,
                resource_id=uploaded_video_id,
            ),
            WorkflowStepSpec(
                step_key="analyze-source-v2",
                step_kind="analyze_source",
                job_kind="visual_analysis",
                queue_name="visual_analysis",
                input_hash=request_hash,
                next_run_state="proposal_ready",
                resource_id=uploaded_video_id,
            ),
        ]
    raise HistoryPayloadRejected("workflow type or version is not executable")


def signal_from_outbox(
    *, event_type: str, event_id: str, payload: Mapping[str, Any]
) -> StepOutcomeSignal | ApprovalGateSignal:
    assert_identifier_only_history(payload)
    workflow_run_id = _required_string(payload, "workflow_run_id")
    if event_type == "workflow.approval.decided":
        return ApprovalGateSignal(
            event_id=event_id,
            workflow_run_id=workflow_run_id,
            proposal_id=_required_string(payload, "proposal_id"),
            decision_id=_required_string(payload, "decision_id"),
            decision=ApprovalDecision(_required_string(payload, "decision")),
            proposal_revision=int(payload["proposal_revision"]),
            workflow_revision=int(payload["workflow_revision"]),
            edit_plan_id=_optional_string(payload, "edit_plan_id"),
            edit_plan_revision=(
                int(payload["edit_plan_revision"])
                if payload.get("edit_plan_revision") is not None
                else None
            ),
        )
    step_key = _optional_string(payload, "step_key")
    workflow_step_id = _optional_string(payload, "step_id")
    attempt_number = int(payload.get("attempt_number") or 0)
    if event_type in {"workflow.step.completed", "workflow.step.succeeded"}:
        outcome = StepOutcome.SUCCEEDED
    elif event_type == "workflow.siblings.cancelled":
        outcome = StepOutcome.CANONICAL_NOTICE
    elif event_type == "workflow.cancelled":
        outcome = StepOutcome.CANCELLED
    elif event_type == "workflow.step.exhausted" or bool(payload.get("terminal")):
        outcome = StepOutcome.TERMINAL_FAILURE
    elif event_type == "workflow.step.failed":
        outcome = StepOutcome.RETRYABLE_FAILURE
    else:
        raise HistoryPayloadRejected("outbox event_type is not an orchestration signal")
    return StepOutcomeSignal(
        event_id=event_id,
        workflow_run_id=workflow_run_id,
        step_key=step_key,
        workflow_step_id=workflow_step_id,
        attempt_number=attempt_number,
        outcome=outcome,
        output_hash=_optional_string(payload, "output_hash"),
        next_run_state=_optional_string(payload, "state"),
        error_code=_optional_string(payload, "error_code"),
    )


def attempt_key_for(workflow_run_id: str, step_key: str, attempt_number: int) -> str:
    digest = hashlib.sha256(f"{workflow_run_id}:{step_key}:{attempt_number}".encode()).hexdigest()[
        :24
    ]
    return f"temporal-attempt:{workflow_run_id}:{attempt_number}:{digest}"


def job_id_for(request: EnqueueStepRequest) -> str:
    digest = hashlib.sha256(
        ":".join(
            (
                request.workflow_run_id,
                request.step_key,
                str(request.attempt_number),
                request.attempt_key,
                request.input_hash,
            )
        ).encode()
    ).hexdigest()[:24]
    safe_step = re.sub(r"[^a-z0-9_-]", "-", request.step_key.lower())[:40]
    return f"workflow--{request.workflow_run_id}--{safe_step}--{digest}"


def _exact_fields(
    value: Mapping[str, object],
    required: set[str],
    allowed: set[str],
    path: str,
) -> None:
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise HistoryPayloadRejected(f"{path} has an invalid field set")


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HistoryPayloadRejected(f"{key} is required")
    return value


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise HistoryPayloadRejected(f"{key} must be an identifier")
    return value


def _uuid_field(value: Mapping[str, object], key: str, path: str) -> None:
    item = value.get(key)
    if not isinstance(item, str):
        raise HistoryPayloadRejected(f"{path}.{key} must be a UUID")
    _uuid(item, key)


def _optional_uuid_field(value: Mapping[str, object], key: str, path: str) -> None:
    item = value.get(key)
    if item is None:
        return
    if not isinstance(item, str):
        raise HistoryPayloadRejected(f"{path}.{key} must be a UUID")
    _uuid(item, key)


def _hash_field(value: Mapping[str, object], key: str, path: str) -> None:
    item = value.get(key)
    if not isinstance(item, str):
        raise HistoryPayloadRejected(f"{path}.{key} must be a hash")
    _hash(item, key)


def _optional_hash_field(value: Mapping[str, object], key: str, path: str) -> None:
    item = value.get(key)
    if item is None:
        return
    if not isinstance(item, str):
        raise HistoryPayloadRejected(f"{path}.{key} must be a hash")
    _hash(item, key)


def _name_field(value: Mapping[str, object], key: str, path: str, *, maximum: int = 120) -> None:
    item = value.get(key)
    if not isinstance(item, str):
        raise HistoryPayloadRejected(f"{path}.{key} must be a name")
    _name(item, key, maximum=maximum)


def _optional_name_field(
    value: Mapping[str, object], key: str, path: str, *, maximum: int = 120
) -> None:
    item = value.get(key)
    if item is None:
        return
    if not isinstance(item, str):
        raise HistoryPayloadRejected(f"{path}.{key} must be a name")
    _name(item, key, maximum=maximum)


def _opaque_field(
    value: Mapping[str, object],
    key: str,
    path: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    item = value.get(key)
    if (
        not isinstance(item, str)
        or not minimum <= len(item) <= maximum
        or not _OPAQUE_ID_RE.fullmatch(item)
    ):
        raise HistoryPayloadRejected(f"{path}.{key} must be a bounded identifier")


def _enum_field(
    value: Mapping[str, object], key: str, allowed: set[str] | frozenset[str], path: str
) -> str:
    item = value.get(key)
    if isinstance(item, StrEnum):
        item = item.value
    if not isinstance(item, str) or item not in allowed:
        raise HistoryPayloadRejected(f"{path}.{key} is not an allowed enum value")
    return item


def _optional_enum_field(
    value: Mapping[str, object],
    key: str,
    allowed: set[str] | frozenset[str],
    path: str,
) -> None:
    if value.get(key) is not None:
        _enum_field(value, key, allowed, path)


def _int_field(
    value: Mapping[str, object],
    key: str,
    path: str,
    *,
    minimum: int,
    maximum: int,
    default: int | None = None,
) -> int:
    item = value.get(key, default)
    if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
        raise HistoryPayloadRejected(f"{path}.{key} is outside the allowed integer bound")
    return item


def _uuid(value: str, field_name: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HistoryPayloadRejected(f"{field_name} must be a UUID") from exc


def _hash(value: str, field_name: str) -> None:
    if not _HASH_RE.fullmatch(value):
        raise HistoryPayloadRejected(f"{field_name} must be a lowercase SHA-256 digest")


def _name(value: str, field_name: str, *, maximum: int = 120) -> None:
    if len(value) > maximum or not _NAME_RE.fullmatch(value):
        raise HistoryPayloadRejected(f"{field_name} is not a bounded identifier")


def _enum_value(value: str, allowed: frozenset[str], field_name: str) -> None:
    if value not in allowed:
        raise HistoryPayloadRejected(f"{field_name} is not an executable enum value")
