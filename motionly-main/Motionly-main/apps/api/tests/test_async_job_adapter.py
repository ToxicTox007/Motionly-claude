"""Tests for the ``async_job`` adapter — Tier 1 slice, blocking mode (Task 16B).

Everything runs against in-process fakes through ``httpx.MockTransport`` and an
in-memory operation repository, with injected fake clock/sleep and a fake audio
runner. No sockets, no real ffmpeg, no provider credentials.

Shape independence is proven with two differently-shaped fake APIs for each of
the two capabilities (diarization and stems): the request/response JSON differs
in every case, and only the route ``mapping`` changes.
"""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from app.services.async_job_adapter import (
    ADAPTER_TYPE,
    ASYNC_JOB_TEMP_PREFIX,
    AsyncJobAdapter,
    AsyncJobRouteConfig,
    AudioTransform,
)
from app.services.inference_http import NoAuth
from app.services.inference_operations import (
    InMemoryInferenceOperationRepository,
    OperationState,
)
from app.services.inference_templates import validate_mapping
from app.services.model_gateway import (
    _ACTIVE_CALL_CONTEXT,
    DiarizationOutput,
    DiarizationRequest,
    ModelCallContext,
    ModelCapability,
    ModelOperation,
    ModelRoute,
    StemSeparationOutput,
    StemSeparationRequest,
)
from app.services.provider_resilience import ProviderCallFailure

BASE_URL = "https://api.async.invalid/v1"
UPLOAD_URL = "https://uploads.async.invalid/u/abc?sig=xyz"
FILES_HOST = "files.async.invalid"


# ---------------------------------------------------------------------------
# A scenario-driven fake async server. Routes are matched by (method, path).
# ---------------------------------------------------------------------------
class FakeAsyncServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], object] = {}
        self.downloads: dict[str, bytes] = {}
        self.upload_hits = 0

    def on(self, method: str, path: str, responder) -> None:  # noqa: ANN001
        self.routes[(method.upper(), path)] = responder

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # Captured-URL requests are pinned to a validated IP (the URL host is
        # the address) and carry the logical host in the Host header, exactly
        # as a real server sees them.
        host = request.headers.get("host", request.url.host).split(":")[0]
        url = str(request.url.copy_with(host=host))
        path = request.url.path
        if host in {"uploads.async.invalid"}:
            self.upload_hits += 1
            return httpx.Response(200, json={})
        if host == FILES_HOST or host.endswith(".cdn.async.invalid"):
            data = self.downloads.get(url)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, content=data)
        responder = self.routes.get((request.method.upper(), path))
        if responder is None:
            # Tolerate the base-url path prefix (e.g. "/v1"): match by suffix.
            for (method, route_path), candidate in self.routes.items():
                if method == request.method.upper() and path.endswith(route_path):
                    responder = candidate
                    break
        if responder is None:
            return httpx.Response(404, json={"error": "no route", "path": path})
        if callable(responder):
            return responder(request)
        return responder

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def hit(self, suffix: str) -> bool:
        return any(p.endswith(suffix) for p in self.paths())

    def hosts(self) -> list[str]:
        return [r.headers.get("host", r.url.host).split(":")[0] for r in self.requests]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _public_resolver(host: str, port: int, **_kwargs):  # noqa: ANN001, ANN202
    # A fake DNS resolver that keeps every fake host public (no sockets).
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _audio_runner_ok(output_writes: list[Path]):  # noqa: ANN202
    def run(command):  # noqa: ANN001
        # The output path is the last argument (mirrors extract_flac_audio).
        out = Path(command[-1])
        out.write_bytes(b"FLACfake-audio")
        output_writes.append(out)

        class _Result:
            returncode = 0

        return _Result()

    return run


def _route(capability: ModelCapability, operation: ModelOperation) -> ModelRoute:
    return ModelRoute(
        route_id=f"{capability.value}-remote",
        capability=capability,
        adapter=f"{capability.value}-endpoint",
        provider="async-provider",
        model="model-x",
        model_revision="pinned-v1-2026-01-01",
        operations=(operation,),
    )


def _diar_route() -> ModelRoute:
    return _route(ModelCapability.AUDIO, ModelOperation.AUDIO_DIARIZATION)


def _stem_route() -> ModelRoute:
    return _route(ModelCapability.AUDIO, ModelOperation.AUDIO_STEM_SEPARATION)


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "speech.wav"
    path.write_bytes(b"RIFFfake-source-media")
    return path


