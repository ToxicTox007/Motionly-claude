from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.api.errors import ApiError
from app.repositories.workflows import (
    InMemoryWorkflowRepository,
    OutboxState,
    WorkflowRunState,
    canonical_payload_hash,
)


def workflow_values() -> dict[str, object]:
    return {
        "workspace_id": uuid4(),
        "project_id": uuid4(),
        "uploaded_video_id": uuid4(),
        "source_media_revision": 3,
        "source_object_reference_id": uuid4(),
        "workflow_type": "first_draft",
        "workflow_version": "first-draft-v2",
        "idempotency_key": "upload-first-draft-0001",
        "request_hash": "a" * 64,
        "started_by": uuid4(),
        "input": {"requested_locale": "en"},
    }


def start(repo: InMemoryWorkflowRepository, values: dict[str, object]):
    return repo.start_workflow(**values)


def begin(repo: InMemoryWorkflowRepository, values: dict[str, object], run_id: UUID):
    return repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run_id,
        step_key="analyze-source-v1",
        step_kind="analyze_source",
        attempt_key="analyze-attempt-0001",
        lease_owner="worker-test-1",
        lease_seconds=60,
        input_hash="b" * 64,
        max_attempts=3,
    )


def test_start_is_idempotent_and_commits_event_and_outbox_atomically() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()

    created, first_deduplicated = start(repo, values)
    replayed, replay_deduplicated = start(repo, values)

    assert replayed.id == created.id
    assert first_deduplicated is False
    assert replay_deduplicated is True
    assert len(repo.runs) == len(repo.events) == len(repo.outbox) == 1
    event = next(iter(repo.events.values()))
    message = next(iter(repo.outbox.values()))
    assert event.sequence_number == 1
    assert message.workflow_event_id == event.id
    assert message.payload_hash == canonical_payload_hash(message.payload)
    assert message.deduplication_key == f"workflow-start:{created.id}"
    assert str(values["source_object_reference_id"]) not in str(message.payload)


def test_start_rejects_same_key_with_different_request_hash() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    start(repo, values)

    with pytest.raises(ApiError) as exc_info:
        start(repo, {**values, "request_hash": "c" * 64})

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "workflow_idempotency_conflict"
    assert len(repo.runs) == len(repo.events) == len(repo.outbox) == 1


def test_concurrent_starts_create_one_run_event_and_outbox() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: start(repo, values), range(32)))

    assert len({result[0].id for result in results}) == 1
    assert sum(result[1] is False for result in results) == 1
    assert len(repo.runs) == len(repo.events) == len(repo.outbox) == 1


def test_workflow_reads_are_scoped_to_workspace_and_project() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    run, _ = start(repo, values)

    with pytest.raises(ApiError) as workspace_error:
        repo.get_workflow(
            workflow_run_id=run.id,
            workspace_id=uuid4(),
            project_id=values["project_id"],
        )
    with pytest.raises(ApiError) as project_error:
        repo.get_workflow(
            workflow_run_id=run.id,
            workspace_id=values["workspace_id"],
            project_id=uuid4(),
        )

    assert workspace_error.value.status_code == 404
    assert project_error.value.status_code == 404
    assert repo.list_workflows(
        workspace_id=uuid4(), project_id=values["project_id"]
    ) == []


