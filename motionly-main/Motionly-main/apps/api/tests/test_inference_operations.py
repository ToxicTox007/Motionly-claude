"""Tests for the Task 16A data layer: state machine, repositories and ``sent``.

Covers the legal/forbidden transition table, idempotent reservation, optimistic
transition conflicts, the route-snapshot secret guard, listing and counts, the
Supabase repository against a fake httpx transport (request shapes, conditional
PATCH, the unique-violation reserve path), and ``sent`` classification for each
httpx exception type.
"""

from __future__ import annotations

import json
import threading
from uuid import uuid4

import httpx
import pytest

from app.repositories.inference_operations import (
    INFERENCE_OPERATION_COLUMNS,
    InferenceOperationRepositoryError,
    SupabaseInferenceOperationRepository,
)
from app.services import provider_resilience as pr
from app.services.inference_operations import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    IllegalTransitionError,
    InMemoryInferenceOperationRepository,
    OperationNotFoundError,
    OperationState,
    SecretValueInSnapshotError,
    TransitionConflictError,
    is_legal_transition,
    route_snapshot,
    sanitize_error_detail,
)

WORKSPACE_A = uuid4()
WORKSPACE_B = uuid4()


def _snapshot() -> dict:
    return route_snapshot(
        adapter_id="endpoint-1",
        adapter_type="async_job",
        base_url="https://example.invalid/v1",
        model="model-x",
        model_revision="rev-1",
        mapping_version="1",
        secret_ref_name="bundle:endpoint-1",
    )


def _reserve(repo, *, key="idem-1", workspace=WORKSPACE_A, capability="audio.diarization"):
    return repo.reserve(
        workspace_id=workspace,
        project_id=None,
        capability=capability,
        idempotency_key=key,
        route_id="route-1",
        model="model-x",
        model_revision="rev-1",
        route_snapshot=_snapshot(),
    )


def _advance(repo, op_id, to_state, from_state):
    return repo.transition(op_id, to_state=to_state, expected_from={from_state})


# --- State machine -----------------------------------------------------------

ALL_LEGAL = {
    (OperationState.RESERVED, OperationState.SUBMITTING),
    (OperationState.SUBMITTING, OperationState.SUBMITTED),
    (OperationState.SUBMITTING, OperationState.SUBMIT_UNKNOWN),
    (OperationState.SUBMITTING, OperationState.FAILED),
    (OperationState.SUBMITTED, OperationState.RUNNING),
    (OperationState.SUBMITTED, OperationState.SUCCEEDED),
    (OperationState.SUBMITTED, OperationState.FAILED),
    (OperationState.SUBMITTED, OperationState.CANCELLED),
    (OperationState.SUBMITTED, OperationState.NEEDS_REVIEW),
    (OperationState.SUBMIT_UNKNOWN, OperationState.SUBMITTED),
    (OperationState.SUBMIT_UNKNOWN, OperationState.RUNNING),
    (OperationState.SUBMIT_UNKNOWN, OperationState.SUCCEEDED),
    (OperationState.SUBMIT_UNKNOWN, OperationState.FAILED),
    (OperationState.SUBMIT_UNKNOWN, OperationState.CANCELLED),
    (OperationState.SUBMIT_UNKNOWN, OperationState.NEEDS_REVIEW),
    (OperationState.RUNNING, OperationState.SUCCEEDED),
    (OperationState.RUNNING, OperationState.FAILED),
    (OperationState.RUNNING, OperationState.CANCELLED),
    (OperationState.RUNNING, OperationState.NEEDS_REVIEW),
}


@pytest.mark.parametrize("from_state,to_state", sorted(ALL_LEGAL, key=lambda p: (p[0], p[1])))
def test_every_allowed_transition_is_legal(from_state, to_state) -> None:
    assert is_legal_transition(from_state, to_state)


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        (OperationState.RESERVED, OperationState.SUBMITTED),
        (OperationState.RESERVED, OperationState.RUNNING),
        (OperationState.SUBMITTING, OperationState.RUNNING),
        (OperationState.SUBMITTING, OperationState.SUCCEEDED),
        (OperationState.RUNNING, OperationState.SUBMITTED),
        (OperationState.RUNNING, OperationState.RUNNING),
        (OperationState.SUCCEEDED, OperationState.RUNNING),
        (OperationState.FAILED, OperationState.RUNNING),
        (OperationState.CANCELLED, OperationState.SUCCEEDED),
        (OperationState.NEEDS_REVIEW, OperationState.SUCCEEDED),
        (OperationState.SUBMITTED, OperationState.SUBMIT_UNKNOWN),
    ],
)
def test_forbidden_transitions_are_rejected(from_state, to_state) -> None:
    assert not is_legal_transition(from_state, to_state)


