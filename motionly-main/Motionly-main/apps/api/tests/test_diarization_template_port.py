"""Port tests for the neutral diarization wrapper (Task 17 item 6/7).

These exercise ``app.services.audio_diarization.GatewayDiarizationProvider``
through the REAL gateway built from the example registry
(``docs/ai-registry/examples/diarization.json``) and the shared
``inference_operations`` repository, proving:

* registry-only enablement: no vendor code, the example registry alone makes
  diarization work;
* a worker retry after the commit step resumes polling the recorded operation
  (one submit) instead of resubmitting;
* no-route fallback: the provider degrades to the ``heuristic_single_speaker_v1``
  estimate and never raises;
* the per-job minimum comes from the route (``pricing.minimum_billable_units``);
* no resolved secret value appears in logs.

This file contains no vendor names: everything runs off the neutral registry
document and the neutral wrapper. (The example *document* carries the operator's
real base URL and data-processor name, which is configuration data the scanner
allows.)
"""

from __future__ import annotations

import json
import logging
import wave
from pathlib import Path

import httpx
import pytest

from app.services.audio_diarization import GatewayDiarizationProvider
from app.services.inference_operations import (
    InMemoryInferenceOperationRepository,
    OperationState,
)
from app.services.inference_runtime import build_inference_runtime
from app.services.inference_secrets import SecretResolver
from app.services.model_gateway import _ACTIVE_CALL_CONTEXT, ModelCallContext
from tests.support.template_scenarios import SENTINEL_SECRET, write_sentinel_bundle

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "docs" / "ai-registry" / "examples"
DIARIZATION_REGISTRY = EXAMPLES_DIR / "diarization.json"


def _registry_host_and_prefix() -> tuple[str, str]:
    """The example route's endpoint host and path prefix, read from the registry.

    Derived from configuration (never hard-coded) so this test source carries no
    vendor host literal; the adapter's outgoing requests still go to whatever the
    operator configured.
    """
    document = json.loads(DIARIZATION_REGISTRY.read_text(encoding="utf-8"))
    route = next(r for r in document["routes"] if r["id"] == "audio-diarization-remote")
    adapter = next(a for a in document["adapters"] if a["id"] == route["adapter_id"])
    url = httpx.URL(adapter["base_url"])
    return url.host, url.path.rstrip("/")


API_HOST, API_PREFIX = _registry_host_and_prefix()
# A synthetic pre-signed upload target the fake reserve step hands back; its host
# is arbitrary test configuration, not a configured endpoint.
UPLOAD_HOST = "storage.uploads.test"
UPLOAD_URL = f"https://{UPLOAD_HOST}/upload?signature=abc"
JOB_ID = "3c8a89a5-dcc6-4edb-a75d-ffd64739674d"
SEGMENTS = [
    {"speaker": "SPEAKER_01", "start": 4.2, "end": 7.0},
    {"speaker": "SPEAKER_00", "start": 0.5, "end": 4.0},
]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


class _FakeAsyncServer:
    """A scripted async diarization server for the example route, counting submits."""

    def __init__(self, poll_statuses: list[dict]) -> None:
        self._polls = list(poll_statuses)
        self.submit_count = 0
        self.reserve_count = 0
        self.paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        logical_host = request.headers.get("host", request.url.host).split(":")[0]
        self.paths.append(path)
        if logical_host == API_HOST and path == f"{API_PREFIX}/media/input":
            self.reserve_count += 1
            return httpx.Response(201, json={"url": UPLOAD_URL})
        if request.method == "PUT" and logical_host == UPLOAD_HOST and path == "/upload":
            return httpx.Response(200)
        if logical_host == API_HOST and path == f"{API_PREFIX}/diarize":
            self.submit_count += 1
            return httpx.Response(200, json={"jobId": JOB_ID, "status": "created"})
        if logical_host == API_HOST and path == f"{API_PREFIX}/jobs/{JOB_ID}":
            body = self._polls.pop(0) if self._polls else self._polls_last()
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": "not found"})

    _last: dict | None = None

    def _polls_last(self) -> dict:
        return self._last or {"jobId": JOB_ID, "status": "running"}


def _wav(tmp_path: Path, seconds: float = 30.0, name: str = "speech.wav") -> Path:
    path = tmp_path / name
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * int(16_000 * seconds))
    return path


