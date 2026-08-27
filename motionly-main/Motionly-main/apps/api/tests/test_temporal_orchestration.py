from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.errors import ApiError
from app.core.config import Settings
from app.orchestration.activities import BullMQActivities
from app.orchestration.client import TemporalConnectionConfig, TemporalOrchestrationClient
from app.orchestration.contracts import (
    ApprovalGateSignal,
    EnqueueStepRequest,
    EnsureDraftRenderRequest,
    EnsureDraftRenderResult,
    EnsureProposalRequest,
    EnsureProposalResult,
    HistoryPayloadRejected,
    StepOutcome,
    StepOutcomeSignal,
    TemporalExecutionDescription,
    TemporalStartResult,
    WorkflowExecutionState,
    WorkflowStartInput,
    WorkflowStepSpec,
    assert_identifier_only_history,
    job_id_for,
    signal_from_outbox,
    workflow_start_for_run,
    workflow_steps_for,
)
from app.orchestration.dispatcher import DispatchBatchResult, OutboxDispatcher, run_dispatcher
from app.orchestration.history import validate_history_document
from app.orchestration.reconciler import (
    WorkflowReconciler,
    run_reconciler,
    run_reconciler_loop,
)
from app.orchestration.replay import replay_history_document
from app.orchestration.worker import create_worker
from app.orchestration.workflow import MotionlyWorkflow
from app.repositories.workflows import (
    InMemoryWorkflowRepository,
    OutboxState,
    SupabaseWorkflowRepository,
    WorkflowRunState,
    WorkflowStepState,
)
from app.services.queue import QueueRegistry

HASH = "a" * 64


def _workflow_values() -> dict[str, object]:
    return {
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "uploaded_video_id": uuid4(),
        "source_media_revision": 1,
        "source_object_reference_id": uuid4(),
        "workflow_type": "first_draft",
        "workflow_version": "first-draft-v1",
        "idempotency_key": "temporal-test-idempotency",
        "request_hash": HASH,
        "started_by": uuid4(),
        "input": {},
    }


class _TemporalFake:
    def __init__(self) -> None:
        self.starts: list[tuple[str, WorkflowStartInput]] = []
        self.signals: list[tuple[str, StepOutcomeSignal]] = []
        self.descriptions: dict[str, TemporalExecutionDescription | None] = {}
        self.start_error: Exception | None = None
        self.signal_error: Exception | None = None

    async def start_workflow(
        self, *, workflow_id: str, input: WorkflowStartInput
    ) -> TemporalStartResult:
        if self.start_error:
            raise self.start_error
        self.starts.append((workflow_id, input))
        return TemporalStartResult(workflow_id=workflow_id, run_id="temporal-run-stable")

    async def signal_workflow(self, *, workflow_id: str, signal: StepOutcomeSignal) -> str:
        if self.signal_error:
            raise self.signal_error
        self.signals.append((workflow_id, signal))
        return f"signal:{signal.event_id}"

    async def describe_workflow(self, *, workflow_id: str) -> TemporalExecutionDescription | None:
        return self.descriptions.get(workflow_id)


def test_temporal_connection_uses_tls_enabled_environment_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMPORAL_TLS", "true")
    monkeypatch.setenv("TEMPORAL_TLS_ENABLED", "false")
    assert TemporalConnectionConfig.from_env().tls_enabled is False

    monkeypatch.setenv("TEMPORAL_TLS_ENABLED", "true")
    assert TemporalConnectionConfig.from_env().tls_enabled is True


def test_self_hosted_temporal_client_requires_ca_client_certificate_and_server_name() -> None:
    config = TemporalConnectionConfig(
        target_host="temporal:7233",
        namespace="motionly",
        task_queue="motionly-orchestration",
        deployment_mode="self_hosted",
        tls_enabled=True,
    )
    with pytest.raises(ValueError, match="Self-hosted Temporal requires mTLS"):
        config.validate()

    TemporalConnectionConfig(
        target_host="temporal:7233",
        namespace="motionly",
        task_queue="motionly-orchestration",
        deployment_mode="self_hosted",
        tls_enabled=True,
        server_root_ca_path="/run/secrets/temporal_ca_cert",
        client_cert_path="/run/secrets/temporal_dispatcher_cert",
        client_private_key_path="/run/secrets/temporal_dispatcher_key",
        server_name="temporal",
    ).validate()


def test_identifier_only_contracts_use_exact_known_schemas() -> None:
    valid = WorkflowStepSpec(
        step_key="analyze-source-v1",
        step_kind="analyze_source",
        job_kind="visual_analysis",
        queue_name="visual_analysis",
        input_hash=HASH,
        next_run_state="proposal_ready",
        resource_id=str(uuid4()),
    )
    assert_identifier_only_history(valid)

    for invalid in (
        {"api_key": "not-allowed"},
        {"content": "short arbitrary prose"},
        {"text": "short arbitrary prose"},
        {"resource_id": "https://storage.example/video"},
        {"resource_id": "//storage.example/video"},
        {"resource_id": "clip.mp4"},
        {"resource_id": b"media"},
        {"model_payload": {"temperature": 0}},
        {
            "step_key": "x" * 600,
            "step_kind": "analyze_source",
            "job_kind": "visual_analysis",
            "queue_name": "visual_analysis",
            "input_hash": HASH,
        },
    ):
        with pytest.raises(HistoryPayloadRejected):
            assert_identifier_only_history(invalid)


def test_history_validator_decodes_and_rejects_sensitive_temporal_payload() -> None:
    payload = {
        "metadata": {
            "encoding": base64.b64encode(b"json/plain").decode(),
        },
        "data": base64.b64encode(json.dumps({"password": "secret"}).encode()).decode(),
    }
    with pytest.raises(HistoryPayloadRejected):
        validate_history_document({"events": [{"attributes": {"input": payload}}]})


def test_dispatcher_fetches_canonical_start_and_binds_temporal_run_once() -> None:
    repository = InMemoryWorkflowRepository()
    run, _ = repository.start_workflow(**_workflow_values())
    temporal = _TemporalFake()
    dispatcher = OutboxDispatcher(
        repository=repository,
        temporal=temporal,
        lease_owner="dispatcher-test",
    )

    result = asyncio.run(dispatcher.dispatch_once())

    assert result.claimed == result.dispatched == 1
    assert repository.runs[run.id].temporal_run_id == "temporal-run-stable"
    assert temporal.starts[0][0] == run.temporal_workflow_id
    assert temporal.starts[0][1].workflow_run_id == str(run.id)
    start_message = next(iter(repository.outbox.values()))
    assert "temporal_workflow_id" not in start_message.payload
    assert start_message.state == OutboxState.DISPATCHED


