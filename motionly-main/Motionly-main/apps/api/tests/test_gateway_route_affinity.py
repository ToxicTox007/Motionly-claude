"""Gateway rules for async submits and route-affine status calls (Task 16A).

These exercise the GATEWAY half of plan Task 16A (invariant 6, Section 2.5):

* an async submit (a ``VideoGenerationSubmitRequest`` or any route flagged
  ``async_submit``) never falls back once the request may have been sent, but a
  failure proven before send still falls back exactly as a synchronous
  capability does today;
* a status/poll call that names a ``required_route_id`` is served only by that
  bound route — even when disabled or reprioritized — and is never re-routed;
* a status call whose bound route no longer exists, or whose revision changed,
  raises ``OperationRouteUnavailable`` and contacts no other route;
* status calls are not billed and do not consume a budget reservation.

The fakes are the Task 1 deterministic adapter (``inference_fake``) and the
Task 16B ``async_job`` adapter driven by an in-process ``httpx.MockTransport``
(mirroring ``tests/test_async_job_adapter.py``). No sockets, no credentials.
"""

from __future__ import annotations

import socket
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.services.async_job_adapter import AsyncJobAdapter, AsyncJobRouteConfig
from app.services.inference_fake import DeterministicInferenceAdapter, ScriptedOutcome
from app.services.inference_http import NoAuth
from app.services.inference_operations import (
    InMemoryInferenceOperationRepository,
    OperationState,
)
from app.services.model_gateway import (
    _ACTIVE_CALL_CONTEXT,
    ChatMessage,
    DiarizationRequest,
    GenerationOperationOutput,
    GenerationOutput,
    InMemoryBudgetLedger,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelProvidersExhausted,
    ModelRoute,
    OperationRouteUnavailable,
    PlanningRequest,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
    budget_scope,
)
from app.services.operations import OperationsRepository

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _route(
    route_id: str,
    capability: ModelCapability,
    *,
    priority: int = 100,
    fixed_cost_usd: float = 0.0,
    enabled: bool = True,
    async_submit: bool = False,
    model_revision: str | None = None,
    operations: tuple[ModelOperation, ...] = (),
    adapter: str = "fake",
    provider: str | None = None,
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        capability=capability,
        adapter=adapter,
        provider=provider or route_id,
        model=f"{route_id}-model",
        model_revision=model_revision or f"{route_id}-2026-07-14",
        priority=priority,
        fixed_cost_usd=fixed_cost_usd,
        enabled=enabled,
        async_submit=async_submit,
        operations=operations,
    )


def _gateway(routes, adapter, **kwargs) -> ModelGateway:
    return ModelGateway(
        adapters={"fake": adapter},
        routes=routes,
        request_budget_usd=kwargs.pop("request_budget_usd", 100.0),
        monthly_budget_usd=kwargs.pop("monthly_budget_usd", 1000.0),
        **kwargs,
    )


def _planning_request() -> PlanningRequest:
    return PlanningRequest(messages=(ChatMessage(role="user", content="plan this"),))


def _submit(key: str = "job-key") -> VideoGenerationSubmitRequest:
    return VideoGenerationSubmitRequest(
        prompt="a sunset",
        prompt_image_url=None,
        duration_s=4,
        ratio="16:9",
        idempotency_key=key,
    )


# ---------------------------------------------------------------------------
# 1. Async submit: no fallback after send; fallback before send.
# ---------------------------------------------------------------------------


def test_async_submit_timeout_after_send_contacts_no_second_route() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    # A read timeout after the request has been written: may have been sent.
    adapter.set_default("video-a", ScriptedOutcome.TIMEOUT_AFTER_SEND)
    routes = [
        _route("video-a", ModelCapability.GENERATION, priority=1),
        _route("video-b", ModelCapability.GENERATION, priority=2),
    ]
    gateway = _gateway(routes, adapter)

    with pytest.raises(ModelProvidersExhausted) as exc:
        gateway.invoke(_submit("after-send"))

    # Only the first route was contacted; the error is not fallback-eligible.
    assert [call.route_id for call in adapter.calls] == ["video-a"]
    assert exc.value.failures == ({"route_id": "video-a", "code": "provider_timeout"},)


