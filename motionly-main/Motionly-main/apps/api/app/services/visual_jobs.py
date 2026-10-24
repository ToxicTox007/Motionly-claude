import hashlib
import json
import logging
from dataclasses import dataclass

from app.repositories.uploads import UploadRecord
from app.repositories.visual import InMemoryVisualRepository, VideoManifestRecord
from app.schemas.visual import VISUAL_MANIFEST_SCHEMA_VERSION, VisualManifestStatus
from app.services.queue import EnqueuedJob, VisualQueue

logger = logging.getLogger(__name__)


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
    return provider, "|".join(
        f"{getattr(settings, model_name, 'unknown')}@"
        f"{getattr(settings, revision_name, 'unknown')}"
        for model_name, revision_name in (
            ("visual_clip_model", "visual_clip_model_revision"),
            ("visual_blip_model", "visual_blip_model_revision"),
            ("visual_yolo_model", "visual_yolo_model_revision"),
        )
    )


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
    )
    return VisualScheduleResult(manifest=manifest, job=job)