def test_transition_table_is_total_and_terminals_are_sinks() -> None:
    assert set(LEGAL_TRANSITIONS) == set(OperationState)
    for state in TERMINAL_STATES:
        assert LEGAL_TRANSITIONS[state] == frozenset()


# --- Route snapshot ----------------------------------------------------------


def test_route_snapshot_keeps_only_references() -> None:
    snapshot = _snapshot()
    assert snapshot["secret_ref_name"] == "bundle:endpoint-1"
    assert snapshot["adapter_type"] == "async_job"
    assert "api_key" not in snapshot


def test_route_snapshot_omits_absent_secret_ref() -> None:
    snapshot = route_snapshot(
        adapter_id="a",
        adapter_type="local",
        base_url="",
        model="m",
        model_revision="r",
        mapping_version="1",
        secret_ref_name=None,
    )
    assert "secret_ref_name" not in snapshot


@pytest.mark.parametrize("key", ["api_key", "token", "secret", "password", "authorization"])
def test_reserve_rejects_snapshot_with_secret_value(key) -> None:
    repo = InMemoryInferenceOperationRepository()
    with pytest.raises(SecretValueInSnapshotError):
        repo.reserve(
            workspace_id=WORKSPACE_A,
            project_id=None,
            capability="audio.diarization",
            idempotency_key="k",
            route_id="route-1",
            model="m",
            model_revision="r",
            route_snapshot={key: "live-value"},
        )


def test_sanitize_error_detail_bounds_and_strips() -> None:
    assert sanitize_error_detail(None) is None
    assert sanitize_error_detail("  ") is None
    assert sanitize_error_detail("a\n\tb   c") == "a b c"
    assert len(sanitize_error_detail("x" * 5000)) == 2000


# --- In-memory repository ----------------------------------------------------


def test_reserve_is_idempotent_per_workspace() -> None:
    repo = InMemoryInferenceOperationRepository()
    first, created_first = _reserve(repo, key="idem-1")
    second, created_second = _reserve(repo, key="idem-1")
    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert first.state is OperationState.RESERVED


def test_reserve_different_workspace_is_a_distinct_operation() -> None:
    repo = InMemoryInferenceOperationRepository()
    a, _ = _reserve(repo, key="idem-1", workspace=WORKSPACE_A)
    b, created = _reserve(repo, key="idem-1", workspace=WORKSPACE_B)
    assert created is True
    assert a.id != b.id


def test_reserve_rejects_unknown_capability() -> None:
    repo = InMemoryInferenceOperationRepository()
    with pytest.raises(ValueError):
        _reserve(repo, capability="not.a.capability")


def test_get_and_get_by_idempotency_key() -> None:
    repo = InMemoryInferenceOperationRepository()
    op, _ = _reserve(repo, key="idem-7")
    assert repo.get(op.id).id == op.id
    assert repo.get_by_idempotency_key(WORKSPACE_A, "idem-7").id == op.id
    assert repo.get_by_idempotency_key(WORKSPACE_A, "missing") is None
    with pytest.raises(OperationNotFoundError):
        repo.get(uuid4())


def test_transition_advances_and_sets_timestamps() -> None:
    repo = InMemoryInferenceOperationRepository()
    op, _ = _reserve(repo)
    submitting = repo.transition(
        op.id, to_state=OperationState.SUBMITTING, expected_from={OperationState.RESERVED}
    )
    assert submitting.state is OperationState.SUBMITTING
    submitted = repo.transition(
        op.id,
        to_state=OperationState.SUBMITTED,
        expected_from={OperationState.SUBMITTING},
        provider_operation_id="ext-1",
    )
    assert submitted.provider_operation_id == "ext-1"
    assert submitted.submitted_at is not None
    done = repo.transition(
        op.id,
        to_state=OperationState.SUCCEEDED,
        expected_from={OperationState.SUBMITTED, OperationState.RUNNING},
        actual_cost_usd=1.25,
    )
    assert done.is_terminal
    assert done.completed_at is not None
    assert done.actual_cost_usd == 1.25


def test_transition_optimistic_conflict() -> None:
    repo = InMemoryInferenceOperationRepository()
    op, _ = _reserve(repo)
    _advance(repo, op.id, OperationState.SUBMITTING, OperationState.RESERVED)
    with pytest.raises(TransitionConflictError):
        _advance(repo, op.id, OperationState.SUBMITTING, OperationState.RESERVED)