def test_dispatcher_signals_by_stable_id_and_acks_duplicate_safe_event() -> None:
    repository = InMemoryWorkflowRepository()
    values = _workflow_values()
    run, _ = repository.start_workflow(**values)
    temporal = _TemporalFake()
    dispatcher = OutboxDispatcher(
        repository=repository,
        temporal=temporal,
        lease_owner="dispatcher-signal",
    )
    asyncio.run(dispatcher.dispatch_once())
    begun = repository.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-source-v1",
        step_kind="analyze_source",
        attempt_key="analyze-attempt-test",
        lease_owner="worker-test",
        lease_seconds=60,
        input_hash=HASH,
    )
    assert begun.attempt is not None
    repository.complete_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-source-v1",
        claim_token=begun.attempt.claim_token,
        output_hash="b" * 64,
        output={"analysis_id": "identifier-only-result"},
        next_run_state=WorkflowRunState.PROPOSAL_READY,
    )

    first = asyncio.run(dispatcher.dispatch_once())
    second = asyncio.run(dispatcher.dispatch_once())

    assert first.dispatched == 1
    assert second.claimed == 0
    workflow_id, signal = temporal.signals[0]
    assert workflow_id == run.temporal_workflow_id
    assert signal.outcome == StepOutcome.SUCCEEDED
    assert signal.output_hash == "b" * 64


def test_dispatcher_failure_uses_bounded_backoff_and_stale_fencing() -> None:
    repository = InMemoryWorkflowRepository()
    repository.start_workflow(**_workflow_values())
    temporal = _TemporalFake()
    temporal.start_error = RuntimeError("Temporal unavailable")
    now = datetime.now(UTC)
    dispatcher = OutboxDispatcher(
        repository=repository,
        temporal=temporal,
        lease_owner="dispatcher-failure",
        base_backoff_seconds=3,
        max_backoff_seconds=12,
        clock=lambda: now,
    )

    result = asyncio.run(dispatcher.dispatch_once())
    message = next(iter(repository.outbox.values()))

    assert result.failed == 1
    assert message.state == OutboxState.FAILED
    assert message.available_at == now + timedelta(seconds=3)
    assert dispatcher.backoff_seconds(99) == 12

    class _StaleAckRepository(InMemoryWorkflowRepository):
        async def ack_outbox_async(self, **values: object):
            raise ApiError(409, "outbox_stale_claim", "Outbox claim is stale.")

    stale_repository = _StaleAckRepository()
    stale_repository.start_workflow(**_workflow_values())
    stale = OutboxDispatcher(
        repository=stale_repository,
        temporal=_TemporalFake(),
        lease_owner="dispatcher-stale",
    )
    stale_result = asyncio.run(stale.dispatch_once())
    assert stale_result.stale == 1
    assert stale_result.failed == 0


def test_activity_enqueue_is_deterministic_idempotent_and_identifier_only() -> None:
    registry = QueueRegistry()
    activity = BullMQActivities(registry)
    request = EnqueueStepRequest(
        workflow_run_id=str(uuid4()),
        workflow_step_id=None,
        workspace_id=str(uuid4()),
        project_id=str(uuid4()),
        step_key="analyze-source-v1",
        step_kind="analyze_source",
        job_kind="visual_analysis",
        queue_name="visual_analysis",
        attempt_number=1,
        attempt_key="temporal-attempt:identifier-0001",
        input_hash=HASH,
        next_run_state="proposal_ready",
        resource_id=str(uuid4()),
    )

    first = asyncio.run(activity.enqueue_bullmq_step(request))
    second = asyncio.run(activity.enqueue_bullmq_step(request))

    assert first == second
    assert first.job_id == job_id_for(request)
    assert len(registry.jobs) == 1
    job = registry.jobs[first.job_id]
    assert job.workflow_run_id == UUID(request.workflow_run_id)
    assert job.kind == "visual_analysis"
    assert job.step_kind == "analyze_source"
    assert job.resource_id == UUID(request.resource_id)
    assert job.attempt_key == request.attempt_key
    assert job.max_attempts == 1
    assert job.workflow_attempt_number == 1
    assert job.workflow_max_attempts == 3
    assert job.input_hash == HASH
    assert job.next_run_state == "proposal_ready"


def test_workflow_signal_handler_deduplicates_and_buffers_out_of_order() -> None:
    run_id = str(uuid4())
    workflow_instance = MotionlyWorkflow()
    future = StepOutcomeSignal(
        event_id="event:future-step:0001",
        workflow_run_id=run_id,
        step_key="render-draft-v1",
        attempt_number=1,
        outcome=StepOutcome.SUCCEEDED,
        output_hash="c" * 64,
    )

    workflow_instance.step_outcome(future)
    workflow_instance.step_outcome(future)
    status = workflow_instance.status()

    assert status.pending_signal_count == 1
    assert status.ignored_signal_count == 1
    assert workflow_instance.pending_signal_count() == 1


def test_sibling_attempt_cancellation_is_a_notice_not_workflow_cancellation() -> None:
    run_id = str(uuid4())
    notice = signal_from_outbox(
        event_type="workflow.siblings.cancelled",
        event_id=f"terminal-siblings:{run_id}:2",
        payload={
            "workflow_run_id": run_id,
            "state": "failed",
            "cancelled_attempt_count": 1,
        },
    )

    assert notice.outcome == StepOutcome.CANONICAL_NOTICE
    workflow_instance = MotionlyWorkflow()
    workflow_instance.step_outcome(notice)
    assert workflow_instance.status().state == WorkflowExecutionState.STARTING
    assert workflow_instance.status().pending_signal_count == 0


def test_reconciler_only_repairs_canonical_terminal_facts() -> None:
    repository = InMemoryWorkflowRepository()
    values = _workflow_values()
    cancelled, _ = repository.start_workflow(**values)
    cancelled.state = WorkflowRunState.CANCELLED
    cancelled.completed_at = datetime.now(UTC)
    temporal = _TemporalFake()
    temporal.descriptions[cancelled.temporal_workflow_id] = TemporalExecutionDescription(
        workflow_id=cancelled.temporal_workflow_id,
        run_id="temporal-cancelled",
        status="running",
    )
    reconciler = WorkflowReconciler(repository=repository, temporal=temporal, limit=10)

    report = asyncio.run(
        reconciler.reconcile_tenant(
            workspace_id=cancelled.workspace_id,
            project_id=cancelled.project_id,
        )
    )

    assert report.repaired == 1
    assert temporal.signals[0][1].outcome == StepOutcome.CANCELLED

    active_repository = InMemoryWorkflowRepository()
    active, _ = active_repository.start_workflow(**_workflow_values())
    active_temporal = _TemporalFake()
    active_reconciler = WorkflowReconciler(
        repository=active_repository,
        temporal=active_temporal,
    )
    active_report = asyncio.run(
        active_reconciler.reconcile_tenant(
            workspace_id=active.workspace_id,
            project_id=active.project_id,
        )
    )
    assert active_report.repaired == 0
    assert active_report.actions[0].action == "reported_missing_temporal_execution"
    assert active_temporal.starts == []
    assert active_temporal.signals == []