def test_async_submit_failure_before_send_falls_back() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    # A connect timeout happens before anything leaves the client (sent=False).
    adapter.script("video-a", [ScriptedOutcome.TIMEOUT_BEFORE_SEND])
    adapter.set_default("video-b", ScriptedOutcome.SUCCESS)
    routes = [
        _route("video-a", ModelCapability.GENERATION, priority=1),
        _route("video-b", ModelCapability.GENERATION, priority=2),
    ]
    gateway = _gateway(routes, adapter)

    result = gateway.invoke(_submit("before-send"))

    # The before-send failure fell back to the second route, as today.
    assert [call.route_id for call in adapter.calls] == ["video-a", "video-b"]
    assert isinstance(result.output, GenerationOperationOutput)
    assert result.metadata.route_id == "video-b"


def test_async_submit_flag_route_stops_fallback_after_send() -> None:
    # A non-video capability whose route is flagged async_submit=True gets the
    # same route-affinity: an after-send failure must not fall back.
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("emb-a", ScriptedOutcome.TIMEOUT_AFTER_SEND)
    routes = [
        _route("emb-a", ModelCapability.EMBEDDINGS, priority=1, async_submit=True),
        _route("emb-b", ModelCapability.EMBEDDINGS, priority=2, async_submit=True),
    ]
    gateway = _gateway(routes, adapter)

    from app.services.model_gateway import EmbeddingsRequest

    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(EmbeddingsRequest(inputs=("x",)))
    assert [call.route_id for call in adapter.calls] == ["emb-a"]


# ---------------------------------------------------------------------------
# A synchronous capability keeps today's fallback behavior for BOTH cases.
# ---------------------------------------------------------------------------


def test_synchronous_capability_falls_back_after_send_and_before_send() -> None:
    # After-send (5xx, retryable) and before-send (connect timeout) both fall
    # back for a synchronous capability, unchanged by the async-submit rule.
    for outcome in (ScriptedOutcome.HTTP_5XX, ScriptedOutcome.TIMEOUT_BEFORE_SEND):
        adapter = DeterministicInferenceAdapter(app_env="test")
        adapter.script("primary", [outcome])
        adapter.set_default("secondary", ScriptedOutcome.SUCCESS)
        routes = [
            _route("primary", ModelCapability.PLANNING, priority=1),
            _route("secondary", ModelCapability.PLANNING, priority=2),
        ]
        gateway = _gateway(routes, adapter)

        result = gateway.invoke(_planning_request())

        assert [call.route_id for call in adapter.calls] == ["primary", "secondary"]
        assert result.metadata.route_id == "secondary"


def test_video_submit_no_fallback_still_holds_for_any_sent_failure() -> None:
    # The original video-submit no-fallback rule stays a subset of the new rule:
    # a 5xx (sent) still stops fallback for a VideoGenerationSubmitRequest.
    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("video-a", ScriptedOutcome.HTTP_5XX)
    routes = [
        _route("video-a", ModelCapability.GENERATION, priority=1),
        _route("video-b", ModelCapability.GENERATION, priority=2),
    ]
    gateway = _gateway(routes, adapter)

    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(_submit("sent-5xx"))
    assert [call.route_id for call in adapter.calls] == ["video-a"]


# ---------------------------------------------------------------------------
# 2. Route-affine status calls.
# ---------------------------------------------------------------------------


def test_status_call_bound_to_required_route_over_higher_priority_route() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    high = _route("video-high", ModelCapability.GENERATION, priority=1)
    low = _route("video-low", ModelCapability.GENERATION, priority=2)
    gateway = _gateway([high, low], adapter)

    # Submit once (unbound) to create a job, then poll bound to the low-priority
    # route even though a higher-priority enabled route exists.
    adapter.script_video_poll("poll-key", ["succeeded"])
    submit = gateway.invoke(
        _submit("poll-key"),
        context=ModelCallContext(required_route_id="video-low"),
    )
    operation_id = submit.output.operation_id  # type: ignore[union-attr]
    adapter.calls.clear()

    polled = gateway.invoke(
        VideoGenerationPollRequest(operation_id=operation_id),
        context=ModelCallContext(required_route_id="video-low"),
    )

    assert isinstance(polled.output, GenerationOutput)
    assert polled.metadata.route_id == "video-low"
    assert all(call.route_id == "video-low" for call in adapter.calls)