def _adapter(server: FakeAsyncServer, repo, route_id, config, clock):  # noqa: ANN001
    return AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="async-provider",
        transport=server.transport(),
        routes={route_id: config},
        audio_runner=_audio_runner_ok([]),
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_public_resolver,
    )


def _diar_short_wait() -> dict:
    return {**DIAR_MAPPING_A, "poll": {**DIAR_MAPPING_A["poll"], "max_wait_s": 10}}


def _stem_capped(max_bytes: int) -> dict:
    return {
        **STEM_MAPPING_A,
        "artifacts": {**STEM_MAPPING_A["artifacts"], "max_bytes": max_bytes},
    }


# ===========================================================================
# Diarization — fake API shape A
# ===========================================================================
DIAR_MAPPING_A = {
    "vars": {
        "name": {
            "$fn": "concat",
            "args": ["media://motionly-", {"$fn": "uuid", "args": []}],
        }
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
        "tolerate_retryable": True,
        "interval_s": 5,
        "backoff": {"factor": 1.5, "max_interval_s": 30},
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
    "cleanup": [
        {"method": "POST", "path": "/delete", "body": {"id": {"$ref": "vars.job_id"}}}
    ],
}


def _program_diar_a(server: FakeAsyncServer, *, poll_statuses=None) -> None:
    statuses = list(poll_statuses or ["succeeded"])
    server.on("POST", "/media/input", httpx.Response(201, json={"url": UPLOAD_URL}))
    server.on("POST", "/diarize", httpx.Response(200, json={"jobId": "job-A-1"}))
    server.on("POST", "/delete", httpx.Response(200, json={}))

    def poll(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        if status == "succeeded":
            return httpx.Response(
                200,
                json={
                    "status": "succeeded",
                    "output": {
                        "diarization": [
                            {"speaker": "S1", "start": 4.2, "end": 7.0},
                            {"speaker": "S0", "start": 0.5, "end": 4.0},
                        ]
                    },
                },
            )
        return httpx.Response(200, json={"status": status})

    server.on("GET", "/jobs/job-A-1", poll)


def test_diarization_config_only_shape_a(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_diar_a(server)
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)

    result = adapter.invoke(
        route=route,
        request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
    )

    assert isinstance(result.output, DiarizationOutput)
    assert [s.speaker for s in result.output.segments] == ["S0", "S1"]
    assert result.output.segments[0].start_ms == 500
    assert result.output.segments[1].end_ms == 7000
    # auth:none upload hit the captured host, not the API host.
    assert server.upload_hits == 1
    # Cleanup ran on success.
    assert server.hit("/delete")
    # The operation reached succeeded.
    op = next(iter(repo._by_id.values()))
    assert op.state == OperationState.SUCCEEDED
    assert op.provider_operation_id == "job-A-1"


# ===========================================================================
# Diarization — fake API shape B (different field names and structure)
# ===========================================================================
DIAR_MAPPING_B = {
    "vars": {"ref": {"$fn": "uuid", "args": []}},
    "steps": [
        {
            "method": "POST",
            "path": "/v2/uploads",
            "body": {"ref": {"$ref": "vars.ref"}},
            "capture": {"put_url": "response.data.put"},
            "require": {"put_url": "https"},
        },
        {"method": "PUT", "url": "vars.put_url", "auth": "none", "body": "input.file"},
        {
            "method": "POST",
            "path": "/v2/tasks",
            "body": {"ref": {"$ref": "vars.ref"}},
            "capture": {"task": "response.task.id"},
            "commit": True,
        },
    ],
    "poll": {
        "method": "POST",
        "path": "/v2/tasks/status",
        "body": {"task": {"$ref": "vars.task"}},
        "status": "response.task.state",
        "status_map": {
            "WORKING": {"state": "running"},
            "DONE": {"state": "succeeded"},
            "ERROR": {"state": "failed"},
        },
        "interval_s": 2,
        "max_wait_s": 600,
    },
    "result": {
        "items": "response.segments",
        "fields": {
            "speaker": "label",
            "start_ms": {"$fn": "to_ms", "args": [{"$ref": "item.from_s"}]},
            "end_ms": {"$fn": "to_ms", "args": [{"$ref": "item.to_s"}]},
        },
    },
}


def test_diarization_config_only_shape_b(audio_file: Path) -> None:
    server = FakeAsyncServer()
    server.on("POST", "/v2/uploads", httpx.Response(200, json={"data": {"put": UPLOAD_URL}}))
    server.on("POST", "/v2/tasks", httpx.Response(200, json={"task": {"id": "T-99"}}))

    def status(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "task": {"state": "DONE"},
                "segments": [
                    {"label": "A", "from_s": 1.0, "to_s": 2.0},
                    {"label": "B", "from_s": 0.0, "to_s": 1.0},
                ],
            },
        )

    server.on("POST", "/v2/tasks/status", status)
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_B)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)

    result = adapter.invoke(
        route=route,
        request=DiarizationRequest(audio_path=audio_file, duration_s=30.0),
    )
    assert isinstance(result.output, DiarizationOutput)
    assert [s.speaker for s in result.output.segments] == ["B", "A"]
    assert result.output.segments[0].start_ms == 0
    assert result.output.segments[1].start_ms == 1000


