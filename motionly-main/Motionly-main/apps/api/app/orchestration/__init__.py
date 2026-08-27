"""Identifier-only Temporal orchestration for Motionly.

Exports are resolved lazily so importing workflow-safe contract modules cannot pull
HTTP, PostgreSQL, Redis, subprocess, or other I/O modules into Temporal's sandbox.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BullMQActivities": ("app.orchestration.activities", "BullMQActivities"),
    "DispatchBatchResult": ("app.orchestration.dispatcher", "DispatchBatchResult"),
    "EnqueueStepRequest": ("app.orchestration.contracts", "EnqueueStepRequest"),
    "EnqueueStepResult": ("app.orchestration.contracts", "EnqueueStepResult"),
    "HistoryPayloadRejected": ("app.orchestration.contracts", "HistoryPayloadRejected"),
    "MotionlyWorkflow": ("app.orchestration.workflow", "MotionlyWorkflow"),
    "OutboxDispatcher": ("app.orchestration.dispatcher", "OutboxDispatcher"),
    "ReconciliationReport": ("app.orchestration.reconciler", "ReconciliationReport"),
    "StepOutcome": ("app.orchestration.contracts", "StepOutcome"),
    "StepOutcomeSignal": ("app.orchestration.contracts", "StepOutcomeSignal"),
    "TemporalConnectionConfig": ("app.orchestration.client", "TemporalConnectionConfig"),
    "TemporalExecutionDescription": (
        "app.orchestration.contracts",
        "TemporalExecutionDescription",
    ),
    "TemporalOrchestrationClient": (
        "app.orchestration.client",
        "TemporalOrchestrationClient",
    ),
    "TemporalStartResult": ("app.orchestration.contracts", "TemporalStartResult"),
    "WorkflowExecutionState": ("app.orchestration.contracts", "WorkflowExecutionState"),
    "WorkflowReconciler": ("app.orchestration.reconciler", "WorkflowReconciler"),
    "WorkflowResult": ("app.orchestration.contracts", "WorkflowResult"),
    "WorkflowStartInput": ("app.orchestration.contracts", "WorkflowStartInput"),
    "WorkflowStatus": ("app.orchestration.contracts", "WorkflowStatus"),
    "WorkflowStepSpec": ("app.orchestration.contracts", "WorkflowStepSpec"),
    "assert_identifier_only_history": (
        "app.orchestration.contracts",
        "assert_identifier_only_history",
    ),
    "attempt_key_for": ("app.orchestration.contracts", "attempt_key_for"),
    "connect_temporal_client": ("app.orchestration.client", "connect_temporal_client"),
    "deterministic_job_id": ("app.orchestration.activities", "deterministic_job_id"),
    "job_id_for": ("app.orchestration.contracts", "job_id_for"),
    "signal_from_outbox": ("app.orchestration.contracts", "signal_from_outbox"),
    "validate_history_document": (
        "app.orchestration.history",
        "validate_history_document",
    ),
    "validate_history_file": ("app.orchestration.history", "validate_history_file"),
    "workflow_start_for_run": ("app.orchestration.contracts", "workflow_start_for_run"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