def test_reconciler_never_invents_success_for_export_ready() -> None:
    repository = InMemoryWorkflowRepository()
    run, _ = repository.start_workflow(**{**_workflow_values(), "workflow_type": "export"})
    run.state = WorkflowRunState.EXPORT_READY
    run.completed_at = datetime.now(UTC)
    temporal = _TemporalFake()
    temporal.descriptions[run.temporal_workflow_id] = TemporalExecutionDescription(
        workflow_id=run.temporal_workflow_id,
        run_id="temporal-export",
        status="running",
    )
    reconciler = WorkflowReconciler(repository=repository, temporal=temporal)

    report = asyncio.run(
        reconciler.reconcile_tenant(
            workspace_id=run.workspace_id,
            project_id=run.project_id,
        )
    )

    assert report.repaired == 0
    assert report.actions[0].action == "reported_completed_canonical_running_temporal"
    assert temporal.signals == []


def test_supabase_get_workflow_query_is_tenant_scoped() -> None:
    run_id, workspace_id, project_id = uuid4(), uuid4(), uuid4()
    row = {
        "id": str(run_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "uploaded_video_id": str(uuid4()),
        "source_media_revision": 1,
        "source_object_reference_id": str(uuid4()),
        "workflow_type": "first_draft",
        "workflow_version": "first-draft-v1",
        "idempotency_key": "tenant-safe-read",
        "request_hash": HASH,
        "temporal_workflow_id": "motionly:tenant-safe-read",
        "state": "uploaded",
    }
    request = httpx.Request("GET", "https://supabase.test/workflow_runs")
    response = httpx.Response(200, request=request, json=[row])
    calls: list[dict[str, object]] = []

    class _Client:
        def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
            calls.append({"method": method, "path": path, **kwargs})
            return response

    repository = SupabaseWorkflowRepository(
        supabase_url="https://supabase.test",
        service_role_key="test-role-key",
    )
    repository.client = _Client()  # type: ignore[assignment]

    loaded = repository.get_workflow(
        workflow_run_id=run_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )

    assert loaded.id == run_id
    params = calls[0]["params"]
    assert isinstance(params, dict)
    assert params["workspace_id"] == f"eq.{workspace_id}"
    assert params["project_id"] == f"eq.{project_id}"


def test_replay_entrypoint_validates_then_invokes_sdk_replayer() -> None:
    class _Replayer:
        def __init__(self) -> None:
            self.histories = []

        async def replay_workflow(self, history):
            self.histories.append(history)
            return SimpleNamespace(replay_failure=None)

    replayer = _Replayer()
    result = asyncio.run(
        replay_history_document(
            {"events": []},
            workflow_id="motionly:replay:test",
            replayer=replayer,  # type: ignore[arg-type]
        )
    )

    assert result.replay_failure is None
    assert replayer.histories[0].workflow_id == "motionly:replay:test"


@pytest.mark.skipif(
    os.getenv("RUN_TEMPORAL_TIME_SKIPPING_TESTS") != "1",
    reason="set RUN_TEMPORAL_TIME_SKIPPING_TESTS=1 to download/run the local SDK server",
)
def test_temporal_workflow_time_skipping_signal_completion() -> None:
    async def scenario() -> None:
        from temporalio import activity
        from temporalio.testing import WorkflowEnvironment

        proposal_id = str(uuid4())
        render_id = str(uuid4())

        class ProposalActivity:
            @activity.defn(name="ensure_workflow_proposal")
            async def ensure_workflow_proposal(
                self, request: EnsureProposalRequest
            ) -> EnsureProposalResult:
                return EnsureProposalResult(
                    workflow_run_id=request.workflow_run_id,
                    proposal_id=proposal_id,
                    proposal_revision=1,
                    request_id="b" * 64,
                    context_digest="c" * 64,
                    proposal_state="queued",
                )

        class RenderActivity:
            @activity.defn(name="ensure_workflow_draft_render")
            async def ensure_workflow_draft_render(
                self, request: EnsureDraftRenderRequest
            ) -> EnsureDraftRenderResult:
                return EnsureDraftRenderResult(
                    workflow_run_id=request.workflow_run_id,
                    render_job_id=render_id,
                    props_hash="9" * 64,
                    render_engine="remotion_premium",
                    render_queue="render_remotion",
                    source_plan_revision=1,
                    source_media_revision=1,
                )

        cache_dir = Path("/tmp/temporal-sdk-cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        environment = await WorkflowEnvironment.start_time_skipping(
            download_dest_dir=str(cache_dir)
        )
        registry = QueueRegistry()
        run_id = str(uuid4())
        workflow_input = WorkflowStartInput(
            workflow_run_id=run_id,
            temporal_workflow_id=f"motionly:test:{run_id}",
            workspace_id=str(uuid4()),
            project_id=str(uuid4()),
            workflow_type="first_draft",
            workflow_version="first-draft-v1",
            request_hash=HASH,
            steps=[
                WorkflowStepSpec(
                    step_key="analyze-source-v1",
                    step_kind="analyze_source",
                    job_kind="visual_analysis",
                    queue_name="visual_analysis",
                    input_hash=HASH,
                    next_run_state="proposal_ready",
                    resource_id=str(uuid4()),
                )
            ],
        )
        try:
            worker = create_worker(
                client=environment.client,
                registry=registry,
                task_queue="orchestration-test",
                proposal_activities=ProposalActivity(),  # type: ignore[arg-type]
                render_activities=RenderActivity(),  # type: ignore[arg-type]
            )
            async with worker:
                temporal = TemporalOrchestrationClient(
                    environment.client,
                    task_queue="orchestration-test",
                )
                first_start = await temporal.start_workflow(
                    workflow_id=workflow_input.temporal_workflow_id,
                    input=workflow_input,
                )
                duplicate_start = await temporal.start_workflow(
                    workflow_id=workflow_input.temporal_workflow_id,
                    input=workflow_input,
                )
                assert duplicate_start.run_id == first_start.run_id
                description = await temporal.describe_workflow(
                    workflow_id=workflow_input.temporal_workflow_id
                )
                assert description is not None
                assert description.run_id == first_start.run_id
                assert description.status == "running"
                handle = environment.client.get_workflow_handle(workflow_input.temporal_workflow_id)
                retry_signal = StepOutcomeSignal(
                    event_id="event:export:retryable",
                    workflow_run_id=run_id,
                    step_key="analyze-source-v1",
                    attempt_number=1,
                    outcome=StepOutcome.RETRYABLE_FAILURE,
                    error_code="worker_unavailable",
                )
                await temporal.signal_workflow(
                    workflow_id=workflow_input.temporal_workflow_id,
                    signal=retry_signal,
                )
                await temporal.signal_workflow(
                    workflow_id=workflow_input.temporal_workflow_id,
                    signal=retry_signal,
                )
                await temporal.signal_workflow(
                    workflow_id=workflow_input.temporal_workflow_id,
                    signal=StepOutcomeSignal(
                        event_id="event:export:complete",
                        workflow_run_id=run_id,
                        step_key="analyze-source-v1",
                        attempt_number=2,
                        outcome=StepOutcome.SUCCEEDED,
                        output_hash="d" * 64,
                    ),
                )
                result = await handle.result()
                assert result["state"] == WorkflowExecutionState.COMPLETED.value
                assert len(registry.jobs) == 2
                history = await handle.fetch_history()
                replay = await replay_history_document(
                    history.to_json_dict(),
                    workflow_id=workflow_input.temporal_workflow_id,
                )
                assert replay.replay_failure is None

                v2_run_id = str(uuid4())
                v2_upload_id = str(uuid4())
                v2_input = WorkflowStartInput(
                    workflow_run_id=v2_run_id,
                    temporal_workflow_id=f"motionly:test:v2:{v2_run_id}",
                    workspace_id=workflow_input.workspace_id,
                    project_id=workflow_input.project_id,
                    workflow_type="first_draft",
                    workflow_version="first-draft-v2",
                    request_hash=HASH,
                    steps=workflow_steps_for(
                        workflow_type="first_draft",
                        workflow_version="first-draft-v2",
                        request_hash=HASH,
                        uploaded_video_id=v2_upload_id,
                    ),
                )
                await temporal.start_workflow(
                    workflow_id=v2_input.temporal_workflow_id,
                    input=v2_input,
                )
                v2_handle = environment.client.get_workflow_handle(v2_input.temporal_workflow_id)
                for index, step in enumerate(v2_input.steps):
                    await temporal.signal_workflow(
                        workflow_id=v2_input.temporal_workflow_id,
                        signal=StepOutcomeSignal(
                            event_id=f"event:v2:analysis:{index}",
                            workflow_run_id=v2_run_id,
                            step_key=step.step_key,
                            attempt_number=1,
                            outcome=StepOutcome.SUCCEEDED,
                            output_hash=chr(ord("d") + index) * 64,
                        ),
                    )

                for _ in range(100):
                    if any(
                        job.workflow_run_id is not None
                        and str(job.workflow_run_id) == v2_run_id
                        and job.step_key == "generate-proposal-v2"
                        and str(job.resource_id) == proposal_id
                        and job.queue_name == "planning"
                        and job.kind == "edit_plan"
                        for job in registry.jobs.values()
                    ):
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("v2 planning job was not enqueued")

                await temporal.signal_workflow(
                    workflow_id=v2_input.temporal_workflow_id,
                    signal=StepOutcomeSignal(
                        event_id="event:v2:planning:complete",
                        workflow_run_id=v2_run_id,
                        step_key="generate-proposal-v2",
                        attempt_number=1,
                        outcome=StepOutcome.SUCCEEDED,
                        output_hash="f" * 64,
                    ),
                )
                for _ in range(100):
                    status = await v2_handle.query("status")
                    if status["current_step_key"] == "approval-gate-v2":
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("v2 did not enter durable approval wait")
                assert (
                    await temporal.describe_workflow(workflow_id=v2_input.temporal_workflow_id)
                ).status == "running"

                approval = signal_from_outbox(
                    event_type="workflow.approval.decided",
                    event_id="event:v2:approval",
                    payload={
                        "workflow_run_id": v2_run_id,
                        "proposal_id": proposal_id,
                        "decision_id": str(uuid4()),
                        "decision": "approve",
                        "proposal_revision": 2,
                        "workflow_revision": 8,
                        "edit_plan_id": str(uuid4()),
                        "edit_plan_revision": 1,
                    },
                )
                assert isinstance(approval, ApprovalGateSignal)
                await temporal.signal_workflow(
                    workflow_id=v2_input.temporal_workflow_id,
                    signal=approval,
                )
                for _ in range(100):
                    if any(
                        job.workflow_run_id is not None
                        and str(job.workflow_run_id) == v2_run_id
                        and job.step_key == "render-first-draft-v2"
                        and str(job.resource_id) == render_id
                        and job.queue_name == "render_remotion"
                        and job.kind == "render"
                        and job.render_engine == "remotion_premium"
                        for job in registry.jobs.values()
                    ):
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("v2 specialized render job was not enqueued")
                assert (await v2_handle.query("status"))["current_step_key"] == (
                    "render-first-draft-v2"
                )
                canonical_draft_ready = {
                    "render_job_id": render_id,
                    "render_status": "complete",
                    "failed": False,
                    "export_count": 1,
                }
                assert canonical_draft_ready["export_count"] == 1
                await temporal.signal_workflow(
                    workflow_id=v2_input.temporal_workflow_id,
                    signal=StepOutcomeSignal(
                        event_id="event:v2:render:complete",
                        workflow_run_id=v2_run_id,
                        step_key="render-first-draft-v2",
                        attempt_number=1,
                        outcome=StepOutcome.SUCCEEDED,
                        output_hash="8" * 64,
                        next_run_state="draft_ready",
                    ),
                )
                v2_result = await v2_handle.result()
                assert v2_result["state"] == WorkflowExecutionState.COMPLETED.value
                v2_history = await v2_handle.fetch_history()
                v2_replay = await replay_history_document(
                    v2_history.to_json_dict(),
                    workflow_id=v2_input.temporal_workflow_id,
                )
                assert v2_replay.replay_failure is None

                cancel_run_id = str(uuid4())
                cancel_input = WorkflowStartInput(
                    workflow_run_id=cancel_run_id,
                    temporal_workflow_id=f"motionly:test:cancel:{cancel_run_id}",
                    workspace_id=workflow_input.workspace_id,
                    project_id=workflow_input.project_id,
                    workflow_type="first_draft",
                    workflow_version="first-draft-v1",
                    request_hash=HASH,
                    steps=workflow_input.steps,
                )
                await temporal.start_workflow(
                    workflow_id=cancel_input.temporal_workflow_id,
                    input=cancel_input,
                )
                cancel_handle = environment.client.get_workflow_handle(
                    cancel_input.temporal_workflow_id
                )
                await temporal.signal_workflow(
                    workflow_id=cancel_input.temporal_workflow_id,
                    signal=StepOutcomeSignal(
                        event_id="event:workflow:cancelled",
                        workflow_run_id=cancel_run_id,
                        outcome=StepOutcome.CANCELLED,
                        error_code="cancelled",
                    ),
                )
                cancelled = await cancel_handle.result()
                assert cancelled["state"] == WorkflowExecutionState.CANCELLED.value
        finally:
            await environment.shutdown()

    asyncio.run(scenario())


def _json_payload(value: object, *, encoding: str = "json/plain", metadata=None):
    return {
        "metadata": metadata or {"encoding": base64.b64encode(encoding.encode("ascii")).decode()},
        "data": base64.b64encode(json.dumps(value).encode()).decode(),
    }


def test_history_validator_accepts_known_schema_and_rejects_metadata_and_encodings() -> None:
    run = InMemoryWorkflowRepository().start_workflow(**_workflow_values())[0]
    known_start = asdict(workflow_start_for_run(run))
    known = _json_payload(known_start)
    validate_history_document({"events": [{"attributes": {"input": known}}]})

    oversized_nested = asdict(workflow_start_for_run(run))
    oversized_nested["steps"][0]["step_key"] = "x" * 600
    with pytest.raises(HistoryPayloadRejected):
        validate_history_document(
            {"events": [{"attributes": {"input": _json_payload(oversized_nested)}}]}
        )

    encoding = base64.b64encode(b"json/plain").decode()
    for metadata in (
        {"encoding": encoding, "authorization": base64.b64encode(b"Bearer secret").decode()},
        {"encoding": encoding, "token": base64.b64encode(b"secret").decode()},
    ):
        with pytest.raises(HistoryPayloadRejected):
            validate_history_document(
                {
                    "events": [
                        {
                            "attributes": {
                                "input": _json_payload(
                                    asdict(workflow_start_for_run(run)), metadata=metadata
                                )
                            }
                        }
                    ]
                }
            )

    for rejected_encoding in (
        "binary/protobuf",
        "image/png",
        "video/mp4",
        "application/octet-stream",
    ):
        with pytest.raises(HistoryPayloadRejected):
            validate_history_document(
                {
                    "events": [
                        {"attributes": {"input": _json_payload({}, encoding=rejected_encoding)}}
                    ]
                }
            )

    binary_with_content = {
        "metadata": {"encoding": base64.b64encode(b"binary/null").decode()},
        "data": base64.b64encode(b"not-null").decode(),
    }
    with pytest.raises(HistoryPayloadRejected):
        validate_history_document({"events": [{"attributes": {"input": binary_with_content}}]})


def test_workflow_run_to_activity_enqueues_executable_visual_bootstrap() -> None:
    repository = InMemoryWorkflowRepository()
    run, _ = repository.start_workflow(**_workflow_values())
    start = workflow_start_for_run(run)

    assert len(start.steps) == 1
    step = start.steps[0]
    request = EnqueueStepRequest(
        workflow_run_id=start.workflow_run_id,
        workflow_step_id=None,
        workspace_id=start.workspace_id,
        project_id=start.project_id,
        step_key=step.step_key,
        step_kind=step.step_kind,
        job_kind=step.job_kind,
        queue_name=step.queue_name,
        attempt_number=1,
        attempt_key="temporal-attempt:bootstrap-0001",
        input_hash=step.input_hash,
        next_run_state=step.next_run_state,
        resource_id=step.resource_id,
        max_attempts=step.max_attempts,
    )
    registry = QueueRegistry()
    result = asyncio.run(BullMQActivities(registry).enqueue_bullmq_step(request))
    job = registry.jobs[result.job_id]

    assert step.step_kind == "analyze_source"
    assert job.kind == "visual_analysis"
    assert job.step_kind == "analyze_source"
    assert job.queue_name == "visual_analysis"
    assert job.resource_id == run.uploaded_video_id


def test_unsupported_workflow_type_fails_closed_at_dispatch() -> None:
    repository = InMemoryWorkflowRepository()
    values = {**_workflow_values(), "workflow_type": "export", "workflow_version": "export-v1"}
    message_run, _ = repository.start_workflow(**values)
    temporal = _TemporalFake()
    dispatcher = OutboxDispatcher(
        repository=repository,
        temporal=temporal,
        lease_owner="dispatcher-unsupported",
    )

    result = asyncio.run(dispatcher.dispatch_once())

    assert result.failed == 1
    assert temporal.starts == []
    message = next(iter(repository.outbox.values()))
    assert message.workflow_run_id == message_run.id
    assert message.state == OutboxState.FAILED


def test_inmemory_reconciliation_candidates_are_global_stale_and_bounded() -> None:
    repository = InMemoryWorkflowRepository()
    base = datetime(2026, 8, 8, tzinfo=UTC)
    runs = []
    for index in range(4):
        values = _workflow_values()
        values["idempotency_key"] = f"stale-candidate-{index}"
        values["request_hash"] = f"{index + 1:064x}"
        run, _ = repository.start_workflow(**values)
        run.updated_at = base + timedelta(seconds=index)
        runs.append(run)

    candidates = repository.list_reconciliation_candidates(
        stale_before=base + timedelta(seconds=2),
        limit=2,
    )

    assert [run.id for run in candidates] == [runs[0].id, runs[1].id]
    assert len({run.workspace_id for run in candidates}) == 2


def test_supabase_reconciliation_query_is_global_oldest_first_and_bounded() -> None:
    values = _workflow_values()
    run, _ = InMemoryWorkflowRepository().start_workflow(**values)
    row = {
        "id": str(run.id),
        "workspace_id": str(run.workspace_id),
        "project_id": str(run.project_id),
        "uploaded_video_id": str(run.uploaded_video_id),
        "source_media_revision": run.source_media_revision,
        "source_object_reference_id": str(run.source_object_reference_id),
        "workflow_type": run.workflow_type,
        "workflow_version": run.workflow_version,
        "idempotency_key": run.idempotency_key,
        "request_hash": run.request_hash,
        "temporal_workflow_id": run.temporal_workflow_id,
        "state": run.state.value,
        "updated_at": run.updated_at.isoformat(),
    }
    request = httpx.Request("GET", "https://supabase.test/workflow_runs")
    response = httpx.Response(200, request=request, json=[row])
    calls = []

    class _Client:
        def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
            calls.append({"method": method, "path": path, **kwargs})
            return response

    repository = SupabaseWorkflowRepository(
        supabase_url="https://supabase.test",
        service_role_key="service-role",
    )
    repository.client = _Client()  # type: ignore[assignment]
    cutoff = datetime(2026, 8, 8, 10, 30, tzinfo=UTC)

    candidates = repository.list_reconciliation_candidates(
        stale_before=cutoff,
        limit=7,
    )

    assert candidates[0].workspace_id == run.workspace_id
    params = calls[0]["params"]
    assert params["updated_at"] == f"lte.{cutoff.isoformat()}"
    assert params["order"] == "updated_at.asc,id.asc"
    assert params["limit"] == "7"
    assert "workspace_id" not in params and "project_id" not in params


def test_reconciler_loop_consumes_configured_staleness_interval_and_batch() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="local",
        PROCESS_ROLE="workflow-reconciler",
        WORKFLOW_RECONCILER_INTERVAL_SECONDS=17,
        WORKFLOW_RECONCILER_STALENESS_SECONDS=61,
        WORKFLOW_RECONCILER_BATCH_SIZE=7,
    )
    fixed = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    cutoffs = []
    sleeps = []

    class _Reconciler:
        limit = settings.workflow_reconciler_batch_size

        async def reconcile_once(self, *, stale_before: datetime):
            cutoffs.append(stale_before)
            return SimpleNamespace(inspected=0)

    class _StopLoop(Exception):
        pass

    async def stop_after_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        raise _StopLoop

    with pytest.raises(_StopLoop):
        asyncio.run(
            run_reconciler_loop(
                _Reconciler(),  # type: ignore[arg-type]
                settings,
                clock=lambda: fixed,
                sleeper=stop_after_sleep,
            )
        )

    assert _Reconciler.limit == 7
    assert cutoffs == [fixed - timedelta(seconds=61)]
    assert sleeps == [17]