# ===========================================================================
# Stems — fake API shape A (two-stem names mapped to vocals/no_vocals)
# ===========================================================================
STEM_MAPPING_A = {
    "vars": {"key": {"$fn": "uuid", "args": []}},
    "steps": [
        {
            "method": "POST",
            "path": "/upload",
            "body": "input.file",
            "capture": {"source_id": "response.id"},
        },
        {
            "method": "POST",
            "path": "/split",
            "body": {"source_id": {"$ref": "vars.source_id"}, "key": {"$ref": "vars.key"}},
            "capture": {"task_id": "response.task_id"},
            "commit": True,
        },
    ],
    "poll": {
        "method": "POST",
        "path": "/check/{vars.task_id}",
        "status": "response.status",
        "status_map": {
            "progress": {"state": "running"},
            "success": {"state": "succeeded"},
            "server_error": {"state": "retryable_failed", "fallback": False},
            "error": {"state": "failed", "fallback": False},
        },
        "tolerate_retryable": True,
        "interval_s": 5,
        "backoff": {"factor": 1.5, "max_interval_s": 30},
        "max_wait_s": 1800,
    },
    "artifacts": {
        "items": "response.tracks",
        "name": "type",
        "url": "url",
        "name_map": {"stem": "vocals", "back": "no_vocals"},
        "required": ["vocals", "no_vocals"],
        "max_bytes": 2147483648,
        "allowed_hosts": [FILES_HOST],
        "force_https": True,
        "auth": "none",
    },
    "cleanup": [
        {"method": "POST", "path": "/delete", "body": {"source_id": {"$ref": "vars.source_id"}}}
    ],
}

VOCALS_URL = f"https://{FILES_HOST}/v.wav"
BACK_URL = f"https://{FILES_HOST}/b.wav"


