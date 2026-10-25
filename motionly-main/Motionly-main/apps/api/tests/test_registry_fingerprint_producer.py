"""The missing producer for Task 6's worker fail-closed check.

The visual and matte workers call :func:`enforce_registry_fingerprint`, but a
job's payload only carries a ``registry_fingerprint`` because the enqueuer stamps
it. These tests prove the producer side:

* an enqueued matte job and an enqueued visual job carry a ``registry_fingerprint``
  equal to the worker's expectation for the same registry (producer == consumer);
* producer and consumer read the SAME per-job capability-set constant
  (``MATTE_CAPABILITY_IDS`` / ``VISUAL_CAPABILITY_IDS``), so they cannot drift;
* a worker built from a *different* registry fails closed with
  ``inference_registry_mismatch`` on that stamped payload;
* an enqueuer with no runtime leaves the payload unstamped, and the worker
  accepts it (the check is additive).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.effects import BackgroundRemovalSettings
from app.schemas.uploads import UploadStatus
from app.services.inference_runtime import build_inference_runtime
from app.services.local_storage import LocalObjectStorage
from app.services.matte_jobs import enqueue_matte_for_upload
from app.services.queue import MatteQueue, QueueRegistry, VisualQueue
from app.services.visual_jobs import (
    build_visual_cache_key,
    queue_visual_analysis_for_upload,
    visual_provider_identity,
)
from app.workers.inference_fingerprint import (
    MATTE_CAPABILITY_IDS,
    VISUAL_CAPABILITY_IDS,
    InferenceRegistryMismatch,
    enforce_registry_fingerprint,
    registry_fingerprint_for,
)

_BUNDLE = {"objects-endpoint": "objects-secret"}


def _bundle_file(tmp_path: Path) -> Path:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(_BUNDLE), encoding="utf-8")
    return path


def _planning_route(**overrides) -> dict:
    route = {
        "id": "planning-remote",
        "capability": "planning.chat",
        "adapter_id": "objects-endpoint",
        "model_id": "planning-model-x",
        "model_revision": "2026-01-01",
        "pricing": {
            "status": "priced",
            "unit": "tokens",
            "input_per_million_usd": 0.5,
            "output_per_million_usd": 1.0,
        },
        "budget": {"feature": "planning"},
    }
    route.update(overrides)
    return route


def _transcription_route(**overrides) -> dict:
    route = {
        "id": "transcription-remote",
        "capability": "transcription.speech_to_text",
        "adapter_id": "objects-endpoint",
        "model_id": "stt-model-x",
        "model_revision": "2026-01-01",
        "pricing": {"status": "priced", "per_request_usd": 0.25},
        "budget": {"feature": "transcription"},
    }
    route.update(overrides)
    return route


def _objects_route(revision: str) -> dict:
    # A vision.object_detection route (a VISUAL capability) so the visual
    # fingerprint depends on the registry contents. object_detection is served
    # by a ``generic_http`` adapter (not openai_compatible).
    return {
        "id": "objects-remote",
        "capability": "vision.object_detection",
        "adapter_id": "objects-http",
        "model_id": "objects-model-x",
        "model_revision": revision,
        "pricing": {"status": "priced", "per_request_usd": 0.1},
        "mapping": {
            "request": {"method": "POST", "path": "/detect", "body": {}},
            "items": "response.results",
            "fields": {"label": "name", "score": "score", "box": "box"},
            "box_format": "xywh_norm",
        },
    }


def _matte_route(revision: str) -> dict:
    # A local matte.segmentation route (a MATTE capability) so the matte
    # fingerprint depends on the registry contents.
    return {
        "id": "matte-local",
        "capability": "matte.segmentation",
        "adapter_id": "matte-local-adapter",
        "model_id": "local-matte-model",
        "model_ref": "matte-model",
        "model_revision": revision,
        "pricing": {"status": "free"},
        "explicitly_free": True,
        "compute_requirement": "cpu",
        "params": {"runner": "mediapipe_selfie_matte"},
    }


def _document(*, objects_revision: str, matte_revision: str) -> dict:
    return {
        "version": 1,
        "adapters": [
            {
                "id": "objects-endpoint",
                "type": "openai_compatible",
                "base_url": "https://objects-endpoint.example.invalid/v1",
                "network": "public",
                "auth": {"type": "bearer", "secret_ref": "bundle:objects-endpoint"},
                "data_processor": {
                    "display_name": "Operator endpoint",
                    "purpose": "Inference",
                    "data_categories": ["images", "text", "audio"],
                    "policy_url": "https://operator.example.invalid/policy",
                },
            },
            {"id": "matte-local-adapter", "type": "local", "network": "private"},
            {
                "id": "objects-http",
                "type": "generic_http",
                "base_url": "https://objects-http.example.invalid/v1",
                "network": "public",
                "auth": {"type": "bearer", "secret_ref": "bundle:objects-endpoint"},
                "data_processor": {
                    "display_name": "Objects endpoint",
                    "purpose": "Object detection",
                    "data_categories": ["images"],
                    "policy_url": "https://objects.example.invalid/policy",
                },
            },
        ],
        "models": [
            {
                "id": "matte-model",
                "model_id": "local-matte-model",
                "revision": matte_revision,
                "licence": "Apache-2.0",
                "commercial_use": True,
            }
        ],
        "routes": [
            _planning_route(),
            _transcription_route(),
            _objects_route(objects_revision),
            _matte_route(matte_revision),
        ],
    }


def _stub_local_factories() -> dict:
    """A stub matte runner so the local matte route activates without model files.

    The catalog fingerprint is computed from the RouteRecord fields (id,
    capability, adapter, model, revision, mapping), not from the adapter object,
    so a stub adapter is sufficient to make the matte capability fingerprint
    depend on the registry contents.
    """

    def _factory(**_kwargs: object) -> object:
        return object()

    return {"mediapipe_selfie_matte": _factory}


def _runtime(tmp_path: Path, document: dict, name: str):
    registry = tmp_path / f"{name}.json"
    registry.write_text(json.dumps(document), encoding="utf-8")
    settings = get_settings().model_copy(
        update={
            "app_env": "local",
            "ai_registry_file": str(registry),
            "ai_secrets_bundle_file": str(_bundle_file(tmp_path)),
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
        }
    )
    return build_inference_runtime(settings, local_adapter_factories=_stub_local_factories())


def _matte_context(tmp_path: Path):
    workspace_id = uuid4()
    project_id = uuid4()
    uploads = InMemoryUploadRepository()
    uploads.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = uploads.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="source.mp4",
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=128,
    )
    upload.status = UploadStatus.READY
    uploads.attach_active_video(project_id=project_id, video_id=upload.id)
    LocalObjectStorage(root=tmp_path).write_object(
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        content=b"fake-video-bytes",
    )
    repository = InMemoryVisualRepository(uploads)
    registry = QueueRegistry()
    return SimpleNamespace(
        workspace_id=workspace_id,
        project_id=project_id,
        upload=upload,
        repository=repository,
        registry=registry,
        matte_queue=MatteQueue(registry),
        visual_queue=VisualQueue(registry),
        uploads=uploads,
    )


# ---------------------------------------------------------------------------
# Matte producer
# ---------------------------------------------------------------------------
def test_enqueued_matte_job_carries_worker_expected_fingerprint(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _document(objects_revision="v1", matte_revision="v1"), "a")
    ctx = _matte_context(tmp_path)
    _record, job = enqueue_matte_for_upload(
        repository=ctx.repository,
        matte_queue=ctx.matte_queue,
        workspace_id=ctx.workspace_id,
        project_id=ctx.project_id,
        upload_id=ctx.upload.id,
        background_removal=BackgroundRemovalSettings(enabled=True),
        runtime=runtime,
    )
    assert job is not None
    expected = runtime.capability_fingerprint(MATTE_CAPABILITY_IDS)
    assert job.registry_fingerprint == expected
    # The worker, built from the same registry, accepts the stamped payload.
    enforce_registry_fingerprint(
        {"registry_fingerprint": job.registry_fingerprint},
        runtime=runtime,
        capability_ids=MATTE_CAPABILITY_IDS,
    )


def test_matte_worker_from_different_registry_fails_closed(tmp_path: Path) -> None:
    runtime_a = _runtime(tmp_path, _document(objects_revision="v1", matte_revision="v1"), "a")
    runtime_b = _runtime(tmp_path, _document(objects_revision="v1", matte_revision="v2"), "b")
    ctx = _matte_context(tmp_path)
    _record, job = enqueue_matte_for_upload(
        repository=ctx.repository,
        matte_queue=ctx.matte_queue,
        workspace_id=ctx.workspace_id,
        project_id=ctx.project_id,
        upload_id=ctx.upload.id,
        background_removal=BackgroundRemovalSettings(enabled=True),
        runtime=runtime_a,
    )
    # The matte capability routes differ, so the fingerprints differ.
    assert runtime_a.capability_fingerprint(
        MATTE_CAPABILITY_IDS
    ) != runtime_b.capability_fingerprint(MATTE_CAPABILITY_IDS)
    with pytest.raises(InferenceRegistryMismatch) as excinfo:
        enforce_registry_fingerprint(
            {"registry_fingerprint": job.registry_fingerprint},
            runtime=runtime_b,
            capability_ids=MATTE_CAPABILITY_IDS,
        )
    assert excinfo.value.code == "inference_registry_mismatch"


# ---------------------------------------------------------------------------
# Visual producer
# ---------------------------------------------------------------------------
def _enqueue_visual(ctx, runtime):
    settings = get_settings()
    provider_name, model = visual_provider_identity(settings)
    cache_key = build_visual_cache_key(
        upload=ctx.upload,
        provider=provider_name,
        model=model,
        sample_rate_fps=settings.visual_sample_fps,
        scene_threshold=settings.visual_scene_threshold,
    )
    return queue_visual_analysis_for_upload(
        upload=ctx.upload,
        repository=ctx.repository,
        visual_queue=ctx.visual_queue,
        provider=provider_name,
        model=model,
        cache_key=cache_key,
        runtime=runtime,
    )


def test_enqueued_visual_job_carries_worker_expected_fingerprint(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _document(objects_revision="v1", matte_revision="v1"), "a")
    ctx = _matte_context(tmp_path)
    result = _enqueue_visual(ctx, runtime)
    assert result.job is not None
    expected = runtime.capability_fingerprint(VISUAL_CAPABILITY_IDS)
    assert result.job.registry_fingerprint == expected
    enforce_registry_fingerprint(
        {"registry_fingerprint": result.job.registry_fingerprint},
        runtime=runtime,
        capability_ids=VISUAL_CAPABILITY_IDS,
    )


def test_visual_worker_from_different_registry_fails_closed(tmp_path: Path) -> None:
    runtime_a = _runtime(tmp_path, _document(objects_revision="v1", matte_revision="v1"), "a")
    runtime_b = _runtime(tmp_path, _document(objects_revision="v2", matte_revision="v1"), "b")
    ctx = _matte_context(tmp_path)
    result = _enqueue_visual(ctx, runtime_a)
    assert runtime_a.capability_fingerprint(
        VISUAL_CAPABILITY_IDS
    ) != runtime_b.capability_fingerprint(VISUAL_CAPABILITY_IDS)
    with pytest.raises(InferenceRegistryMismatch):
        enforce_registry_fingerprint(
            {"registry_fingerprint": result.job.registry_fingerprint},
            runtime=runtime_b,
            capability_ids=VISUAL_CAPABILITY_IDS,
        )


# ---------------------------------------------------------------------------
# Unstamped path (no runtime) and the shared-constant invariant
# ---------------------------------------------------------------------------
def test_enqueuers_without_runtime_leave_payload_unstamped(tmp_path: Path) -> None:
    ctx = _matte_context(tmp_path)
    _record, matte_job = enqueue_matte_for_upload(
        repository=ctx.repository,
        matte_queue=ctx.matte_queue,
        workspace_id=ctx.workspace_id,
        project_id=ctx.project_id,
        upload_id=ctx.upload.id,
        background_removal=BackgroundRemovalSettings(enabled=True),
    )
    assert matte_job is not None
    assert matte_job.registry_fingerprint is None
    visual = _enqueue_visual(ctx, None)
    assert visual.job is not None
    assert visual.job.registry_fingerprint is None


def test_producer_and_consumer_share_capability_constants(tmp_path: Path) -> None:
    # The producer helper computes the stamp from the SAME constant the worker
    # checks; passing the worker's constant to the helper yields the worker's
    # expectation exactly.
    runtime = _runtime(tmp_path, _document(objects_revision="v1", matte_revision="v1"), "a")
    assert registry_fingerprint_for(
        runtime, MATTE_CAPABILITY_IDS
    ) == runtime.capability_fingerprint(MATTE_CAPABILITY_IDS)
    assert registry_fingerprint_for(
        runtime, VISUAL_CAPABILITY_IDS
    ) == runtime.capability_fingerprint(VISUAL_CAPABILITY_IDS)
    assert registry_fingerprint_for(None, MATTE_CAPABILITY_IDS) is None
