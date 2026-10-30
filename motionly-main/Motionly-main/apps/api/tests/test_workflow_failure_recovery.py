"""Audit WJ-06: Temporal-side failures reach the canonical run, waits are
bounded, and the changes stay replay-compatible with pre-audit executions."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.orchestration.contracts import (
    FailWorkflowRunRequest,
    HistoryPayloadRejected,
    WorkflowStartInput,
    WorkflowStepSpec,
    assert_identifier_only_history,
    orchestration_error_code,
)
from app.orchestration.replay import replay_history_document
from app.orchestration.worker import create_worker
from app.repositories.workflows import InMemoryWorkflowRepository, WorkflowRunState
from app.services.queue import QueueRegistry

HASH = "a" * 64
GOLDEN = Path(__file__).parent / "fixtures" / "temporal" / "first_draft_v2_pre_audit_history.json"
GOLDEN_WORKFLOW_ID = "motionly:golden:0b000000-0000-4000-8000-000000000001"
TIME_SKIPPING = pytest.mark.skipif(
    os.getenv("RUN_TEMPORAL_TIME_SKIPPING_TESTS") != "1",
    reason="set RUN_TEMPORAL_TIME_SKIPPING_TESTS=1 to download/run the local SDK server",
)


def test_pre_audit_completed_history_replays_on_the_current_workflow() -> None:
    # A first-draft execution recorded by the workflow code before this change
    # (retryable analyze failure, planning, approval gate, render). The new
    # patched code paths must not produce a non-determinism error on it.
    document = json.loads(GOLDEN.read_text())
    result = asyncio.run(replay_history_document(document, workflow_id=GOLDEN_WORKFLOW_ID))
    assert result.replay_failure is None


def test_pre_audit_in_flight_history_replays_on_the_current_workflow() -> None:
    # The same execution cut while it waits at the approval gate: what a new
    # worker sees when it picks up a workflow started by the previous release.
    document = json.loads(GOLDEN.read_text())
    events = document["events"]
    signals = [
        index
        for index, event in enumerate(events)
        if event["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED"
    ]
    approval_signal = signals[3]
    last_completed_task = max(
        index
        for index, event in enumerate(events[:approval_signal])
        if event["eventType"] == "EVENT_TYPE_WORKFLOW_TASK_COMPLETED"
    )
    partial = copy.deepcopy(document)
    partial["events"] = events[: last_completed_task + 1]
    result = asyncio.run(replay_history_document(partial, workflow_id=GOLDEN_WORKFLOW_ID))
    assert result.replay_failure is None


def test_fail_run_request_is_identifier_only() -> None:
    request = FailWorkflowRunRequest(
        workflow_run_id=str(uuid4()),
        workspace_id=str(uuid4()),
        project_id=str(uuid4()),
        error_code="workflow_step_outcome_timeout",
    )
    assert_identifier_only_history(request)
    with pytest.raises(HistoryPayloadRejected):
        FailWorkflowRunRequest(
            workflow_run_id=str(uuid4()),
            workspace_id=str(uuid4()),
            project_id=str(uuid4()),
            error_code="https://example.com/leak",
        )
    assert orchestration_error_code("BullmqEnqueueUnavailable") == "bullmq_enqueue_unavailable"
    assert orchestration_error_code("https://x") == "https_x"
    assert orchestration_error_code("***") == "orchestration_failed"


def test_in_memory_fail_workflow_run_is_idempotent() -> None:
    repository = InMemoryWorkflowRepository()
    run, _ = repository.start_workflow(
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        source_media_revision=1,
        source_object_reference_id=uuid4(),
        workflow_type="first_draft",
        workflow_version="first-draft-v2",
        idempotency_key="wj06-idempotency",
        request_hash=HASH,
        started_by=None,
    )
    failed, changed = repository.fail_workflow_run(
        workflow_run_id=run.id,
        workspace_id=run.workspace_id,
        project_id=run.project_id,
        error_code="workflow_step_outcome_timeout",
    )
    assert changed is True
    assert failed.state == WorkflowRunState.FAILED
    assert failed.failure_code == "workflow_step_outcome_timeout"
    again, changed_again = repository.fail_workflow_run(
        workflow_run_id=run.id,
        workspace_id=run.workspace_id,
        project_id=run.project_id,
        error_code="something_else",
    )
    assert changed_again is False
    assert again.failure_code == "workflow_step_outcome_timeout"


class _RecordingRepository:
    """The two repository calls the Temporal worker's BullMQ activities make."""

    def __init__(self, *, prepare_error: ApiError | None = None) -> None:
        self.prepare_error = prepare_error
        self.failed: list[dict[str, object]] = []

    def prepare_step(self, **_values: object) -> None:
        if self.prepare_error is not None:
            raise self.prepare_error

    def fail_workflow_run(self, **values: object) -> tuple[None, bool]:
        self.failed.append(values)
        return None, True


