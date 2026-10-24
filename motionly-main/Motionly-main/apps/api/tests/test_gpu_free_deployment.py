"""GPU-free deployments (GPU_WORKERS_ENABLED=false) and the per-feature AI report."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import Actor
from app.api.health import _ai_feature_health, dependencies
from app.core.config import Settings, get_settings
from app.core.middleware import InMemoryRateLimiter
from app.main import _validate_production_settings, create_app
from app.services.ai_features import (
    ai_feature_report,
    audio_feature_provider,
    gpu_worker_switches,
)
from app.workers import prefetch_models
from tests.test_service_hardening import _production_settings

REMOTE = {
    "gpu_workers_enabled": False,
    "visual_intelligence_provider": "remote",
    "embeddings_provider": "jina",
    "matte_provider": "local_cpu",
    "audio_diarization_provider": "pyannoteai",
    "audio_emotion_provider": "openrouter_audio",
    "audio_sound_provider": "yamnet_tflite",
    "stem_separation_provider": "lalal",
    "jina_api_key": "jina-key",
    "jina_text_cost_per_million_tokens_usd": 0.05,
    "jina_image_cost_per_image_usd": 0.0005,
    "google_vision_api_key": "vision-key",
    "google_vision_cost_per_image_usd": 0.0015,
    "visual_caption_input_cost_per_million_usd": 0.3,
    "visual_caption_output_cost_per_million_usd": 2.5,
    "pyannoteai_api_key": "pyannote-key",
    "pyannoteai_cost_per_hour_usd": 0.3,
    "audio_emotion_input_cost_per_million_usd": 0.3,
    "audio_emotion_output_cost_per_million_usd": 2.5,
    "lalal_license_key": "lalal-license",
    "lalal_cost_per_minute_usd": 0.5,
}


def _settings(**values) -> Settings:
    return Settings(_env_file=None, APP_ENV="local", **values)


# -- the switch ---------------------------------------------------------------------


def test_gpu_workers_are_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GPU_WORKERS_ENABLED", raising=False)
    assert _settings().gpu_workers_enabled is True
    assert _settings(GPU_WORKERS_ENABLED="false").gpu_workers_enabled is False


@pytest.mark.parametrize(("value", "observer"), [("true", True), ("false", False)])
def test_the_gpu_capacity_observer_runs_only_with_gpu_workers(
    monkeypatch: pytest.MonkeyPatch, value: str, observer: bool
) -> None:
    monkeypatch.setenv("GPU_WORKERS_ENABLED", value)
    monkeypatch.setenv("WORKER_API_TOKEN", "worker-token-for-test")
    get_settings.cache_clear()
    try:
        app = create_app()
        assert (app.state.gpu_scaling_service is not None) is observer
        with TestClient(app) as client:
            response = client.post(
                "/internal/operations/gpu-autoscaling/run",
                headers={"x-motionly-worker-token": "worker-token-for-test"},
            )
        assert response.status_code == 200, response.text
        if not observer:
            assert response.json() == {"status": "disabled", "reason": "gpu_workers_disabled"}
        else:
            assert response.json().get("reason") != "gpu_workers_disabled"
    finally:
        get_settings.cache_clear()


# -- production ---------------------------------------------------------------------


def test_production_accepts_a_fully_remote_gpu_free_deployment() -> None:
    for role in ("api", "worker"):
        _validate_production_settings(_production_settings(process_role=role, **REMOTE))


@pytest.mark.parametrize(
    ("update", "offender"),
    [
        ({"visual_intelligence_provider": "local_ml"}, "VISUAL_PROVIDER=local_ml"),
        ({"matte_provider": "local_ml"}, "MATTE_PROVIDER=local_ml"),
        ({"audio_diarization_provider": "local_ml"}, "AUDIO_DIARIZATION_PROVIDER=local_ml"),
        ({"audio_emotion_provider": ""}, "AUDIO_EMOTION_PROVIDER=local_ml"),
        ({"audio_sound_provider": "local_ml"}, "AUDIO_SOUND_PROVIDER=local_ml"),
        ({"ffmpeg_gpu_enabled": True}, "FFMPEG_GPU_ENABLED=true"),
    ],
)
def test_production_refuses_a_gpu_free_deployment_that_still_needs_a_gpu(
    update: dict, offender: str
) -> None:
    with pytest.raises(RuntimeError, match="GPU_WORKERS_ENABLED=false") as refused:
        _validate_production_settings(
            _production_settings(process_role="worker", **{**REMOTE, **update})
        )

    assert offender in str(refused.value)


def test_disabled_or_cpu_features_need_no_gpu_worker() -> None:
    settings = _production_settings(
        process_role="worker",
        **{
            **REMOTE,
            "audio_diarization_provider": "disabled",
            "audio_emotion_provider": "disabled",
            "audio_sound_provider": "disabled",
            # Demucs and local CLIP run on CPU workers.
            "stem_separation_provider": "demucs",
            "embeddings_provider": "local_clip",
            "visual_embeddings_provider": "disabled",
        },
    )

    assert gpu_worker_switches(settings) == []
    _validate_production_settings(settings)


def test_gpu_deployments_are_not_checked() -> None:
    # Today's defaults: every GPU feature on, GPU workers on.
    settings = _production_settings(process_role="worker")

    assert settings.gpu_workers_enabled is True
    assert "VISUAL_PROVIDER=local_ml" in gpu_worker_switches(settings)
    _validate_production_settings(settings)


# -- the feature report ---------------------------------------------------------------


def test_default_report_is_the_local_gpu_stack() -> None:
    report = ai_feature_report(_settings())

    assert {name: entry["execution"] for name, entry in report.items()} == {
        "visual_analysis": "gpu_worker",
        "visual_objects": "gpu_worker",
        "visual_captions": "gpu_worker",
        "visual_embeddings": "gpu_worker",
        "embeddings": "cpu_worker",
        "matte": "gpu_worker",
        "audio_diarization": "gpu_worker",
        "audio_emotion": "gpu_worker",
        "audio_sound": "gpu_worker",
        "stems": "cpu_worker",
    }
    assert all(entry["configured"] for entry in report.values())


def test_remote_report_names_providers_and_missing_credentials() -> None:
    settings = _settings(
        VISUAL_PROVIDER="remote",
        VISUAL_CAPTIONS_PROVIDER="disabled",
        EMBEDDINGS_PROVIDER="jina",
        JINA_API_KEY="jina-key",
        MATTE_PROVIDER="local_cpu",
        AUDIO_DIARIZATION_PROVIDER="pyannoteai",
        AUDIO_SOUND_PROVIDER="yamnet_tflite",
        STEM_SEPARATION_PROVIDER="lalal",
    )

    report = ai_feature_report(settings)

    assert report["visual_analysis"] == {
        "provider": "remote", "execution": "cpu_worker", "switch": "VISUAL_PROVIDER",
        "configured": True,
    }
    assert report["visual_objects"]["provider"] == "google_vision"
    assert report["visual_objects"]["configured"] is False  # no GOOGLE_VISION_API_KEY
    assert report["visual_captions"]["execution"] == "disabled"
    assert report["visual_embeddings"] == {
        "provider": "jina", "execution": "remote", "switch": "VISUAL_EMBEDDINGS_PROVIDER",
        "configured": True,
    }
    assert report["matte"]["execution"] == "cpu_worker"
    assert report["audio_diarization"]["configured"] is False  # no PYANNOTEAI_API_KEY
    assert report["audio_sound"]["execution"] == "cpu_worker"
    assert report["stems"]["provider"] == "lalal" and report["stems"]["configured"] is False


def test_audio_switches_follow_the_group_unless_set() -> None:
    assert audio_feature_provider("", group_disabled=False) == "local_ml"
    assert audio_feature_provider("", group_disabled=True) == "disabled"
    assert audio_feature_provider(" PyannoteAI ", group_disabled=True) == "pyannoteai"
    report = ai_feature_report(_settings(AUDIO_ML_PROVIDER="disabled"))
    assert {report[name]["execution"] for name in ("audio_emotion", "audio_sound")} == {
        "disabled"
    }


def test_health_reports_features_without_secret_values() -> None:
    settings = _settings(
        GPU_WORKERS_ENABLED="false",
        VISUAL_PROVIDER="remote",
        GOOGLE_VISION_API_KEY="super-secret-vision-key",
        OPENROUTER_API_KEY="super-secret-openrouter-key",
        VISUAL_EMBEDDINGS_PROVIDER="disabled",
        STEM_SEPARATION_PROVIDER="lalal",
    )

    health = _ai_feature_health(settings)

    assert health.status == "degraded"
    assert health.message.endswith("stems")
    assert health.metadata["gpu_workers"] == "disabled"
    assert health.metadata["features"]["visual_objects"]["configured"] is True
    assert "super-secret" not in json.dumps(health.model_dump())
    assert _ai_feature_health(_settings()).status == "ok"


def test_diagnostics_include_the_ai_feature_report(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_supabase_health(*_args, **_kwargs):
        from app.api.health import DependencyHealth

        return DependencyHealth(status="ok", message="Mocked Supabase.")

    monkeypatch.setattr("app.api.health._supabase_health", fake_supabase_health)
    settings = _settings(HEALTH_DIAGNOSTICS_CACHE_TTL_SECONDS=0)
    state = SimpleNamespace(
        settings=settings,
        queue_registry=SimpleNamespace(snapshots=lambda: []),
        rate_limiter=InMemoryRateLimiter(),
        audio_ml_providers={},
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=state), state=SimpleNamespace(request_id="req-ai")
    )

    response = asyncio.run(dependencies(request, Actor(user_id=uuid4(), workspace_id=uuid4())))

    report = response.dependencies["ai_features"]
    assert report.status == "ok"
    assert report.metadata["gpu_workers"] == "enabled"
    assert report.metadata["features"]["visual_analysis"]["provider"] == "local_ml"


# -- model prefetch -------------------------------------------------------------------


@pytest.mark.parametrize(("value", "gpu_models"), [("true", True), ("false", False)])
def test_prefetch_skips_gpu_models_on_gpu_free_deployments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str,
    gpu_models: bool,
) -> None:
    monkeypatch.setenv("MODEL_PREFETCH", "true")
    monkeypatch.setattr(
        prefetch_models,
        "get_settings",
        lambda: _settings(GPU_WORKERS_ENABLED=value, MEDIA_WORK_DIR="/tmp"),
    )
    # Only Demucs is "installed", so GPU models would show up as attempts.
    monkeypatch.setattr(
        prefetch_models.importlib.util,
        "find_spec",
        lambda name: object() if name in {"huggingface_hub", "demucs"} else None,
    )
    downloads: list[str] = []
    import sys

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda **kwargs: downloads.append(kwargs["repo_id"])),
    )
    monkeypatch.setitem(sys.modules, "demucs", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "demucs.pretrained",
        SimpleNamespace(get_model=lambda name: downloads.append(f"demucs:{name}")),
    )

    prefetch_models.main()

    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert ("demucs:htdemucs" in downloads) is True
    assert any(not item.startswith("demucs:") for item in downloads) is gpu_models
    assert event["models_attempted"] == (4 if gpu_models else 1)