def test_outbox_claimers_are_disjoint_and_expired_leases_are_fenced() -> None:
    repo = InMemoryWorkflowRepository()
    now = datetime(2026, 8, 8, tzinfo=UTC)
    for index in range(10):
        values = workflow_values()
        values["idempotency_key"] = f"upload-first-draft-{index:04d}"
        values["request_hash"] = f"{index:064x}"
        start(repo, values)
    for item in repo.outbox.values():
        item.available_at = now

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            repo.claim_outbox,
            lease_owner="dispatcher-a",
            limit=5,
            lease_seconds=20,
            now=now,
        )
        second_future = pool.submit(
            repo.claim_outbox,
            lease_owner="dispatcher-b",
            limit=5,
            lease_seconds=20,
            now=now,
        )
        first = first_future.result()
        second = second_future.result()

    assert len(first) == len(second) == 5
    assert {item.id for item in first}.isdisjoint(item.id for item in second)

    original = first[0]
    stale_token = original.lease_token
    reclaimed = repo.claim_outbox(
        lease_owner="dispatcher-c",
        limit=1,
        lease_seconds=20,
        now=now + timedelta(seconds=21),
    )[0]
    assert reclaimed.id == original.id
    assert reclaimed.claim_generation == 2
    assert reclaimed.lease_token != stale_token

    with pytest.raises(ApiError) as stale_error:
        repo.ack_outbox(
            outbox_id=reclaimed.id,
            lease_token=stale_token,
            transport_message_id="temporal-run-stale",
            temporal_run_id="temporal-run-stale",
            now=now + timedelta(seconds=22),
        )
    assert stale_error.value.code == "outbox_stale_claim"

    acknowledged = repo.ack_outbox(
        outbox_id=reclaimed.id,
        lease_token=reclaimed.lease_token,
        transport_message_id="temporal-run-current",
        temporal_run_id="temporal-run-current",
        now=now + timedelta(seconds=22),
    )
    assert acknowledged.state == OutboxState.DISPATCHED
    assert repo.runs[acknowledged.workflow_run_id].temporal_run_id == "temporal-run-current"


def test_outbox_failure_retries_then_dead_letters_at_bound() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    start(repo, values)
    message = next(iter(repo.outbox.values()))
    message.max_attempts = 2
    now = datetime.now(UTC)

    first = repo.claim_outbox(lease_owner="dispatcher-a", now=now)[0]
    failed = repo.fail_outbox(
        outbox_id=first.id,
        lease_token=first.lease_token,
        error_code="temporal_unavailable",
        error_message="retry safely",
        retry_at=now + timedelta(seconds=5),
    )
    assert failed.state == OutboxState.FAILED

    second = repo.claim_outbox(
        lease_owner="dispatcher-b", now=now + timedelta(seconds=5)
    )[0]
    dead = repo.fail_outbox(
        outbox_id=second.id,
        lease_token=second.lease_token,
        error_code="temporal_unavailable",
        error_message="bounded failure",
        retry_at=now + timedelta(seconds=10),
    )
    assert dead.state == OutboxState.DEAD_LETTER
    assert dead.dead_lettered_at is not None
    assert repo.claim_outbox(
        lease_owner="dispatcher-c", now=now + timedelta(seconds=20)
    ) == []


def test_step_begin_and_completion_replays_are_idempotent() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    run, _ = start(repo, values)

    created = begin(repo, values, run.id)
    replay = begin(repo, values, run.id)

    assert created.deduplicated is False
    assert replay.deduplicated is True
    assert replay.attempt is not None
    assert replay.attempt.id == created.attempt.id
    assert created.attempt is not None

    completed_run, completed_step, first_replay = repo.complete_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-source-v1",
        claim_token=created.attempt.claim_token,
        output_hash="d" * 64,
        output={"analysis_id": "analysis-1"},
        next_run_state=WorkflowRunState.PROPOSAL_READY,
    )
    replayed_run, replayed_step, second_replay = repo.complete_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-source-v1",
        claim_token=created.attempt.claim_token,
        output_hash="d" * 64,
        output={"analysis_id": "analysis-1"},
        next_run_state=WorkflowRunState.PROPOSAL_READY,
    )

    assert first_replay is False
    assert second_replay is True
    assert completed_run.state == replayed_run.state == WorkflowRunState.PROPOSAL_READY
    assert completed_step.id == replayed_step.id
    assert len(repo.events) == len(repo.outbox) == 2

    with pytest.raises(ApiError) as result_conflict:
        repo.complete_step(
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
            workflow_run_id=run.id,
            step_key="analyze-source-v1",
            claim_token=created.attempt.claim_token,
            output_hash="e" * 64,
            output={"analysis_id": "analysis-2"},
        )
    assert result_conflict.value.code == "workflow_step_result_conflict"


