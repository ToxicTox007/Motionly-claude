"""Config-only non-blocking video generation (plan Task 28, Tier 3 gate item 2).

Tier 3 gate item 2: "A config-only long async video route works in non-blocking
mode against fakes, and an after-send timeout never reaches a second route."

These tests drive a ``video.generation`` route defined ONLY by an ``async_job``
mapping with ``mode: non_blocking`` through the :class:`InferencePollingEngine`
against an in-process fake provider server (``httpx.MockTransport``). No sockets,
no credentials, no vendor fixtures — the route differs from the diarization/stem
routes only by its mapping, which proves shape independence.

They prove, end to end on a long async video job:

* submit returns a pending operation with a first poll delay (no blocking poll);
* the first poll is ``pending`` with a retry delay, the second is terminal and
  yields the generated artifact URL as a ``GenerationOutput``;
* a simulated worker restart between polls resumes without a second submit
  (``submit_hits == 1``);
* an after-send timeout on the commit leaves the operation ``submit_unknown``
  and never contacts a second route.
"""

from __future__ import annotations

import socket
from uuid import uuid4

import httpx
import pytest

from app.services.async_job_adapter import AsyncJobAdapter, AsyncJobRouteConfig
from app.services.inference_http import NoAuth
from app.services.inference_operations import (
    InMemoryInferenceOperationRepository,
    OperationState,
)
from app.services.inference_polling import InferencePollingEngine, PollTarget
from app.services.model_gateway import (
    GenerationOutput,
    ModelCapability,
    ModelOperation,
    ModelRoute,
    VideoGenerationSubmitRequest,
)
from app.services.provider_resilience import ProviderCallFailure

BASE_URL = "https://api.video.invalid/v1"
ARTIFACT_URL = "https://files.video.invalid/clips/clip-1.mp4"

# A config-only ``video.generation`` async_job route in NON-BLOCKING mode. The
# submit body reads the typed request fields via ``input.*`` (Section 2.10
# ``input`` root); the poll reads a captured ``job_id``; the result resolves one
# artifact URL. Nothing vendor-specific appears here — only the mapping shape.
NON_BLOCKING_VIDEO_MAPPING = {
    "mode": "non_blocking",
    "steps": [
        {
            "method": "POST",
            "path": "/renders",
            "body": {
                "prompt": {"$ref": "input.prompt"},
                "seconds": {"$ref": "input.duration_s"},
                "aspect": {"$ref": "input.ratio"},
            },
            "capture": {"job_id": "response.renderId"},
            "commit": True,
        },
    ],
    "poll": {
        "method": "GET",
        "path": "/renders/{vars.job_id}",
        "status": "response.state",
        "status_map": {
            "rendering": {"state": "running"},
            "done": {"state": "succeeded"},
            "error": {"state": "failed", "billable": False, "fallback": False},
        },
        "interval_s": 30,
        "max_wait_s": 3600,
    },
    "result": {
        "items": "response.outputs",
        "fields": {"url": "assetUrl"},
    },
}


