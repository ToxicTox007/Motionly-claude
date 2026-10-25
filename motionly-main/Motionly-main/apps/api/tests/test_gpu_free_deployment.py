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
    # Object detection, diarization and stem separation are registry capability
    # routes now (plan Task 17), not vendor switches, so a GPU-free deployment
    # configures them through the registry. Keyframe/text embeddings are a
    # registry route in Tier 2 (plan Task 12); ranking uses local CLIP on the CPU
    # worker. Diarization is disabled here unless a registry route serves it.
    "matte_provider": "local_cpu",
    "audio_diarization_provider": "disabled",
    "audio_emotion_provider": "disabled",
    "audio_sound_provider": "yamnet_tflite",
    "stem_separation_provider": "demucs",
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
        MATTE_PROVIDER="local_cpu",
        AUDIO_DIARIZATION_PROVIDER="disabled",
        AUDIO_SOUND_PROVIDER="yamnet_tflite",
        STEM_SEPARATION_PROVIDER="demucs",
    )

    report = ai_feature_report(settings)

    assert report["visual_analysis"] == {
        "provider": "remote", "execution": "cpu_worker", "switch": "VISUAL_PROVIDER",
        "configured": True,
    }
    # Object detection is a registry capability route (plan Task 17), not a vendor
    # switch; the remote report lists it on the CPU worker with no vendor name.
    assert report["visual_objects"] == {
        "provider": "remote", "execution": "cpu_worker", "switch": "VISUAL_PROVIDER",
        "configured": True,
    }
    # Keyframe captions become a registry capability route in Tier 2 (plan Task
    # 11); until then the remote report lists them on the CPU worker with no
    # vendor switch.
    assert report["visual_captions"] == {
        "provider": "remote", "execution": "cpu_worker", "switch": "VISUAL_PROVIDER",
        "configured": True,
    }
    # Keyframe embeddings become a registry capability route in Tier 2; until
    # then the remote report lists them on the CPU worker with no vendor switch.
    assert report["visual_embeddings"] == {
        "provider": "remote", "execution": "cpu_worker", "switch": "VISUAL_PROVIDER",
        "configured": True,
    }
    assert report["embeddings"] == {
        "provider": "local_clip", "execution": "cpu_worker", "switch": "EMBEDDINGS_PROVIDER",
        "configured": True,
    }
    assert report["matte"]["execution"] == "cpu_worker"
    # Diarization is disabled unless a registry route serves it; no vendor name.
    assert report["audio_diarization"] == {
        "provider": "disabled", "execution": "disabled",
        "switch": "AUDIO_DIARIZATION_PROVIDER", "configured": True,
    }
    assert report["audio_sound"]["execution"] == "cpu_worker"
    # Demucs runs on the CPU worker; a remote stem provider is a registry route.
    assert report["stems"]["provider"] == "demucs"
    assert report["stems"]["execution"] == "cpu_worker"


def test_audio_switches_follow_the_group_unless_set() -> None:
    assert audio_feature_provider("", group_disabled=False) == "local_ml"
    assert audio_feature_provider("", group_disabled=True) == "disabled"
    # An explicit value wins over the group default and is normalised (lower-cased
    # and trimmed), whatever that value is.
    assert audio_feature_provider(" Local_ML ", group_disabled=True) == "local_ml"
    report = ai_feature_report(_settings(AUDIO_ML_PROVIDER="disabled"))
    assert {report[name]["execution"] for name in ("audio_emotion", "audio_sound")} == {
        "disabled"
    }


