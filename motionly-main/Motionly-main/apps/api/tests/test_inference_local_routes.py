"""Local model routes, licence metadata and the registry fragment merge (Task 14).

These tests use the REAL local runner factory registry
(``app.services.inference_local.local_runner_factories``) wired by
``build_inference_runtime``; they assert the wiring, not a fake catalogue. Where
a real model is unavailable in CI, the factory's heavy load is never triggered:
the tests only build the runtime and read its catalogue, so no model weights are
needed. The CPU-import test asserts that importing/constructing the factories
pulls in no GPU-only package (``torch``/``tensorflow``/``transformers``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.inference_local import local_runner_factories
from app.services.inference_registry import (
    DiagnosticCode,
    load_registry_document,
    load_registry_sources,
    merge_registry_documents,
    validate_registry,
)
from app.services.inference_runtime import build_inference_runtime

ROOT = Path(__file__).resolve().parents[3]
CPU_LOCAL = ROOT / "infra" / "ai-registry" / "cpu-local.json"
GPU_LOCAL = ROOT / "infra" / "ai-registry" / "gpu-local.json"

# A minimal remote registry providing the two required capabilities, so a merge
# with the local fragments boots with planning + transcription satisfied.
_REMOTE_REQUIRED = {
    "version": 1,
    "adapters": [
        {
            "id": "remote-chat",
            "type": "openai_compatible",
            "base_url": "https://chat.example.invalid/v1",
            "network": "public",
            "auth": {"type": "bearer", "secret_ref": "bundle:chat"},
            "data_processor": {
                "display_name": "Chat",
                "purpose": "Planning",
                "data_categories": ["text"],
                "policy_url": "https://chat.example.invalid/policy",
            },
        },
        {
            "id": "remote-stt",
            "type": "openai_compatible",
            "base_url": "https://stt.example.invalid/v1",
            "network": "public",
            "auth": {"type": "bearer", "secret_ref": "bundle:stt"},
            "data_processor": {
                "display_name": "STT",
                "purpose": "Transcription",
                "data_categories": ["audio"],
                "policy_url": "https://stt.example.invalid/policy",
            },
        },
    ],
    "models": [],
    "routes": [
        {
            "id": "planning-remote",
            "capability": "planning.chat",
            "adapter_id": "remote-chat",
            "model_id": "chat-x",
            "model_revision": "2026-01-01",
            "pricing": {
                "status": "priced",
                "unit": "tokens",
                "input_per_million_usd": 0.5,
                "output_per_million_usd": 1.0,
            },
            "budget": {"feature": "planning"},
        },
        {
            "id": "transcription-remote",
            "capability": "transcription.speech_to_text",
            "adapter_id": "remote-stt",
            "model_id": "stt-x",
            "model_revision": "2026-01-01",
            "pricing": {"status": "priced", "per_request_usd": 0.25},
            "budget": {"feature": "transcription"},
        },
    ],
}

_BUNDLE = {"chat": "chat-secret", "stt": "stt-secret"}


def _bundle_file(tmp_path: Path) -> Path:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(_BUNDLE), encoding="utf-8")
    return path


def _remote_file(tmp_path: Path) -> Path:
    path = tmp_path / "remote.json"
    path.write_text(json.dumps(_REMOTE_REQUIRED), encoding="utf-8")
    return path


def _settings(tmp_path: Path, *, registry_spec: str, app_env: str = "local", **updates):
    from app.core.config import get_settings

    values = {
        "app_env": app_env,
        "ai_registry_file": registry_spec,
        "ai_secrets_bundle_file": str(_bundle_file(tmp_path)),
        "redis_url": None,
        "supabase_url": None,
        "supabase_service_role_key": None,
    }
    values.update(updates)
    return get_settings().model_copy(update=values)


def _capability_ids(runtime) -> set[str]:
    return {route.capability.value for route in runtime.route_records.values()}


# ---------------------------------------------------------------------------
# No-GPU boot with cpu-local.json: matte, stems, sound available.
# ---------------------------------------------------------------------------
def test_no_gpu_boot_with_cpu_local_reports_matte_stems_sound(tmp_path: Path) -> None:
    spec = f"{_remote_file(tmp_path)},{CPU_LOCAL}"
    settings = _settings(tmp_path, registry_spec=spec, gpu_workers_enabled=False)
    runtime = build_inference_runtime(settings)

    capabilities = _capability_ids(runtime)
    assert "matte.segmentation" in capabilities
    assert "audio.stem_separation" in capabilities
    assert "audio.sound_classification" in capabilities
    # Planning + transcription came from the remote fragment, so startup is clean.
    assert "planning.chat" in capabilities
    assert "transcription.speech_to_text" in capabilities
    # No local route was dropped.
    assert not [
        d for d in runtime.dropped_routes if d.code == DiagnosticCode.LOCAL_RUNNER_UNAVAILABLE
    ], runtime.dropped_routes


def test_cpu_local_routes_use_the_real_runner_factory_registry(tmp_path: Path) -> None:
    # The runtime builds the real factory-backed adapters (not a fake): each local
    # route's gateway adapter is one of the lazy runner wrappers.
    spec = f"{_remote_file(tmp_path)},{CPU_LOCAL}"
    settings = _settings(tmp_path, registry_spec=spec, gpu_workers_enabled=False)
    runtime = build_inference_runtime(settings)

    adapter_types = {
        type(runtime.gateway._adapters[adapter_id]).__name__
        for adapter_id in ("local-cpu-matte", "local-cpu-sound", "local-cpu-stems")
    }
    assert adapter_types == {
        "_LazyMatteAdapter",
        "_LazyAudioIntelligenceAdapter",
        "_LazyStemAdapter",
    }
    # And the registry really maps those runner names to factories.
    factories = local_runner_factories()
    for runner in ("mediapipe_selfie_matte", "demucs_stems", "yamnet_sound"):
        assert callable(factories[runner])


# ---------------------------------------------------------------------------
# Importing the CPU factories imports no GPU-only package.
# ---------------------------------------------------------------------------
def test_importing_cpu_factories_does_not_import_heavy_packages() -> None:
    # Run in a fresh interpreter so other tests' imports do not pollute the check.
    code = (
        "import sys;"
        "import app.services.inference_local as il;"
        "il.local_runner_factories();"
        "heavy=[m for m in ('torch','tensorflow','transformers') if m in sys.modules];"
        "print(heavy)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={"APP_ENV": "test", "PATH": __import__("os").environ.get("PATH", "")},
        check=True,
    )
    assert result.stdout.strip() == "[]", result.stdout + result.stderr


# ---------------------------------------------------------------------------
# GPU routes are dropped with GPU_WORKERS_ENABLED=false.
# ---------------------------------------------------------------------------
def test_gpu_local_routes_dropped_when_gpu_workers_disabled() -> None:
    document, _ = load_registry_document(GPU_LOCAL)
    result = validate_registry(
        document, app_env="local", gpu_workers_enabled=False, block_noncommercial=False
    )
    gpu_warnings = [
        d for d in result.diagnostics if d.code == DiagnosticCode.ROUTE_REQUIRES_GPU
    ]
    # Every GPU route carries the requires-gpu warning (the capability is dropped).
    assert {d.object_id for d in gpu_warnings} == {
        "local-embeddings-text",
        "local-embeddings-image",
        "local-vision-caption",
        "local-audio-diarization",
        "local-audio-emotion",
        "local-vision-caption-pipeline",
        "local-vision-object-detection-pipeline",
    }


def test_gpu_local_routes_available_when_gpu_workers_enabled() -> None:
    document, _ = load_registry_document(GPU_LOCAL)
    result = validate_registry(
        document, app_env="local", gpu_workers_enabled=True, block_noncommercial=False
    )
    assert not [
        d for d in result.diagnostics if d.code == DiagnosticCode.ROUTE_REQUIRES_GPU
    ]
    assert set(result.valid_route_ids) == {
        "local-embeddings-text",
        "local-embeddings-image",
        "local-vision-caption",
        "local-audio-diarization",
        "local-audio-emotion",
        "local-vision-caption-pipeline",
        "local-vision-object-detection-pipeline",
    }


# ---------------------------------------------------------------------------
# Licence / revision rules on local routes.
# ---------------------------------------------------------------------------
def _local_doc(*, revision="2026-01-01", licence="MIT", commercial=True) -> dict:
    model = {
        "id": "local-model",
        "model_id": "local-model-id",
        "revision": revision,
    }
    if licence is not None:
        model["licence"] = licence
    if commercial is not None:
        model["commercial_use"] = commercial
    return {
        "version": 1,
        "adapters": [{"id": "local-a", "type": "local", "network": "private"}],
        "models": [model],
        "routes": [
            {
                "id": "local-matte",
                "capability": "matte.segmentation",
                "adapter_id": "local-a",
                "model_id": "local-model-id",
                "model_ref": "local-model",
                "model_revision": revision,
                "pricing": {"status": "free"},
                "explicitly_free": True,
                "params": {"runner": "mediapipe_selfie_matte"},
            }
        ],
    }


@pytest.mark.parametrize(
    ("revision", "code"),
    [
        ("latest", DiagnosticCode.REVISION_FLOATING),
        ("provider-managed", DiagnosticCode.REVISION_PROVIDER_MANAGED_SELF_HOSTED),
    ],
)
def test_local_route_with_floating_or_provider_managed_revision_is_rejected(
    revision: str, code: str
) -> None:
    document, _ = load_registry_document(_local_doc(revision=revision))
    result = validate_registry(
        document, app_env="local", gpu_workers_enabled=True, block_noncommercial=False
    )
    assert "local-matte" in result.dropped_route_ids
    assert any(d.code == code for d in result.errors())


def test_local_route_without_licence_is_rejected() -> None:
    document, _ = load_registry_document(_local_doc(licence=None))
    result = validate_registry(
        document, app_env="local", gpu_workers_enabled=True, block_noncommercial=False
    )
    assert "local-matte" in result.dropped_route_ids
    assert any(d.code == DiagnosticCode.LICENCE_REQUIRED for d in result.errors())


def test_local_noncommercial_licence_warns_by_default_and_errors_when_blocked() -> None:
    document, _ = load_registry_document(_local_doc(commercial=False))

    warn = validate_registry(
        document, app_env="local", gpu_workers_enabled=True, block_noncommercial=False
    )
    assert "local-matte" in warn.valid_route_ids
    assert any(
        d.code == DiagnosticCode.LICENCE_NONCOMMERCIAL and not d.is_error
        for d in warn.diagnostics
    )

    blocked = validate_registry(
        document, app_env="local", gpu_workers_enabled=True, block_noncommercial=True
    )
    assert "local-matte" in blocked.dropped_route_ids
    assert any(
        d.code == DiagnosticCode.LICENCE_NONCOMMERCIAL and d.is_error
        for d in blocked.errors()
    )


# ---------------------------------------------------------------------------
# Registry fragment merge: id collision fails.
# ---------------------------------------------------------------------------
def test_fragment_merge_combines_cpu_and_gpu() -> None:
    cpu, _ = load_registry_document(CPU_LOCAL)
    gpu, _ = load_registry_document(GPU_LOCAL)
    merged, diagnostics = merge_registry_documents([cpu, gpu])
    assert merged is not None
    assert not diagnostics
    assert len(merged.routes) == len(cpu.routes) + len(gpu.routes)
    assert len(merged.adapters) == len(cpu.adapters) + len(gpu.adapters)


def test_fragment_merge_with_id_collision_fails() -> None:
    cpu, _ = load_registry_document(CPU_LOCAL)
    merged, diagnostics = merge_registry_documents([cpu, cpu])
    assert merged is None
    assert all(
        d.code == DiagnosticCode.REGISTRY_FRAGMENT_COLLISION for d in diagnostics
    )
    assert any(d.is_error for d in diagnostics)


def test_comma_separated_registry_file_merges_sources(tmp_path: Path) -> None:
    spec = f"{_remote_file(tmp_path)},{CPU_LOCAL},{GPU_LOCAL}"
    document, diagnostics = load_registry_sources(spec)
    assert document is not None, diagnostics
    route_ids = {route.id for route in document.routes}
    assert {"planning-remote", "local-matte-segmentation", "local-embeddings-text"} <= route_ids


# ---------------------------------------------------------------------------
# Every enabled local model has a pinned revision and licence metadata.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fragment", [CPU_LOCAL, GPU_LOCAL])
def test_every_local_model_has_pinned_revision_and_licence(fragment: Path) -> None:
    document, _ = load_registry_document(fragment)
    for model in document.models:
        assert model.revision, model.id
        assert "latest" not in model.revision.lower(), model.id
        assert model.revision.strip().lower() != "provider-managed", model.id
        assert model.licence, model.id
        assert model.commercial_use is not None, model.id
        assert model.verified_at == "2026-10-24", model.id


# ---------------------------------------------------------------------------
# No local model id remains hard-coded as a default in the embedding call sites
# or in config.py.
#
# Task 14 removes the hard-coded ``openai/clip-vit-base-patch32`` default from
# the three embedding call sites (stock_video, layer_jobs, broll): they read the
# embedding route's model from the gateway/runtime and fall back to a neutral
# lexical placeholder when no route exists. Task 26 finishes the job: the local
# model-identity defaults in ``config.py`` (pyannote, wav2vec2, CLIP, BLIP, YOLO,
# Demucs and the planning/transcription model ids) are removed, and their
# readers (main.py, the workers, scale.py) source identity from the registry.
# ---------------------------------------------------------------------------
def test_no_clip_model_id_remains_as_a_code_default_in_call_sites() -> None:
    api_root = Path(__file__).resolve().parents[1] / "app"
    forbidden = "openai/clip-vit-base-patch32"
    offenders: list[str] = []
    for path in (
        api_root / "services" / "stock_video.py",
        api_root / "services" / "layer_jobs.py",
        api_root / "api" / "broll.py",
        api_root / "services" / "job_engine.py",
    ):
        if forbidden in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert not offenders, offenders


def test_no_local_model_id_remains_as_a_code_default_in_config() -> None:
    """config.py must not hard-code any local/vendor model id or pinned revision
    as a Settings default (plan Task 14 acceptance, finished in Task 26)."""
    config_source = (
        Path(__file__).resolve().parents[1] / "app" / "core" / "config.py"
    ).read_text(encoding="utf-8")
    forbidden_model_ids = (
        "openai/clip-vit-base-patch32",
        "Salesforce/blip2-opt-2.7b",
        "pyannote/speaker-diarization-3.1",
        "superb/wav2vec2-base-superb-er",
        "ultralytics/assets",
        "htdemucs",
        "whisper-large-v3-turbo",
        "openai/gpt-4.1-mini",
        "tfhub.dev/google/yamnet",
        # Pinned revisions that used to live as code defaults.
        "84fd25912480287da0247647c3d2b4853cb3ee5d",
        "441a7599c3b22107314dcbd9166621c5c83f2cc5",
        "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
        "59a1ef6c1e5117b3f65523d1c6066825bcf315e3",
        "demucs==4.1.0",
    )
    offenders = [token for token in forbidden_model_ids if token in config_source]
    assert not offenders, offenders


def test_no_local_model_id_remains_as_a_code_default_in_request_schemas() -> None:
    """Request schemas must not hard-code a local model id as a field default
    (plan Task 14 acceptance, finished in Task 26): the stem request's model
    identity comes from the configured ``audio.stem_separation`` route."""
    scale_schema = (
        Path(__file__).resolve().parents[1] / "app" / "schemas" / "scale.py"
    ).read_text(encoding="utf-8")
    assert 'default="htdemucs"' not in scale_schema
    assert "htdemucs" not in scale_schema


# ---------------------------------------------------------------------------
# transformers_pipeline local routes and several local routes per capability.
# ---------------------------------------------------------------------------
def test_gpu_local_has_two_vision_caption_routes() -> None:
    # Two local routes serve ``vision.caption`` (a BLIP runner and the generic
    # transformers_pipeline runner), so users can choose between them (plan Task
    # 33 "several local routes may serve one capability").
    document, _ = load_registry_document(GPU_LOCAL)
    result = validate_registry(
        document, app_env="local", gpu_workers_enabled=True, block_noncommercial=False
    )
    caption_routes = [
        route
        for route in document.routes
        if route.capability.value == "vision.caption"
        and route.id in result.valid_route_ids
    ]
    assert len(caption_routes) == 2
    assert {r.id for r in caption_routes} == {
        "local-vision-caption",
        "local-vision-caption-pipeline",
    }


def test_transformers_pipeline_routes_use_the_real_runner_factory(tmp_path: Path) -> None:
    spec = f"{_remote_file(tmp_path)},{GPU_LOCAL}"
    settings = _settings(tmp_path, registry_spec=spec, gpu_workers_enabled=True)
    runtime = build_inference_runtime(settings)
    # Both transformers_pipeline routes build the generic lazy pipeline adapter.
    for adapter_id in (
        "local-gpu-pipeline-caption",
        "local-gpu-pipeline-objects",
    ):
        adapter = runtime.gateway._adapters[adapter_id]  # noqa: SLF001
        assert type(adapter).__name__ == "_TransformersPipelineAdapter"
    # And the runner name maps to a factory.
    assert callable(local_runner_factories()["transformers_pipeline"])
