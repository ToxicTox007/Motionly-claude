"""Tests for the non-blocking async polling engine (plan Task 28).

These prove the three contract points:

* ``submit`` persists an operation, runs the steps through commit, and returns a
  handle with the first poll delay (no blocking poll);
* ``poll_once`` advances exactly one poll and returns ``pending`` (with a
  ``retry_after_s``) or a terminal result;
* a worker that resumes after a "restart" (a fresh engine over the same repo)
  never re-runs the submit steps — it polls the persisted operation.

Everything runs against an in-process fake async server via ``httpx.MockTransport``
and an in-memory operation repository. No sockets, no credentials.
"""

from __future__ import annotations

import socket
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.services.async_job_adapter import AsyncJobAdapter, AsyncJobRouteConfig
from app.services.inference_http import NoAuth
from app.services.inference_operations import (
    InMemoryInferenceOperationRepository,
    OperationState,
)
from app.services.inference_polling import (
    InferencePollingEngine,
    PollTarget,
    RouteUnavailableError,
)
from app.services.model_gateway import (
    DiarizationOutput,
    DiarizationRequest,
    ModelCapability,
    ModelOperation,
    ModelRoute,
)

BASE_URL = "https://api.async.invalid/v1"
UPLOAD_URL = "https://uploads.async.invalid/u/abc?sig=xyz"

NON_BLOCKING_DIAR_MAPPING = {
    "mode": "non_blocking",
    "vars": {
        "name": {"$fn": "concat", "args": ["media://motionly-", {"$fn": "uuid", "args": []}]}
    },
    "steps": [
        {
            "method": "POST",
            "path": "/media/input",
            "body": {"url": {"$ref": "vars.name"}},
            "capture": {"upload_url": "response.url"},
            "require": {"upload_url": "https"},
        },
        {"method": "PUT", "url": "vars.upload_url", "auth": "none", "body": "input.file"},
        {
            "method": "POST",
            "path": "/diarize",
            "body": {"url": {"$ref": "vars.name"}},
            "capture": {"job_id": "response.jobId"},
            "commit": True,
        },
    ],
    "poll": {
        "method": "GET",
        "path": "/jobs/{vars.job_id}",
        "status": "response.status",
        "status_map": {
            "running": {"state": "running"},
            "succeeded": {"state": "succeeded"},
            "failed": {"state": "failed", "billable": False, "fallback": False},
        },
        "interval_s": 7,
        "max_wait_s": 900,
    },
    "result": {
        "items": "response.output.diarization",
        "fields": {
            "speaker": "speaker",
            "start_ms": {"$fn": "to_ms", "args": [{"$ref": "item.start"}]},
            "end_ms": {"$fn": "to_ms", "args": [{"$ref": "item.end"}]},
        },
    },
}


class FakeServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.poll_statuses: list[str] = ["running", "succeeded"]
        self.submit_hits = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers.get("host", request.url.host).split(":")[0]
        path = request.url.path
        if host == "uploads.async.invalid":
            return httpx.Response(200, json={})
        if path.endswith("/media/input"):
            return httpx.Response(201, json={"url": UPLOAD_URL})
        if path.endswith("/diarize"):
            self.submit_hits += 1
            return httpx.Response(200, json={"jobId": "job-1"})
        if path.endswith("/jobs/job-1"):
            status = (
                self.poll_statuses.pop(0)
                if len(self.poll_statuses) > 1
                else self.poll_statuses[0]
            )
            if status == "succeeded":
                return httpx.Response(
                    200,
                    json={
                        "status": "succeeded",
                        "output": {"diarization": [{"speaker": "S0", "start": 0.0, "end": 1.0}]},
                    },
                )
            return httpx.Response(200, json={"status": status})
        return httpx.Response(404, json={"path": path})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def submit_paths(self) -> list[str]:
        return [r.url.path for r in self.requests if r.url.path.endswith("/diarize")]


def _resolver(host, port, **_kw):  # noqa: ANN001, ANN202
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _audio_runner(command):  # noqa: ANN001, ANN202
    Path(command[-1]).write_bytes(b"FLACfake")

    class _Result:
        returncode = 0

    return _Result()


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="diar-remote",
        capability=ModelCapability.AUDIO,
        adapter="diar-endpoint",
        provider="async-provider",
        model="model-x",
        model_revision="pinned-v1-2026-01-01",
        operations=(ModelOperation.AUDIO_DIARIZATION,),
        async_submit=True,
    )


def _build(server, repo) -> tuple[InferencePollingEngine, PollTarget, DiarizationRequest]:
    config = AsyncJobRouteConfig(mapping=NON_BLOCKING_DIAR_MAPPING)
    adapter = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="async-provider",
        transport=server.transport(),
        routes={"diar-remote": config},
        audio_runner=_audio_runner,
        dns_resolver=_resolver,
    )
    route = _route()
    request = DiarizationRequest(audio_path=Path("/tmp/does-not-matter.wav"), duration_s=10.0)
    target = PollTarget(adapter=adapter, route=route, config=config, request=request)
    resolver = lambda operation: target  # noqa: E731
    engine = InferencePollingEngine(operations_repository=repo, resolver=resolver)
    return engine, target, request


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "speech.wav"
    path.write_bytes(b"RIFFfake-source")
    return path


