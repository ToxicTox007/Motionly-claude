from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.production import validate_production_settings
from app.orchestration.client import (
    TemporalConnectionConfig,
    TemporalOrchestrationClient,
    connect_temporal_client,
)
from app.orchestration.contracts import (
    StepOutcome,
    StepOutcomeSignal,
    TemporalExecutionDescription,
)
from app.orchestration.dispatcher import DispatchBatchResult, OutboxDispatcher
from app.repositories.workflows import (
    TERMINAL_RUN_STATES,
    SupabaseWorkflowRepository,
    WorkflowRunRecord,
    WorkflowRunState,
    WorkflowStepRecord,
    WorkflowStepState,
)

logger = logging.getLogger(__name__)

# Run states the first-draft workflow itself drives. Later states (refining,
# exporting, ...) are owned by the API after the workflow completed, so a
# closed Temporal execution is normal for them.
WORKFLOW_DRIVEN_STATES = frozenset(
    {
        WorkflowRunState.UPLOADED,
        WorkflowRunState.ANALYZING,
        WorkflowRunState.PROPOSAL_READY,
        WorkflowRunState.AWAITING_APPROVAL,
        WorkflowRunState.ASSEMBLING,
        WorkflowRunState.DRAFT_RENDERING,
    }
)
# Temporal statuses that mean the execution ended without completing.
FAILED_TEMPORAL_STATUSES = frozenset({"failed", "terminated", "timed_out", "canceled"})
# Terminal runs are re-checked for this long (a cancel/fail signal to a still
# running Temporal execution may be pending); older terminal history is not
# rescanned on every pass (audit WJ-18).
RECENT_TERMINAL_WINDOW = timedelta(hours=24)


class ReconciliationRepositoryProtocol(Protocol):
    async def list_reconciliation_candidates_async(
        self,
        *,
        stale_before: datetime,
        limit: int,
        after_updated_at: datetime | None = None,
        after_id: UUID | None = None,
    ) -> list[WorkflowRunRecord]: ...

    async def list_workflows_async(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        limit: int,
        states: set[WorkflowRunState] | frozenset[WorkflowRunState] | None = None,
    ) -> list[WorkflowRunRecord]: ...

    async def list_steps_async(
        self,
        *,
        workflow_run_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
    ) -> list[WorkflowStepRecord]: ...


class ReconciliationTemporalProtocol(Protocol):
    async def describe_workflow(
        self, *, workflow_id: str
    ) -> TemporalExecutionDescription | None: ...

    async def signal_workflow(self, *, workflow_id: str, signal: StepOutcomeSignal) -> str: ...


@dataclass(frozen=True)
class ReconciliationAction:
    workflow_run_id: UUID
    action: str


@dataclass(frozen=True)
class ReconciliationReport:
    inspected: int
    repaired: int
    reported: int
    upload_workflows_started: int = 0
    actions: list[ReconciliationAction] = field(default_factory=list)
    dispatch: DispatchBatchResult | None = None