def test_expired_step_attempt_is_abandoned_and_old_token_cannot_complete() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    run, _ = start(repo, values)
    now = datetime(2026, 8, 8, tzinfo=UTC)
    first = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="render-v1",
        step_kind="render",
        attempt_key="render-attempt-0001",
        lease_owner="worker-a",
        lease_seconds=5,
        input_hash="f" * 64,
        now=now,
    )
    second = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="render-v1",
        step_kind="render",
        attempt_key="render-attempt-0002",
        lease_owner="worker-b",
        lease_seconds=30,
        input_hash="f" * 64,
        now=now + timedelta(seconds=6),
    )
    assert first.attempt is not None and second.attempt is not None
    assert first.attempt.state == "abandoned"

    with pytest.raises(ApiError) as stale_error:
        repo.complete_step(
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
            workflow_run_id=run.id,
            step_key="render-v1",
            claim_token=first.attempt.claim_token,
            output_hash="1" * 64,
            output={"render_id": "stale"},
            now=now + timedelta(seconds=7),
        )
    assert stale_error.value.code == "workflow_step_stale_claim"


def test_complete_and_fail_race_accepts_one_terminal_attempt_result() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    run, _ = start(repo, values)
    started = begin(repo, values, run.id)
    assert started.attempt is not None

    def complete():
        return repo.complete_step(
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
            workflow_run_id=run.id,
            step_key="analyze-source-v1",
            claim_token=started.attempt.claim_token,
            output_hash="9" * 64,
            output={"analysis_id": "accepted"},
        )

    def fail():
        return repo.fail_step(
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
            workflow_run_id=run.id,
            step_key="analyze-source-v1",
            claim_token=started.attempt.claim_token,
            error_code="worker_lost",
            error_message="race candidate",
            retryable=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete), pool.submit(fail)]
        successes = 0
        failures = []
        for future in futures:
            try:
                future.result()
                successes += 1
            except ApiError as exc:
                failures.append(exc.code)

    assert successes == 1
    assert failures[0] in {"workflow_step_stale_claim", "workflow_terminal"}
    attempt = started.attempt
    assert attempt.state in {"succeeded", "failed"}
    assert attempt.ended_at is not None


def test_create_app_wires_workflow_repository_dependency() -> None:
    from types import SimpleNamespace

    from app.api.dependencies import get_workflow_repository
    from app.main import create_app
    from app.repositories.workflows import SupabaseWorkflowRepository

    app = create_app()
    repository = get_workflow_repository(
        SimpleNamespace(app=SimpleNamespace(state=app.state))
    )

    assert repository is app.state.workflow_repository
    assert isinstance(
        repository,
        InMemoryWorkflowRepository | SupabaseWorkflowRepository,
    )


def test_final_expired_outbox_lease_is_dead_lettered_without_reclaim() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    start(repo, values)
    message = next(iter(repo.outbox.values()))
    message.max_attempts = 1
    now = datetime(2026, 8, 8, tzinfo=UTC)
    message.available_at = now

    claimed = repo.claim_outbox(
        lease_owner="dispatcher-final",
        limit=1,
        lease_seconds=5,
        now=now,
    )[0]
    assert claimed.attempt_count == 1

    assert repo.claim_outbox(
        lease_owner="dispatcher-reaper",
        limit=1,
        lease_seconds=5,
        now=now + timedelta(seconds=6),
    ) == []
    assert message.state == OutboxState.DEAD_LETTER
    assert message.last_error_code == "lease_expired_attempts_exhausted"
    assert message.lease_token is None


