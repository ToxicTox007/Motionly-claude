"""Audit WJ-04/WJ-05/WJ-06/WJ-07/WJ-20: the orchestration roles survive
transient faults, keep cancellations, repair failed runs and stop cleanly."""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.api.errors import ApiError
from app.orchestration.contracts import TemporalExecutionDescription
from app.orchestration.dispatcher import (
    DispatchBatchResult,
    OutboxDispatcher,
    run_dispatcher_loop,
)
from app.orchestration.main import ProgressTracker, run_until_signalled, run_with_liveness
from app.orchestration.reconciler import WorkflowReconciler
from app.repositories.workflows import (
    InMemoryWorkflowRepository,
    SupabaseWorkflowRepository,
    WorkflowRunState,
)

HASH = "a" * 64


def _start_run(repository: InMemoryWorkflowRepository, key: str = "resilience-key"):
    run, _ = repository.start_workflow(
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        source_media_revision=1,
        source_object_reference_id=uuid4(),
        workflow_type="first_draft",
        workflow_version="first-draft-v2",
        idempotency_key=key,
        request_hash=HASH,
        started_by=None,
    )
    return run


class _Temporal:
    def __init__(self) -> None:
        self.cancel_failures = 0
        self.cancels: list[str] = []
        self.descriptions: dict[str, TemporalExecutionDescription | None] = {}

    async def start_workflow(self, **_kwargs):  # pragma: no cover - unused
        raise AssertionError("no starts expected")

    async def signal_workflow(self, *, workflow_id, signal):
        return f"signal:{signal.event_id}"

    async def describe_workflow(self, *, workflow_id):
        return self.descriptions.get(workflow_id)

    async def cancel_workflow(self, *, workflow_id: str, reason: str) -> bool:
        if self.cancel_failures:
            self.cancel_failures -= 1
            raise RuntimeError("temporal frontend unavailable")
        self.cancels.append(workflow_id)
        return True


# WJ-04 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"code": "P0001", "message": "OUTBOX_STALE_CLAIM"}, 409, "outbox_stale_claim"),
        ({"code": "P0002", "message": "WORKFLOW_NOT_FOUND"}, 404, "workflow_not_found"),
        (
            {
                "code": "P0001",
                "message": "WORKFLOW_TERMINAL: terminal workflow lifecycle cannot change",
            },
            409,
            "workflow_terminal",
        ),
        ({"code": "23505", "message": "duplicate key value"}, 409, "23505"),
        ({}, 502, "workflow_persistence_failed"),
    ],
)
def test_postgrest_sql_errors_map_to_the_codes_callers_compare(body, status, code) -> None:
    # PostgREST reports raise-exception errors as {"code": "<SQLSTATE>",
    # "message": "<TOKEN>"}; the dispatcher and activities compare tokens.
    response = httpx.Response(400, json=body)
    with pytest.raises(ApiError) as exc_info:
        SupabaseWorkflowRepository._rows(response)
    assert exc_info.value.status_code == status
    assert exc_info.value.code == code


def test_dispatcher_loop_outlives_transient_failures() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    class FlakyDispatcher:
        async def dispatch_once(self):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise httpx.ConnectError("supabase unavailable")
            return DispatchBatchResult(claimed=0)

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)
        if calls["n"] >= 3:
            raise asyncio.CancelledError

    progress: list[int] = []
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_dispatcher_loop(
                FlakyDispatcher(),  # type: ignore[arg-type]
                poll_interval_seconds=1.0,
                max_backoff_seconds=60.0,
                sleeper=sleeper,
                progress=lambda: progress.append(1),
            )
        )
    assert calls["n"] == 3, "the loop kept dispatching after two failures"
    assert sleeps[:2] == [2.0, 4.0], "failures back off"
    assert progress == [1]