def test_run_dispatcher_consumes_nondefault_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.orchestration.dispatcher as dispatcher_module

    settings = Settings(
        _env_file=None,
        APP_ENV="local",
        PROCESS_ROLE="temporal-dispatcher",
        SUPABASE_URL="https://supabase.example.com",
        SUPABASE_SERVICE_ROLE_KEY="service-role",
        TEMPORAL_ADDRESS="temporal.example.com:7233",
        TEMPORAL_NAMESPACE="custom-namespace",
        TEMPORAL_TASK_QUEUE="custom-task-queue",
        TEMPORAL_TLS_ENABLED=False,
        TEMPORAL_DISPATCHER_ID="dispatcher-configured",
        WORKFLOW_DISPATCHER_BATCH_SIZE=7,
        WORKFLOW_DISPATCHER_LEASE_SECONDS=37,
        WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS=2.5,
        WORKFLOW_DISPATCHER_BACKOFF_INITIAL_SECONDS=4.5,
        WORKFLOW_DISPATCHER_BACKOFF_MAX_SECONDS=45,
    )
    captured = {}

    async def connect(config):
        captured["connection"] = config
        return object()

    class _Dispatcher:
        def __init__(self, **kwargs):
            captured["dispatcher"] = kwargs

        async def dispatch_once(self):
            return DispatchBatchResult()

    monkeypatch.setattr(dispatcher_module, "connect_temporal_client", connect)
    monkeypatch.setattr(dispatcher_module, "TemporalOrchestrationClient", lambda *a, **k: object())
    monkeypatch.setattr(dispatcher_module, "SupabaseWorkflowRepository", lambda **kwargs: object())
    monkeypatch.setattr(dispatcher_module, "OutboxDispatcher", _Dispatcher)

    result = asyncio.run(run_dispatcher(settings, once=True))

    assert result == DispatchBatchResult()
    assert captured["connection"].tls_enabled is False
    configured = captured["dispatcher"]
    assert configured["lease_owner"] == "dispatcher-configured"
    assert configured["batch_size"] == 7
    assert configured["lease_seconds"] == 37
    assert configured["base_backoff_seconds"] == 4.5
    assert configured["max_backoff_seconds"] == 45

    class _StopPolling(Exception):
        pass

    async def stop_polling(seconds: float) -> None:
        captured["poll_interval"] = seconds
        raise _StopPolling

    with pytest.raises(_StopPolling):
        asyncio.run(run_dispatcher(settings, sleeper=stop_polling))
    assert captured["poll_interval"] == 2.5


