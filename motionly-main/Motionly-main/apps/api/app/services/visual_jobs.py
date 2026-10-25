import hashlib
import json
import logging
from dataclasses import dataclass

from app.repositories.uploads import UploadRecord
from app.repositories.visual import InMemoryVisualRepository, VideoManifestRecord
from app.schemas.visual import VISUAL_MANIFEST_SCHEMA_VERSION, VisualManifestStatus
from app.services.queue import EnqueuedJob, VisualQueue

logger = logging.getLogger(__name__)


# Capability -> (fallback model id, fallback revision) for the local visual
# manifest pipeline. Identity is read from the registry ``local`` routes
# (gpu-local.json, plan Task 14) when present, never a config model-id default.
_VISUAL_MANIFEST_CAPABILITIES = (
    ("clip", "embeddings.image", "visual-embeddings"),
    ("blip", "vision.caption", "visual-caption"),
    ("yolo", "vision.object_detection", "visual-objects"),
)


def local_visual_model_identity(gateway: object | None) -> dict[str, str]:
    """Resolve the local CLIP/BLIP/YOLO model ids and revisions from the registry.

    Returns a mapping with ``{clip,blip,yolo}_model`` and
    ``{clip,blip,yolo}_revision`` keys taken from the gateway's highest-priority
    ``local`` route for each capability (plan Task 14: the registry is the only
    source of a local model id). When a route is absent, a neutral capability
    label is used so the worker still builds for availability reporting.
    """
    routes = list(getattr(gateway, "routes", []) or []) if gateway is not None else []
    identity: dict[str, str] = {}
    for key, capability, fallback in _VISUAL_MANIFEST_CAPABILITIES:
        candidates = [
            route
            for route in routes
            if getattr(getattr(route, "capability", None), "value", None) == capability
            and getattr(route, "enabled", True)
        ]
        if candidates:
            route = min(candidates, key=lambda item: getattr(item, "priority", 100))
            identity[f"{key}_model"] = str(getattr(route, "model", fallback))
            identity[f"{key}_revision"] = str(
                getattr(route, "model_revision", f"{fallback}-v1")
            )
        else:
            identity[f"{key}_model"] = fallback
            identity[f"{key}_revision"] = f"{fallback}-v1"
    return identity


def _visual_registry_fingerprint(runtime: object | None) -> str | None:
    """Stamp for an enqueued visual-analysis job (plan Task 6 fail-closed check).

    Uses the SAME capability-set constant the visual worker checks
    (``VISUAL_CAPABILITY_IDS``), imported lazily so the queue path does not pull
    the inference runtime into module import. ``None`` when no runtime is
    available (a non-API enqueue path): the payload is left unstamped and the
    worker accepts it.
    """
    if runtime is None:
        return None
    from app.workers.inference_fingerprint import (
        VISUAL_CAPABILITY_IDS,
        registry_fingerprint_for,
    )

    return registry_fingerprint_for(runtime, VISUAL_CAPABILITY_IDS)


@dataclass(frozen=True)
class VisualScheduleResult:
    manifest: VideoManifestRecord
    job: EnqueuedJob | None
    cached: bool = False


def visual_provider_identity(settings: object) -> tuple[str, str]:
    provider = str(getattr(settings, "visual_intelligence_provider", "local_ml"))
    if provider == "fixture":
        return provider, "fixture-visual-v1"
    if provider.strip().lower() == "remote":
        from app.services.remote_visual import remote_visual_identity

        return "remote", remote_visual_identity(settings)
    # The local visual-manifest identity is sourced from the registry local
    # routes at worker startup (``local_visual_model_identity``); the enqueue-side
    # cache key uses a neutral, stable manifest label (plan Task 14: no local
    # model id remains a code default; Path B keeps no cache to protect).
    return provider, "local-visual-manifest-v1"


def build_visual_cache_key(
    *,
    upload: UploadRecord,
    provider: str,
    model: str,
    sample_rate_fps: float,
    scene_threshold: float,
) -> str:
    source_identity = {
        "bucket": upload.storage_bucket,
        "path": upload.storage_path,
        "size": upload.file_size_bytes,
        "updated_at": upload.updated_at.isoformat(),
    }
    payload = {
        "kind": "visual_manifest",
        "schema_version": VISUAL_MANIFEST_SCHEMA_VERSION,
        "upload_id": str(upload.id),
        "source": source_identity,
        "provider": provider,
        "model": model,
        "sample_rate_fps": round(float(sample_rate_fps), 4),
        "scene_threshold": round(float(scene_threshold), 4),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"visual:v1:{digest}"


def queue_visual_analysis_for_upload(
    *,
    upload: UploadRecord,
    repository: InMemoryVisualRepository,
    visual_queue: VisualQueue,
    provider: str,
    model: str,
    cache_key: str,
    force: bool = False,
    runtime: object | None = None,
) -> VisualScheduleResult:
    existing = repository.find_manifest_by_cache_key(
        uploaded_video_id=upload.id,
        project_id=upload.project_id,
        workspace_id=upload.workspace_id,
        cache_key=cache_key,
    )
    if existing and existing.status == VisualManifestStatus.READY and not force:
        logger.info(
            "idempotent_output_reused",
            extra={"job_kind": "visual_analysis", "status": existing.status.value},
        )
        return VisualScheduleResult(manifest=existing, job=None, cached=True)
    if (
        existing
        and existing.status
        in {
            VisualManifestStatus.QUEUED,
            VisualManifestStatus.PROCESSING,
        }
        and not force
    ):
        logger.info(
            "duplicate_job_deduped",
            extra={"job_kind": "visual_analysis", "status": existing.status.value},
        )
        return VisualScheduleResult(manifest=existing, job=None, cached=True)

    manifest = repository.create_manifest(
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        uploaded_video_id=upload.id,
        status=VisualManifestStatus.QUEUED,
        provider=provider,
        model=model,
        manifest=None,
        cache_key=cache_key,
        error_code=None,
        error_message=None,
    )
    job = visual_queue.enqueue_visual_analysis(
        project_id=upload.project_id,
        workspace_id=upload.workspace_id,
        video_id=upload.id,
        registry_fingerprint=_visual_registry_fingerprint(runtime),
    )
    return VisualScheduleResult(manifest=manifest, job=job)