def _program_stem_a(server: FakeAsyncServer, *, statuses=None, tracks=None) -> None:
    status_list = list(statuses or ["success"])
    server.on("POST", "/upload", httpx.Response(200, json={"id": "src-1"}))
    server.on("POST", "/split", httpx.Response(200, json={"task_id": "task-1"}))
    server.on("POST", "/delete", httpx.Response(200, json={}))
    server.downloads[VOCALS_URL] = b"RIFF-vocals-data"
    server.downloads[BACK_URL] = b"RIFF-back-data"
    track_list = tracks if tracks is not None else [
        {"type": "stem", "url": VOCALS_URL},
        {"type": "back", "url": BACK_URL},
    ]

    def check(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        status = status_list.pop(0) if len(status_list) > 1 else status_list[0]
        if status == "success":
            return httpx.Response(200, json={"status": "success", "tracks": track_list})
        return httpx.Response(200, json={"status": status})

    server.on("POST", "/check/task-1", check)


def test_stems_config_only_shape_a(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_stem_a(server)
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(
        mapping=STEM_MAPPING_A,
        input_transform_audio=AudioTransform("flac", 44100, 2),
        upload_filename="motionly-audio.flac",
    )
    route = _stem_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)

    result = adapter.invoke(
        route=route,
        request=StemSeparationRequest(source_path=audio_file, model="demucs", duration_s=90.0),
    )
    assert isinstance(result.output, StemSeparationOutput)
    assert set(result.output.stems) == {"vocals", "no_vocals"}
    for path in result.output.stems.values():
        assert path.exists() and path.stat().st_size > 0
    assert server.hit("/delete")
    # The caller-handed directory uses the neutral prefix.
    assert ASYNC_JOB_TEMP_PREFIX in str(next(iter(result.output.stems.values())))


# ===========================================================================
# Stems — fake API shape B (different names, nested list, wildcard host)
# ===========================================================================
STEM_MAPPING_B = {
    "steps": [
        {
            "method": "PUT",
            "path": "/files",
            "body": "input.file",
            "capture": {"fid": "response.file_id"},
        },
        {
            "method": "POST",
            "path": "/separate",
            "body": {"fid": {"$ref": "vars.fid"}},
            "capture": {"job": "response.job"},
            "commit": True,
        },
    ],
    "poll": {
        "method": "GET",
        "path": "/separate/{vars.job}",
        "status": "response.phase",
        "status_map": {"run": {"state": "running"}, "ok": {"state": "succeeded"}},
        "interval_s": 3,
        "max_wait_s": 300,
    },
    "artifacts": {
        "items": "response.outputs",
        "name": "kind",
        "url": "link",
        "name_map": {"voice": "vocals", "music": "no_vocals", "other": "other"},
        "required": ["vocals", "no_vocals"],
        "allowed_hosts": ["*.cdn.async.invalid"],
        "force_https": True,
        "auth": "none",
    },
}


def test_stems_config_only_shape_b(audio_file: Path) -> None:
    server = FakeAsyncServer()
    v_url = "https://edge.cdn.async.invalid/voice.wav"
    m_url = "https://edge.cdn.async.invalid/music.wav"
    extra_url = "https://edge.cdn.async.invalid/other.wav"
    server.downloads[v_url] = b"RIFF-voice"
    server.downloads[m_url] = b"RIFF-music"
    server.downloads[extra_url] = b"RIFF-other"
    server.on("PUT", "/files", httpx.Response(200, json={"file_id": "f1"}))
    server.on("POST", "/separate", httpx.Response(200, json={"job": "j1"}))
    server.on(
        "GET",
        "/separate/j1",
        httpx.Response(
            200,
            json={
                "phase": "ok",
                "outputs": [
                    {"kind": "voice", "link": v_url},
                    {"kind": "music", "link": m_url},
                    {"kind": "other", "link": extra_url},
                ],
            },
        ),
    )
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=STEM_MAPPING_B)
    route = _stem_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)

    result = adapter.invoke(
        route=route,
        request=StemSeparationRequest(source_path=audio_file, model="demucs", duration_s=60.0),
    )
    assert isinstance(result.output, StemSeparationOutput)
    # "other" is mapped but not required; it is downloaded too (mapped name).
    assert {"vocals", "no_vocals"} <= set(result.output.stems)


# ===========================================================================
# Resume after commit: a second invoke with the same key never resubmits.
# ===========================================================================
def test_retry_after_commit_resumes_polling(audio_file: Path, monkeypatch) -> None:
    server = FakeAsyncServer()
    # First invoke: submit succeeds, then poll returns running (never finishes
    # before a short deadline) to leave the operation at submitted/running.
    _program_diar_a(server, poll_statuses=["running"])
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=_diar_short_wait())
    route = _diar_route()
    key = "caller-key-1"
    ctx = ModelCallContext(idempotency_key=key)
    token = _ACTIVE_CALL_CONTEXT.set(ctx)
    try:
        adapter = _adapter(server, repo, route.route_id, config, clock)
        with pytest.raises(ProviderCallFailure):
            adapter.invoke(
                route=route,
                request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
            )
        # The submit endpoint was hit exactly once.
        op = repo.get_by_idempotency_key(_as_ws(ctx), key)
        assert op is not None
        assert op.state in {OperationState.SUBMITTED, OperationState.RUNNING}
        assert op.provider_operation_id == "job-A-1"

        # Second invoke with the same key: resumes polling, no resubmit.
        server2 = FakeAsyncServer()
        # Only the poll route is programmed; submit routes are intentionally absent
        # so any resubmit would 404 and the test would fail.
        server2.on(
            "GET",
            "/jobs/job-A-1",
            httpx.Response(
                200,
                json={
                    "status": "succeeded",
                    "output": {"diarization": [{"speaker": "S0", "start": 0.0, "end": 1.0}]},
                },
            ),
        )
        server2.on("POST", "/delete", httpx.Response(200, json={}))
        clock2 = FakeClock()
        adapter2 = _adapter(server2, repo, route.route_id, config, clock2)
        result = adapter2.invoke(
            route=route,
            request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
        )
        assert isinstance(result.output, DiarizationOutput)
        # No submit endpoint was contacted on resume: only the poll and cleanup.
        assert not server2.hit("/media/input")
        assert not server2.hit("/diarize")
        assert all(
            p.endswith("/jobs/job-A-1") or p.endswith("/delete") for p in server2.paths()
        )
    finally:
        _ACTIVE_CALL_CONTEXT.reset(token)