def test_run_reconciler_wires_configured_batch_and_dispatcher_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.orchestration.reconciler as reconciler_module

    settings = Settings(
        _env_file=None,
        APP_ENV="local",
        PROCESS_ROLE="workflow-reconciler",
        SUPABASE_URL="https://supabase.example.com",
        SUPABASE_SERVICE_ROLE_KEY="service-role",
        TEMPORAL_ADDRESS="temporal.example.com:7233",
        TEMPORAL_NAMESPACE="custom-namespace",
        TEMPORAL_TASK_QUEUE="custom-task-queue",
        WORKFLOW_DISPATCHER_BATCH_SIZE=9,
        WORKFLOW_DISPATCHER_LEASE_SECONDS=41,
        WORKFLOW_DISPATCHER_BACKOFF_INITIAL_SECONDS=2.5,
        WORKFLOW_DISPATCHER_BACKOFF_MAX_SECONDS=35,
        WORKFLOW_RECONCILER_INTERVAL_SECONDS=11,
        WORKFLOW_RECONCILER_STALENESS_SECONDS=73,
        WORKFLOW_RECONCILER_BATCH_SIZE=6,
    )
    captured = {}

    async def connect(config):
        return object()

    class _Dispatcher:
        def __init__(self, **kwargs):
            captured["dispatcher"] = kwargs

    class _Reconciler:
        def __init__(self, **kwargs):
            captured["reconciler"] = kwargs

        async def reconcile_once(self, *, stale_before: datetime):
            captured["stale_before"] = stale_before
            return SimpleNamespace(inspected=0)

    monkeypatch.setattr(reconciler_module, "connect_temporal_client", connect)
    monkeypatch.setattr(reconciler_module, "TemporalOrchestrationClient", lambda *a, **k: object())
    monkeypatch.setattr(reconciler_module, "SupabaseWorkflowRepository", lambda **kwargs: object())
    monkeypatch.setattr(reconciler_module, "OutboxDispatcher", _Dispatcher)
    monkeypatch.setattr(reconciler_module, "WorkflowReconciler", _Reconciler)
    fixed = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)

    asyncio.run(run_reconciler(settings, once=True, clock=lambda: fixed))

    assert captured["dispatcher"]["batch_size"] == 9
    assert captured["dispatcher"]["lease_seconds"] == 41
    assert captured["dispatcher"]["base_backoff_seconds"] == 2.5
    assert captured["dispatcher"]["max_backoff_seconds"] == 35
    assert captured["reconciler"]["limit"] == 6
    assert captured["stale_before"] == fixed - timedelta(seconds=73)