def _public_resolver(host: str, port: int, **_kwargs: object):
    import socket

    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _runtime(fake: _FakeAsyncServer, operations, tmp_path: Path, clock: _Clock):
    from app.core.config import get_settings
    from app.services.async_job_adapter import AsyncJobAdapter

    bundle = write_sentinel_bundle(DIARIZATION_REGISTRY, tmp_path)
    settings = get_settings().model_copy(
        update={
            "ai_registry_file": str(DIARIZATION_REGISTRY),
            "ai_secrets_bundle_file": str(bundle),
            "ai_secrets_dir": str(tmp_path / "secrets"),
            "redis_url": None,
        }
    )
    runtime = build_inference_runtime(
        settings,
        operations_repository=operations,
        secret_resolver=SecretResolver(settings=settings),
        transport=httpx.MockTransport(fake.handler),
    )
    # Hermetic clock/sleep/resolver on the built async adapter (as the harness does).
    for adapter in runtime.gateway._adapters.values():  # noqa: SLF001
        if isinstance(adapter, AsyncJobAdapter):
            adapter._sleep = clock.sleep  # noqa: SLF001
            adapter._clock = clock  # noqa: SLF001
            adapter._dns_resolver = _public_resolver  # noqa: SLF001
    return runtime


def _provider(runtime) -> GatewayDiarizationProvider:
    return GatewayDiarizationProvider(
        gateway=runtime.gateway, model="precision-3", model_revision="precision-v3"
    )


# ---------------------------------------------------------------------------
# Registry-only enablement
# ---------------------------------------------------------------------------
def test_registry_only_enablement_runs_diarization(tmp_path: Path) -> None:
    fake = _FakeAsyncServer(
        [
            {"jobId": JOB_ID, "status": "running"},
            {"jobId": JOB_ID, "status": "succeeded", "output": {"diarization": SEGMENTS}},
        ]
    )
    clock = _Clock()
    operations = InMemoryInferenceOperationRepository()
    runtime = _runtime(fake, operations, tmp_path, clock)
    provider = _provider(runtime)

    assert provider.check_availability() == (True, "remote:gateway_diarization")
    result = provider.diarize(audio_path=_wav(tmp_path))

    assert result["speaker_count"] == 2
    assert [(s["speaker"], s["start_ms"], s["end_ms"]) for s in result["segments"]] == [
        ("SPEAKER_00", 500, 4000),
        ("SPEAKER_01", 4200, 7000),
    ]
    assert "degraded" not in result
    assert fake.submit_count == 1


# ---------------------------------------------------------------------------
# Minimum billing from the route
# ---------------------------------------------------------------------------
def test_minimum_billing_comes_from_the_route(tmp_path: Path) -> None:
    from app.services.model_gateway import InMemoryBudgetLedger, budget_scope

    fake = _FakeAsyncServer(
        [{"jobId": JOB_ID, "status": "succeeded", "output": {"diarization": []}}]
    )
    clock = _Clock()
    operations = InMemoryInferenceOperationRepository()
    runtime = _runtime(fake, operations, tmp_path, clock)
    ledger = InMemoryBudgetLedger()
    runtime.gateway._budget_ledger = ledger  # noqa: SLF001
    provider = _provider(runtime)

    # A 10.5-second clip bills the route's 20-second minimum.
    short = _wav(tmp_path, seconds=10.5, name="short.wav")
    with _active(ModelCallContext(workspace_id="workspace-min")):
        result = provider.diarize(audio_path=short)

    assert "degraded" not in result
    # price is input_per_million_usd=500 -> 500/1e6 per second; 20 s billed.
    expected = 20 * 500.0 / 1_000_000
    assert ledger.usage(budget_scope("workspace-min")) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Retry after commit resumes polling (one submit)