def test_status_call_on_disabled_route_still_works() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    disabled = _route("video-off", ModelCapability.GENERATION, enabled=False)
    gateway = _gateway([disabled], adapter)

    # The disabled route is not part of normal selection...
    assert all(route.route_id != "video-off" for route in gateway.routes)

    # ...but a status call bound to it by id is still served.
    adapter.script_video_poll("disabled-key", ["succeeded"])
    submit = gateway.invoke(
        _submit("disabled-key"),
        context=ModelCallContext(required_route_id="video-off"),
    )
    polled = gateway.invoke(
        VideoGenerationPollRequest(operation_id=submit.output.operation_id),  # type: ignore[union-attr]
        context=ModelCallContext(required_route_id="video-off"),
    )
    assert isinstance(polled.output, GenerationOutput)
    assert polled.metadata.route_id == "video-off"


def test_status_call_after_revision_change_raises_and_contacts_no_route() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _route("video-x", ModelCapability.GENERATION, model_revision="rev-2026-01-01")
    gateway = _gateway([route], adapter)
    adapter.calls.clear()

    with pytest.raises(OperationRouteUnavailable) as exc:
        gateway.invoke(
            VideoGenerationPollRequest(operation_id="op-1"),
            context=ModelCallContext(
                required_route_id="video-x",
                required_model_revision="rev-2026-09-09",
            ),
        )

    assert exc.value.code == "operation_route_unavailable"
    assert adapter.calls == []  # No route was contacted.


def test_status_call_for_missing_route_raises_operation_route_unavailable() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    gateway = _gateway([_route("video-y", ModelCapability.GENERATION)], adapter)
    adapter.calls.clear()

    with pytest.raises(OperationRouteUnavailable) as exc:
        gateway.invoke(
            VideoGenerationPollRequest(operation_id="op-2"),
            context=ModelCallContext(required_route_id="does-not-exist"),
        )
    assert exc.value.code == "operation_route_unavailable"
    assert adapter.calls == []


def test_status_calls_are_not_billed_and_reserve_no_budget() -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    ledger = InMemoryBudgetLedger()
    route = _route("video-z", ModelCapability.GENERATION, fixed_cost_usd=1.0)
    workspace = uuid4()
    gateway = _gateway([route], adapter, budget_ledger=ledger)

    adapter.script_video_poll("bill-key", ["succeeded"])
    submit = gateway.invoke(
        _submit("bill-key"),
        context=ModelCallContext(workspace_id=workspace, required_route_id="video-z"),
    )
    spend_after_submit = ledger.usage(budget_scope(workspace))
    assert submit.metadata.actual_cost_usd == 1.0

    polled = gateway.invoke(
        VideoGenerationPollRequest(operation_id=submit.output.operation_id),  # type: ignore[union-attr]
        context=ModelCallContext(workspace_id=workspace, required_route_id="video-z"),
    )

    # The poll cost nothing and did not grow the workspace spend.
    assert polled.metadata.actual_cost_usd == 0
    assert ledger.usage(budget_scope(workspace)) == spend_after_submit


# ---------------------------------------------------------------------------
# 5. Telemetry records the `sent` disposition on a failed async submit.
# ---------------------------------------------------------------------------


def test_failed_async_submit_records_sent_in_telemetry() -> None:
    from app.services.model_gateway import OperationsGatewayTelemetry

    adapter = DeterministicInferenceAdapter(app_env="test")
    adapter.set_default("video-a", ScriptedOutcome.TIMEOUT_AFTER_SEND)
    operations = OperationsRepository()
    telemetry = OperationsGatewayTelemetry(metrics_registry=None, operations_repository=operations)
    gateway = _gateway(
        [_route("video-a", ModelCapability.GENERATION)], adapter, telemetry=telemetry
    )

    with pytest.raises(ModelProvidersExhausted):
        gateway.invoke(_submit("sent-telemetry"))

    failed = [e for e in operations.provider_events if not e["success"]]
    assert failed, "a failed attempt should be recorded"
    assert failed[-1]["usage"]["sent"] == "true"