def test_exact_role_main_validates_and_invokes_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.orchestration.main as role_main

    settings = Settings(
        _env_file=None,
        APP_ENV="local",
        PROCESS_ROLE="temporal-dispatcher",
    )
    calls = []

    async def runner(value):
        calls.append(("runner", value))

    def validate(value):
        calls.append(("validate", value))

    monkeypatch.setattr(role_main, "get_settings", lambda: settings)
    monkeypatch.setattr(role_main, "validate_production_settings", validate)
    monkeypatch.setattr(role_main, "run_dispatcher", runner)

    role_main.main(["temporal-dispatcher"])

    assert calls == [("validate", settings), ("runner", settings)]


def test_role_main_rejects_cli_process_role_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.orchestration.main as role_main

    settings = Settings(_env_file=None, APP_ENV="local", PROCESS_ROLE="temporal-worker")
    monkeypatch.setattr(role_main, "get_settings", lambda: settings)
    monkeypatch.setattr(role_main, "validate_production_settings", lambda value: None)

    with pytest.raises(RuntimeError, match="does not match"):
        role_main.main(["temporal-dispatcher"])


def test_reconciler_cursor_prevents_old_noop_rows_from_starving_later_repairs() -> None:
    repository = InMemoryWorkflowRepository()
    base = datetime(2026, 8, 8, tzinfo=UTC)
    runs = []
    for index in range(3):
        values = _workflow_values()
        values["idempotency_key"] = f"reconcile-cursor-{index}"
        values["request_hash"] = f"{index + 20:064x}"
        run, _ = repository.start_workflow(**values)
        run.updated_at = base + timedelta(seconds=index)
        runs.append(run)

    runs[0].state = WorkflowRunState.EXPORT_READY
    runs[1].state = WorkflowRunState.EXPORT_READY
    runs[2].state = WorkflowRunState.CANCELLED
    temporal = _TemporalFake()
    temporal.descriptions[runs[2].temporal_workflow_id] = TemporalExecutionDescription(
        workflow_id=runs[2].temporal_workflow_id,
        run_id="temporal-cursor-cancelled",
        status="running",
    )
    reconciler = WorkflowReconciler(repository=repository, temporal=temporal, limit=2)

    first = asyncio.run(reconciler.reconcile_once(stale_before=base + timedelta(minutes=1)))
    second = asyncio.run(reconciler.reconcile_once(stale_before=base + timedelta(minutes=1)))

    assert first.inspected == 2
    assert first.repaired == 0
    assert second.inspected == 1
    assert second.repaired == 1
    assert temporal.signals[0][1].workflow_run_id == str(runs[2].id)
    assert temporal.signals[0][1].outcome == StepOutcome.CANCELLED