def _as_ws(ctx: ModelCallContext):  # noqa: ANN202
    from app.services.async_job_adapter import _as_uuid

    return _as_uuid(ctx.workspace_id)


# ===========================================================================
# Timeout after commit -> submit_unknown recorded, no fallback.
# ===========================================================================
def test_commit_request_timeout_is_submit_unknown(audio_file: Path) -> None:
    server = FakeAsyncServer()
    server.on("POST", "/media/input", httpx.Response(201, json={"url": UPLOAD_URL}))

    def submit_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("after send", request=request)

    server.on("POST", "/diarize", submit_timeout)
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)

    with pytest.raises(ProviderCallFailure) as excinfo:
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
        )
    assert excinfo.value.error_info.can_fallback is False
    op = next(iter(repo._by_id.values()))
    assert op.state == OperationState.SUBMIT_UNKNOWN


# ===========================================================================
# auth:none steps send no adapter credentials or extra headers.
# ===========================================================================
class _StampAuth:
    def apply(self, headers) -> None:  # noqa: ANN001
        headers["authorization"] = "Bearer secret-token"


def test_auth_none_sends_no_credentials(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_diar_a(server)
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    adapter = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=_StampAuth(),
        operations_repository=repo,
        provider="async-provider",
        extra_headers={"x-adapter-extra": "present"},
        transport=server.transport(),
        routes={route.route_id: config},
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_public_resolver,
    )
    adapter.invoke(
        route=route,
        request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
    )
    put_requests = [r for r in server.requests if r.method == "PUT"]
    assert len(put_requests) == 1
    put = put_requests[0]
    assert "authorization" not in {k.lower() for k in put.headers}
    assert "x-adapter-extra" not in {k.lower() for k in put.headers}
    # The adapter-auth submit did carry the credential and extra header.
    submit = next(r for r in server.requests if r.url.path.endswith("/diarize"))
    assert submit.headers.get("authorization") == "Bearer secret-token"
    assert submit.headers.get("x-adapter-extra") == "present"


# ===========================================================================
# Captured URL SSRF refusals.
# ===========================================================================
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://uploads.async.invalid/u",  # http where https is required
        "https://user:pass@uploads.async.invalid/u",  # userinfo
    ],
)
def test_captured_upload_url_refused(audio_file: Path, bad_url: str) -> None:
    server = FakeAsyncServer()
    server.on("POST", "/media/input", httpx.Response(201, json={"url": bad_url}))
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    with pytest.raises(ProviderCallFailure) as excinfo:
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
        )
    assert excinfo.value.error_info.code == "provider_malformed_response"
    # The upload host was never contacted.
    assert server.upload_hits == 0


@pytest.mark.parametrize(
    "private_url",
    [
        "https://10.0.0.5/u",
        "https://169.254.169.254/latest",
        "https://[::1]/u",
    ],
)
def test_captured_upload_private_ip_refused(audio_file: Path, private_url: str) -> None:
    server = FakeAsyncServer()
    server.on("POST", "/media/input", httpx.Response(201, json={"url": private_url}))
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    with pytest.raises(ProviderCallFailure):
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
        )
    assert server.upload_hits == 0


def _rebinding_resolver():  # noqa: ANN202
    """Public on the first lookup of each host, private (metadata IP) afterwards."""
    seen: dict[str, int] = {}

    def resolve(host: str, port: int, **_kwargs):  # noqa: ANN001, ANN202
        seen[host] = seen.get(host, 0) + 1
        address = "93.184.216.34" if seen[host] == 1 else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolve


def test_captured_url_rebinding_to_private_ip_never_connects(audio_file: Path) -> None:
    # A host that validates as public and then rebinds to a metadata address is
    # refused: the connection is pinned to addresses validated at connect time,
    # never to a second, unvalidated DNS answer.
    server = FakeAsyncServer()
    upload_url = "https://uploads.async.invalid/u"
    server.on("POST", "/media/input", httpx.Response(201, json={"url": upload_url}))
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    route = _diar_route()
    adapter = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="async-provider",
        transport=server.transport(),
        routes={route.route_id: AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)},
        audio_runner=_audio_runner_ok([]),
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_rebinding_resolver(),
    )
    with pytest.raises(ProviderCallFailure) as excinfo:
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
        )
    assert excinfo.value.error_info.code == "provider_malformed_response"
    assert server.upload_hits == 0
    assert all(r.url.host != "169.254.169.254" for r in server.requests)


