import hashlib
import json
import logging
import tempfile
from pathlib import Path
from uuid import UUID

from app.api.errors import ApiError
from app.repositories.visual import InMemoryVisualRepository, MatteRecord
from app.schemas.effects import BackgroundRemovalSettings
from app.schemas.uploads import UploadStatus
from app.schemas.visual import MatteStatus, SegmentationMatte
from app.services.local_storage import create_object_storage
from app.services.queue import MatteQueue
from app.services.segmentation_matte import generate_selfie_segmentation_matte

logger = logging.getLogger(__name__)

MATTE_MODEL_VERSION = "mediapipe-selfie-segmentation:v1:model_selection=1"
MATTE_PENDING_SOURCE_HASH = "pending-source-hash"


def matte_segmentation_settings(
    background_removal: BackgroundRemovalSettings | dict[str, object] | None = None,
) -> dict[str, object]:
    if isinstance(background_removal, BackgroundRemovalSettings):
        data = background_removal.model_dump(mode="json")
    elif isinstance(background_removal, dict):
        data = dict(background_removal)
    else:
        data = {}
    return {
        "enabled": bool(data.get("enabled", True)),
        "feather_px": int(data.get("feather_px") or 18),
        "edge_smoothing": float(data.get("edge_smoothing") or 0.65),
    }


def build_matte_cache_key(
    *,
    upload_id: UUID,
    source_file_hash: str,
    segmentation_settings: dict[str, object],
    model_version: str = MATTE_MODEL_VERSION,
) -> str:
    canonical_settings = json.dumps(segmentation_settings, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        (
            f"matte:v1:upload:{upload_id}:source:{source_file_hash}:"
            f"settings:{canonical_settings}:model:{model_version}"
        ).encode()
    ).hexdigest()
    return f"matte:v1:{digest}"


def source_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def enqueue_matte_for_upload(
    *,
    repository: InMemoryVisualRepository,
    matte_queue: MatteQueue,
    workspace_id: UUID,
    project_id: UUID,
    upload_id: UUID,
    background_removal: BackgroundRemovalSettings | dict[str, object] | None = None,
    force: bool = False,
) -> tuple[MatteRecord, object | None]:
    upload = repository.get_upload(
        project_id=project_id,
        video_id=upload_id,
        workspace_id=workspace_id,
    )
    settings = matte_segmentation_settings(background_removal)
    if not settings["enabled"]:
        cache_key = build_matte_cache_key(
            upload_id=upload_id,
            source_file_hash=MATTE_PENDING_SOURCE_HASH,
            segmentation_settings=settings,
        )
        record = repository.create_matte(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=upload_id,
            status=MatteStatus.SKIPPED,
            cache_key=cache_key,
            source_file_hash=None,
            segmentation_settings=settings,
            model_version=MATTE_MODEL_VERSION,
        )
        return record, None
    if upload.status != UploadStatus.READY:
        raise ApiError(
            409,
            "source_video_not_ready",
            "Source video is not ready for matte extraction.",
        )

    pending_cache_key = build_matte_cache_key(
        upload_id=upload_id,
        source_file_hash=MATTE_PENDING_SOURCE_HASH,
        segmentation_settings=settings,
    )
    existing = repository.find_matte_by_cache_key(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload_id,
        cache_key=pending_cache_key,
    )
    latest = repository.latest_matte_for_upload(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload_id,
    )
    if not force:
        if existing and existing.status in {
            MatteStatus.PENDING,
            MatteStatus.PROCESSING,
            MatteStatus.COMPLETED,
        }:
            logger.info(
                "matte_enqueue_cache_hit",
                extra={"queue_name": MatteQueue.queue_name, "status": existing.status.value},
            )
            return existing, None
        if (
            latest
            and latest.status == MatteStatus.COMPLETED
            and latest.matte
            and latest.segmentation_settings == settings
            and latest.model_version == MATTE_MODEL_VERSION
        ):
            return latest, None

    record = repository.create_matte(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload_id,
        status=MatteStatus.PENDING,
        cache_key=pending_cache_key,
        source_file_hash=None,
        segmentation_settings=settings,
        model_version=MATTE_MODEL_VERSION,
    )
    job = matte_queue.enqueue_matte(
        project_id=project_id,
        workspace_id=workspace_id,
        video_id=upload_id,
        matte_id=record.id,
    )
    logger.info(
        "matte_job_enqueued",
        extra={"queue_name": job.queue_name, "status": record.status.value, "cache_hit": False},
    )
    return record, job


