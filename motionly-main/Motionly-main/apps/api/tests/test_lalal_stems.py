"""Remote stem separation through LALAL.AI (STEM_SEPARATION_PROVIDER=lalal)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest

from app.api.errors import ApiError
from app.core.config import get_settings
from app.main import _validate_production_settings, create_app
from app.services.lalal_stems import (
    LALAL_TEMP_PREFIX,
    LalalStemSeparator,
    extract_flac_audio,
    lalal_model_revision,
)
from app.services.model_domain_adapters import register_lalal_stem_route, register_stem_route
from app.services.model_gateway import (
    InMemoryBudgetLedger,
    ModelGateway,
    ModelOperation,
    ModelProvidersExhausted,
    budget_scope,
    feature_budget_scope,
)
from app.services.provider_resilience import ProviderCallFailure
from app.services.scale import FixtureStemSeparator, _separate_stems
from tests.test_scale import _stem_context
from tests.test_service_hardening import _production_settings

API = "https://www.lalal.ai/api/v1"
SOURCE_ID = "2fe8f214-1771-4900-9e7e-570f823bd359"
TASK_ID = "8cad7e3d-a5a3-4e79-9463-59cd4da16977"
VOCALS_URL = "https://d.lalal.ai/task/abcd/vocals"
BACK_URL = "https://d.lalal.ai/task/abcd/no_vocals"


def _tracks(vocals_url: str = VOCALS_URL, back_url: str = BACK_URL) -> list[dict]:
    return [
        {"label": "vocals", "type": "stem", "url": vocals_url},
        {"label": "no_vocals", "type": "back", "url": back_url},
    ]


def _check(status: str, **extra: object) -> dict:
    return {"result": {TASK_ID: {"status": status, "source_id": SOURCE_ID, **extra}}}


class FakeLalal:
    """Scripted LALAL.AI API. ``checks`` are returned in order, one per poll."""

    def __init__(self, checks: list[tuple[int, dict]], *, upload_status: int = 200) -> None:
        self.checks = list(checks)
        self.upload_status = upload_status
        self.requests: list[httpx.Request] = []
        self.downloads = {VOCALS_URL: b"RIFF-vocals", BACK_URL: b"RIFF-back"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == f"{API}/upload/":
            body = {"id": SOURCE_ID, "name": "motionly-audio.flac", "size": 4, "duration": 90}
            return httpx.Response(self.upload_status, json=body)
        if url == f"{API}/split/stem_separator/":
            return httpx.Response(200, json={"task_id": TASK_ID})
        if url == f"{API}/check/":
            status, body = self.checks.pop(0)
            return httpx.Response(status, json=body)
        if url == f"{API}/delete/":
            return httpx.Response(200, json={"status": "success"})
        if url in self.downloads:
            return httpx.Response(200, content=self.downloads[url])
        return httpx.Response(404, json={"detail": "not found"})

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


def _copy_audio(source: Path, output_dir: Path) -> Path:
    target = output_dir / "source-audio.flac"
    target.write_bytes(source.read_bytes())
    return target


def _separator(fake: FakeLalal, clock: Clock | None = None, **overrides) -> LalalStemSeparator:
    clock = clock or Clock()
    options = {
        "license_key": "lalal-license",
        "poll_interval_seconds": 2.0,
        "timeout_seconds": 60.0,
        "audio_preparer": _copy_audio,
        "transport": httpx.MockTransport(fake.handler),
        "sleep": clock.sleep,
        "clock": clock,
        **overrides,
    }
    return LalalStemSeparator(**options)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-media")
    return source


def _lalal_temp_dirs() -> list[Path]:
    return sorted(Path(tempfile.gettempdir()).glob(f"{LALAL_TEMP_PREFIX}*"))


def test_full_flow_returns_demucs_compatible_stems(tmp_path: Path) -> None:
    fake = FakeLalal(
        [
            (200, _check("progress", progress=0)),
            (200, _check("progress", progress=40)),
            (200, _check("success", result={"duration": 90, "tracks": _tracks()})),
        ]
    )
    clock = Clock()

    stems = _separator(fake, clock).separate(source_path=_source(tmp_path), model="htdemucs")

    assert set(stems) == {"vocals", "no_vocals"}
    assert stems["vocals"].read_bytes() == b"RIFF-vocals"
    assert stems["no_vocals"].read_bytes() == b"RIFF-back"
    assert stems["vocals"].parent.name.startswith(LALAL_TEMP_PREFIX)
    # Only the WAV stems remain; the prepared upload copy is removed.
    assert sorted(path.name for path in stems["vocals"].parent.iterdir()) == [
        "no_vocals.wav",
        "vocals.wav",
    ]
    assert fake.paths() == [
        "/api/v1/upload/",
        "/api/v1/split/stem_separator/",
        "/api/v1/check/",
        "/api/v1/check/",
        "/api/v1/check/",
        "/task/abcd/vocals",
        "/task/abcd/no_vocals",
        "/api/v1/delete/",
    ]
    # Polling backs off and stays bounded.
    assert clock.sleeps == [2.0, 3.0]
    shutil.rmtree(stems["vocals"].parent)


def test_requests_carry_license_only_to_the_api_and_never_the_filename(tmp_path: Path) -> None:
    fake = FakeLalal([(200, _check("success", result={"duration": 1, "tracks": _tracks()}))])

    stems = _separator(fake).separate(source_path=_source(tmp_path), model="htdemucs")

    upload, split = fake.requests[0], fake.requests[1]
    assert upload.headers["x-license-key"] == "lalal-license"
    assert upload.headers["content-disposition"] == (
        'attachment; filename="motionly-audio.flac"'
    )
    assert upload.content == b"source-media"
    body = json.loads(split.content)
    assert body["source_id"] == SOURCE_ID
    assert body["presets"] == {
        "stem": "vocals",
        "splitter": "andromeda",
        "extraction_level": "deep_extraction",
        "encoder_format": "wav",
        "dereverb_enabled": False,
    }
    assert body["idempotency_key"]
    downloads = [request for request in fake.requests if request.url.host == "d.lalal.ai"]
    assert downloads and all("x-license-key" not in request.headers for request in downloads)
    assert json.loads(fake.requests[-1].content) == {"source_id": SOURCE_ID}
    shutil.rmtree(stems["vocals"].parent)


def test_plain_http_download_links_are_fetched_over_https(tmp_path: Path) -> None:
    http_tracks = _tracks(
        vocals_url=VOCALS_URL.replace("https://", "http://"),
        back_url=BACK_URL.replace("https://", "http://"),
    )
    fake = FakeLalal([(200, _check("success", result={"duration": 1, "tracks": http_tracks}))])

    stems = _separator(fake).separate(source_path=_source(tmp_path), model="htdemucs")

    downloads = [request for request in fake.requests if request.url.host == "d.lalal.ai"]
    assert {request.url.scheme for request in downloads} == {"https"}
    shutil.rmtree(stems["vocals"].parent)


@pytest.mark.parametrize(
    "tracks",
    [
        _tracks(vocals_url="https://attacker.example/vocals"),
        _tracks(vocals_url="https://lalal.ai.attacker.example/vocals"),
        _tracks(vocals_url="file:///etc/passwd"),
        [_tracks()[0]],
        [_tracks()[0], _tracks()[0]],
    ],
)
def test_unsafe_or_incomplete_tracks_are_rejected_and_cleaned_up(
    tmp_path: Path, tracks: list[dict]
) -> None:
    fake = FakeLalal([(200, _check("success", result={"duration": 1, "tracks": tracks}))])
    before = _lalal_temp_dirs()

    with pytest.raises(ProviderCallFailure) as raised:
        _separator(fake).separate(source_path=_source(tmp_path), model="htdemucs")

    assert raised.value.error_info.code == "provider_malformed_response"
    assert all(request.url.host != "attacker.example" for request in fake.requests)
    assert fake.paths()[-1] == "/api/v1/delete/"
    assert _lalal_temp_dirs() == before


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        ("error", "provider_job_failed", False),
        ("cancelled", "provider_job_failed", False),
        ("server_error", "provider_unavailable", True),
    ],
)
def test_failed_tasks_raise_and_still_delete_the_upload(
    tmp_path: Path, status: str, code: str, retryable: bool
) -> None:
    fake = FakeLalal([(200, _check(status, error="boom"))])
    before = _lalal_temp_dirs()

    with pytest.raises(ProviderCallFailure) as raised:
        _separator(fake).separate(source_path=_source(tmp_path), model="htdemucs")

    assert raised.value.error_info.code == code
    assert raised.value.error_info.retryable is retryable
    assert fake.paths()[-1] == "/api/v1/delete/"
    assert _lalal_temp_dirs() == before


def test_transient_check_errors_keep_polling(tmp_path: Path) -> None:
    fake = FakeLalal(
        [
            (503, {"detail": "busy"}),
            (429, {"detail": "slow down"}),
            (200, _check("success", result={"duration": 1, "tracks": _tracks()})),
        ]
    )

    stems = _separator(fake).separate(source_path=_source(tmp_path), model="htdemucs")

    assert stems["vocals"].exists()
    shutil.rmtree(stems["vocals"].parent)


def test_permanent_check_errors_stop_polling(tmp_path: Path) -> None:
    fake = FakeLalal([(401, {"detail": "bad license"})])

    with pytest.raises(ProviderCallFailure) as raised:
        _separator(fake).separate(source_path=_source(tmp_path), model="htdemucs")

    assert raised.value.error_info.code == "provider_auth_failed"
    assert fake.paths()[-1] == "/api/v1/delete/"


def test_slow_tasks_time_out_and_delete_the_upload(tmp_path: Path) -> None:
    fake = FakeLalal([(200, _check("progress", progress=1))] * 50)
    clock = Clock()

    with pytest.raises(ProviderCallFailure) as raised:
        _separator(fake, clock, timeout_seconds=10.0).separate(
            source_path=_source(tmp_path), model="htdemucs"
        )

    assert raised.value.error_info.code == "provider_timeout"
    assert clock.now <= 10.0
    assert fake.paths()[-1] == "/api/v1/delete/"


def test_upload_failure_makes_no_further_calls(tmp_path: Path) -> None:
    fake = FakeLalal([], upload_status=403)

    with pytest.raises(ProviderCallFailure) as raised:
        _separator(fake).separate(source_path=_source(tmp_path), model="htdemucs")

    assert raised.value.error_info.code == "provider_auth_failed"
    assert fake.paths() == ["/api/v1/upload/"]


def test_oversized_tracks_are_rejected(tmp_path: Path) -> None:
    fake = FakeLalal([(200, _check("success", result={"duration": 1, "tracks": _tracks()}))])

    with pytest.raises(ProviderCallFailure) as raised:
        _separator(fake, max_track_bytes=4).separate(
            source_path=_source(tmp_path), model="htdemucs"
        )

    assert raised.value.error_info.code == "provider_malformed_response"


def test_splitter_revision_is_pinned() -> None:
    assert lalal_model_revision("andromeda") == "lalal-andromeda-v6"
    assert lalal_model_revision("perseus") == "lalal-perseus-v5"
    with pytest.raises(ValueError, match="splitter"):
        lalal_model_revision("auto")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_audio_preparation_strips_video_and_rejects_silent_sources(tmp_path: Path) -> None:
    with_audio = tmp_path / "with-audio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-shortest", "-c:v", "libx264", "-c:a", "aac", str(with_audio),
        ],
        check=True,
    )
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-c:v", "libx264", str(silent),
        ],
        check=True,
    )

    flac = extract_flac_audio(with_audio, tmp_path)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(flac)],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert [stream["codec_type"] for stream in streams] == ["audio"]
    assert streams[0]["codec_name"] == "flac"

    with pytest.raises(ProviderCallFailure) as raised:
        extract_flac_audio(silent, tmp_path / "out")
    assert raised.value.error_info.code == "provider_request_rejected"
    assert raised.value.error_info.billable is False


def test_route_is_a_gated_per_second_priced_stems_route() -> None:
    gateway = ModelGateway(adapters={}, routes=[])
    separator = _separator(FakeLalal([]))

    route = register_lalal_stem_route(gateway, separator=separator, cost_per_minute_usd=0.6)

    assert route.route_id == "lalal-stem-separation"
    assert route.provider == "lalal"
    assert route.model == "lalal/andromeda"
    assert route.model_revision == "lalal-andromeda-v6"
    assert route.operations == (ModelOperation.AUDIO_STEM_SEPARATION,)
    assert route.endpoint_host == "www.lalal.ai"
    assert route.requires_live_effects_gate is True
    assert route.budget_feature == "audio_stems"
    # $0.60 per minute is $0.01 per second of audio.
    assert route.input_cost_per_million_usd * 90 / 1_000_000 == pytest.approx(0.9)


def _materialize_from_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_materialize(*, upload, storage):
        return storage.ensure_object(bucket=upload.storage_bucket, path=upload.storage_path)

    monkeypatch.setattr("app.services.scale.materialize_upload_source", fake_materialize)


def test_separate_stems_prices_by_duration_and_records_lalal_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    uploads, record, settings = _stem_context(tmp_path)
    _materialize_from_storage(monkeypatch)
    monkeypatch.setattr("app.services.scale.probe_duration_seconds", lambda _path: 89.2)
    ledger = InMemoryBudgetLedger()
    gateway = ModelGateway(
        adapters={},
        routes=[],
        budget_ledger=ledger,
        feature_budgets_usd={"audio_stems": 5.0},
    )
    fake = FakeLalal([(200, _check("success", result={"duration": 90, "tracks": _tracks()}))])
    separator = _separator(fake)
    route = register_lalal_stem_route(gateway, separator=separator, cost_per_minute_usd=0.6)
    temp_dirs_before = _lalal_temp_dirs()

    result = _separate_stems(
        record=record,
        settings=settings,
        upload_repository=uploads,
        stem_separator=separator,
        model_gateway=gateway,
        stem_gateway_route=route,
    )

    assert result["provider"] == "lalal"
    assert result["model"] == "lalal/andromeda"
    assert "stem_provider" not in result  # real stems: no "Test stems" badge
    assert result["model_gateway"]["route_id"] == "lalal-stem-separation"
    assert result["model_gateway"]["model_revision"] == "lalal-andromeda-v6"
    # 89.2 s rounds up to 90 billable seconds at $0.01/s.
    assert result["model_gateway"]["actual_cost_usd"] == pytest.approx(0.9)
    assert ledger.usage(budget_scope(record.workspace_id)) == pytest.approx(0.9)
    assert ledger.usage(feature_budget_scope("audio_stems")) == pytest.approx(0.9)
    for name in ("vocals", "no_vocals"):
        stored = tmp_path / result[name]["bucket"] / result[name]["path"]
        assert stored.read_bytes() == fake.downloads[VOCALS_URL if name == "vocals" else BACK_URL]
    # The LALAL temp directory is removed after the stems are stored.
    assert _lalal_temp_dirs() == temp_dirs_before


def test_separate_stems_refuses_priced_routes_without_a_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    uploads, record, settings = _stem_context(tmp_path)
    _materialize_from_storage(monkeypatch)
    monkeypatch.setattr("app.services.scale.probe_duration_seconds", lambda _path: None)
    gateway = ModelGateway(adapters={}, routes=[])
    fake = FakeLalal([])
    separator = _separator(fake)
    route = register_lalal_stem_route(gateway, separator=separator, cost_per_minute_usd=0.6)

    with pytest.raises(ApiError) as raised:
        _separate_stems(
            record=record,
            settings=settings,
            upload_repository=uploads,
            stem_separator=separator,
            model_gateway=gateway,
            stem_gateway_route=route,
        )

    assert raised.value.code == "stem_source_duration_unknown"
    assert fake.requests == []


def test_separate_stems_budget_exhaustion_blocks_the_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    uploads, record, settings = _stem_context(tmp_path)
    _materialize_from_storage(monkeypatch)
    monkeypatch.setattr("app.services.scale.probe_duration_seconds", lambda _path: 600.0)
    gateway = ModelGateway(
        adapters={}, routes=[], feature_budgets_usd={"audio_stems": 1.0}
    )
    fake = FakeLalal([])
    separator = _separator(fake)
    route = register_lalal_stem_route(gateway, separator=separator, cost_per_minute_usd=0.6)

    with pytest.raises(Exception) as raised:  # noqa: PT011 - gateway budget error
        _separate_stems(
            record=record,
            settings=settings,
            upload_repository=uploads,
            stem_separator=separator,
            model_gateway=gateway,
            stem_gateway_route=route,
        )

    assert type(raised.value).__name__ == "ModelBudgetExceeded"
    assert fake.requests == []


def test_local_demucs_route_never_probes_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    uploads, record, settings = _stem_context(tmp_path)
    _materialize_from_storage(monkeypatch)

    def forbidden_probe(_path):
        raise AssertionError("unpriced local routes must not probe the source")

    monkeypatch.setattr("app.services.scale.probe_duration_seconds", forbidden_probe)
    gateway = ModelGateway(adapters={}, routes=[])
    separator = FixtureStemSeparator()
    route = register_stem_route(
        gateway, separator=separator, model="htdemucs", revision="demucs==4.1.0"
    )

    result = _separate_stems(
        record=record,
        settings=settings,
        upload_repository=uploads,
        stem_separator=separator,
        model_gateway=gateway,
        stem_gateway_route=route,
    )

    assert result["provider"] == "demucs"
    assert result["model"] == "htdemucs"


def test_provider_failure_surfaces_through_the_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    uploads, record, settings = _stem_context(tmp_path)
    _materialize_from_storage(monkeypatch)
    monkeypatch.setattr("app.services.scale.probe_duration_seconds", lambda _path: 30.0)
    gateway = ModelGateway(adapters={}, routes=[])
    separator = _separator(FakeLalal([(200, _check("error", error="bad file"))]))
    route = register_lalal_stem_route(gateway, separator=separator, cost_per_minute_usd=0.6)

    with pytest.raises(ModelProvidersExhausted) as raised:
        _separate_stems(
            record=record,
            settings=settings,
            upload_repository=uploads,
            stem_separator=separator,
            model_gateway=gateway,
            stem_gateway_route=route,
        )

    assert raised.value.failures[0]["code"] == "provider_job_failed"


def test_create_app_worker_registers_the_lalal_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    monkeypatch.setenv("STEM_SEPARATION_PROVIDER", "lalal")
    monkeypatch.setenv("LALAL_LICENSE_KEY", "lalal-license")
    get_settings.cache_clear()

    app = create_app()

    assert isinstance(app.state.stem_separator, LalalStemSeparator)
    assert app.state.stem_gateway_route.route_id == "lalal-stem-separation"
    route_ids = {route.route_id for route in app.state.model_gateway.routes}
    assert "local-demucs-stem-separation" not in route_ids


def test_create_app_worker_requires_a_lalal_license(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    monkeypatch.setenv("STEM_SEPARATION_PROVIDER", "lalal")
    monkeypatch.delenv("LALAL_LICENSE_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="LALAL_LICENSE_KEY"):
        create_app()


def test_api_role_never_builds_a_remote_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEM_SEPARATION_PROVIDER", "lalal")
    monkeypatch.delenv("LALAL_LICENSE_KEY", raising=False)
    get_settings.cache_clear()

    app = create_app()

    assert isinstance(app.state.stem_separator, FixtureStemSeparator)
    assert app.state.stem_gateway_route is None


def test_create_app_worker_defaults_to_demucs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_ROLE", "worker")
    get_settings.cache_clear()

    app = create_app()

    assert type(app.state.stem_separator).__name__ == "DemucsStemSeparator"
    assert app.state.stem_gateway_route.route_id == "local-demucs-stem-separation"


_PRICED_LALAL = {
    "stem_separation_provider": "lalal",
    "lalal_license_key": "lalal-license",
    "lalal_cost_per_minute_usd": 0.05,
}


def test_production_accepts_a_priced_lalal_configuration() -> None:
    _validate_production_settings(_production_settings(**_PRICED_LALAL))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"lalal_license_key": None}, "LALAL_LICENSE_KEY"),
        ({"lalal_cost_per_minute_usd": 0.0}, "LALAL_COST_PER_MINUTE_USD"),
        ({"stem_separation_provider": "fixture"}, "demucs or lalal"),
    ],
)
def test_production_rejects_incomplete_lalal_configuration(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _validate_production_settings(_production_settings(**{**_PRICED_LALAL, **overrides}))