def test_health_reports_features_without_secret_values() -> None:
    settings = _settings(
        GPU_WORKERS_ENABLED="false",
        VISUAL_PROVIDER="remote",
        MATTE_PROVIDER="local_cpu",
        AUDIO_DIARIZATION_PROVIDER="disabled",
        AUDIO_SOUND_PROVIDER="yamnet_tflite",
        STEM_SEPARATION_PROVIDER="demucs",
        # A secret-bearing setting unrelated to the AI report; its value must
        # never appear in the health payload.
        WORKER_API_TOKEN="super-secret-worker-token",
    )

    health = _ai_feature_health(settings)

    assert health.status == "ok"
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
def test_prefetch_downloads_enabled_local_models_from_the_registry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
    value: str,
    gpu_models: bool,
) -> None:
    import hashlib
    import json as _json
    import sys

    # A file-weights CPU model: write it and record its real SHA-256 so prefetch
    # verifies it. matte + yamnet are file models; demucs + GPU models are hub.
    matte_file = tmp_path / "selfie.tflite"
    matte_file.write_bytes(b"matte-weights")
    matte_sha = hashlib.sha256(b"matte-weights").hexdigest()

    registry = {
        "version": 1,
        "adapters": [
            {
                "id": "remote-chat",
                "type": "openai_compatible",
                "base_url": "https://chat.example.invalid/v1",
                "network": "public",
                "auth": {"type": "bearer", "secret_ref": "bundle:chat"},
                "data_processor": {
                    "display_name": "Chat", "purpose": "Planning",
                    "data_categories": ["text"],
                    "policy_url": "https://chat.example.invalid/p",
                },
            },
            {
                "id": "remote-stt",
                "type": "openai_compatible",
                "base_url": "https://stt.example.invalid/v1",
                "network": "public",
                "auth": {"type": "bearer", "secret_ref": "bundle:stt"},
                "data_processor": {
                    "display_name": "STT", "purpose": "Transcription",
                    "data_categories": ["audio"],
                    "policy_url": "https://stt.example.invalid/p",
                },
            },
            {"id": "local-matte", "type": "local", "network": "private"},
            {"id": "local-stems", "type": "local", "network": "private"},
            {"id": "local-embeddings", "type": "local", "network": "private"},
            {"id": "local-pipeline", "type": "local", "network": "private"},
        ],
        "models": [
            {
                "id": "matte-m", "model_id": "mediapipe-matte",
                "revision": f"sha256:{matte_sha}", "licence": "Apache-2.0",
                "commercial_use": True, "weights_sha256": matte_sha,
            },
            {
                "id": "demucs-m", "model_id": "htdemucs", "revision": "demucs==4.1.0",
                "licence": "MIT", "commercial_use": True,
            },
            {
                "id": "clip-m", "model_id": "example/clip-model",
                "revision": "2026-01-01", "licence": "MIT", "commercial_use": True,
            },
            {
                "id": "caption-m", "model_id": "example/caption-model",
                "revision": "2026-02-02", "licence": "MIT", "commercial_use": True,
            },
        ],
        "routes": [
            {
                "id": "planning-remote", "capability": "planning.chat",
                "adapter_id": "remote-chat", "model_id": "chat-x",
                "model_revision": "2026-01-01",
                "pricing": {"status": "priced", "unit": "tokens",
                            "input_per_million_usd": 0.5, "output_per_million_usd": 1.0},
                "budget": {"feature": "planning"},
            },
            {
                "id": "transcription-remote", "capability": "transcription.speech_to_text",
                "adapter_id": "remote-stt", "model_id": "stt-x",
                "model_revision": "2026-01-01",
                "pricing": {"status": "priced", "per_request_usd": 0.25},
                "budget": {"feature": "transcription"},
            },
            {
                "id": "matte", "capability": "matte.segmentation",
                "adapter_id": "local-matte", "model_id": "mediapipe-matte",
                "model_ref": "matte-m", "model_revision": f"sha256:{matte_sha}",
                "requires_gpu": False, "pricing": {"status": "free"},
                "explicitly_free": True,
                "params": {"runner": "mediapipe_selfie_matte",
                           "model_path": str(matte_file), "weights_sha256": matte_sha},
            },
            {
                "id": "stems", "capability": "audio.stem_separation",
                "adapter_id": "local-stems", "model_id": "htdemucs",
                "model_ref": "demucs-m", "model_revision": "demucs==4.1.0",
                "requires_gpu": False, "pricing": {"status": "free"},
                "explicitly_free": True,
                "params": {"runner": "demucs_stems", "model_name": "htdemucs"},
            },
            {
                "id": "clip", "capability": "embeddings.image",
                "adapter_id": "local-embeddings", "model_id": "example/clip-model",
                "model_ref": "clip-m", "model_revision": "2026-01-01",
                "requires_gpu": True, "embedding_space": "clip",
                "pricing": {"status": "free"}, "explicitly_free": True,
                "params": {"runner": "clip_embeddings", "model_name": "example/clip-model"},
            },
            {
                "id": "caption-pipeline", "capability": "vision.caption",
                "adapter_id": "local-pipeline", "model_id": "example/caption-model",
                "model_ref": "caption-m", "model_revision": "2026-02-02",
                "requires_gpu": True,
                "pricing": {"status": "free"}, "explicitly_free": True,
                "params": {
                    "runner": "transformers_pipeline",
                    "pipeline_task": "image-to-text",
                    "model_name": "example/caption-model",
                },
            },
        ],
    }
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(_json.dumps(registry), encoding="utf-8")
    bundle = tmp_path / "bundle.json"
    bundle.write_text(_json.dumps({"chat": "c", "stt": "s"}), encoding="utf-8")

    monkeypatch.setenv("MODEL_PREFETCH", "true")
    monkeypatch.setattr(
        prefetch_models,
        "get_settings",
        lambda: _settings(
            GPU_WORKERS_ENABLED=value,
            MEDIA_WORK_DIR="/tmp",
            AI_REGISTRY_FILE=str(registry_file),
            AI_SECRETS_BUNDLE_FILE=str(bundle),
        ),
    )
    monkeypatch.setattr(
        prefetch_models.importlib.util,
        "find_spec",
        lambda name: object() if name in {"huggingface_hub", "demucs"} else None,
    )
    downloads: list[str] = []
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
    # Demucs (CPU) is always prefetched; the matte file SHA verifies without a
    # download (no failure recorded). The GPU CLIP model is only attempted when
    # GPU workers are enabled.
    assert "demucs:htdemucs" in downloads
    assert ("example/clip-model" in downloads) is gpu_models
    # The transformers_pipeline GPU model is prefetched exactly like any other
    # enabled local model (plan Task 33), gated by GPU workers being enabled.
    assert ("example/caption-model" in downloads) is gpu_models
    assert event["failure_count"] == 0
    # matte (file) + demucs (hub) always; + clip + caption (hub) with GPU.
    assert event["models_attempted"] == (4 if gpu_models else 2)