def test_submit_persists_operation_and_returns_handle(audio: Path) -> None:
    server = FakeServer()
    repo = InMemoryInferenceOperationRepository()
    engine, target, _ = _build(server, repo)
    target = PollTarget(
        adapter=target.adapter,
        route=target.route,
        config=target.config,
        request=DiarizationRequest(audio_path=audio, duration_s=10.0),
    )
    operation, handle = engine.submit(
        target=target,
        workspace_id=uuid4(),
        project_id=None,
        idempotency_key="job-key-1",
    )
    assert operation.state == OperationState.SUBMITTED
    assert operation.provider_operation_id == "job-1"
    assert handle.operation_id == operation.id
    assert handle.first_delay_s == 7  # the poll interval
    assert server.submit_hits == 1  # the submit ran exactly once


def test_poll_once_pending_then_terminal(audio: Path) -> None:
    server = FakeServer()
    server.poll_statuses = ["running", "succeeded"]
    repo = InMemoryInferenceOperationRepository()
    engine, target, _ = _build(server, repo)
    target = PollTarget(
        adapter=target.adapter,
        route=target.route,
        config=target.config,
        request=DiarizationRequest(audio_path=audio, duration_s=10.0),
    )
    operation, _ = engine.submit(
        target=target, workspace_id=uuid4(), project_id=None, idempotency_key="k"
    )
    # First poll: running -> pending with a retry delay.
    first = engine.poll_once(operation.id)
    assert first.pending is True
    assert first.retry_after_s == 7
    assert first.state == OperationState.RUNNING
    # Second poll: succeeded -> terminal result.
    second = engine.poll_once(operation.id)
    assert second.pending is False
    assert second.is_terminal
    assert isinstance(second.result.output, DiarizationOutput)
    assert second.result.output.segments[0].speaker == "S0"


def test_resume_after_restart_never_resubmits(audio: Path) -> None:
    server = FakeServer()
    server.poll_statuses = ["running", "succeeded"]
    repo = InMemoryInferenceOperationRepository()
    engine, target, _ = _build(server, repo)
    target = PollTarget(
        adapter=target.adapter,
        route=target.route,
        config=target.config,
        request=DiarizationRequest(audio_path=audio, duration_s=10.0),
    )
    operation, _ = engine.submit(
        target=target, workspace_id=uuid4(), project_id=None, idempotency_key="k"
    )
    assert server.submit_hits == 1

    # Simulate a worker restart: a brand-new engine over the SAME repo.
    engine2 = InferencePollingEngine(
        operations_repository=repo, resolver=lambda op: target
    )
    engine2.poll_once(operation.id)  # running
    final = engine2.poll_once(operation.id)  # succeeded
    assert final.is_terminal
    # The submit endpoint was never hit again after the restart.
    assert server.submit_hits == 1


def test_idempotent_submit_does_not_resubmit(audio: Path) -> None:
    server = FakeServer()
    repo = InMemoryInferenceOperationRepository()
    engine, target, _ = _build(server, repo)
    target = PollTarget(
        adapter=target.adapter,
        route=target.route,
        config=target.config,
        request=DiarizationRequest(audio_path=audio, duration_s=10.0),
    )
    ws = uuid4()
    op1, _ = engine.submit(target=target, workspace_id=ws, project_id=None, idempotency_key="same")
    op2, _ = engine.submit(target=target, workspace_id=ws, project_id=None, idempotency_key="same")
    assert op1.id == op2.id
    assert server.submit_hits == 1


def test_poll_once_terminal_operation_short_circuits(audio: Path) -> None:
    server = FakeServer()
    server.poll_statuses = ["succeeded"]
    repo = InMemoryInferenceOperationRepository()
    engine, target, _ = _build(server, repo)
    target = PollTarget(
        adapter=target.adapter,
        route=target.route,
        config=target.config,
        request=DiarizationRequest(audio_path=audio, duration_s=10.0),
    )
    operation, _ = engine.submit(
        target=target, workspace_id=uuid4(), project_id=None, idempotency_key="k"
    )
    engine.poll_once(operation.id)  # reaches succeeded, builds result
    before = len(server.requests)
    result = engine.poll_once(operation.id)  # already terminal: no provider call
    assert result.is_terminal
    assert len(server.requests) == before


def test_route_unavailable_raises(audio: Path) -> None:
    server = FakeServer()
    repo = InMemoryInferenceOperationRepository()
    engine, target, _ = _build(server, repo)
    target = PollTarget(
        adapter=target.adapter,
        route=target.route,
        config=target.config,
        request=DiarizationRequest(audio_path=audio, duration_s=10.0),
    )
    operation, _ = engine.submit(
        target=target, workspace_id=uuid4(), project_id=None, idempotency_key="k"
    )
    # A resolver that can no longer find the route.
    gone = InferencePollingEngine(operations_repository=repo, resolver=lambda op: None)
    with pytest.raises(RouteUnavailableError):
        gone.poll_once(operation.id)
