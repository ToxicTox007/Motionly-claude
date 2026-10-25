"""Persisted async inference operations (plan Task 16A, Section 2.5).

An asynchronous submission is route-affine (plan invariant 6): once a remote job
may exist, retries poll the original route snapshot and never submit to a
different route. This module is the state machine and repository for that
durable record. The gateway (a later wave) reserves an operation before a
submit, marks it ``submit_unknown`` on any post-send failure, and reconciles it
by polling the original route.

"provider" here means an opaque external execution identity, not a vendor. The
neutral persisted fields are ``provider_operation_id``, ``provider_route_id``,
``provider_model``, ``provider_model_revision`` and ``route_snapshot``.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

# The 14 Section 2.1 capability ids. Kept as a literal frozenset (not imported
# from inference_capabilities, which a parallel wave owns) so this data layer is
# self-contained; the database CHECK in the migration carries the same set.
CAPABILITY_IDS: frozenset[str] = frozenset(
    {
        "planning.chat",
        "transcription.speech_to_text",
        "vision.caption",
        "vision.object_detection",
        "embeddings.text",
        "embeddings.image",
        "audio.diarization",
        "audio.emotion",
        "audio.stem_separation",
        "image.generation",
        "video.generation.submit",
        "video.generation.status",
        "matte.segmentation",
        "audio.sound_classification",
    }
)

# Keys that must never appear in a route snapshot: they would hold a secret
# *value* rather than a secret *reference* (plan invariant 5). ``secret_ref_name``
# is allowed because it is a reference name, not a value.
_FORBIDDEN_SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "authorization",
        "auth_token",
        "access_token",
        "bearer",
    }
)

# error_detail is bounded and stripped to a plain message; the database CHECK
# enforces the same ceiling as a backstop.
MAX_ERROR_DETAIL_LENGTH = 2000
MAX_ERROR_CODE_LENGTH = 200


class OperationState(StrEnum):
    """The Section 2.5 async operation states."""

    RESERVED = "reserved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    SUBMIT_UNKNOWN = "submit_unknown"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


TERMINAL_STATES: frozenset[OperationState] = frozenset(
    {OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED}
)

# Legal transitions (plan Section 2.5), as one table:
#   reserved -> submitting
#   submitting -> submitted | submit_unknown | failed
#   submitted | submit_unknown -> running | succeeded | failed | cancelled | needs_review
#   running -> succeeded | failed | cancelled | needs_review
#   submit_unknown -> submitted  (reconciliation finds the provider id)
_ADVANCED = frozenset(
    {
        OperationState.RUNNING,
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.NEEDS_REVIEW,
    }
)
LEGAL_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.RESERVED: frozenset({OperationState.SUBMITTING}),
    OperationState.SUBMITTING: frozenset(
        {OperationState.SUBMITTED, OperationState.SUBMIT_UNKNOWN, OperationState.FAILED}
    ),
    OperationState.SUBMITTED: _ADVANCED,
    OperationState.SUBMIT_UNKNOWN: _ADVANCED | {OperationState.SUBMITTED},
    OperationState.RUNNING: _ADVANCED - {OperationState.RUNNING},
    OperationState.SUCCEEDED: frozenset(),
    OperationState.FAILED: frozenset(),
    OperationState.CANCELLED: frozenset(),
    OperationState.NEEDS_REVIEW: frozenset(),
}


def is_legal_transition(from_state: OperationState, to_state: OperationState) -> bool:
    """Whether ``from_state -> to_state`` is allowed by the Section 2.5 table."""
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


class IllegalTransitionError(RuntimeError):
    """A transition not permitted by the state machine was requested."""

    def __init__(self, from_state: OperationState, to_state: OperationState) -> None:
        super().__init__(f"illegal operation transition {from_state} -> {to_state}")
        self.from_state = from_state
        self.to_state = to_state


class OperationNotFoundError(LookupError):
    """No operation exists for the given id."""


class TransitionConflictError(RuntimeError):
    """The operation was not in the expected state (optimistic check failed)."""

    def __init__(self, operation_id: UUID, expected_from: frozenset[OperationState]) -> None:
        super().__init__(
            f"operation {operation_id} was not in one of {sorted(s.value for s in expected_from)}"
        )
        self.operation_id = operation_id
        self.expected_from = expected_from


class SecretValueInSnapshotError(ValueError):
    """A route snapshot carried a secret value rather than a reference."""

    def __init__(self, key: str) -> None:
        super().__init__(f"route snapshot key {key!r} looks like a secret value, not a reference")
        self.key = key


def route_snapshot(
    *,
    adapter_id: str,
    adapter_type: str,
    base_url: str,
    model: str,
    model_revision: str,
    mapping_version: str,
    secret_ref_name: str | None,
) -> dict[str, Any]:
    """Build an immutable route snapshot carrying only references, never secrets.

    The snapshot records the adapter id/type, base URL, model, revision, mapping
    version and the secret *reference* name. It rejects any field that looks like
    a secret value (plan invariant 5): keys such as ``api_key``, ``token`` or
    ``password`` are refused outright, since a snapshot must never hold a live
    credential.
    """
    snapshot = {
        "adapter_id": adapter_id,
        "adapter_type": adapter_type,
        "base_url": base_url,
        "model": model,
        "model_revision": model_revision,
        "mapping_version": mapping_version,
    }
    if secret_ref_name is not None:
        snapshot["secret_ref_name"] = secret_ref_name
    _reject_secret_values(snapshot)
    return snapshot


def _reject_secret_values(snapshot: Mapping[str, Any]) -> None:
    for key in snapshot:
        if key.strip().lower() in _FORBIDDEN_SNAPSHOT_KEYS:
            raise SecretValueInSnapshotError(key)


def sanitize_error_detail(detail: str | None) -> str | None:
    """Reduce an error detail to a bounded, single-line plain message.

    Control characters are stripped and the string is clipped to the column
    ceiling so a provider payload (which may embed secrets or large bodies) can
    never be stored verbatim.
    """
    if detail is None:
        return None
    collapsed = " ".join(detail.split())
    if not collapsed:
        return None
    return collapsed[:MAX_ERROR_DETAIL_LENGTH]


def _sanitize_error_code(code: str | None) -> str | None:
    if code is None:
        return None
    collapsed = " ".join(code.split())
    if not collapsed:
        return None
    return collapsed[:MAX_ERROR_CODE_LENGTH]


@dataclass(frozen=True)
class InferenceOperation:
    """The durable record of one async inference operation."""

    id: UUID
    workspace_id: UUID
    project_id: UUID | None
    capability: str
    idempotency_key: str
    provider_route_id: str
    provider_model: str
    provider_model_revision: str
    route_snapshot: Mapping[str, Any]
    state: OperationState
    provider_operation_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    cost_unknown: bool = False
    # Non-secret captured identifiers a worker retry needs to resume polling and
    # cleanup after the commit step (plan Task 16B resume design). Only opaque
    # job/task identifiers and captured URLs live here, never credentials.
    resume_vars: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    submitted_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


class InferenceOperationRepository(Protocol):
    """Storage contract for persisted async operations."""

    def reserve(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID | None,
        capability: str,
        idempotency_key: str,
        route_id: str,
        model: str,
        model_revision: str,
        route_snapshot: Mapping[str, Any],
        estimated_cost_usd: float | None = None,
    ) -> tuple[InferenceOperation, bool]:
        """Reserve an operation idempotently.

        Returns ``(operation, created)``. The same ``(workspace_id,
        idempotency_key)`` returns the existing row with ``created=False``.
        """
        ...

    def get(self, operation_id: UUID) -> InferenceOperation: ...

    def get_by_idempotency_key(
        self, workspace_id: UUID, key: str
    ) -> InferenceOperation | None: ...

    def transition(
        self,
        operation_id: UUID,
        *,
        to_state: OperationState,
        expected_from: set[OperationState] | None,
        provider_operation_id: str | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        actual_cost_usd: float | None = None,
        cost_unknown: bool | None = None,
    ) -> InferenceOperation:
        """Advance an operation, checking the optimistic ``expected_from`` set."""
        ...

    def save_resume_vars(
        self, operation_id: UUID, resume_vars: Mapping[str, Any]
    ) -> InferenceOperation:
        """Persist the non-secret captured vars a worker retry needs to resume.

        Stored alongside the commit transition so a retry after the billable
        boundary resumes polling with the same job/task identifiers and captured
        URLs instead of resubmitting. Secret-looking keys are rejected.
        """
        ...

    def list_non_terminal_for_route(self, route_id: str) -> list[InferenceOperation]: ...

    def count_by_state(self, state: OperationState) -> int: ...


def _validate_reserve_capability(capability: str) -> None:
    if capability not in CAPABILITY_IDS:
        raise ValueError(f"unknown capability {capability!r}")


class InMemoryInferenceOperationRepository:
    """Thread-safe in-memory repository for tests and local development."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: dict[UUID, InferenceOperation] = {}
        self._by_idempotency: dict[tuple[UUID, str], UUID] = {}

    def reserve(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID | None,
        capability: str,
        idempotency_key: str,
        route_id: str,
        model: str,
        model_revision: str,
        route_snapshot: Mapping[str, Any],
        estimated_cost_usd: float | None = None,
    ) -> tuple[InferenceOperation, bool]:
        _validate_reserve_capability(capability)
        _reject_secret_values(route_snapshot)
        key = (workspace_id, idempotency_key)
        with self._lock:
            existing_id = self._by_idempotency.get(key)
            if existing_id is not None:
                return self._by_id[existing_id], False
            now = datetime.now(UTC)
            operation = InferenceOperation(
                id=uuid4(),
                workspace_id=workspace_id,
                project_id=project_id,
                capability=capability,
                idempotency_key=idempotency_key,
                provider_route_id=route_id,
                provider_model=model,
                provider_model_revision=model_revision,
                route_snapshot=dict(route_snapshot),
                state=OperationState.RESERVED,
                estimated_cost_usd=estimated_cost_usd,
                created_at=now,
                updated_at=now,
            )
            self._by_id[operation.id] = operation
            self._by_idempotency[key] = operation.id
            return operation, True

    def get(self, operation_id: UUID) -> InferenceOperation:
        with self._lock:
            operation = self._by_id.get(operation_id)
            if operation is None:
                raise OperationNotFoundError(str(operation_id))
            return operation

    def get_by_idempotency_key(self, workspace_id: UUID, key: str) -> InferenceOperation | None:
        with self._lock:
            operation_id = self._by_idempotency.get((workspace_id, key))
            return self._by_id.get(operation_id) if operation_id is not None else None

    def transition(
        self,
        operation_id: UUID,
        *,
        to_state: OperationState,
        expected_from: set[OperationState] | None,
        provider_operation_id: str | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        actual_cost_usd: float | None = None,
        cost_unknown: bool | None = None,
    ) -> InferenceOperation:
        with self._lock:
            operation = self.get(operation_id)
            if expected_from is not None and operation.state not in expected_from:
                raise TransitionConflictError(operation_id, frozenset(expected_from))
            if not is_legal_transition(operation.state, to_state):
                raise IllegalTransitionError(operation.state, to_state)
            now = datetime.now(UTC)
            changes: dict[str, Any] = {"state": to_state, "updated_at": now}
            if provider_operation_id is not None:
                changes["provider_operation_id"] = provider_operation_id
            if error_code is not None:
                changes["error_code"] = _sanitize_error_code(error_code)
            if error_detail is not None:
                changes["error_detail"] = sanitize_error_detail(error_detail)
            if actual_cost_usd is not None:
                changes["actual_cost_usd"] = actual_cost_usd
            if cost_unknown is not None:
                changes["cost_unknown"] = cost_unknown
            if to_state in {OperationState.SUBMITTED, OperationState.SUBMIT_UNKNOWN} and (
                operation.submitted_at is None
            ):
                changes["submitted_at"] = now
            if to_state in TERMINAL_STATES:
                changes["completed_at"] = now
            updated = replace(operation, **changes)
            self._by_id[operation_id] = updated
            return updated

    def save_resume_vars(
        self, operation_id: UUID, resume_vars: Mapping[str, Any]
    ) -> InferenceOperation:
        _reject_secret_values(resume_vars)
        with self._lock:
            operation = self.get(operation_id)
            updated = replace(
                operation,
                resume_vars=dict(resume_vars),
                updated_at=datetime.now(UTC),
            )
            self._by_id[operation_id] = updated
            return updated

    def list_non_terminal_for_route(self, route_id: str) -> list[InferenceOperation]:
        with self._lock:
            return [
                operation
                for operation in self._by_id.values()
                if operation.provider_route_id == route_id and not operation.is_terminal
            ]

    def count_by_state(self, state: OperationState) -> int:
        with self._lock:
            return sum(1 for operation in self._by_id.values() if operation.state == state)