# ---------------------------------------------------------------------------
# 6 + ACCEPTANCE. A real ModelGateway wired to the async_job adapter never
# falls back after commit, with a complete persisted operation state trace.
# ---------------------------------------------------------------------------

BASE_URL = "https://api.async.invalid/v1"
UPLOAD_URL = "https://uploads.async.invalid/u/abc?sig=xyz"

DIAR_MAPPING = {
    "vars": {"name": {"$fn": "concat", "args": ["media://x-", {"$fn": "uuid", "args": []}]}},
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
        },
        "interval_s": 5,
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
    "cleanup": [{"method": "POST", "path": "/delete", "body": {"id": {"$ref": "vars.job_id"}}}],
}


class _FakeAsyncServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], object] = {}

    def on(self, method: str, path: str, responder) -> None:  # noqa: ANN001
        self.routes[(method.upper(), path)] = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers.get("host", request.url.host).split(":")[0]
        if host == "uploads.async.invalid":
            return httpx.Response(200, json={})
        path = request.url.path
        responder = self.routes.get((request.method.upper(), path))
        if responder is None:
            for (method, route_path), candidate in self.routes.items():
                if method == request.method.upper() and path.endswith(route_path):
                    responder = candidate
                    break
        if responder is None:
            return httpx.Response(404, json={"error": "no route", "path": path})
        return responder(request) if callable(responder) else responder

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def hit(self, suffix: str) -> bool:
        return any(p.endswith(suffix) for p in self.paths())


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _public_resolver(host: str, port: int, **_kwargs):  # noqa: ANN001, ANN202
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _audio_runner_ok(command):  # noqa: ANN001, ANN202
    Path(command[-1]).write_bytes(b"FLACfake")

    class _Result:
        returncode = 0

    return _Result()


def _async_route(route_id: str) -> ModelRoute:
    # async_submit=True mirrors inference_registry.to_model_route for async_job.
    return _route(
        route_id,
        ModelCapability.AUDIO,
        adapter="async_job",
        provider="async-provider",
        model_revision="pinned-v1-2026-01-01",
        operations=(ModelOperation.AUDIO_DIARIZATION,),
        async_submit=True,
    )


def _program_diar(server: _FakeAsyncServer) -> None:
    server.on("POST", "/media/input", httpx.Response(201, json={"url": UPLOAD_URL}))
    server.on("POST", "/diarize", httpx.Response(200, json={"jobId": "job-1"}))
    server.on("POST", "/delete", httpx.Response(200, json={}))
    server.on(
        "GET",
        "/jobs/job-1",
        httpx.Response(
            200,
            json={
                "status": "succeeded",
                "output": {"diarization": [{"speaker": "S0", "start": 0.0, "end": 1.0}]},
            },
        ),
    )


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "speech.wav"
    path.write_bytes(b"RIFFfake-source-media")
    return path