def test_captured_url_request_is_pinned_to_validated_address(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_stem_a(server)
    server.downloads[VOCALS_URL] = b"RIFFvocals"
    server.downloads[BACK_URL] = b"RIFFback"
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    route = _stem_route()
    adapter = _adapter(
        server, repo, route.route_id, AsyncJobRouteConfig(mapping=STEM_MAPPING_A), clock
    )
    result = adapter.invoke(
        route=route,
        request=StemSeparationRequest(source_path=audio_file, model="d", duration_s=90.0),
    )
    downloads = [r for r in server.requests if r.headers.get("host") == FILES_HOST]
    assert len(downloads) == 2
    # The socket target is the validated address; the logical host travels in
    # the Host header (and SNI), so DNS is not consulted again at connect time.
    assert {r.url.host for r in downloads} == {"93.184.216.34"}
    for path in result.output.stems.values():
        path.unlink(missing_ok=True)


def test_artifact_disallowed_host_refused(audio_file: Path) -> None:
    server = FakeAsyncServer()
    bad = "https://evil.invalid/v.wav"
    _program_stem_a(
        server,
        tracks=[{"type": "stem", "url": bad}, {"type": "back", "url": BACK_URL}],
    )
    server.downloads[bad] = b"RIFF"
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=STEM_MAPPING_A)
    route = _stem_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    with pytest.raises(ProviderCallFailure):
        adapter.invoke(
            route=route,
            request=StemSeparationRequest(source_path=audio_file, model="d", duration_s=90.0),
        )


# ===========================================================================
# Artifact size cap and missing-required.
# ===========================================================================
def test_artifact_size_cap(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_stem_a(server)
    server.downloads[VOCALS_URL] = b"X" * 5000
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=_stem_capped(1000))
    route = _stem_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    baseline = _async_temp_dirs()
    with pytest.raises(ProviderCallFailure) as excinfo:
        adapter.invoke(
            route=route,
            request=StemSeparationRequest(source_path=audio_file, model="d", duration_s=90.0),
        )
    assert excinfo.value.error_info.code == "provider_malformed_response"
    _assert_no_leftover_dirs(baseline)


def test_artifact_missing_required(audio_file: Path) -> None:
    server = FakeAsyncServer()
    # Only the vocals stem is returned; no_vocals is required but missing.
    _program_stem_a(server, tracks=[{"type": "stem", "url": VOCALS_URL}])
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=STEM_MAPPING_A)
    route = _stem_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    with pytest.raises(ProviderCallFailure) as excinfo:
        adapter.invoke(
            route=route,
            request=StemSeparationRequest(source_path=audio_file, model="d", duration_s=90.0),
        )
    assert excinfo.value.error_info.code == "provider_malformed_response"


def test_unmapped_artifact_skipped(audio_file: Path) -> None:
    server = FakeAsyncServer()
    junk = f"https://{FILES_HOST}/junk.wav"
    _program_stem_a(
        server,
        tracks=[
            {"type": "stem", "url": VOCALS_URL},
            {"type": "back", "url": BACK_URL},
            {"type": "preview", "url": junk},  # not in name_map -> skipped
        ],
    )
    server.downloads[junk] = b"RIFF-junk"
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=STEM_MAPPING_A)
    route = _stem_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    result = adapter.invoke(
        route=route,
        request=StemSeparationRequest(source_path=audio_file, model="d", duration_s=90.0),
    )
    assert set(result.output.stems) == {"vocals", "no_vocals"}
    # The skipped preview was never downloaded.
    assert junk not in [str(r.url) for r in server.requests]


# ===========================================================================
# Cleanup runs on success, failure and timeout; skipped when var missing.
# ===========================================================================
def test_cleanup_on_failure(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_diar_a(server, poll_statuses=["failed"])
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    baseline = _async_temp_dirs()
    with pytest.raises(ProviderCallFailure):
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
        )
    assert server.hit("/delete")
    _assert_no_leftover_dirs(baseline)