def test_generic_worker_error_is_normalized_before_temporal_signal() -> None:
    repository = InMemoryWorkflowRepository()
    values = _workflow_values()
    run, _ = repository.start_workflow(**values)
    temporal = _TemporalFake()
    dispatcher = OutboxDispatcher(
        repository=repository,
        temporal=temporal,
        lease_owner="dispatcher-normalized-error",
    )
    asyncio.run(dispatcher.dispatch_once())
    begun = repository.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-source-v1",
        step_kind="analyze_source",
        attempt_key="normalized-error-attempt",
        lease_owner="worker-normalized-error",
        lease_seconds=60,
        input_hash=HASH,
    )
    assert begun.attempt is not None
    repository.fail_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-source-v1",
        claim_token=begun.attempt.claim_token,
        error_code="GenericNodeError",
        error_message="generic failure",
        retryable=True,
    )

    result = asyncio.run(dispatcher.dispatch_once())

    assert result.dispatched == 1
    assert result.failed == 0
    assert temporal.signals[0][1].error_code == "generic_node_error"
    failure_message = next(
        item for item in repository.outbox.values() if item.event_type == "workflow.step.failed"
    )
    assert failure_message.payload["error_code"] == "generic_node_error"


def test_supabase_reconciliation_query_emits_composite_cursor() -> None:
    cutoff = datetime(2026, 8, 8, 10, 30, tzinfo=UTC)
    cursor_time = cutoff - timedelta(minutes=5)
    cursor_id = uuid4()

    params = SupabaseWorkflowRepository._reconciliation_candidate_params(
        stale_before=cutoff,
        limit=7,
        after_updated_at=cursor_time,
        after_id=cursor_id,
    )

    assert params["updated_at"] == f"lte.{cutoff.isoformat()}"
    assert params["or"] == (
        f"(updated_at.gt.{cursor_time.isoformat()},"
        f"and(updated_at.eq.{cursor_time.isoformat()},id.gt.{cursor_id}))"
    )


def test_approval_gate_payload_is_exact_and_duplicate_or_out_of_order_is_buffered() -> None:
    run_id, proposal_id, plan_id = uuid4(), uuid4(), uuid4()
    payload = {
        "workflow_run_id": str(run_id),
        "proposal_id": str(proposal_id),
        "decision_id": str(uuid4()),
        "decision": "approve",
        "proposal_revision": 2,
        "workflow_revision": 8,
        "edit_plan_id": str(plan_id),
        "edit_plan_revision": 2,
    }
    early = signal_from_outbox(
        event_type="workflow.approval.decided",
        event_id="approval-gate-event",
        payload=payload,
    )
    assert isinstance(early, ApprovalGateSignal)
    workflow_instance = MotionlyWorkflow()
    workflow_instance.approval_gate(early)
    workflow_instance.approval_gate(early)
    assert workflow_instance._approval_signals == [early]

    workflow_instance._workflow_run_id = str(run_id)
    workflow_instance._proposal_id = str(proposal_id)
    assert workflow_instance._pop_approval_signal() == early

    with pytest.raises(HistoryPayloadRejected, match="invalid field set"):
        signal_from_outbox(
            event_type="workflow.approval.decided",
            event_id="approval-gate-unknown",
            payload={**payload, "token": "not-allowed"},
        )
    with pytest.raises(HistoryPayloadRejected, match="rejection must not contain a plan"):
        signal_from_outbox(
            event_type="workflow.approval.decided",
            event_id="approval-gate-reject-plan",
            payload={**payload, "decision": "reject"},
        )


