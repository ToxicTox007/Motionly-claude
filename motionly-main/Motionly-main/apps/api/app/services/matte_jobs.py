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
from app.services.model_gateway import (
    MatteSegmentationOutput,
    MatteSegmentationRequest,
    ModelCallContext,
)
from app.services.queue import MatteQueue
from app.services.segmentation_matte import generate_selfie_segmentation_matte
from app.services.subprocess_env import media_tool_env
from app.services.worker_media import materialize_upload_source

logger = logging.getLogger(__name__)

# The matte model identity is NOT a code constant any more (plan Task 33: "the
# matte model identity and version come from the route"). It is derived from the
# active ``matte.segmentation`` registry route, or from the executed gateway
# route at completion time. ``_FALLBACK_MATTE_MODEL_VERSION`` is used only when
# no runtime/route is available (a test or a non-registry enqueue path) so the
# pending cache key and the stored identity still agree with each other.
_FALLBACK_MATTE_MODEL_VERSION = "matte.segmentation"


def matte_model_version(runtime: object | None) -> str:
    """The matte identity from the active ``matte.segmentation`` route.

    Returns ``"<route_id>@<model_revision>"`` for the highest-priority enabled
    matte route in the runtime, so the cache key and provenance follow the
    registry (never a code-bound model name). Falls back to a neutral capability
    label when no runtime or route is available, which keeps the pending cache
    key stable for test/no-registry enqueue paths.
    """
    if runtime is None:
        return _FALLBACK_MATTE_MODEL_VERSION
    gateway = getattr(runtime, "gateway", None)
    routes = getattr(gateway, "routes", None) if gateway is not None else None
    if not routes:
        return _FALLBACK_MATTE_MODEL_VERSION
    candidates = [
        route
        for route in routes
        if getattr(route, "capability_id", None) == "matte.segmentation"
        and getattr(route, "enabled", True)
    ]
    if not candidates:
        # ``capability_id`` may be absent on some route objects; fall back to a
        # ``route_id`` prefix match for a matte route.
        candidates = [
            route
            for route in routes
            if getattr(route, "route_id", "").startswith("matte")
        ]
    if not candidates:
        return _FALLBACK_MATTE_MODEL_VERSION
    route = min(candidates, key=lambda item: getattr(item, "priority", 100))
    return f"{route.route_id}@{route.model_revision}"


def _matte_registry_fingerprint(runtime: object | None) -> str | None:
    """Stamp for an enqueued matte job (plan Task 6 worker fail-closed check).

    Uses the SAME capability-set constant the matte worker checks
    (``MATTE_CAPABILITY_IDS``), imported lazily so the queue path does not pull
    the inference runtime into module import. ``None`` when no runtime is
    available (a non-API enqueue path): the payload is left unstamped and the
    worker accepts it.
    """
    if runtime is None:
        return None
    from app.workers.inference_fingerprint import (
        MATTE_CAPABILITY_IDS,
        registry_fingerprint_for,
    )

    return registry_fingerprint_for(runtime, MATTE_CAPABILITY_IDS)


MATTE_PENDING_SOURCE_HASH = "pending-source-hash"

# fixture-matte:v1 — the CPU-stage test matte. It is a visible, deterministic
# stand-in for the GPU segmentation matte: a centered white ellipse on black.
# It is labelled with the registry label ("Test matte") in its provenance and
# never carries the real MediaPipe model name. Production requires the real
# matte model (the gateway route) and never reaches this path.
FIXTURE_MATTE_MODEL = "fixture-matte:v1"
FIXTURE_MATTE_LABEL = "Test matte"


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
    model_version: str = _FALLBACK_MATTE_MODEL_VERSION,
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
    runtime: object | None = None,
) -> tuple[MatteRecord, object | None]:
    upload = repository.get_upload(
        project_id=project_id,
        video_id=upload_id,
        workspace_id=workspace_id,
    )
    settings = matte_segmentation_settings(background_removal)
    # The matte identity comes from the active registry route (plan Task 33), so
    # the pending cache key and stored model_version follow the configured model.
    version = matte_model_version(runtime)
    if not settings["enabled"]:
        cache_key = build_matte_cache_key(
            upload_id=upload_id,
            source_file_hash=MATTE_PENDING_SOURCE_HASH,
            segmentation_settings=settings,
            model_version=version,
        )
        record = repository.create_matte(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=upload_id,
            status=MatteStatus.SKIPPED,
            cache_key=cache_key,
            source_file_hash=None,
            segmentation_settings=settings,
            model_version=version,
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
        model_version=version,
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
            and latest.model_version == version
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
        model_version=version,
    )
    job = matte_queue.enqueue_matte(
        project_id=project_id,
        workspace_id=workspace_id,
        video_id=upload_id,
        matte_id=record.id,
        registry_fingerprint=_matte_registry_fingerprint(runtime),
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
    model_gateway=None,
    matte_gateway_route=None,
    production: bool = False,
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
        # The matte identity comes from the executed gateway route (plan Task
        # 33); without one (a degraded/fixture path) the neutral fallback keeps
        # the pending and final cache keys consistent.
        fallback_version = (
            f"{matte_gateway_route.route_id}@{matte_gateway_route.model_revision}"
            if matte_gateway_route is not None
            else _FALLBACK_MATTE_MODEL_VERSION
        )
        record = repository.create_matte(
            workspace_id=workspace_id,
            project_id=project_id,
            uploaded_video_id=video_id,
            status=MatteStatus.PENDING,
            cache_key=build_matte_cache_key(
                upload_id=video_id,
                source_file_hash=MATTE_PENDING_SOURCE_HASH,
                segmentation_settings=matte_segmentation_settings(),
                model_version=fallback_version,
            ),
            source_file_hash=None,
            segmentation_settings=matte_segmentation_settings(),
            model_version=fallback_version,
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
    source_path = materialize_upload_source(upload=upload, storage=storage)
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
            matte_model = record.model_version
            matte_provenance: str | None = None
            if model_gateway is not None and matte_gateway_route is not None:
                segmented = model_gateway.invoke(
                    MatteSegmentationRequest(
                        source_path=source_path,
                        output_path=local_matte,
                        metadata=upload.metadata,
                    ),
                    context=ModelCallContext(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        required_route_id=matte_gateway_route.route_id,
                        required_model_revision=matte_gateway_route.model_revision,
                    ),
                )
                if not isinstance(segmented.output, MatteSegmentationOutput):
                    raise TypeError("matte route returned the wrong typed output")
                stats = {
                    "fps": segmented.output.fps,
                    "width": segmented.output.width,
                    "height": segmented.output.height,
                    "frame_count": segmented.output.frame_count,
                }
                matte_model = f"{segmented.metadata.route_id}@{segmented.metadata.model_revision}"
            elif production:
                raise ApiError(
                    503,
                    "matte_gateway_route_required",
                    "Production matte segmentation requires an executable gateway route.",
                )
            elif not provider_models or "cv2" not in provider_models:
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
                        f"color=c=black:s={width}x{height}:r={fps}:d={duration_sec}",
                        "-vf",
                        (
                            "geq=lum='if(lte("
                            "pow((X-W/2)/(W*0.3)\\,2)+pow((Y-H/2)/(H*0.38)\\,2)\\,1)"
                            ",255,0)':cb=128:cr=128"
                        ),
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        str(local_matte),
                    ],
                    check=True,
                    env=media_tool_env(),
                )
                matte_model = FIXTURE_MATTE_MODEL
                matte_provenance = FIXTURE_MATTE_LABEL
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
            model=matte_model,
            provenance=matte_provenance,
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
