from dataclasses import dataclass

from app.repositories.uploads import UploadRecord
from app.repositories.visual import InMemoryVisualRepository, VideoManifestRecord
from app.schemas.visual import VisualManifestStatus
from app.services.queue import EnqueuedJob, VisualQueue


@dataclass(frozen=True)
class VisualScheduleResult:
    manifest: VideoManifestRecord
    job: EnqueuedJob | None
    cached: bool = False


def visual_provider_identity(settings: object) -> tuple[str, str]:
    provider = str(getattr(settings, "visual_intelligence_provider", "local_ml"))
    if provider == "fixture":
        return provider, "fixture-visual-v1"
    return provider, "|".join(
        str(getattr(settings, name, "unknown"))
        for name in ("visual_clip_model", "visual_blip_model", "visual_yolo_model")
    )


def queue_visual_analysis_for_upload(
    *,
    upload: UploadRecord,
    repository: InMemoryVisualRepository,
    visual_queue: VisualQueue,
    provider: str,
    model: str,
    force: bool = False,
) -> VisualScheduleResult:
    existing = repository.latest_for_upload(
        uploaded_video_id=upload.id,
        project_id=upload.project_id,
        workspace_id=upload.workspace_id,
    )
    if existing and existing.status == VisualManifestStatus.READY and not force:
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
        job = visual_queue.enqueue_visual_analysis(
            project_id=upload.project_id,
            workspace_id=upload.workspace_id,
            video_id=upload.id,
        )
        return VisualScheduleResult(manifest=existing, job=job)

    manifest = repository.create_manifest(
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        uploaded_video_id=upload.id,
        status=VisualManifestStatus.QUEUED,
        provider=provider,
        model=model,
        manifest=None,
        error_code=None,
        error_message=None,
    )
    job = visual_queue.enqueue_visual_analysis(
        project_id=upload.project_id,
        workspace_id=upload.workspace_id,
        video_id=upload.id,
    )
    return VisualScheduleResult(manifest=manifest, job=job)