def test_cleanup_on_timeout(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_diar_a(server, poll_statuses=["running"])
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=_diar_short_wait())
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    baseline = _async_temp_dirs()
    with pytest.raises(ProviderCallFailure):
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
        )
    assert server.hit("/delete")
    _assert_no_leftover_dirs(baseline)


def test_cleanup_skipped_when_var_missing(audio_file: Path) -> None:
    # A cleanup referencing a var that is never captured is skipped silently.
    mapping = {
        **DIAR_MAPPING_A,
        "cleanup": [
            {"method": "POST", "path": "/delete", "body": {"id": {"$ref": "vars.never_set"}}}
        ],
    }
    server = FakeAsyncServer()
    _program_diar_a(server)
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=mapping)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    adapter.invoke(
        route=route,
        request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
    )
    assert not server.hit("/delete")


# ===========================================================================
# Temp directory removed on success-of-input-transform, failure and timeout.
# ===========================================================================
def _async_temp_dirs() -> set[Path]:
    import tempfile

    return set(Path(tempfile.gettempdir()).glob(f"{ASYNC_JOB_TEMP_PREFIX}*"))


def _assert_no_leftover_dirs(baseline: set[Path] | None = None) -> None:
    # Only new dirs count: successful stems tests legitimately hand their output
    # directory to the caller, so earlier tests' dirs may still exist.
    new_dirs = _async_temp_dirs() - (baseline or set())
    assert new_dirs == set(), f"leftover async temp dirs: {sorted(new_dirs)}"


def test_temp_dir_removed_on_success(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_stem_a(server)
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(
        mapping=STEM_MAPPING_A,
        input_transform_audio=AudioTransform("flac", 44100, 2),
        upload_filename="motionly-audio.flac",
    )
    route = _stem_route()
    writes: list[Path] = []
    adapter = AsyncJobAdapter(
        base_url=BASE_URL,
        auth=NoAuth(),
        operations_repository=repo,
        provider="async-provider",
        transport=server.transport(),
        routes={route.route_id: config},
        audio_runner=_audio_runner_ok(writes),
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_public_resolver,
    )
    result = adapter.invoke(
        route=route,
        request=StemSeparationRequest(source_path=audio_file, model="d", duration_s=90.0),
    )
    # The input-transform file lived inside the adapter temp dir, now removed.
    assert writes and not writes[0].exists()
    # The caller got its stems (in the same adapter-managed dir, also handed over).
    assert all(p.exists() for p in result.output.stems.values())


# ===========================================================================
# Poll status map semantics.
# ===========================================================================
def test_poll_tolerate_retryable_then_success(audio_file: Path) -> None:
    server = FakeAsyncServer()
    server.on("POST", "/media/input", httpx.Response(201, json={"url": UPLOAD_URL}))
    server.on("POST", "/diarize", httpx.Response(200, json={"jobId": "job-A-1"}))
    server.on("POST", "/delete", httpx.Response(200, json={}))
    calls = {"n": 0}

    def poll(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "busy"})  # transient
        return httpx.Response(
            200,
            json={"status": "succeeded", "output": {"diarization": []}},
        )

    server.on("GET", "/jobs/job-A-1", poll)
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    result = adapter.invoke(
        route=route,
        request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
    )
    assert isinstance(result.output, DiarizationOutput)
    assert calls["n"] == 2  # tolerated one transient 503, then succeeded


def test_poll_unknown_status_is_malformed(audio_file: Path) -> None:
    server = FakeAsyncServer()
    _program_diar_a(server, poll_statuses=["weird-unknown"])
    repo = InMemoryInferenceOperationRepository()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    adapter = _adapter(server, repo, route.route_id, config, clock)
    with pytest.raises(ProviderCallFailure) as excinfo:
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
        )
    assert excinfo.value.error_info.code == "provider_malformed_response"


# ===========================================================================
# Validator rejections for each async_job rule.
# ===========================================================================
def _base_async_mapping() -> dict:
    return {
        "steps": [
            {"method": "POST", "path": "/jobs", "capture": {"j": "response.id"}, "commit": True}
        ],
        "poll": {
            "method": "GET",
            "path": "/jobs/{vars.j}",
            "status": "response.status",
            "status_map": {"done": {"state": "succeeded"}},
        },
        "result": {"items": "response.segs", "fields": {"speaker": "s"}},
    }


def test_validator_accepts_minimal() -> None:
    assert validate_mapping(ADAPTER_TYPE, _base_async_mapping()) == []