class WorkflowReconciler:
    def __init__(
        self,
        *,
        repository: ReconciliationRepositoryProtocol,
        temporal: ReconciliationTemporalProtocol,
        dispatcher: OutboxDispatcher | None = None,
        limit: int = 50,
    ) -> None:
        if not 1 <= limit <= 100:
            raise ValueError("reconciliation limit must be between 1 and 100")
        self.repository = repository
        self.temporal = temporal
        self.dispatcher = dispatcher
        self.limit = limit
        self._cursor_updated_at: datetime | None = None
        self._cursor_id: UUID | None = None

    async def reconcile_once(self, *, stale_before: datetime) -> ReconciliationReport:
        upload_workflows_started = 0
        upload_reconciler = getattr(self.repository, "reconcile_finalized_uploads_async", None)
        if upload_reconciler is not None:
            repaired_uploads = await upload_reconciler(limit=self.limit)
            upload_workflows_started = len(repaired_uploads)
        dispatch = await self.dispatcher.dispatch_once() if self.dispatcher else None
        runs = await self.repository.list_reconciliation_candidates_async(
            stale_before=stale_before,
            limit=self.limit,
            after_updated_at=self._cursor_updated_at,
            after_id=self._cursor_id,
        )
        if runs:
            last = runs[-1]
            self._cursor_updated_at = last.updated_at
            self._cursor_id = last.id
        else:
            self._cursor_updated_at = None
            self._cursor_id = None
        recent_terminal = getattr(self.repository, "list_recent_terminal_runs_async", None)
        if recent_terminal is not None:
            seen = {run.id for run in runs}
            runs = runs + [
                run
                for run in await recent_terminal(
                    updated_since=stale_before - RECENT_TERMINAL_WINDOW,
                    stale_before=stale_before,
                    limit=self.limit,
                )
                if run.id not in seen
            ]
        return await self._reconcile_runs(
            runs,
            dispatch=dispatch,
            upload_workflows_started=upload_workflows_started,
        )

    async def reconcile_tenant(
        self, *, workspace_id: UUID, project_id: UUID
    ) -> ReconciliationReport:
        dispatch = await self.dispatcher.dispatch_once() if self.dispatcher else None
        runs = await self.repository.list_workflows_async(
            workspace_id=workspace_id,
            project_id=project_id,
            limit=self.limit,
        )
        return await self._reconcile_runs(runs, dispatch=dispatch)

    async def _reconcile_runs(
        self,
        runs: list[WorkflowRunRecord],
        *,
        dispatch: DispatchBatchResult | None,
        upload_workflows_started: int = 0,
    ) -> ReconciliationReport:
        actions: list[ReconciliationAction] = []
        repaired = 0
        for run in runs:
            try:
                description = await self.temporal.describe_workflow(
                    workflow_id=run.temporal_workflow_id
                )
                action = await self._reconcile_run(run, description)
            except Exception as exc:  # noqa: BLE001 - one bad run must not stop the pass
                action = f"error_{_error_name(exc)}"
            if action is None:
                continue
            actions.append(ReconciliationAction(workflow_run_id=run.id, action=action))
            repaired += action.startswith(("signalled_", "repaired_"))
            # Every action is visible to operators (it used to be discarded).
            log = logger.warning if action.startswith(("reported_", "error_")) else logger.info
            log(
                "workflow_reconciliation_action",
                extra={
                    "workflow_run_id": str(run.id),
                    "workflow_state": run.state.value,
                    "action": action,
                },
            )
        return ReconciliationReport(
            inspected=len(runs),
            repaired=repaired,
            reported=len(actions) - repaired,
            upload_workflows_started=upload_workflows_started,
            actions=actions,
            dispatch=dispatch,
        )

    async def _reconcile_run(
        self,
        run: WorkflowRunRecord,
        description: TemporalExecutionDescription | None,
    ) -> str | None:
        if description is None:
            if run.state in TERMINAL_RUN_STATES:
                return None
            if run.temporal_run_id is not None and run.state in WORKFLOW_DRIVEN_STATES:
                # The execution was started (fenced ack) and is gone (namespace
                # retention after it closed): nothing will ever advance the run.
                return await self._fail_run(
                    run, "temporal_execution_missing", "repaired_failed_missing_temporal"
                )
            return "reported_missing_temporal_execution"
        temporal_running = description.status in {
            "running",
            "continued_as_new",
        }
        if run.state == WorkflowRunState.DRAFT_READY:
            if not temporal_running:
                return None
            if run.temporal_run_id is None:
                return "reported_awaiting_fenced_start_ack"
            steps = await self.repository.list_steps_async(
                workflow_run_id=run.id,
                workspace_id=run.workspace_id,
                project_id=run.project_id,
            )
            render_steps = [
                step
                for step in steps
                if step.step_kind == "render_draft" and step.state == WorkflowStepState.SUCCEEDED
            ]
            if len(render_steps) != 1:
                return "reported_draft_ready_success_evidence_missing"
            step = render_steps[0]
            expected_output = {
                "render_job_id": str(step.resource_id),
                "render_status": "complete",
                "failed": False,
            }
            if (
                step.resource_id is None
                or step.output_hash is None
                or step.output != expected_output
                or step.attempt_count < 1
            ):
                return "reported_draft_ready_success_evidence_invalid"
            signal = StepOutcomeSignal(
                event_id=(f"reconcile:{run.id}:{run.revision}:{step.id}:{step.output_hash}"),
                workflow_run_id=str(run.id),
                outcome=StepOutcome.SUCCEEDED,
                step_key=step.step_key,
                workflow_step_id=str(step.id),
                attempt_number=step.attempt_count,
                output_hash=step.output_hash,
                next_run_state=WorkflowRunState.DRAFT_READY.value,
            )
            await self.temporal.signal_workflow(
                workflow_id=run.temporal_workflow_id,
                signal=signal,
            )
            return "signalled_canonical_draft_ready"
        if run.state not in TERMINAL_RUN_STATES:
            if run.temporal_run_id is None:
                return "reported_awaiting_fenced_start_ack"
            if not temporal_running:
                if (
                    run.state in WORKFLOW_DRIVEN_STATES
                    and description.status in FAILED_TEMPORAL_STATUSES
                ):
                    # The workflow failed/was terminated on the Temporal side
                    # (or timed out) without the canonical run following (WJ-06).
                    return await self._fail_run(
                        run,
                        f"temporal_execution_{description.status}",
                        "repaired_failed_closed_temporal",
                    )
                return "reported_active_canonical_terminal_temporal"
            return None
        if not temporal_running:
            return None
        if run.state in {WorkflowRunState.CANCELLED, WorkflowRunState.SUPERSEDED}:
            signal = StepOutcomeSignal(
                event_id=f"reconcile:{run.id}:{run.revision}:{run.state.value}",
                workflow_run_id=str(run.id),
                outcome=StepOutcome.CANCELLED,
                error_code=run.state.value,
            )
            await self.temporal.signal_workflow(
                workflow_id=run.temporal_workflow_id,
                signal=signal,
            )
            return "signalled_canonical_cancellation"
        if run.state == WorkflowRunState.FAILED:
            signal = StepOutcomeSignal(
                event_id=f"reconcile:{run.id}:{run.revision}:failed",
                workflow_run_id=str(run.id),
                outcome=StepOutcome.TERMINAL_FAILURE,
                error_code="canonical_workflow_failed",
            )
            await self.temporal.signal_workflow(
                workflow_id=run.temporal_workflow_id,
                signal=signal,
            )
            return "signalled_canonical_failure"
        # EXPORT_READY lacks the canonical step output hash. A success signal here
        # would invent a result, so the reconciler reports the mismatch only.
        return "reported_completed_canonical_running_temporal"


    async def _fail_run(self, run: WorkflowRunRecord, error_code: str, action: str) -> str:
        fail = getattr(self.repository, "fail_workflow_run_async", None)
        if fail is None:
            return "reported_active_canonical_terminal_temporal"
        await fail(
            workflow_run_id=run.id,
            workspace_id=run.workspace_id,
            project_id=run.project_id,
            error_code=error_code,
            error_message="The workflow stopped before it finished.",
        )
        return action