# WJ-05 ----------------------------------------------------------------------
def test_a_failed_project_delete_cancellation_is_retried_not_lost() -> None:
    repository = InMemoryWorkflowRepository()
    run = _start_run(repository)
    repository.record_project_delete_cancellation(run=run)
    temporal = _Temporal()
    temporal.cancel_failures = 1
    dispatcher = OutboxDispatcher(
        repository=repository, temporal=temporal, lease_owner="dispatcher-wj05"
    )

    first = asyncio.run(dispatcher.cancel_orphaned_workflows())
    assert first == (1, 0)
    assert temporal.cancels == []
    [request] = repository.cancellations.values()
    assert request.completed_at is None and request.last_error_code

    # The release backoff passes; the next pass cancels it.
    repository.cancellations[request.id] = type(request)(
        **{**request.__dict__, "lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    second = asyncio.run(dispatcher.cancel_orphaned_workflows())
    assert second == (1, 1)
    assert temporal.cancels == [run.temporal_workflow_id]
    [done] = repository.cancellations.values()
    assert done.completed_at is not None and done.attempt_count == 2
    assert asyncio.run(dispatcher.cancel_orphaned_workflows()) == (0, 0)


# WJ-06 ----------------------------------------------------------------------
def test_reconciler_fails_a_run_whose_temporal_execution_failed() -> None:
    repository = InMemoryWorkflowRepository()
    run = _start_run(repository)
    run.temporal_run_id = "temporal-run-1"
    run.state = WorkflowRunState.ANALYZING
    run.updated_at = datetime.now(UTC) - timedelta(hours=1)
    temporal = _Temporal()
    temporal.descriptions[run.temporal_workflow_id] = TemporalExecutionDescription(
        workflow_id=run.temporal_workflow_id, run_id="temporal-run-1", status="failed"
    )
    reconciler = WorkflowReconciler(repository=repository, temporal=temporal)
    report = asyncio.run(reconciler.reconcile_once(stale_before=datetime.now(UTC)))

    assert [action.action for action in report.actions] == ["repaired_failed_closed_temporal"]
    assert report.repaired == 1
    stored = repository.runs[run.id]
    assert stored.state == WorkflowRunState.FAILED
    assert stored.failure_code == "temporal_execution_failed"


def test_reconciler_leaves_api_owned_states_alone_after_the_workflow_completed() -> None:
    repository = InMemoryWorkflowRepository()
    run = _start_run(repository)
    run.temporal_run_id = "temporal-run-2"
    run.state = WorkflowRunState.EXPORTING
    run.updated_at = datetime.now(UTC) - timedelta(hours=1)
    temporal = _Temporal()
    temporal.descriptions[run.temporal_workflow_id] = TemporalExecutionDescription(
        workflow_id=run.temporal_workflow_id, run_id="temporal-run-2", status="completed"
    )
    reconciler = WorkflowReconciler(repository=repository, temporal=temporal)
    report = asyncio.run(reconciler.reconcile_once(stale_before=datetime.now(UTC)))
    assert repository.runs[run.id].state == WorkflowRunState.EXPORTING
    assert report.repaired == 0


def test_one_failing_run_does_not_abort_the_reconciliation_pass() -> None:
    repository = InMemoryWorkflowRepository()
    broken = _start_run(repository, "resilience-broken")
    healthy = _start_run(repository, "resilience-healthy")
    for run in (broken, healthy):
        run.temporal_run_id = f"temporal-{run.id}"
        run.state = WorkflowRunState.ANALYZING
        run.updated_at = datetime.now(UTC) - timedelta(hours=1)

    class Temporal(_Temporal):
        async def describe_workflow(self, *, workflow_id):
            if workflow_id == broken.temporal_workflow_id:
                raise RuntimeError("rpc deadline exceeded")
            return TemporalExecutionDescription(
                workflow_id=workflow_id, run_id="x", status="terminated"
            )

    report = asyncio.run(
        WorkflowReconciler(repository=repository, temporal=Temporal()).reconcile_once(
            stale_before=datetime.now(UTC)
        )
    )
    actions = {action.workflow_run_id: action.action for action in report.actions}
    assert actions[broken.id] == "error_runtimeerror"
    assert actions[healthy.id] == "repaired_failed_closed_temporal"


# WJ-07 / WJ-20 ---------------------------------------------------------------
@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_sigterm_cancels_the_role_for_a_graceful_shutdown() -> None:
    stopped: list[str] = []

    async def role() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            stopped.append("cancelled")
            raise

    async def scenario() -> bool:
        asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
        return await run_until_signalled(role(), signals=(signal.SIGTERM,))

    assert asyncio.run(scenario()) is True
    assert stopped == ["cancelled"]


def test_liveness_stops_refreshing_when_a_loop_role_makes_no_progress(tmp_path: Path) -> None:
    file = tmp_path / "alive"
    clock = {"now": 0.0}
    progress = ProgressTracker(clock=lambda: clock["now"])

    async def stuck_role() -> None:
        await asyncio.sleep(0.03)
        file.unlink(missing_ok=True)
        clock["now"] = 1_000.0  # no progress for far longer than the window
        await asyncio.sleep(0.05)

    asyncio.run(
        run_with_liveness(
            stuck_role(),
            file,
            interval_seconds=0.01,
            progress=progress,
            progress_window_seconds=180,
        )
    )
    assert not file.exists(), "a stalled loop no longer reports itself alive"