def test_validator_rejects_unknown_key() -> None:
    m = {**_base_async_mapping(), "surprise": 1}
    assert any("unknown async_job keys" in e for e in validate_mapping(ADAPTER_TYPE, m))


def test_validator_auth_none_must_use_url() -> None:
    m = _base_async_mapping()
    m["steps"] = [
        {"method": "PUT", "path": "/x", "auth": "none"},
        *m["steps"],
    ]
    assert any("auth:none step must use 'url'" in e for e in validate_mapping(ADAPTER_TYPE, m))


def test_validator_adapter_step_must_use_path() -> None:
    m = _base_async_mapping()
    m["steps"] = [
        {"method": "POST", "url": "vars.x", "capture": {"j": "response.id"}, "commit": True}
    ]
    errs = validate_mapping(ADAPTER_TYPE, m)
    assert any("adapter-auth step must use 'path'" in e for e in errs)


def test_validator_commit_at_most_once() -> None:
    m = _base_async_mapping()
    m["steps"] = [
        {"method": "POST", "path": "/a", "commit": True},
        {"method": "POST", "path": "/b", "capture": {"j": "response.id"}, "commit": True},
    ]
    assert any("at most one step" in e for e in validate_mapping(ADAPTER_TYPE, m))


def test_validator_commit_not_on_auth_none() -> None:
    m = _base_async_mapping()
    m["steps"] = [{"method": "PUT", "url": "vars.u", "auth": "none", "commit": True}]
    assert any(
        "commit may not be set on an auth:none step" in e
        for e in validate_mapping(ADAPTER_TYPE, m)
    )


def test_validator_poll_required() -> None:
    m = _base_async_mapping()
    del m["poll"]
    assert any(e == "$.poll: required" for e in validate_mapping(ADAPTER_TYPE, m))


def test_validator_result_or_artifacts_required() -> None:
    m = _base_async_mapping()
    del m["result"]
    assert any("either 'result' or 'artifacts'" in e for e in validate_mapping(ADAPTER_TYPE, m))


def test_validator_artifacts_allowed_hosts_required() -> None:
    m = _base_async_mapping()
    del m["result"]
    m["artifacts"] = {
        "items": "response.tracks",
        "name": "type",
        "url": "url",
        "name_map": {"a": "vocals"},
        "auth": "none",
    }
    assert any(
        "allowed_hosts" in e for e in validate_mapping(ADAPTER_TYPE, m)
    )


def test_validator_unknown_status_state() -> None:
    m = _base_async_mapping()
    m["poll"]["status_map"] = {"done": {"state": "finished"}}
    assert any("status_map.done.state" in e for e in validate_mapping(ADAPTER_TYPE, m))


def test_validator_rejects_route_config_at_construction() -> None:
    with pytest.raises(ValueError, match="async_job route mapping is invalid"):
        AsyncJobRouteConfig(mapping={"steps": []})


# ===========================================================================
# Terminal succeeded re-poll; failed op not re-run.
# ===========================================================================
def test_terminal_failed_not_rerun(audio_file: Path) -> None:
    repo = InMemoryInferenceOperationRepository()
    # Pre-seed a failed operation for the same idempotency key.
    key = "term-key"
    ctx = ModelCallContext(idempotency_key=key)
    op, _ = repo.reserve(
        workspace_id=_as_ws(ctx),
        project_id=None,
        capability="audio.diarization",
        idempotency_key=key,
        route_id="audio-diarization-remote",
        model="model-x",
        model_revision="pinned-v1-2026-01-01",
        route_snapshot={"adapter_id": "x"},
    )
    repo.transition(
        op.id, to_state=OperationState.SUBMITTING, expected_from={OperationState.RESERVED}
    )
    repo.transition(
        op.id, to_state=OperationState.FAILED, expected_from={OperationState.SUBMITTING}
    )

    server = FakeAsyncServer()
    clock = FakeClock()
    config = AsyncJobRouteConfig(mapping=DIAR_MAPPING_A)
    route = _diar_route()
    token = _ACTIVE_CALL_CONTEXT.set(ctx)
    try:
        adapter = _adapter(server, repo, route.route_id, config, clock)
        with pytest.raises(ProviderCallFailure):
            adapter.invoke(
                route=route,
                request=DiarizationRequest(audio_path=audio_file, duration_s=90.0),
            )
        # Nothing was submitted.
        assert server.requests == []
    finally:
        _ACTIVE_CALL_CONTEXT.reset(token)