def _start_input(run_id: str) -> WorkflowStartInput:
    return WorkflowStartInput(
        workflow_run_id=run_id,
        temporal_workflow_id=f"motionly:wj06:{run_id}",
        workspace_id=str(uuid4()),
        project_id=str(uuid4()),
        workflow_type="first_draft",
        workflow_version="first-draft-v2",
        request_hash=HASH,
        steps=[
            WorkflowStepSpec(
                step_key="analyze-source-v2",
                step_kind="analyze_source",
                job_kind="visual_analysis",
                queue_name="visual_analysis",
                input_hash=HASH,
                next_run_state="proposal_ready",
                resource_id=str(uuid4()),
            )
        ],
    )


async def _run_until_closed(repository: _RecordingRepository) -> tuple[object, object]:
    from temporalio.client import WorkflowFailureError
    from temporalio.testing import WorkflowEnvironment

    cache_dir = Path("/tmp/temporal-sdk-cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    environment = await WorkflowEnvironment.start_time_skipping(download_dest_dir=str(cache_dir))
    run_id = str(uuid4())
    start = _start_input(run_id)
    try:
        worker = create_worker(
            client=environment.client,
            registry=QueueRegistry(),
            task_queue="wj06-test",
            workflow_repository=repository,  # type: ignore[arg-type]
        )
        async with worker:
            handle = await environment.client.start_workflow(
                "motionly.identifier_orchestration",
                start,
                id=start.temporal_workflow_id,
                task_queue="wj06-test",
            )
            try:
                outcome: object = await handle.result()
            except WorkflowFailureError as error:
                outcome = error
            history = (await handle.fetch_history()).to_json_dict()
            return outcome, history
    finally:
        await environment.shutdown()


@TIME_SKIPPING
def test_a_lost_step_outcome_fails_the_run_instead_of_waiting_30_days() -> None:
    repository = _RecordingRepository()
    outcome, history = asyncio.run(_run_until_closed(repository))
    assert isinstance(outcome, dict)
    assert outcome["state"] == "failed"
    assert outcome["error_code"] == "workflow_step_outcome_timeout"
    assert [call["error_code"] for call in repository.failed] == ["workflow_step_outcome_timeout"]
    # The new history (with its patch markers) is still identifier-only.
    replay = asyncio.run(
        replay_history_document(history, workflow_id=f"motionly:wj06:{outcome['workflow_run_id']}")
    )
    assert replay.replay_failure is None


@TIME_SKIPPING
def test_an_exhausted_activity_fails_the_canonical_run() -> None:
    from temporalio.client import WorkflowFailureError

    repository = _RecordingRepository(
        prepare_error=ApiError(409, "workflow_step_transport_conflict", "conflict")
    )
    outcome, _history = asyncio.run(_run_until_closed(repository))
    assert isinstance(outcome, WorkflowFailureError)
    assert [call["error_code"] for call in repository.failed] == [
        "workflow_step_transport_conflict"
    ]
