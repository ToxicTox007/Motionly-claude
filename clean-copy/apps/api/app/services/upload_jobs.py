from uuid import UUID

from app.api.errors import ApiError
from app.repositories.uploads import InMemoryUploadRepository, UploadRecord
from app.schemas.uploads import UploadStatus
from app.services.local_storage import create_object_storage
from app.services.preprocessing import VideoPreprocessor


def complete_upload_preprocessing(
    *,
    upload: UploadRecord,
    repository: InMemoryUploadRepository,
    media_work_dir: str,
    supabase_url: str | None,
    service_role_key: str | None,
) -> UploadRecord:
    if upload.status == UploadStatus.READY:
        return upload
    if upload.status == UploadStatus.FAILED:
        upload = repository.transition_upload(upload=upload, status=UploadStatus.PROCESSING)
    if upload.status == UploadStatus.UPLOADED:
        upload = repository.transition_upload(upload=upload, status=UploadStatus.PROCESSING)
    if upload.status != UploadStatus.PROCESSING:
        raise ApiError(
            409,
            "upload_not_processable",
            "Upload must be fully uploaded before preprocessing can run.",
        )

    repository.attach_active_video(project_id=upload.project_id, video_id=upload.id)
    try:
        storage = create_object_storage(
            root=media_work_dir,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
        )
        source_path = storage.ensure_object(
            bucket=upload.storage_bucket,
            path=upload.storage_path,
        )
        thumbnail_path = (
            f"workspaces/{upload.workspace_id}/projects/{upload.project_id}/"
            f"thumbnails/{upload.id}.jpg"
        )
        metadata, thumbnail = VideoPreprocessor(storage_root=media_work_dir).process(
            source_path=source_path,
            thumbnail_bucket="thumbnails",
            thumbnail_path=thumbnail_path,
        )
        return repository.mark_processed(
            upload=upload,
            metadata=metadata,
            thumbnail_path=thumbnail,
        )
    except ApiError as exc:
        repository.mark_failed(
            upload=upload,
            error_code=exc.code,
            error_message=exc.message,
        )
        raise


def complete_upload_preprocessing_by_id(
    *,
    repository: InMemoryUploadRepository,
    workspace_id: UUID,
    project_id: UUID,
    video_id: UUID,
    media_work_dir: str,
    supabase_url: str | None,
    service_role_key: str | None,
) -> UploadRecord:
    upload = repository.get_upload_for_project(
        project_id=project_id,
        video_id=video_id,
        workspace_id=workspace_id,
    )
    return complete_upload_preprocessing(
        upload=upload,
        repository=repository,
        media_work_dir=media_work_dir,
        supabase_url=supabase_url,
        service_role_key=service_role_key,
    )