def test_transition_illegal_is_rejected() -> None:
    repo = InMemoryInferenceOperationRepository()
    op, _ = _reserve(repo)
    with pytest.raises(IllegalTransitionError):
        repo.transition(op.id, to_state=OperationState.SUCCEEDED, expected_from=None)


def test_submit_unknown_reconciles_to_submitted() -> None:
    repo = InMemoryInferenceOperationRepository()
    op, _ = _reserve(repo)
    _advance(repo, op.id, OperationState.SUBMITTING, OperationState.RESERVED)
    _advance(repo, op.id, OperationState.SUBMIT_UNKNOWN, OperationState.SUBMITTING)
    reconciled = repo.transition(
        op.id,
        to_state=OperationState.SUBMITTED,
        expected_from={OperationState.SUBMIT_UNKNOWN},
        provider_operation_id="ext-found",
    )
    assert reconciled.state is OperationState.SUBMITTED
    assert reconciled.provider_operation_id == "ext-found"


def test_list_non_terminal_and_counts() -> None:
    repo = InMemoryInferenceOperationRepository()
    a, _ = _reserve(repo, key="a")
    b, _ = _reserve(repo, key="b")
    _advance(repo, a.id, OperationState.SUBMITTING, OperationState.RESERVED)
    _advance(repo, a.id, OperationState.SUBMIT_UNKNOWN, OperationState.SUBMITTING)
    _advance(repo, b.id, OperationState.SUBMITTING, OperationState.RESERVED)
    _advance(repo, b.id, OperationState.FAILED, OperationState.SUBMITTING)

    non_terminal = repo.list_non_terminal_for_route("route-1")
    assert {op.id for op in non_terminal} == {a.id}
    assert repo.count_by_state(OperationState.SUBMIT_UNKNOWN) == 1
    assert repo.count_by_state(OperationState.FAILED) == 1
    assert repo.count_by_state(OperationState.RESERVED) == 0


def test_in_memory_repository_is_thread_safe() -> None:
    repo = InMemoryInferenceOperationRepository()
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        _, created = _reserve(repo, key="shared")
        results.append(created)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert results.count(False) == 7


# --- Supabase repository against a fake transport ----------------------------