def test_expired_outbox_tokens_cannot_ack_or_reschedule() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    start(repo, values)
    now = datetime(2026, 8, 8, tzinfo=UTC)
    message = next(iter(repo.outbox.values()))
    message.available_at = now
    claimed = repo.claim_outbox(
        lease_owner="dispatcher-expired",
        limit=1,
        lease_seconds=5,
        now=now,
    )[0]

    with pytest.raises(ApiError) as ack_error:
        repo.ack_outbox(
            outbox_id=claimed.id,
            lease_token=claimed.lease_token,
            transport_message_id="late-start",
            temporal_run_id="late-run",
            now=now + timedelta(seconds=6),
        )
    with pytest.raises(ApiError) as fail_error:
        repo.fail_outbox(
            outbox_id=claimed.id,
            lease_token=claimed.lease_token,
            error_code="late_failure",
            error_message="must not reschedule",
            retry_at=now + timedelta(seconds=30),
            now=now + timedelta(seconds=6),
        )

    assert ack_error.value.code == "outbox_stale_claim"
    assert fail_error.value.code == "outbox_stale_claim"
    assert message.state == OutboxState.LEASED


def test_final_expired_step_attempt_commits_terminal_exhaustion() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    run, _ = start(repo, values)
    now = datetime(2026, 8, 8, tzinfo=UTC)
    first = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-final-v1",
        step_kind="analyze_source",
        attempt_key="analyze-final-attempt-0001",
        lease_owner="worker-final",
        lease_seconds=5,
        input_hash="6" * 64,
        max_attempts=1,
        now=now,
    )
    exhausted = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-final-v1",
        step_kind="analyze_source",
        attempt_key="analyze-final-attempt-0002",
        lease_owner="worker-reaper",
        lease_seconds=30,
        input_hash="6" * 64,
        max_attempts=1,
        now=now + timedelta(seconds=6),
    )

    assert first.attempt is not None
    assert first.attempt.state == "abandoned"
    assert exhausted.exhausted is True
    assert exhausted.step.state.value == "failed"
    assert run.state == WorkflowRunState.FAILED
    assert any(event.event_type == "workflow.step.exhausted" for event in repo.events.values())

    replay = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-final-v1",
        step_kind="analyze_source",
        attempt_key="analyze-final-attempt-0001",
        lease_owner="worker-final",
        lease_seconds=5,
        input_hash="6" * 64,
        max_attempts=1,
        now=now + timedelta(seconds=7),
    )
    assert replay.deduplicated is True
    assert replay.exhausted is True


def test_terminal_run_rejects_late_step_but_keeps_accepted_replay() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    run, _ = start(repo, values)
    analysis = begin(repo, values, run.id)
    assert analysis.attempt is not None
    completed_run, _, _ = repo.complete_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-source-v1",
        claim_token=analysis.attempt.claim_token,
        output_hash="7" * 64,
        output={"analysis_id": "accepted"},
        next_run_state=WorkflowRunState.PROPOSAL_READY,
    )
    late = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="late-side-effect-v1",
        step_kind="side_effect",
        attempt_key="late-side-effect-attempt-0001",
        lease_owner="worker-late",
        lease_seconds=60,
        input_hash="8" * 64,
    )
    assert late.attempt is not None
    sibling = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="parallel-sibling-v1",
        step_kind="side_effect",
        attempt_key="parallel-sibling-attempt-0001",
        lease_owner="worker-sibling",
        lease_seconds=60,
        input_hash="a" * 64,
    )
    assert sibling.attempt is not None
    repo.fail_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="late-side-effect-v1",
        claim_token=late.attempt.claim_token,
        error_code="terminal_failure",
        error_message="close the run",
        retryable=False,
    )
    assert completed_run.state == WorkflowRunState.FAILED
    assert sibling.attempt.state == "cancelled"
    assert sibling.step.state.value == "cancelled"
    sibling_replay = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="parallel-sibling-v1",
        step_kind="side_effect",
        attempt_key="parallel-sibling-attempt-0001",
        lease_owner="worker-sibling",
        lease_seconds=60,
        input_hash="a" * 64,
    )
    assert sibling_replay.deduplicated is True
    assert sibling_replay.attempt is not None
    assert sibling_replay.attempt.state == "cancelled"

    replay_run, _, deduplicated = repo.complete_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="analyze-source-v1",
        claim_token=analysis.attempt.claim_token,
        output_hash="7" * 64,
        output={"analysis_id": "accepted"},
        next_run_state=WorkflowRunState.PROPOSAL_READY,
    )
    assert replay_run.state == WorkflowRunState.FAILED
    assert deduplicated is True

    with pytest.raises(ApiError) as late_error:
        repo.complete_step(
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
            workflow_run_id=run.id,
            step_key="late-side-effect-v1",
            claim_token=late.attempt.claim_token,
            output_hash="8" * 64,
            output={"late": True},
        )
    assert late_error.value.code == "workflow_terminal"