def test_async_job_route_never_falls_back_after_commit_through_gateway(
    audio_file: Path,
) -> None:
    # Two async_job routes behind one real ModelGateway. The first commits (the
    # submit reaches the fake server), then times out polling before a short
    # deadline. The second route must never be contacted (plan invariant 6), and
    # the persisted operation carries a complete state trace.
    server = _FakeAsyncServer()
    _program_diar(server)
    # After commit, the poll never reports success before the deadline.
    server.on("GET", "/jobs/job-1", httpx.Response(200, json={"status": "running"}))
    repo = InMemoryInferenceOperationRepository()
    clock = _FakeClock()
    mapping = {**DIAR_MAPPING, "poll": {**DIAR_MAPPING["poll"], "max_wait_s": 10}}
    config = AsyncJobRouteConfig(mapping=mapping)

    route_a = _async_route("diar-a")
    route_b = _async_route("diar-b")
    adapter_a = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="async-provider",
        transport=server.transport(),
        routes={route_a.route_id: config},
        audio_runner=_audio_runner_ok,
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_public_resolver,
    )
    # A second server/adapter that would 404 any submit, proving no fallback.
    other_server = _FakeAsyncServer()
    adapter_b = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="async-provider-b",
        transport=other_server.transport(),
        routes={route_b.route_id: config},
        audio_runner=_audio_runner_ok,
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_public_resolver,
    )
    gateway = ModelGateway(
        adapters={"async_job": _DispatchAdapter({"diar-a": adapter_a, "diar-b": adapter_b})},
        routes=[route_a, route_b],
        monthly_budget_usd=100.0,
        request_budget_usd=100.0,
    )

    context = ModelCallContext(idempotency_key="affine-key")
    token = _ACTIVE_CALL_CONTEXT.set(context)
    try:
        with pytest.raises(ModelProvidersExhausted) as exc:
            gateway.invoke(
                DiarizationRequest(audio_path=audio_file, duration_s=90.0),
                context=context,
            )
    finally:
        _ACTIVE_CALL_CONTEXT.reset(token)

    # The second route was never contacted: its server saw no requests.
    assert exc.value.failures == ({"route_id": "diar-a", "code": "provider_timeout"},)
    assert other_server.requests == []
    assert server.hit("/diarize")  # route-a submitted (committed) exactly once.

    # Persisted state trace: the operation committed and sits at submit/running,
    # never resubmitted to another route.
    op = next(iter(repo._by_id.values()))
    assert op.provider_route_id == "diar-a"
    assert op.provider_operation_id == "job-1"
    assert op.state in {OperationState.SUBMITTED, OperationState.RUNNING}


def test_async_job_commit_timeout_is_route_affine_submit_unknown(
    audio_file: Path,
) -> None:
    # A commit that times out after send records submit_unknown and never falls
    # back to the second route (the acceptance "no retry after send" case).
    server = _FakeAsyncServer()
    server.on("POST", "/media/input", httpx.Response(201, json={"url": UPLOAD_URL}))

    def submit_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("after send", request=request)

    server.on("POST", "/diarize", submit_timeout)
    repo = InMemoryInferenceOperationRepository()
    clock = _FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING)
    route_a = _async_route("diar-a")
    route_b = _async_route("diar-b")
    adapter_a = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="async-provider",
        transport=server.transport(),
        routes={route_a.route_id: config},
        audio_runner=_audio_runner_ok,
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_public_resolver,
    )
    other_server = _FakeAsyncServer()
    adapter_b = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="async-provider-b",
        transport=other_server.transport(),
        routes={route_b.route_id: config},
        audio_runner=_audio_runner_ok,
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_public_resolver,
    )
    gateway = ModelGateway(
        adapters={"async_job": _DispatchAdapter({"diar-a": adapter_a, "diar-b": adapter_b})},
        routes=[route_a, route_b],
        monthly_budget_usd=100.0,
        request_budget_usd=100.0,
    )

    context = ModelCallContext(idempotency_key="affine-key-2")
    token = _ACTIVE_CALL_CONTEXT.set(context)
    try:
        with pytest.raises(ModelProvidersExhausted):
            gateway.invoke(
                DiarizationRequest(audio_path=audio_file, duration_s=90.0),
                context=context,
            )
    finally:
        _ACTIVE_CALL_CONTEXT.reset(token)

    assert other_server.requests == []
    op = next(iter(repo._by_id.values()))
    assert op.state == OperationState.SUBMIT_UNKNOWN


class _DispatchAdapter:
    """Routes a gateway invoke to the right per-route async_job adapter.

    One registry adapter record would own a base URL and all its routes; here two
    standalone ``AsyncJobAdapter`` instances stand in for two providers so the
    "second route is never contacted" assertion can inspect each server.
    """

    def __init__(self, by_route: dict[str, AsyncJobAdapter]) -> None:
        self._by_route = by_route

    def invoke(self, *, route: ModelRoute, request):  # noqa: ANN001, ANN202
        return self._by_route[route.route_id].invoke(route=route, request=request)