class FakeVideoServer:
    """An in-process fake async video provider (no sockets, scenario-driven)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.poll_states: list[str] = ["rendering", "done"]
        self.submit_hits = 0
        self.submit_timeout = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/renders"):
            self.submit_hits += 1
            if self.submit_timeout:
                raise httpx.ReadTimeout("fake after-send timeout", request=request)
            return httpx.Response(201, json={"renderId": "render-1"})
        if path.endswith("/renders/render-1"):
            state = self.poll_states.pop(0) if len(self.poll_states) > 1 else self.poll_states[0]
            if state == "done":
                return httpx.Response(
                    200,
                    json={"state": "done", "outputs": [{"assetUrl": ARTIFACT_URL}]},
                )
            return httpx.Response(200, json={"state": state})
        return httpx.Response(404, json={"path": path})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def submit_paths(self) -> list[str]:
        return [r.url.path for r in self.requests if r.url.path.endswith("/renders")]


def _resolver(host, port, **_kw):  # noqa: ANN001, ANN202
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="video-remote",
        capability=ModelCapability.GENERATION,
        adapter="video-endpoint",
        provider="generated",
        model="video-model-x",
        model_revision="pinned-v1-2026-10-25",
        operations=(ModelOperation.GENERATION_VIDEO_SUBMIT,),
        async_submit=True,
    )


def _request() -> VideoGenerationSubmitRequest:
    return VideoGenerationSubmitRequest(
        prompt="a calm ocean at sunrise",
        prompt_image_url=None,
        duration_s=5,
        ratio="768:1280",
        idempotency_key="generated_broll:test",
    )


def _build(server: FakeVideoServer, repo) -> tuple[InferencePollingEngine, PollTarget]:
    config = AsyncJobRouteConfig(mapping=NON_BLOCKING_VIDEO_MAPPING)
    adapter = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="generated",
        transport=server.transport(),
        routes={"video-remote": config},
        dns_resolver=_resolver,
    )
    target = PollTarget(
        adapter=adapter, route=_route(), config=config, request=_request()
    )
    engine = InferencePollingEngine(
        operations_repository=repo, resolver=lambda _operation: target
    )
    return engine, target


def test_non_blocking_mode_is_detected_from_config() -> None:
    config = AsyncJobRouteConfig(mapping=NON_BLOCKING_VIDEO_MAPPING)
    assert AsyncJobAdapter.is_non_blocking(config) is True


def test_submit_pending_then_poll_to_terminal_artifact() -> None:
    server = FakeVideoServer()
    server.poll_states = ["rendering", "done"]
    repo = InMemoryInferenceOperationRepository()
    engine, target = _build(server, repo)

    operation, handle = engine.submit(
        target=target,
        workspace_id=uuid4(),
        project_id=uuid4(),
        idempotency_key="generated_broll:test",
    )
    # Submitted (no blocking poll), with the route's poll interval as the delay.
    assert operation.state is OperationState.SUBMITTED
    assert operation.provider_operation_id == "render-1"
    assert handle.first_delay_s == 30
    assert server.submit_hits == 1

    # First poll: still rendering -> pending with a retry delay.
    first = engine.poll_once(operation.id)
    assert first.pending is True
    assert first.retry_after_s == 30
    assert first.state is OperationState.RUNNING

    # Second poll: done -> terminal GenerationOutput with the artifact URL.
    second = engine.poll_once(operation.id)
    assert second.is_terminal
    assert isinstance(second.result.output, GenerationOutput)
    assert second.result.output.artifacts[0].url == ARTIFACT_URL
    assert second.result.output.operation_id == "render-1"
    assert server.submit_hits == 1  # still exactly one submit


def test_worker_restart_between_polls_never_resubmits() -> None:
    server = FakeVideoServer()
    server.poll_states = ["rendering", "done"]
    repo = InMemoryInferenceOperationRepository()
    engine, target = _build(server, repo)

    operation, _ = engine.submit(
        target=target,
        workspace_id=uuid4(),
        project_id=uuid4(),
        idempotency_key="generated_broll:test",
    )
    assert server.submit_hits == 1
    engine.poll_once(operation.id)  # rendering

    # Simulate a worker restart: a brand-new engine over the SAME repo resumes.
    engine2 = InferencePollingEngine(
        operations_repository=repo, resolver=lambda _operation: target
    )
    final = engine2.poll_once(operation.id)  # done
    assert final.is_terminal
    assert isinstance(final.result.output, GenerationOutput)
    assert server.submit_hits == 1  # never resubmitted after the restart


def test_idempotent_submit_does_not_resubmit() -> None:
    server = FakeVideoServer()
    repo = InMemoryInferenceOperationRepository()
    engine, target = _build(server, repo)
    ws = uuid4()
    op1, _ = engine.submit(
        target=target, workspace_id=ws, project_id=None, idempotency_key="same"
    )
    op2, _ = engine.submit(
        target=target, workspace_id=ws, project_id=None, idempotency_key="same"
    )
    assert op1.id == op2.id
    assert server.submit_hits == 1


def test_after_send_timeout_marks_submit_unknown_and_contacts_no_second_route() -> None:
    server = FakeVideoServer()
    server.submit_timeout = True  # the commit send times out after reaching the wire
    repo = InMemoryInferenceOperationRepository()
    engine, target = _build(server, repo)

    workspace_id = uuid4()
    with pytest.raises(ProviderCallFailure) as exc_info:
        engine.submit(
            target=target,
            workspace_id=workspace_id,
            project_id=None,
            idempotency_key="generated_broll:test",
        )
    # The after-send failure is route-affine and must not fall back.
    assert exc_info.value.error_info.fallback_eligible is False

    operation = repo.get_by_idempotency_key(workspace_id, "generated_broll:test")
    assert operation is not None
    assert operation.state is OperationState.SUBMIT_UNKNOWN

    # Exactly one submit reached the wire, and only the one route path was ever
    # contacted; no second route was tried.
    assert server.submit_hits == 1
    assert all(r.url.path.endswith("/renders") for r in server.requests)
