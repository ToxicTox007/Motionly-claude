"""Remote speaker diarization through pyannoteAI (AUDIO_DIARIZATION_PROVIDER=pyannoteai)."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.main import _validate_production_settings, create_app
from app.schemas.scale import SpeakerDiarization
from app.services.model_domain_adapters import (
    GatewayAudioIntelligenceProvider,
    bundle_revision,
    register_audio_route,
    register_pyannoteai_diarization_route,
)
from app.services.model_gateway import (
    DiarizationOutput,
    DiarizationRequest,
    InMemoryBudgetLedger,
    ModelCallContext,
    ModelGateway,
    ModelOperation,
    ModelProvidersExhausted,
    budget_scope,
    feature_budget_scope,
    inherited_call_context,
)
from app.services.provider_resilience import ProviderCallFailure
from app.services.pyannoteai_diarization import (
    PyannoteAIDiarizationAdapter,
    PyannoteAIDiarizationProvider,
    audio_duration_seconds,
    pyannoteai_model_revision,
)
from tests.test_service_hardening import _production_settings

API = "https://api.pyannote.ai/v1"
UPLOAD_URL = "https://storage.pyannote.test/upload?signature=abc"
JOB_ID = "3c8a89a5-dcc6-4edb-a75d-ffd64739674d"
SEGMENTS = [
    {"speaker": "SPEAKER_01", "start": 4.2, "end": 7.0},
    {"speaker": "SPEAKER_00", "start": 0.5, "end": 4.0},
]


def _job(status: str, **extra: object) -> dict:
    return {"jobId": JOB_ID, "status": status, **extra}


class FakePyannote:
    """Scripted pyannoteAI API. ``jobs`` are returned in order, one per poll."""

    def __init__(self, jobs: list[tuple[int, dict]], *, upload_url: str = UPLOAD_URL) -> None:
        self.jobs = list(jobs)
        self.upload_url = upload_url
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == f"{API}/media/input":
            return httpx.Response(201, json={"url": self.upload_url})
        if request.method == "PUT" and url == self.upload_url:
            return httpx.Response(200)
        if url == f"{API}/diarize":
            return httpx.Response(200, json={"jobId": JOB_ID, "status": "created"})
        if url == f"{API}/jobs/{JOB_ID}":
            status, body = self.jobs.pop(0)
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"message": "not found"})

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _adapter(fake: FakePyannote, clock: Clock | None = None, **overrides):
    clock = clock or Clock()
    options = {
        "api_key": "pyannote-key",
        "poll_interval_seconds": 2.0,
        "timeout_seconds": 60.0,
        "transport": httpx.MockTransport(fake.handler),
        "sleep": clock.sleep,
        "clock": clock,
        **overrides,
    }
    return PyannoteAIDiarizationAdapter(**options)


def _wav(tmp_path: Path, seconds: float = 3.0, name: str = "speech.wav") -> Path:
    path = tmp_path / name
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * int(16_000 * seconds))
    return path


def _gateway(adapter, *, cost_per_hour_usd: float = 1.8, **kwargs):
    gateway = ModelGateway(adapters={}, routes=[], **kwargs)
    route = register_pyannoteai_diarization_route(
        gateway, adapter=adapter, model="precision-3", cost_per_hour_usd=cost_per_hour_usd
    )
    return gateway, route


def _provider(gateway) -> PyannoteAIDiarizationProvider:
    return PyannoteAIDiarizationProvider(
        gateway=gateway, model="precision-3", model_revision="pyannoteai-precision-v3"
    )


def test_full_flow_uploads_submits_polls_and_maps_segments(tmp_path: Path) -> None:
    fake = FakePyannote(
        [
            (200, _job("running")),
            (200, _job("succeeded", output={"diarization": SEGMENTS})),
        ]
    )
    clock = Clock()
    gateway, _route = _gateway(_adapter(fake, clock))

    result = gateway.invoke(DiarizationRequest(audio_path=_wav(tmp_path), duration_s=3.0))

    assert isinstance(result.output, DiarizationOutput)
    assert [(s.speaker, s.start_ms, s.end_ms) for s in result.output.segments] == [
        ("SPEAKER_00", 500, 4000),
        ("SPEAKER_01", 4200, 7000),
    ]
    assert result.output.speaker_count == 2
    assert result.metadata.provider_request_id == JOB_ID
    assert fake.paths() == [
        "/v1/media/input",
        "/upload",
        "/v1/diarize",
        f"/v1/jobs/{JOB_ID}",
        f"/v1/jobs/{JOB_ID}",
    ]
    assert clock.sleeps == [2.0]


def test_requests_share_no_identifiers_and_keep_the_key_off_storage(tmp_path: Path) -> None:
    fake = FakePyannote([(200, _job("succeeded", output={"diarization": []}))])
    gateway, _route = _gateway(_adapter(fake))

    gateway.invoke(DiarizationRequest(audio_path=_wav(tmp_path), duration_s=3.0))

    reserve, upload, submit = fake.requests[0], fake.requests[1], fake.requests[2]
    media_url = json.loads(reserve.content)["url"]
    assert media_url.startswith("media://motionly-")
    assert "speech" not in media_url
    assert reserve.headers["authorization"] == "Bearer pyannote-key"
    assert "authorization" not in upload.headers
    assert upload.content.startswith(b"RIFF")
    assert json.loads(submit.content) == {"url": media_url, "model": "precision-3"}


def test_non_https_upload_urls_are_rejected(tmp_path: Path) -> None:
    fake = FakePyannote([], upload_url="http://storage.pyannote.test/upload")
    adapter = _adapter(fake)
    gateway, route = _gateway(adapter)

    with pytest.raises(ProviderCallFailure) as raised:
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=_wav(tmp_path), duration_s=3.0),
        )

    assert raised.value.error_info.code == "provider_malformed_response"
    assert fake.paths() == ["/v1/media/input"]


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_failed_jobs_are_not_billable(tmp_path: Path, status: str) -> None:
    fake = FakePyannote([(200, _job(status, output={"error": "bad audio"}))])
    ledger = InMemoryBudgetLedger()
    gateway, _route = _gateway(_adapter(fake), budget_ledger=ledger)

    with pytest.raises(ModelProvidersExhausted) as raised:
        gateway.invoke(
            DiarizationRequest(audio_path=_wav(tmp_path), duration_s=30.0),
            context=ModelCallContext(workspace_id="workspace-one"),
        )

    assert raised.value.failures[0]["code"] == "provider_job_failed"
    assert ledger.usage(budget_scope("workspace-one")) == 0.0


def test_transient_poll_errors_keep_polling(tmp_path: Path) -> None:
    fake = FakePyannote(
        [
            (429, {"message": "slow down"}),
            (503, {"message": "busy"}),
            (200, _job("succeeded", output={"diarization": SEGMENTS})),
        ]
    )
    gateway, _route = _gateway(_adapter(fake))

    result = gateway.invoke(DiarizationRequest(audio_path=_wav(tmp_path), duration_s=3.0))

    assert result.output.speaker_count == 2


def test_permanent_poll_errors_stop(tmp_path: Path) -> None:
    fake = FakePyannote([(401, {"message": "bad key"})])
    adapter = _adapter(fake)
    _gateway_unused, route = _gateway(adapter)

    with pytest.raises(ProviderCallFailure) as raised:
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=_wav(tmp_path), duration_s=3.0),
        )

    assert raised.value.error_info.code == "provider_auth_failed"


def test_slow_jobs_time_out(tmp_path: Path) -> None:
    fake = FakePyannote([(200, _job("running"))] * 50)
    clock = Clock()
    adapter = _adapter(fake, clock, timeout_seconds=10.0)
    _gateway_unused, route = _gateway(adapter)

    with pytest.raises(ProviderCallFailure) as raised:
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=_wav(tmp_path), duration_s=3.0),
        )

    assert raised.value.error_info.code == "provider_timeout"
    assert clock.now <= 10.0


@pytest.mark.parametrize(
    "output",
    [
        {},
        {"diarization": "nope"},
        {"diarization": [{"speaker": "SPEAKER_00", "start": 2.0, "end": 1.0}]},
        {"diarization": [{"speaker": "", "start": 0, "end": 1}]},
        {"diarization": [{"start": 0, "end": 1}]},
    ],
)
def test_malformed_outputs_are_rejected(tmp_path: Path, output: dict) -> None:
    fake = FakePyannote([(200, _job("succeeded", output=output))])
    adapter = _adapter(fake)
    _gateway_unused, route = _gateway(adapter)

    with pytest.raises(ProviderCallFailure) as raised:
        adapter.invoke(
            route=route,
            request=DiarizationRequest(audio_path=_wav(tmp_path), duration_s=3.0),
        )

    assert raised.value.error_info.code == "provider_malformed_response"


@pytest.mark.parametrize(
    ("duration_s", "billable_s"),
    [(10.5, 20), (45.2, 46), (3600.0, 3600)],
)
def test_billing_uses_seconds_with_the_twenty_second_minimum(
    tmp_path: Path, duration_s: float, billable_s: int
) -> None:
    fake = FakePyannote([(200, _job("succeeded", output={"diarization": []}))])
    ledger = InMemoryBudgetLedger()
    gateway, _route = _gateway(
        _adapter(fake),
        cost_per_hour_usd=1.8,
        budget_ledger=ledger,
        feature_budgets_usd={"audio_diarization": 50.0},
    )
    provider = _provider(gateway)
    audio_route = register_audio_route(
        gateway,
        providers={"speaker_diarization": provider},
        revision=bundle_revision("audio-bundle"),
    )

    GatewayAudioIntelligenceProvider(gateway=gateway, route=audio_route).analyze_with_context(
        audio_path=_wav(tmp_path, seconds=duration_s),
        workspace_id="workspace-one",
        project_id="project-one",
    )

    expected = billable_s * 1.8 / 3600
    assert ledger.usage(budget_scope("workspace-one")) == pytest.approx(expected)
    assert ledger.usage(feature_budget_scope("audio_diarization")) == pytest.approx(expected)


def test_provider_output_matches_the_local_diarization_schema(tmp_path: Path) -> None:
    fake = FakePyannote([(200, _job("succeeded", output={"diarization": SEGMENTS}))])
    gateway, _route = _gateway(_adapter(fake))

    result = _provider(gateway).diarize(audio_path=_wav(tmp_path))

    SpeakerDiarization.model_validate(result)
    assert result["provider"] == "pyannoteai"
    assert result["speaker_count"] == 2
    assert [segment["segment_index"] for segment in result["segments"]] == [0, 1]
    assert "degraded" not in result


def test_provider_degrades_instead_of_raising(tmp_path: Path) -> None:
    fake = FakePyannote([(200, _job("failed"))])
    gateway, _route = _gateway(_adapter(fake))

    result = _provider(gateway).diarize(audio_path=_wav(tmp_path))

    assert result["degraded"] is True
    assert result["status"] == "unavailable:provider_job_failed"
    assert result["heuristic_fallback"] is True
    assert result["segments"] == [] and result["speaker_count"] == 0
    SpeakerDiarization.model_validate(result)


def test_provider_degrades_when_the_budget_is_exhausted(tmp_path: Path) -> None:
    fake = FakePyannote([])
    gateway, _route = _gateway(
        _adapter(fake), feature_budgets_usd={"audio_diarization": 0.001}
    )

    result = _provider(gateway).diarize(audio_path=_wav(tmp_path, seconds=120.0))

    assert result["status"] == "unavailable:model_budget_exceeded"
    assert fake.requests == []


def test_provider_degrades_without_a_route_or_duration(tmp_path: Path) -> None:
    empty_gateway = ModelGateway(adapters={}, routes=[])
    provider = _provider(empty_gateway)
    garbage = tmp_path / "noise.bin"
    garbage.write_bytes(b"not audio")

    assert provider.check_availability() == (False, "route_missing")
    assert provider.diarize(audio_path=_wav(tmp_path))["status"] == (
        "unavailable:model_route_unavailable"
    )
    assert provider.diarize(audio_path=garbage)["status"] == "unavailable:duration_unknown"


def test_nested_calls_inherit_the_audio_job_workspace(tmp_path: Path) -> None:
    fake = FakePyannote([(200, _job("succeeded", output={"diarization": SEGMENTS}))])
    ledger = InMemoryBudgetLedger()
    gateway, _route = _gateway(_adapter(fake), budget_ledger=ledger)
    provider = _provider(gateway)
    audio_route = register_audio_route(
        gateway,
        providers={"speaker_diarization": provider},
        revision=bundle_revision("audio-bundle"),
    )
    audio = GatewayAudioIntelligenceProvider(gateway=gateway, route=audio_route)

    insights = audio.analyze_with_context(
        audio_path=_wav(tmp_path, seconds=40.0),
        workspace_id="workspace-a",
        project_id="project-a",
    )

    assert insights["speaker_diarization"]["speaker_count"] == 2
    assert ledger.usage(budget_scope("workspace-a")) == pytest.approx(40 * 1.8 / 3600)
    assert ledger.usage(budget_scope(None)) == 0.0


def test_inherited_context_outside_a_gateway_call() -> None:
    assert inherited_call_context().workspace_id is None
    explicit = inherited_call_context(workspace_id="w", project_id="p")
    assert (explicit.workspace_id, explicit.project_id) == ("w", "p")


def test_audio_duration_reads_wav_headers_and_falls_back_to_ffprobe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert audio_duration_seconds(_wav(tmp_path, seconds=2.5)) == pytest.approx(2.5)
    other = tmp_path / "speech.m4a"
    other.write_bytes(b"not-a-wav")
    monkeypatch.setattr(
        "app.services.pyannoteai_diarization.probe_duration_seconds", lambda _path: 12.0
    )
    assert audio_duration_seconds(other) == 12.0


def test_route_is_a_gated_per_second_priced_diarization_route() -> None:
    _gateway_unused, route = _gateway(_adapter(FakePyannote([])), cost_per_hour_usd=3.6)

    assert route.route_id == "pyannoteai-diarization"
    assert route.operations == (ModelOperation.AUDIO_DIARIZATION,)
    assert route.model_revision == "pyannoteai-precision-v3"
    assert route.endpoint_host == "api.pyannote.ai"
    assert route.requires_live_effects_gate is True
    assert route.budget_feature == "audio_diarization"
    assert route.input_cost_per_million_usd / 1_000_000 == pytest.approx(0.001)
    with pytest.raises(ValueError, match="pyannoteAI model"):
        pyannoteai_model_revision("precision-latest")


def _audio_route(app):
    return next(
        route for route in app.state.model_gateway.routes
        if route.route_id == "local-audio-intelligence"
    )


def test_create_app_worker_uses_pyannoteai_and_changes_the_bundle_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    get_settings.cache_clear()
    default_app = create_app()
    settings = get_settings()
    default_revision = _audio_route(default_app).model_revision

    monkeypatch.setenv("AUDIO_DIARIZATION_PROVIDER", "pyannoteai")
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "pyannote-key")
    get_settings.cache_clear()
    remote_app = create_app()

    # Defaults keep the exact pre-existing local bundle revision.
    assert default_revision == bundle_revision(
        settings.pyannote_model_revision,
        settings.wav2vec_emotion_model_revision,
        settings.yamnet_model_url,
    )
    assert "speaker_diarization" not in default_app.state.audio_ml_providers
    provider = remote_app.state.audio_ml_providers["speaker_diarization"]
    assert isinstance(provider, PyannoteAIDiarizationProvider)
    assert _audio_route(remote_app).model_revision != default_revision
    assert "pyannoteai-diarization" in {r.route_id for r in remote_app.state.model_gateway.routes}


def test_explicit_switch_wins_over_a_disabled_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    monkeypatch.setenv("AUDIO_ML_PROVIDER", "disabled")
    monkeypatch.setenv("AUDIO_DIARIZATION_PROVIDER", "pyannoteai")
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "pyannote-key")
    get_settings.cache_clear()

    app = create_app()

    assert list(app.state.audio_ml_providers) == ["speaker_diarization"]


def test_create_app_requires_a_pyannoteai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    monkeypatch.setenv("AUDIO_DIARIZATION_PROVIDER", "pyannoteai")
    monkeypatch.delenv("PYANNOTEAI_API_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="PYANNOTEAI_API_KEY"):
        create_app()


def test_api_role_never_builds_audio_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIO_DIARIZATION_PROVIDER", "pyannoteai")
    monkeypatch.delenv("PYANNOTEAI_API_KEY", raising=False)
    get_settings.cache_clear()

    app = create_app()

    assert app.state.audio_ml_providers == {}


_PRICED_PYANNOTEAI = {
    "audio_diarization_provider": "pyannoteai",
    "pyannoteai_api_key": "pyannote-key",
    "pyannoteai_cost_per_hour_usd": 1.0,
}


def test_production_accepts_a_priced_pyannoteai_configuration() -> None:
    _validate_production_settings(_production_settings(**_PRICED_PYANNOTEAI))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"pyannoteai_api_key": None}, "PYANNOTEAI_API_KEY"),
        ({"pyannoteai_cost_per_hour_usd": 0.0}, "PYANNOTEAI_COST_PER_HOUR_USD"),
    ],
)
def test_production_rejects_incomplete_pyannoteai_configuration(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _validate_production_settings(
            _production_settings(**{**_PRICED_PYANNOTEAI, **overrides})
        )
