from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from app.orchestration.contracts import (
    ApprovalDecision,
    ApprovalGateSignal,
    EnqueueStepRequest,
    EnqueueStepResult,
    EnsureDraftRenderRequest,
    EnsureDraftRenderResult,
    EnsureProposalRequest,
    EnsureProposalResult,
    StepOutcome,
    StepOutcomeSignal,
    WorkflowExecutionState,
    WorkflowResult,
    WorkflowStartInput,
    WorkflowStatus,
    assert_identifier_only_history,
    attempt_key_for,
)


@workflow.defn(name="motionly.identifier_orchestration")
class MotionlyWorkflow:
    def __init__(self) -> None:
        self._workflow_run_id: str | None = None
        self._state = WorkflowExecutionState.STARTING
        self._current_step_key: str | None = None
        self._current_attempt = 0
        self._completed_step_keys: set[str] = set()
        self._signals: list[StepOutcomeSignal] = []
        self._approval_signals: list[ApprovalGateSignal] = []
        self._proposal_id: str | None = None
        self._seen_event_ids: set[str] = set()
        self._ignored_signal_count = 0
        self._error_code: str | None = None

    @workflow.run
    async def run(self, input: WorkflowStartInput) -> WorkflowResult:
        assert_identifier_only_history(input)
        self._workflow_run_id = input.workflow_run_id
        self._state = WorkflowExecutionState.RUNNING
        self._drop_foreign_prestart_signals()
        terminal = self._workflow_terminal_signal()
        if terminal is not None:
            return self._terminal_result(terminal)

        for step in input.steps:
            self._current_step_key = step.step_key
            for attempt_number in range(1, step.max_attempts + 1):
                self._current_attempt = attempt_number
                terminal = self._workflow_terminal_signal()
                if terminal is not None:
                    return self._terminal_result(terminal)
                attempt_key = attempt_key_for(
                    input.workflow_run_id,
                    step.step_key,
                    attempt_number,
                )
                request = EnqueueStepRequest(
                    workflow_run_id=input.workflow_run_id,
                    workflow_step_id=None,
                    workspace_id=input.workspace_id,
                    project_id=input.project_id,
                    step_key=step.step_key,
                    step_kind=step.step_kind,
                    job_kind=step.job_kind,
                    queue_name=step.queue_name,
                    attempt_number=attempt_number,
                    attempt_key=attempt_key,
                    input_hash=step.input_hash,
                    next_run_state=step.next_run_state,
                    resource_id=step.resource_id,
                    max_attempts=step.max_attempts,
                )
                await workflow.execute_activity(
                    "enqueue_bullmq_step",
                    request,
                    result_type=EnqueueStepResult,
                    activity_id=f"enqueue:{step.step_key}:{attempt_number}",
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2.0,
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=5,
                        non_retryable_error_types=["bullmq_job_identity_conflict"],
                    ),
                )
                await workflow.wait_condition(
                    lambda step_key=step.step_key, attempt=attempt_number: self._has_outcome(
                        step_key, attempt
                    )
                )
                outcome = self._pop_outcome(step.step_key, attempt_number)
                if outcome.step_key is None:
                    return self._terminal_result(outcome)
                if outcome.outcome == StepOutcome.SUCCEEDED:
                    self._completed_step_keys.add(step.step_key)
                    self._discard_completed_step_signals(step.step_key)
                    break
                if outcome.outcome == StepOutcome.CANCELLED:
                    return self._terminal_result(outcome)
                if outcome.outcome == StepOutcome.TERMINAL_FAILURE:
                    return self._terminal_result(outcome)
                if attempt_number == step.max_attempts:
                    self._state = WorkflowExecutionState.FAILED
                    self._error_code = outcome.error_code or "step_attempts_exhausted"
                    return self._result()
            else:
                self._state = WorkflowExecutionState.FAILED
                self._error_code = "step_attempts_exhausted"
                return self._result()

        if input.workflow_version == "first-draft-v2":
            terminal = self._workflow_terminal_signal()
            if terminal is not None:
                return self._terminal_result(terminal)
            proposal = await workflow.execute_activity(
                "ensure_workflow_proposal",
                EnsureProposalRequest(
                    workflow_run_id=input.workflow_run_id,
                    workspace_id=input.workspace_id,
                    project_id=input.project_id,
                    request_hash=input.request_hash,
                ),
                result_type=EnsureProposalResult,
                activity_id="ensure-proposal-v2",
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=5,
                    non_retryable_error_types=[
                        "workflow_proposal_rejected",
                        "workflow_proposal_response_invalid",
                        "workflow_proposal_identity_conflict",
                        "workflow_proposal_transport_missing",
                    ],
                ),
            )
            assert_identifier_only_history(proposal)
            self._proposal_id = proposal.proposal_id
            self._discard_foreign_approval_signals()
            terminal = self._workflow_terminal_signal()
            if terminal is not None:
                return self._terminal_result(terminal)

            planning_step_key = "generate-proposal-v2"
            self._current_step_key = planning_step_key
            planning_max_attempts = 3
            for attempt_number in range(1, planning_max_attempts + 1):
                self._current_attempt = attempt_number
                terminal = self._workflow_terminal_signal()
                if terminal is not None:
                    return self._terminal_result(terminal)
                attempt_key = attempt_key_for(
                    input.workflow_run_id,
                    planning_step_key,
                    attempt_number,
                )
                request = EnqueueStepRequest(
                    workflow_run_id=input.workflow_run_id,
                    workflow_step_id=None,
                    workspace_id=input.workspace_id,
                    project_id=input.project_id,
                    step_key=planning_step_key,
                    step_kind="generate_proposal",
                    job_kind="edit_plan",
                    queue_name="planning",
                    attempt_number=attempt_number,
                    attempt_key=attempt_key,
                    input_hash=proposal.context_digest,
                    next_run_state="awaiting_approval",
                    resource_id=proposal.proposal_id,
                    max_attempts=planning_max_attempts,
                )
                await workflow.execute_activity(
                    "enqueue_bullmq_step",
                    request,
                    result_type=EnqueueStepResult,
                    activity_id=f"enqueue:{planning_step_key}:{attempt_number}",
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2.0,
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=5,
                        non_retryable_error_types=["bullmq_job_identity_conflict"],
                    ),
                )
                await workflow.wait_condition(
                    lambda attempt=attempt_number: self._has_outcome(planning_step_key, attempt)
                )
                outcome = self._pop_outcome(planning_step_key, attempt_number)
                if outcome.step_key is None:
                    return self._terminal_result(outcome)
                if outcome.outcome == StepOutcome.SUCCEEDED:
                    self._completed_step_keys.add(planning_step_key)
                    self._discard_completed_step_signals(planning_step_key)
                    break
                if outcome.outcome in {StepOutcome.CANCELLED, StepOutcome.TERMINAL_FAILURE}:
                    return self._terminal_result(outcome)
                if attempt_number == planning_max_attempts:
                    self._state = WorkflowExecutionState.FAILED
                    self._error_code = outcome.error_code or "step_attempts_exhausted"
                    return self._result()

            self._current_step_key = "approval-gate-v2"
            self._current_attempt = 0
            await workflow.wait_condition(
                lambda: self._has_workflow_terminal_signal() or self._has_approval_signal()
            )
            terminal = self._workflow_terminal_signal()
            if terminal is not None:
                return self._terminal_result(terminal)
            decision = self._pop_approval_signal()
            if decision.decision == ApprovalDecision.REJECT:
                return self._approval_result(decision)

            render = await workflow.execute_activity(
                "ensure_workflow_draft_render",
                EnsureDraftRenderRequest(
                    workflow_run_id=input.workflow_run_id,
                    workspace_id=input.workspace_id,
                    project_id=input.project_id,
                    request_hash=input.request_hash,
                ),
                result_type=EnsureDraftRenderResult,
                activity_id="ensure-draft-render-v2",
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=5,
                    non_retryable_error_types=[
                        "workflow_render_rejected",
                        "workflow_render_response_invalid",
                        "workflow_render_identity_conflict",
                        "workflow_render_transport_missing",
                    ],
                ),
            )
            assert_identifier_only_history(render)
            if render.source_plan_revision != decision.edit_plan_revision:
                self._state = WorkflowExecutionState.FAILED
                self._error_code = "workflow_render_plan_revision_conflict"
                return self._result()

            render_step_key = "render-first-draft-v2"
            self._current_step_key = render_step_key
            render_max_attempts = 3
            for attempt_number in range(1, render_max_attempts + 1):
                self._current_attempt = attempt_number
                terminal = self._workflow_terminal_signal()
                if terminal is not None:
                    return self._terminal_result(terminal)
                attempt_key = attempt_key_for(
                    input.workflow_run_id,
                    render_step_key,
                    attempt_number,
                )
                request = EnqueueStepRequest(
                    workflow_run_id=input.workflow_run_id,
                    workflow_step_id=None,
                    workspace_id=input.workspace_id,
                    project_id=input.project_id,
                    step_key=render_step_key,
                    step_kind="render_draft",
                    job_kind="render",
                    queue_name=render.render_queue,
                    attempt_number=attempt_number,
                    attempt_key=attempt_key,
                    input_hash=render.props_hash,
                    next_run_state="draft_ready",
                    resource_id=render.render_job_id,
                    max_attempts=render_max_attempts,
                )
                await workflow.execute_activity(
                    "enqueue_bullmq_step",
                    request,
                    result_type=EnqueueStepResult,
                    activity_id=f"enqueue:{render_step_key}:{attempt_number}",
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2.0,
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=5,
                        non_retryable_error_types=["bullmq_job_identity_conflict"],
                    ),
                )
                await workflow.wait_condition(
                    lambda attempt=attempt_number: self._has_outcome(render_step_key, attempt)
                )
                outcome = self._pop_outcome(render_step_key, attempt_number)
                if outcome.step_key is None:
                    return self._terminal_result(outcome)
                if outcome.outcome == StepOutcome.SUCCEEDED:
                    self._completed_step_keys.add(render_step_key)
                    self._discard_completed_step_signals(render_step_key)
                    break
                if outcome.outcome in {StepOutcome.CANCELLED, StepOutcome.TERMINAL_FAILURE}:
                    return self._terminal_result(outcome)
                if attempt_number == render_max_attempts:
                    self._state = WorkflowExecutionState.FAILED
                    self._error_code = outcome.error_code or "step_attempts_exhausted"
                    return self._result()

            self._state = WorkflowExecutionState.COMPLETED
            self._error_code = None
            self._current_step_key = None
            self._current_attempt = 0
            return self._result()

        self._state = WorkflowExecutionState.COMPLETED
        self._current_step_key = None
        self._current_attempt = 0
        return self._result()

    @workflow.signal(name="step_outcome")
    def step_outcome(self, signal: StepOutcomeSignal) -> None:
        assert_identifier_only_history(signal)
        if signal.event_id in self._seen_event_ids:
            self._ignored_signal_count += 1
            return
        self._seen_event_ids.add(signal.event_id)
        if self._workflow_run_id is not None and signal.workflow_run_id != self._workflow_run_id:
            self._ignored_signal_count += 1
            return
        if signal.outcome == StepOutcome.CANONICAL_NOTICE:
            self._ignored_signal_count += 1
            return
        if signal.step_key in self._completed_step_keys:
            self._ignored_signal_count += 1
            return
        if (
            signal.step_key == self._current_step_key
            and signal.attempt_number not in {0}
            and signal.attempt_number < self._current_attempt
        ):
            self._ignored_signal_count += 1
            return
        self._signals.append(signal)

    @workflow.signal(name="approval_gate")
    def approval_gate(self, signal: ApprovalGateSignal) -> None:
        assert_identifier_only_history(signal)
        if signal.event_id in self._seen_event_ids:
            self._ignored_signal_count += 1
            return
        self._seen_event_ids.add(signal.event_id)
        if self._workflow_run_id is not None and signal.workflow_run_id != self._workflow_run_id:
            self._ignored_signal_count += 1
            return
        if self._proposal_id is not None and signal.proposal_id != self._proposal_id:
            self._ignored_signal_count += 1
            return
        self._approval_signals.append(signal)

    @workflow.query(name="status")
    def status(self) -> WorkflowStatus:
        return WorkflowStatus(
            workflow_run_id=self._workflow_run_id,
            state=self._state,
            current_step_key=self._current_step_key,
            current_attempt=self._current_attempt,
            completed_step_count=len(self._completed_step_keys),
            pending_signal_count=len(self._signals) + len(self._approval_signals),
            ignored_signal_count=self._ignored_signal_count,
            error_code=self._error_code,
        )

    @workflow.query(name="pending_signal_count")
    def pending_signal_count(self) -> int:
        return len(self._signals) + len(self._approval_signals)

    def _has_outcome(self, step_key: str, attempt_number: int) -> bool:
        return any(
            signal.step_key is None
            or (signal.step_key == step_key and signal.attempt_number in {0, attempt_number})
            for signal in self._signals
        )

    def _pop_outcome(self, step_key: str, attempt_number: int) -> StepOutcomeSignal:
        for index, signal in enumerate(self._signals):
            if signal.step_key is None or (
                signal.step_key == step_key and signal.attempt_number in {0, attempt_number}
            ):
                return self._signals.pop(index)
        raise RuntimeError("wait condition resolved without a matching signal")

    def _has_workflow_terminal_signal(self) -> bool:
        return any(signal.step_key is None for signal in self._signals)

    def _has_approval_signal(self) -> bool:
        return any(
            self._proposal_id is None or signal.proposal_id == self._proposal_id
            for signal in self._approval_signals
        )

    def _pop_approval_signal(self) -> ApprovalGateSignal:
        for index, signal in enumerate(self._approval_signals):
            if self._proposal_id is None or signal.proposal_id == self._proposal_id:
                return self._approval_signals.pop(index)
        raise RuntimeError("approval wait resolved without a matching signal")

    def _workflow_terminal_signal(self) -> StepOutcomeSignal | None:
        for index, signal in enumerate(self._signals):
            if signal.step_key is None:
                return self._signals.pop(index)
        return None

    def _approval_result(self, signal: ApprovalGateSignal) -> WorkflowResult:
        if signal.decision == ApprovalDecision.REJECT:
            self._state = WorkflowExecutionState.CANCELLED
            self._error_code = "proposal_rejected"
        else:
            self._state = WorkflowExecutionState.COMPLETED
            self._error_code = None
            self._current_step_key = None
            self._current_attempt = 0
        return self._result()

    def _terminal_result(self, signal: StepOutcomeSignal) -> WorkflowResult:
        if signal.outcome == StepOutcome.CANCELLED:
            self._state = WorkflowExecutionState.CANCELLED
            self._error_code = signal.error_code
        else:
            self._state = WorkflowExecutionState.FAILED
            self._error_code = signal.error_code or "workflow_terminal_failure"
        return self._result()

    def _result(self) -> WorkflowResult:
        assert self._workflow_run_id is not None
        return WorkflowResult(
            workflow_run_id=self._workflow_run_id,
            state=self._state,
            completed_step_count=len(self._completed_step_keys),
            current_step_key=self._current_step_key,
            error_code=self._error_code,
        )

    def _drop_foreign_prestart_signals(self) -> None:
        retained = []
        for signal in self._signals:
            if signal.workflow_run_id == self._workflow_run_id:
                retained.append(signal)
            else:
                self._ignored_signal_count += 1
        self._signals = retained
        retained_approvals = []
        for signal in self._approval_signals:
            if signal.workflow_run_id == self._workflow_run_id:
                retained_approvals.append(signal)
            else:
                self._ignored_signal_count += 1
        self._approval_signals = retained_approvals

    def _discard_foreign_approval_signals(self) -> None:
        retained = []
        for signal in self._approval_signals:
            if signal.proposal_id == self._proposal_id:
                retained.append(signal)
            else:
                self._ignored_signal_count += 1
        self._approval_signals = retained

    def _discard_completed_step_signals(self, step_key: str) -> None:
        retained = []
        for signal in self._signals:
            if signal.step_key == step_key:
                self._ignored_signal_count += 1
            else:
                retained.append(signal)
        self._signals = retained