class _FakeSupabase:
    """Records requests and replays scripted responses via httpx.MockTransport."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        transport = httpx.MockTransport(self._dispatch)
        self._client = httpx.Client(transport=transport, base_url="https://db.invalid/rest/v1")
        self._handler = handler

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _make_repo(handler):
    repo = SupabaseInferenceOperationRepository(
        supabase_url="https://db.invalid", service_role_key="svc"
    )
    fake = _FakeSupabase(handler)

    def request(method, path, *, params=None, json=None, headers=None, timeout=None):
        merged = {**repo.client.headers, **(headers or {})}
        return fake._client.request(
            method, path.lstrip("/"), params=params, json=json, headers=merged
        )

    repo.client.request = request  # type: ignore[method-assign]
    return repo, fake


def _row(**overrides) -> dict:
    base = {
        "id": str(uuid4()),
        "workspace_id": str(WORKSPACE_A),
        "project_id": None,
        "capability": "audio.diarization",
        "idempotency_key": "idem-1",
        "provider_route_id": "route-1",
        "provider_model": "model-x",
        "provider_model_revision": "rev-1",
        "route_snapshot": {"adapter_id": "e1", "secret_ref_name": "bundle:e1"},
        "provider_operation_id": None,
        "state": "reserved",
        "error_code": None,
        "error_detail": None,
        "estimated_cost_usd": None,
        "actual_cost_usd": None,
        "cost_unknown": False,
        "created_at": "2026-10-24T00:00:00+00:00",
        "updated_at": "2026-10-24T00:00:00+00:00",
        "submitted_at": None,
        "completed_at": None,
    }
    base.update(overrides)
    return base


def test_supabase_reserve_posts_and_returns_representation() -> None:
    row = _row()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/inference_operations")
        assert request.url.params["select"] == INFERENCE_OPERATION_COLUMNS
        body = json.loads(request.content)
        assert body["state"] == "reserved"
        assert body["route_snapshot"]["secret_ref_name"] == "bundle:endpoint-1"
        return httpx.Response(201, json=[row])

    repo, fake = _make_repo(handler)
    op, created = _reserve(repo)
    assert created is True
    assert op.state is OperationState.RESERVED
    assert len(fake.requests) == 1


def test_supabase_reserve_unique_violation_returns_existing() -> None:
    existing = _row(state="running", provider_operation_id="ext-9")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.method == "POST":
            return httpx.Response(409, json={"code": "23505", "message": "duplicate key"})
        assert request.method == "GET"
        assert request.url.params["idempotency_key"] == "eq.idem-1"
        return httpx.Response(200, json=[existing])

    repo, fake = _make_repo(handler)
    op, created = _reserve(repo, key="idem-1")
    assert created is False
    assert op.state is OperationState.RUNNING
    assert op.provider_operation_id == "ext-9"
    assert calls["n"] == 2


def test_supabase_transition_uses_conditional_state_filter() -> None:
    row = _row(state="submitting")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.params["state"] == "in.(reserved)"
        body = json.loads(request.content)
        assert body["state"] == "submitting"
        return httpx.Response(200, json=[row])

    repo, _ = _make_repo(handler)
    op_id = uuid4()
    result = repo.transition(
        op_id, to_state=OperationState.SUBMITTING, expected_from={OperationState.RESERVED}
    )
    assert result.state is OperationState.SUBMITTING


def test_supabase_transition_conflict_when_no_row_patched() -> None:
    op_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(200, json=[])
        # Follow-up GET confirms the row exists (so it is a state conflict).
        return httpx.Response(200, json=[_row(id=str(op_id), state="running")])

    repo, _ = _make_repo(handler)
    with pytest.raises(TransitionConflictError):
        repo.transition(
            op_id, to_state=OperationState.SUBMITTING, expected_from={OperationState.RESERVED}
        )


def test_supabase_transition_missing_row_raises_not_found() -> None:
    op_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[])

    repo, _ = _make_repo(handler)
    with pytest.raises(OperationNotFoundError):
        repo.transition(
            op_id, to_state=OperationState.SUBMITTING, expected_from={OperationState.RESERVED}
        )


def test_supabase_list_non_terminal_filters_by_route_and_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["provider_route_id"] == "eq.route-1"
        assert "reserved" in request.url.params["state"]
        assert "succeeded" not in request.url.params["state"]
        return httpx.Response(200, json=[_row(state="submit_unknown")])

    repo, _ = _make_repo(handler)
    ops = repo.list_non_terminal_for_route("route-1")
    assert len(ops) == 1
    assert ops[0].state is OperationState.SUBMIT_UNKNOWN


def test_supabase_count_by_state_reads_content_range() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["state"] == "eq.submit_unknown"
        return httpx.Response(200, json=[], headers={"content-range": "0-4/5"})

    repo, _ = _make_repo(handler)
    assert repo.count_by_state(OperationState.SUBMIT_UNKNOWN) == 5


def test_supabase_raises_on_server_error() -> None:
    repo, _ = _make_repo(lambda request: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(InferenceOperationRepositoryError):
        repo.get(uuid4())


# --- sent classification -----------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("x"),
        httpx.ConnectTimeout("x"),
        httpx.PoolTimeout("x"),
        httpx.UnsupportedProtocol("x"),
        httpx.InvalidURL("x"),
    ],
)
def test_pre_send_failures_are_not_sent(exc) -> None:
    info = pr.classify_provider_exception(exc)
    assert info.sent is False
    assert pr.may_have_been_sent(info) is False


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("x"),
        httpx.WriteTimeout("x"),
        httpx.RemoteProtocolError("x"),
        httpx.ReadError("x"),
        httpx.WriteError("x"),
    ],
)
def test_after_send_failures_are_sent(exc) -> None:
    info = pr.classify_provider_exception(exc)
    assert info.sent is True
    assert pr.may_have_been_sent(info) is True


@pytest.mark.parametrize("status", [429, 401, 402, 500, 400, 418])
def test_status_classification_is_sent(status) -> None:
    info = pr.classify_provider_status(status)
    assert info.sent is True
    assert pr.may_have_been_sent(info) is True


def test_unknown_exception_sent_is_unknown() -> None:
    info = pr.classify_provider_exception(RuntimeError("x"))
    assert info.sent is None
    assert pr.may_have_been_sent(info) is True  # unknown is treated as maybe-sent


def test_provider_call_failure_preserves_sent() -> None:
    inner = pr.ProviderErrorInfo("provider_timeout", True, sent=False)
    failure = pr.ProviderCallFailure(provider="p", error_info=inner)
    assert pr.classify_provider_exception(failure).sent is False