def test_temporal_start_run_identity_is_exact_and_attempt_keys_are_step_scoped() -> None:
    repo = InMemoryWorkflowRepository()
    values = workflow_values()
    run, _ = start(repo, values)
    now = datetime.now(UTC)
    claimed = repo.claim_outbox(lease_owner="dispatcher", now=now)[0]
    repo.ack_outbox(
        outbox_id=claimed.id,
        lease_token=claimed.lease_token,
        transport_message_id="start-message",
        temporal_run_id="temporal-run-exact",
        now=now + timedelta(seconds=1),
    )
    with pytest.raises(ApiError) as conflict:
        repo.ack_outbox(
            outbox_id=claimed.id,
            lease_token=claimed.lease_token,
            transport_message_id="start-message",
            temporal_run_id="temporal-run-conflict",
            now=now + timedelta(seconds=2),
        )
    assert conflict.value.code == "outbox_temporal_run_id_conflict"

    shared_attempt_key = "step-scoped-attempt-key"
    first = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="parallel-a",
        step_kind="parallel",
        attempt_key=shared_attempt_key,
        lease_owner="worker-a",
        lease_seconds=60,
    )
    with pytest.raises(ApiError) as identity_conflict:
        repo.begin_step(
            workspace_id=values["workspace_id"],
            project_id=values["project_id"],
            workflow_run_id=run.id,
            step_key="parallel-a",
            step_kind="changed-kind",
            attempt_key=shared_attempt_key,
            lease_owner="worker-a",
            lease_seconds=60,
            input_hash="b" * 64,
            max_attempts=4,
        )
    assert identity_conflict.value.code == "workflow_step_idempotency_conflict"
    second = repo.begin_step(
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        workflow_run_id=run.id,
        step_key="parallel-b",
        step_kind="parallel",
        attempt_key=shared_attempt_key,
        lease_owner="worker-b",
        lease_seconds=60,
    )
    assert first.attempt is not None and second.attempt is not None
    assert first.attempt.id != second.attempt.id


def test_b20_stalled_workflow_is_reported_as_delayed_retrying() -> None:
    from app.schemas.workflows import (
        WORKFLOW_DELAYED_AFTER_SECONDS,
        WorkflowSummaryResponse,
        workflow_run_is_delayed,
    )

    repository = InMemoryWorkflowRepository()
    run, _ = repository.start_workflow(**workflow_values())

    # A freshly updated non-terminal run is not delayed.
    assert workflow_run_is_delayed(run, now=run.updated_at) is False

    # Once it has not progressed past the threshold, it is delayed and the
    # status API surfaces the "Delayed — retrying" detail for the status UI.
    stalled_at = run.updated_at + timedelta(seconds=WORKFLOW_DELAYED_AFTER_SECONDS + 1)
    assert workflow_run_is_delayed(run, now=stalled_at) is True

    # A terminal run is never "delayed".
    cancelled, _ = repository.cancel_workflow(
        workflow_run_id=run.id,
        workspace_id=run.workspace_id,
        project_id=run.project_id,
        expected_revision=run.revision,
        idempotency_key="cancel-delayed-0001",
        requested_by=run.started_by,
    )
    assert cancelled.state == WorkflowRunState.CANCELLED
    far_future = cancelled.updated_at + timedelta(days=1)
    assert workflow_run_is_delayed(cancelled, now=far_future) is False

    summary = WorkflowSummaryResponse.from_record(cancelled)
    assert summary.delayed is False
    assert summary.status_detail is None