def _error_name(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code[:80]
    return type(exc).__name__.lower()[:80]


async def run_reconciler_loop(
    reconciler: WorkflowReconciler,
    settings: Settings,
    *,
    once: bool = False,
    clock: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    progress: Callable[[], None] | None = None,
) -> ReconciliationReport | None:
    now = clock or (lambda: datetime.now(UTC))
    failures = 0
    while True:
        stale_before = now() - timedelta(seconds=settings.workflow_reconciler_staleness_seconds)
        try:
            report = await reconciler.reconcile_once(stale_before=stale_before)
        except Exception as exc:  # noqa: BLE001 - the loop must outlive transient faults
            if once:
                raise
            failures += 1
            logger.error(
                "workflow_reconciliation_pass_failed",
                extra={"error_code": _error_name(exc), "consecutive_failures": failures},
            )
            await sleeper(
                min(300.0, settings.workflow_reconciler_interval_seconds * (2 ** min(failures, 4)))
            )
            continue
        failures = 0
        if progress is not None:
            progress()
        inspected = getattr(report, "inspected", 0)
        if inspected or getattr(report, "upload_workflows_started", 0):
            logger.info(
                "workflow_reconciliation_pass",
                extra={
                    "inspected": inspected,
                    "repaired": getattr(report, "repaired", 0),
                    "reported": getattr(report, "reported", 0),
                    "upload_workflows_started": getattr(report, "upload_workflows_started", 0),
                },
            )
        if once:
            return report
        await sleeper(settings.workflow_reconciler_interval_seconds)


async def run_reconciler(
    settings: Settings,
    *,
    once: bool = False,
    clock: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    progress: Callable[[], None] | None = None,
) -> ReconciliationReport | None:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    config = TemporalConnectionConfig.from_settings(settings)
    client = await connect_temporal_client(config)
    temporal = TemporalOrchestrationClient(client, task_queue=config.task_queue)
    repository = SupabaseWorkflowRepository(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )
    dispatcher = OutboxDispatcher(
        repository=repository,
        temporal=temporal,
        lease_owner=settings.temporal_dispatcher_id or f"reconciler-dispatcher-{os.getpid()}",
        batch_size=settings.workflow_dispatcher_batch_size,
        lease_seconds=settings.workflow_dispatcher_lease_seconds,
        base_backoff_seconds=settings.workflow_dispatcher_backoff_initial_seconds,
        max_backoff_seconds=settings.workflow_dispatcher_backoff_max_seconds,
    )
    reconciler = WorkflowReconciler(
        repository=repository,
        temporal=temporal,
        dispatcher=dispatcher,
        limit=settings.workflow_reconciler_batch_size,
    )
    return await run_reconciler_loop(
        reconciler,
        settings,
        once=once,
        clock=clock,
        sleeper=sleeper,
        progress=progress,
    )


def main() -> None:
    settings = get_settings()
    validate_production_settings(settings)
    if settings.process_role.strip().lower() != "workflow-reconciler":
        raise RuntimeError("PROCESS_ROLE must be workflow-reconciler for this entrypoint")
    asyncio.run(run_reconciler(settings))


if __name__ == "__main__":
    main()