# ---------------------------------------------------------------------------
def test_retry_after_commit_resumes_without_resubmitting(tmp_path: Path) -> None:
    # First attempt: submit succeeds but the job never finishes before the
    # deadline, so the wrapper degrades. The operation is left submitted/running.
    fake1 = _FakeAsyncServer([{"jobId": JOB_ID, "status": "running"}])
    fake1._last = {"jobId": JOB_ID, "status": "running"}
    operations = InMemoryInferenceOperationRepository()
    clock1 = _Clock()
    # Shrink the deadline so the blocking poll gives up quickly under the fake clock.
    runtime1 = _runtime(fake1, operations, tmp_path, clock1)
    _shrink_deadline(runtime1)
    audio = _wav(tmp_path)

    first = _provider(runtime1).diarize(audio_path=audio)
    assert first["degraded"] is True
    assert first["status"] == "unavailable:provider_timeout"
    assert fake1.submit_count == 1

    # The operation is recorded at or past the commit point under a stable key.
    ops = list(operations._by_id.values())  # noqa: SLF001
    assert len(ops) == 1
    assert ops[0].state in {OperationState.SUBMITTED, OperationState.RUNNING}
    assert ops[0].provider_operation_id == JOB_ID

    # Second attempt (a worker retry) on the SAME audio: the submit/reserve
    # endpoints are absent, so any resubmit would 404; it must resume polling.
    fake2 = _FakeAsyncServer(
        [{"jobId": JOB_ID, "status": "succeeded", "output": {"diarization": SEGMENTS}}]
    )
    clock2 = _Clock()
    runtime2 = _runtime(fake2, operations, tmp_path, clock2)
    second = _provider(runtime2).diarize(audio_path=audio)

    assert second["speaker_count"] == 2
    assert "degraded" not in second
    # No reserve and no submit on resume: only the poll was contacted.
    assert fake2.reserve_count == 0
    assert fake2.submit_count == 0
    assert all(p.startswith(f"{API_PREFIX}/jobs/") for p in fake2.paths)


# ---------------------------------------------------------------------------
# No-route fallback
# ---------------------------------------------------------------------------
def test_no_route_degrades_to_the_heuristic(tmp_path: Path) -> None:
    from app.core.config import get_settings
    from app.services.model_gateway import ModelGateway

    # An empty gateway (no diarization route at all).
    empty = ModelGateway(adapters={}, routes=[])
    provider = GatewayDiarizationProvider(
        gateway=empty, model="precision-3", model_revision="precision-v3"
    )
    del get_settings  # not needed; the empty gateway exercises the no-route path

    assert provider.check_availability() == (False, "route_missing")
    result = provider.diarize(audio_path=_wav(tmp_path))
    assert result["degraded"] is True
    assert result["heuristic_fallback"] is True
    assert result["status"] == "unavailable:model_route_unavailable"
    assert result["segments"] == [] and result["speaker_count"] == 0


# ---------------------------------------------------------------------------
# No secret in logs
# ---------------------------------------------------------------------------
def test_no_secret_in_logs(tmp_path: Path, caplog) -> None:
    fake = _FakeAsyncServer(
        [{"jobId": JOB_ID, "status": "succeeded", "output": {"diarization": SEGMENTS}}]
    )
    clock = _Clock()
    operations = InMemoryInferenceOperationRepository()
    runtime = _runtime(fake, operations, tmp_path, clock)
    provider = _provider(runtime)

    with caplog.at_level(logging.DEBUG):
        provider.diarize(audio_path=_wav(tmp_path))

    for record in caplog.records:
        assert SENTINEL_SECRET not in record.getMessage()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _active(context: ModelCallContext):
    import contextlib

    @contextlib.contextmanager
    def _cm():
        token = _ACTIVE_CALL_CONTEXT.set(context)
        try:
            yield
        finally:
            _ACTIVE_CALL_CONTEXT.reset(token)

    return _cm()


def _shrink_deadline(runtime) -> None:
    """Make the blocking poll give up on the first deadline check.

    The example route's mapping sets max_wait_s=900; rewrite the live route
    config's poll max_wait to a tiny value so the fake-clock poll loop ends
    quickly while still recording the operation at submitted/running.
    """
    from app.services.async_job_adapter import AsyncJobAdapter

    for adapter in runtime.gateway._adapters.values():  # noqa: SLF001
        if isinstance(adapter, AsyncJobAdapter):
            for config in adapter._routes.values():  # noqa: SLF001
                mapping = json.loads(json.dumps(config.mapping))
                mapping["poll"]["max_wait_s"] = 1
                mapping["poll"]["interval_s"] = 5
                object.__setattr__(config, "mapping", mapping)