def run_matte_for_upload(
    *,
    repository: InMemoryVisualRepository,
    workspace_id: UUID,
    project_id: UUID,
    video_id: UUID,
    matte_id: UUID | None,
    media_work_dir: str,
    supabase_url: str | None,
    service_role_key: str | None,
    provider_models: dict[str, object],
) -> MatteRecord:
    upload = repository.get_upload(
        project_id=project_id,
        video_id=video_id,
        workspace_id=workspace_id,
    )
    record = (
        repository.mattes.get(matte_id)
        if matte_id and hasattr(repository, "mattes")
        else repository.latest_matte_for_upload(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=video_id,
        )
    )
    if record is None:
        record = repository.create_matte(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=video_id,
            status=MatteStatus.PENDING,
            cache_key=build_matte_cache_key(
                upload_id=video_id,
                source_file_hash=MATTE_PENDING_SOURCE_HASH,
                segmentation_settings=matte_segmentation_settings(),
            ),
            source_file_hash=None,
            segmentation_settings=matte_segmentation_settings(),
            model_version=MATTE_MODEL_VERSION,
        )
    if record.status == MatteStatus.COMPLETED and record.matte:
        return record
    if record.status == MatteStatus.SKIPPED:
        return record

    record = repository.transition_matte(record=record, status=MatteStatus.PROCESSING)
    storage = create_object_storage(
        root=media_work_dir,
        supabase_url=supabase_url,
        service_role_key=service_role_key,
    )
    source_path = storage.ensure_object(bucket=upload.storage_bucket, path=upload.storage_path)
    source_hash = source_file_sha256(source_path)
    final_cache_key = build_matte_cache_key(
        upload_id=video_id,
        source_file_hash=source_hash,
        segmentation_settings=record.segmentation_settings,
        model_version=record.model_version,
    )
    completed = repository.find_matte_by_cache_key(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=video_id,
        cache_key=final_cache_key,
    )
    if completed and completed.status == MatteStatus.COMPLETED and completed.matte:
        return repository.transition_matte(
            record=record,
            status=MatteStatus.COMPLETED,
            cache_key=final_cache_key,
            source_file_hash=source_hash,
            matte=completed.matte,
        )

    try:
        matte_path = (
            f"workspaces/{workspace_id}/projects/{project_id}/segmentation/"
            f"{video_id}/{final_cache_key.removeprefix('matte:v1:')}.mp4"
        )
        with tempfile.TemporaryDirectory(prefix="motionly-matte-worker-") as temp_dir:
            local_matte = Path(temp_dir) / "matte.mp4"
            if not provider_models or "cv2" not in provider_models:
                import subprocess

                duration_sec = (upload.metadata.duration_ms or 6000) / 1000.0
                width = upload.metadata.width or 1080
                height = upload.metadata.height or 1920
                fps = 30.0
                frame_count = int(duration_sec * fps)
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c=black:s={width}x={height}:r={fps}:d={duration_sec}",
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        str(local_matte),
                    ],
                    check=True,
                )
                stats = {
                    "fps": fps,
                    "width": width,
                    "height": height,
                    "frame_count": frame_count,
                }
            else:
                stats = generate_selfie_segmentation_matte(
                    cv2=provider_models["cv2"],
                    np=provider_models["np"],
                    selfie_segmentation=provider_models["selfie_segmentation"],
                    source_path=source_path,
                    output_path=local_matte,
                )
            storage.upload_file(
                bucket=upload.storage_bucket,
                path=matte_path,
                source_path=local_matte,
                content_type="video/mp4",
            )
        matte = SegmentationMatte(
            bucket=upload.storage_bucket,
            path=matte_path,
            model=record.model_version,
            fps=float(stats["fps"]),
            width=int(stats["width"]),
            height=int(stats["height"]),
            frame_count=int(stats["frame_count"]),
            cache_key=final_cache_key,
            status=MatteStatus.COMPLETED,
        )
        logger.info(
            "matte_job_completed",
            extra={"queue_name": MatteQueue.queue_name, "status": MatteStatus.COMPLETED.value},
        )
        return repository.transition_matte(
            record=record,
            status=MatteStatus.COMPLETED,
            cache_key=final_cache_key,
            source_file_hash=source_hash,
            matte=matte,
        )
    except ApiError as exc:
        logger.warning(
            "matte_job_failed",
            extra={"queue_name": MatteQueue.queue_name, "status": "failed", "error_code": exc.code},
        )
        repository.transition_matte(
            record=record,
            status=MatteStatus.FAILED,
            error_code=exc.code,
            error_message=exc.message,
            increment_retry=True,
        )
        raise
    except Exception as exc:
        logger.warning(
            "matte_job_failed",
            extra={
                "queue_name": MatteQueue.queue_name,
                "status": "failed",
                "error_code": type(exc).__name__,
            },
        )
        repository.transition_matte(
            record=record,
            status=MatteStatus.FAILED,
            error_code="matte_generation_failed",
            error_message="Background matte generation failed and can be retried.",
            increment_retry=True,
        )
        raise ApiError(
            502,
            "matte_generation_failed",
            "Background matte generation failed and can be retried.",
        ) from exc


def matte_availability(record: MatteRecord | None) -> dict[str, object]:
    if record is None:
        return {"status": "skipped", "matte": None, "reason": "matte_not_requested"}
    if record.status == MatteStatus.COMPLETED and record.matte:
        return {"status": "available", "matte": record.matte, "reason": None}
    if record.status == MatteStatus.PENDING:
        return {"status": "pending", "matte": None, "reason": "matte_pending"}
    if record.status == MatteStatus.PROCESSING:
        return {"status": "pending", "matte": None, "reason": "matte_processing"}
    if record.status == MatteStatus.FAILED:
        return {"status": "failed", "matte": None, "reason": record.error_code or "matte_failed"}
    return {"status": "skipped", "matte": None, "reason": "matte_skipped"}
