"""Task 17 stem-separation template port: behaviors beyond the shared harness.

The conformance and differential suites (``test_inference_template_conformance``
and ``test_inference_template_differential``) already prove, scenario by
scenario, that the ``audio.stem_separation`` example route matches the archived
adapter on every parity row. This file covers the Task 17 items that are not a
single scenario comparison:

* registry-only enablement — the example registry builds an executable
  ``async_job`` route through the shared harness with no stem-specific code
  path;
* the "one improvement" — a worker retry after the commit boundary resumes
  polling the recorded operation and submits exactly once;
* the API refuses a stem request with ``capability_unavailable`` (503) when no
  ``audio.stem_separation`` route is configured;
* a CPU-local Demucs route still serves stems (fake runner);
* no secret appears in logs;
* the caller-handed stem directory uses the neutral temp prefix and
  ``scale._remove_stem_temp_directory`` removes it.

This file contains NO vendor names. It drives the ``async_job`` adapter with a
neutral synthetic mapping (neutral hosts/paths), the way
``tests/test_async_job_adapter.py`` does; the vendor-shaped example registry and
the archived module (``tests/differential/archived_stems.py``) carry the real
provider names, and the conformance/differential suites exercise those.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import httpx
import pytest

from app.services.async_job_adapter import (
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
from app.services.model_gateway import (
    _ACTIVE_CALL_CONTEXT,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelRoute,
    StemSeparationOutput,
    StemSeparationRequest,
)
from app.services.provider_resilience import ProviderCallFailure

SENTINEL = "sentinel-license-key-do-not-log"  # noqa: S105 - test sentinel
SOURCE_ID = "2fe8f214-1771-4900-9e7e-570f823bd359"
TASK_ID = "8cad7e3d-a5a3-4e79-9463-59cd4da16977"
BASE_URL = "https://jobs.example.invalid"
FILES_HOST = "files.example.invalid"
VOCALS_URL = f"https://{FILES_HOST}/task/abcd/vocals"
BACK_URL = f"https://{FILES_HOST}/task/abcd/no_vocals"


# A neutral async_job mapping shaped like the stems example (upload -> split
# commit -> keyed POST poll -> artifacts with name_map -> cleanup delete), but
# with synthetic hosts/paths so this test carries no vendor names.
MAPPING = {
    "steps": [
        {
            "method": "POST",
            "path": "/api/v1/upload/",
            "body": "input.file",
            "capture": {"source_id": "response.id"},
        },
        {
            "method": "POST",
            "path": "/api/v1/split/stem_separator/",
            "body": {
                "source_id": {"$ref": "vars.source_id"},
                "presets": {"stem": {"$ref": "params.stem"}},
                "idempotency_key": {"$fn": "uuid", "args": []},
            },
            "capture": {"task_id": "response.task_id"},
            "commit": True,
        },
    ],
    "poll": {
        "method": "POST",
        "path": "/api/v1/check/",
        "body": {"task_ids": [{"$ref": "vars.task_id"}]},
        "status": "response.result.{vars.task_id}.status",
        "status_map": {
            "progress": {"state": "running"},
            "success": {"state": "succeeded"},
            "error": {"state": "failed", "fallback": False},
            "server_error": {"state": "retryable_failed", "fallback": False},
        },
        "tolerate_retryable": True,
        "interval_s": 5,
        "backoff": {"factor": 1.5, "max_interval_s": 30},
        "max_wait_s": 1800,
    },
    "artifacts": {
        "items": "response.result.{vars.task_id}.result.tracks",
        "name": "type",
        "url": "url",
        "name_map": {"stem": "vocals", "back": "no_vocals"},
        "required": ["vocals", "no_vocals"],
        "max_bytes": 2147483648,
        "allowed_hosts": [FILES_HOST, f"*.{FILES_HOST}"],
        "force_https": True,
        "auth": "none",
    },
    "cleanup": [
        {
            "method": "POST",
            "path": "/api/v1/delete/",
            "body": {"source_id": {"$ref": "vars.source_id"}},
        }
    ],
}
PARAMS = {"stem": "vocals"}


def _public_resolver(host: str, port: int, **_kwargs):  # noqa: ANN001, ANN202
    import socket

    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _audio_runner(command: list[str]):
    import subprocess

    Path(command[-1]).write_bytes(b"FLACfake-prepared-audio")
    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


class _Server:
    """A scripted async-job API; ``checks`` returned one per poll (last repeats)."""

    def __init__(self, checks: list[dict]) -> None:
        self.checks = list(checks)
        self.requests: list[httpx.Request] = []
        self.hits: dict[str, int] = {}
        self.downloads = {VOCALS_URL: b"RIFF-vocals", BACK_URL: b"RIFF-back"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers.get("host", request.url.host).split(":")[0]
        path = request.url.path
        self.hits[path] = self.hits.get(path, 0) + 1
        if host == FILES_HOST or host.endswith(f".{FILES_HOST}"):
            return httpx.Response(200, content=self.downloads.get(f"https://{host}{path}", b""))
        if path == "/api/v1/upload/":
            return httpx.Response(200, json={"id": SOURCE_ID})
        if path == "/api/v1/split/stem_separator/":
            return httpx.Response(200, json={"task_id": TASK_ID})
        if path == "/api/v1/check/":
            body = self.checks.pop(0) if len(self.checks) > 1 else self.checks[0]
            return httpx.Response(200, json=body)
        if path == "/api/v1/delete/":
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404, json={"path": path})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _check(status: str, **extra) -> dict:
    return {"result": {TASK_ID: {"status": status, "source_id": SOURCE_ID, **extra}}}


def _success_check() -> dict:
    return _check(
        "success",
        result={
            "duration": 90,
            "tracks": [
                {"type": "stem", "url": VOCALS_URL},
                {"type": "back", "url": BACK_URL},
            ],
        },
    )


def _adapter(server: _Server, repo, clock, auth=None) -> AsyncJobAdapter:
    return AsyncJobAdapter(
        base_url=BASE_URL,
        auth=auth or NoAuth(),
        operations_repository=repo,
        provider="async-stems",
        transport=server.transport(),
        routes={
            "audio-stems-remote": AsyncJobRouteConfig(
                mapping=MAPPING,
                params=PARAMS,
                input_transform_audio=AudioTransform("flac", 44100, 2),
                upload_filename="motionly-audio.flac",
            )
        },
        audio_runner=_audio_runner,
        sleep=clock.sleep,
        clock=clock,
        dns_resolver=_public_resolver,
    )


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="audio-stems-remote",
        capability=ModelCapability.AUDIO,
        adapter="async_job",
        provider="async-stems",
        model="stem/model",
        model_revision="andromeda-v6",
        operations=(ModelOperation.AUDIO_STEM_SEPARATION,),
    )


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "source.mp4"
    src.write_bytes(b"source-media")
    return src


def _rmtree(output: StemSeparationOutput) -> None:
    import shutil

    if output.stems:
        shutil.rmtree(next(iter(output.stems.values())).parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. Registry-only enablement through the example registry (no vendor names in
#    the test: the harness builds the route from the example document by glob).
# ---------------------------------------------------------------------------
def test_registry_only_enablement_builds_an_executable_route() -> None:
    from tests.support import template_scenarios as ts

    scenario_path = ts.SCENARIOS_DIR / "audio.stem_separation" / "full-flow-success.json"
    assert scenario_path.is_file()
    with tempfile.TemporaryDirectory() as td:
        observation = ts.run_template_scenario(scenario_path, tmp_path=Path(td))
    # The example registry alone produced stems, billed per second, and recorded
    # a succeeded async operation — no stem-specific wiring was involved.
    assert observation.output == {"stems": {"no_vocals": 9, "vocals": 11}}
    assert observation.usage["input_units"] == 90
    assert observation.operation == {"state": "succeeded", "provider_operation_id": TASK_ID}


# ---------------------------------------------------------------------------
# 2. Retry after commit resumes polling and submits exactly once.
# ---------------------------------------------------------------------------
def test_retry_after_commit_resumes_with_exactly_one_submit(tmp_path: Path) -> None:
    mapping_short = {**MAPPING, "poll": {**MAPPING["poll"], "max_wait_s": 10}}
    repo = InMemoryInferenceOperationRepository()
    route = _route()
    key = "stem_separation:resume-resource-id"
    ctx = ModelCallContext(idempotency_key=key)
    token = _ACTIVE_CALL_CONTEXT.set(ctx)
    try:
        server1 = _Server([_check("progress")])
        adapter1 = AsyncJobAdapter(
            base_url=BASE_URL,
            auth=NoAuth(),
            operations_repository=repo,
            provider="async-stems",
            transport=server1.transport(),
            routes={
                "audio-stems-remote": AsyncJobRouteConfig(
                    mapping=mapping_short,
                    params=PARAMS,
                    input_transform_audio=AudioTransform("flac", 44100, 2),
                    upload_filename="motionly-audio.flac",
                )
            },
            audio_runner=_audio_runner,
            sleep=_FakeClock().sleep,
            clock=_FakeClock(),
            dns_resolver=_public_resolver,
        )
        with pytest.raises(ProviderCallFailure):
            adapter1.invoke(
                route=route,
                request=StemSeparationRequest(
                    source_path=_source(tmp_path), model="demucs", duration_s=90.0
                ),
            )
        op = repo.get_by_idempotency_key(_ws(ctx), key)
        assert op is not None
        assert op.state in {OperationState.SUBMITTED, OperationState.RUNNING}
        assert op.provider_operation_id == TASK_ID
        assert server1.hits.get("/api/v1/split/stem_separator/") == 1
        assert server1.hits.get("/api/v1/upload/") == 1

        # Second attempt with the SAME key: no upload, no split; resume polling.
        server2 = _Server([_success_check()])
        adapter2 = _adapter(server2, repo, _FakeClock())
        result = adapter2.invoke(
            route=route,
            request=StemSeparationRequest(
                source_path=_source(tmp_path), model="demucs", duration_s=90.0
            ),
        )
        assert isinstance(result.output, StemSeparationOutput)
        assert "/api/v1/upload/" not in server2.hits  # no re-upload
        assert "/api/v1/split/stem_separator/" not in server2.hits  # no re-submit
        assert server2.hits.get("/api/v1/check/") == 1
        _rmtree(result.output)
    finally:
        _ACTIVE_CALL_CONTEXT.reset(token)


def _ws(ctx: ModelCallContext):  # noqa: ANN202
    from app.services.async_job_adapter import _as_uuid

    return _as_uuid(ctx.workspace_id)


# ---------------------------------------------------------------------------
# 3. No route -> the API refuses at once with capability_unavailable (503).
# ---------------------------------------------------------------------------
def test_separate_audio_stems_refuses_without_a_route() -> None:
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4

    from app.api.errors import ApiError
    from app.api.scale import separate_audio_stems
    from app.schemas.scale import StemSeparationRequest as StemRequestSchema

    class _Uploads:
        def get_active_upload(self, **_kwargs):
            return SimpleNamespace(id=uuid4())

    class _Scale:
        def create(self, **_kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("no job may be queued when there is no route")

    app_state = SimpleNamespace(
        upload_repository=_Uploads(),
        scale_repository=_Scale(),
        stem_gateway_route=None,
        scale_queue=SimpleNamespace(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=app_state))
    actor = SimpleNamespace(workspace_id=uuid4())

    with pytest.raises(ApiError) as raised:
        asyncio.run(
            separate_audio_stems(
                project_id=uuid4(),
                payload=StemRequestSchema(),
                actor=actor,
                request=request,
            )
        )
    assert raised.value.status_code == 503
    assert raised.value.code == "capability_unavailable"


# ---------------------------------------------------------------------------
# 4. A CPU-local Demucs route still serves stems (fake runner).
# ---------------------------------------------------------------------------
def test_demucs_cpu_route_still_serves(tmp_path: Path) -> None:
    from app.services.model_domain_adapters import register_stem_route

    class _FakeDemucs:
        provider_name = "demucs"

        def separate(self, *, source_path: Path, model: str):
            del model
            root = Path(tempfile.mkdtemp(prefix="motionly-demucs-"))
            out = {name: root / f"{name}.wav" for name in ("vocals", "no_vocals")}
            for path in out.values():
                path.write_bytes(source_path.read_bytes() or b"stem")
            return out

    gateway = ModelGateway(adapters={}, routes=[])
    route = register_stem_route(
        gateway, separator=_FakeDemucs(), model="htdemucs", revision="demucs==4.1.0"
    )
    assert ModelOperation.AUDIO_STEM_SEPARATION in route.operations
    result = gateway.invoke(
        StemSeparationRequest(source_path=_source(tmp_path), model="htdemucs"),
        context=ModelCallContext(),
    )
    assert isinstance(result.output, StemSeparationOutput)
    assert set(result.output.stems) == {"vocals", "no_vocals"}


# ---------------------------------------------------------------------------
# 5. No secret appears in logs.
# ---------------------------------------------------------------------------
def test_no_secret_appears_in_logs(tmp_path: Path, caplog) -> None:
    from app.services.inference_auth import _CustomHeaderAuth

    repo = InMemoryInferenceOperationRepository()
    server = _Server([_success_check()])
    adapter = _adapter(server, repo, _FakeClock(), auth=_CustomHeaderAuth("x-key", SENTINEL))
    with caplog.at_level(logging.DEBUG):
        result = adapter.invoke(
            route=_route(),
            request=StemSeparationRequest(
                source_path=_source(tmp_path), model="demucs", duration_s=1.0
            ),
        )
    assert isinstance(result.output, StemSeparationOutput)
    assert SENTINEL not in caplog.text
    # The auth header never leaves the API: download requests carry no auth.
    downloads = [r for r in server.requests if r.url.path.startswith("/task/abcd/")]
    assert downloads
    assert all("x-key" not in r.headers for r in downloads)
    _rmtree(result.output)


# ---------------------------------------------------------------------------
# 6. Temp-prefix cleanup: the caller-handed dir uses ASYNC_JOB_TEMP_PREFIX and
#    scale._remove_stem_temp_directory removes it.
# ---------------------------------------------------------------------------
def test_stem_temp_prefix_is_cleaned_up(tmp_path: Path) -> None:
    from app.services.scale import _remove_stem_temp_directory

    repo = InMemoryInferenceOperationRepository()
    server = _Server([_success_check()])
    adapter = _adapter(server, repo, _FakeClock())
    result = adapter.invoke(
        route=_route(),
        request=StemSeparationRequest(
            source_path=_source(tmp_path), model="demucs", duration_s=1.0
        ),
    )
    stems = dict(result.output.stems)
    parent = next(iter(stems.values())).parent
    assert parent.name.startswith(ASYNC_JOB_TEMP_PREFIX)
    assert parent.exists()
    _remove_stem_temp_directory(stems)
    assert not parent.exists()