def test_v2_rejection_cancels_temporal_workflow_without_a_plan() -> None:
    workflow_instance = MotionlyWorkflow()
    run_id, proposal_id = uuid4(), uuid4()
    workflow_instance._workflow_run_id = str(run_id)
    workflow_instance._proposal_id = str(proposal_id)
    rejected = signal_from_outbox(
        event_type="workflow.approval.decided",
        event_id="approval-gate-rejected",
        payload={
            "workflow_run_id": str(run_id),
            "proposal_id": str(proposal_id),
            "decision_id": str(uuid4()),
            "decision": "reject",
            "proposal_revision": 2,
            "workflow_revision": 8,
        },
    )
    assert isinstance(rejected, ApprovalGateSignal)
    result = workflow_instance._approval_result(rejected)
    assert result.state == WorkflowExecutionState.CANCELLED
    assert result.error_code == "proposal_rejected"
    assert rejected.edit_plan_id is None
    assert rejected.edit_plan_revision is None


def test_v2_contract_keeps_v1_static_steps_and_adds_durable_proposal_gate() -> None:
    import inspect

    run_id, upload_id = uuid4(), uuid4()
    v1_steps = workflow_steps_for(
        workflow_type="first_draft",
        workflow_version="first-draft-v1",
        request_hash="a" * 64,
        uploaded_video_id=str(upload_id),
    )
    assert [step.step_key for step in v1_steps] == ["analyze-source-v1"]
    assert job_id_for(
        EnqueueStepRequest(
            workflow_run_id=str(run_id),
            workflow_step_id=None,
            workspace_id=str(uuid4()),
            project_id=str(uuid4()),
            step_key=v1_steps[0].step_key,
            step_kind=v1_steps[0].step_kind,
            job_kind=v1_steps[0].job_kind,
            queue_name=v1_steps[0].queue_name,
            attempt_number=1,
            attempt_key="temporal-attempt-v1",
            input_hash=v1_steps[0].input_hash,
            next_run_state=v1_steps[0].next_run_state,
            resource_id=v1_steps[0].resource_id,
        )
    ).startswith(f"workflow--{run_id}--analyze-source-v1--")

    source = inspect.getsource(MotionlyWorkflow.run)
    assert 'if input.workflow_version == "first-draft-v2"' in source
    assert '"ensure_workflow_proposal"' in source
    assert 'step_kind="generate_proposal"' in source
    assert 'job_kind="edit_plan"' in source
    assert 'queue_name="planning"' in source
    durable_wait = "lambda: self._has_workflow_terminal_signal() or self._has_approval_signal()"
    assert durable_wait in source
    assert source.index("self._completed_step_keys.add(planning_step_key)") < source.index(
        durable_wait
    )


def test_render_draft_activity_routes_specialized_queue_without_changing_v1_contract() -> None:
    for queue_name, expected_engine in (
        ("render", "ffmpeg_basic"),
        ("render_gpu", "ffmpeg_basic"),
        ("render_remotion", "remotion_premium"),
    ):
        registry = QueueRegistry()
        render_id = uuid4()
        request = EnqueueStepRequest(
            workflow_run_id=str(uuid4()),
            workflow_step_id=None,
            workspace_id=str(uuid4()),
            project_id=str(uuid4()),
            step_key="render-first-draft-v2",
            step_kind="render_draft",
            job_kind="render",
            queue_name=queue_name,
            attempt_number=1,
            attempt_key=f"render-attempt-{queue_name}",
            input_hash="9" * 64,
            next_run_state="draft_ready",
            resource_id=str(render_id),
        )
        result = asyncio.run(BullMQActivities(registry).enqueue_bullmq_step(request))
        job = registry.jobs[result.job_id]
        assert job.queue_name == queue_name
        assert job.kind == "render"
        assert job.resource_id == render_id
        assert job.render_engine == expected_engine
        assert job.max_attempts == 1


def test_reconciler_repairs_draft_ready_only_from_stored_render_success_hash() -> None:
    repository = InMemoryWorkflowRepository()
    values = {**_workflow_values(), "workflow_version": "first-draft-v2"}
    run, _ = repository.start_workflow(**values)
    run.temporal_run_id = "temporal-draft-ready"
    render_id = uuid4()
    step = repository.prepare_step(
        workspace_id=run.workspace_id,
        project_id=run.project_id,
        workflow_run_id=run.id,
        step_key="render-first-draft-v2",
        step_kind="render_draft",
        input_hash="9" * 64,
        max_attempts=3,
        bullmq_job_id="workflow-draft-ready-render",
        resource_id=render_id,
    )
    step.state = WorkflowStepState.SUCCEEDED
    step.attempt_count = 1
    step.output_hash = "8" * 64
    step.output = {
        "render_job_id": str(render_id),
        "render_status": "complete",
        "failed": False,
    }
    run.state = WorkflowRunState.DRAFT_READY
    temporal = _TemporalFake()
    temporal.descriptions[run.temporal_workflow_id] = TemporalExecutionDescription(
        workflow_id=run.temporal_workflow_id,
        run_id=run.temporal_run_id,
        status="running",
    )
    report = asyncio.run(
        WorkflowReconciler(repository=repository, temporal=temporal).reconcile_tenant(
            workspace_id=run.workspace_id,
            project_id=run.project_id,
        )
    )
    assert report.repaired == 1
    assert report.actions[0].action == "signalled_canonical_draft_ready"
    signal = temporal.signals[0][1]
    assert signal.outcome == StepOutcome.SUCCEEDED
    assert signal.output_hash == step.output_hash
    assert signal.step_key == step.step_key
    assert signal.attempt_number == 1
    assert signal.next_run_state == "draft_ready"

    step.output = {"render_job_id": str(render_id), "render_status": "complete"}
    temporal.signals.clear()
    invalid = asyncio.run(
        WorkflowReconciler(repository=repository, temporal=temporal).reconcile_tenant(
            workspace_id=run.workspace_id,
            project_id=run.project_id,
        )
    )
    assert invalid.repaired == 0
    assert invalid.actions[0].action == "reported_draft_ready_success_evidence_invalid"
    assert temporal.signals == []
