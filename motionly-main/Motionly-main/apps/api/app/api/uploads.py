from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    Actor,
    get_actor,
    get_preprocess_queue,
    get_storage_signer,
    get_upload_repository,
)
from app.api.errors import ApiError
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.uploads import (
    UploadAssetUrlsResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadReadResponse,
    UploadStatus,
)
from app.services.offload import run_blocking
from app.services.queue import PreprocessQueue
from app.services.storage import StorageSigner
from app.services.upload_validation import validate_upload_preflight

router = APIRouter(prefix="/projects/{project_id}/uploads", tags=["uploads"])

ActorDep = Annotated[Actor, Depends(get_actor)]
UploadRepositoryDep = Annotated[InMemoryUploadRepository, Depends(get_upload_repository)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]
PreprocessQueueDep = Annotated[PreprocessQueue, Depends(get_preprocess_queue)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return await run_blocking(getattr(repository, sync_name), **kwargs)


@router.post("/init", response_model=UploadInitResponse, status_code=201)
async def init_upload(
    project_id: UUID,
    payload: UploadInitRequest,
    request: Request,
    actor: ActorDep,
    repository: UploadRepositoryDep,
    storage_signer: StorageSignerDep,
) -> UploadInitResponse:
    settings = request.app.state.settings
    validation = validate_upload_preflight(
        filename=payload.filename,
        mime_type=payload.mime_type,
        file_size_bytes=payload.file_size_bytes,
        max_file_size_bytes=settings.upload_max_file_size_bytes,
    )
    upload = await _repository_call(
        repository,
        "initialize_upload_async",
        "initialize_upload",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        storage_bucket=settings.upload_storage_bucket,
        storage_extension=validation.extension,
        original_filename=payload.filename,
        mime_type=payload.mime_type,
        file_size_bytes=payload.file_size_bytes,
        max_active_uploads=settings.upload_max_concurrent_per_workspace,
    )

    signed_upload = await run_blocking(
        storage_signer.create_signed_upload,
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        mime_type=upload.mime_type,
        expires_in_seconds=settings.upload_signed_url_ttl_seconds,
    )

    return UploadInitResponse(
        video_id=upload.id,
        status=upload.status,
        storage_bucket=upload.storage_bucket,
        storage_path=upload.storage_path,
        signed_upload_url=signed_upload.url,
        upload_headers=signed_upload.headers,
        expires_in_seconds=settings.upload_signed_url_ttl_seconds,
        warnings=validation.warnings,
    )


@router.post("/{video_id}/complete", response_model=UploadCompleteResponse)
async def complete_upload(
    project_id: UUID,
    video_id: UUID,
    payload: UploadCompleteRequest,
    request: Request,
    actor: ActorDep,
    repository: UploadRepositoryDep,
    preprocess_queue: PreprocessQueueDep,
) -> UploadCompleteResponse:
    upload = await _repository_call(
        repository,
        "complete_upload_for_processing_async",
        "complete_upload_for_processing",
        project_id=project_id,
        video_id=video_id,
        workspace_id=actor.workspace_id,
        observed_file_size_bytes=payload.observed_file_size_bytes,
    )

    job = await run_blocking(
        preprocess_queue.enqueue_video_preprocess,
        video_id=upload.id,
        project_id=upload.project_id,
        workspace_id=upload.workspace_id,
    )
    return UploadCompleteResponse(video_id=upload.id, status=upload.status, job_id=job.id)


@router.post("/{video_id}/cancel", response_model=UploadReadResponse)
async def cancel_upload(
    project_id: UUID,
    video_id: UUID,
    actor: ActorDep,
    repository: UploadRepositoryDep,
) -> UploadReadResponse:
    upload = await _repository_call(
        repository,
        "get_upload_for_project_async",
        "get_upload_for_project",
        project_id=project_id,
        video_id=video_id,
        workspace_id=actor.workspace_id,
    )
    if upload.status == UploadStatus.FAILED:
        return upload.to_response()
    if upload.status in {UploadStatus.PROCESSING, UploadStatus.READY}:
        raise ApiError(
            409,
            "upload_not_cancelable",
            "This upload is already being processed and can no longer be canceled.",
        )

    upload = await _repository_call(
        repository,
        "mark_failed_async",
        "mark_failed",
        upload=upload,
        error_code="upload_canceled",
        error_message="Upload canceled before the source video was completed.",
    )
    return upload.to_response()


@router.get("/active", response_model=UploadReadResponse)
async def get_active_upload(
    project_id: UUID,
    actor: ActorDep,
    repository: UploadRepositoryDep,
) -> UploadReadResponse:
    upload = await _repository_call(
        repository,
        "get_active_upload_async",
        "get_active_upload",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    return upload.to_response()


@router.get("/{video_id}", response_model=UploadReadResponse)
async def get_upload(
    project_id: UUID,
    video_id: UUID,
    actor: ActorDep,
    repository: UploadRepositoryDep,
) -> UploadReadResponse:
    upload = await _repository_call(
        repository,
        "get_upload_for_project_async",
        "get_upload_for_project",
        project_id=project_id,
        video_id=video_id,
        workspace_id=actor.workspace_id,
    )
    return upload.to_response()


@router.get("/{video_id}/asset-urls", response_model=UploadAssetUrlsResponse)
async def get_upload_asset_urls(
    project_id: UUID,
    video_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: UploadRepositoryDep,
    storage_signer: StorageSignerDep,
) -> UploadAssetUrlsResponse:
    upload = await _repository_call(
        repository,
        "get_upload_for_project_async",
        "get_upload_for_project",
        project_id=project_id,
        video_id=video_id,
        workspace_id=actor.workspace_id,
    )
    if upload.status in {UploadStatus.CREATED, UploadStatus.UPLOADING}:
        raise ApiError(
            409,
            "source_video_not_uploaded",
            "Finish the source video upload before opening a preview.",
        )

    ttl = min(
        request.app.state.settings.upload_signed_url_ttl_seconds,
        StorageSigner.MAX_DOWNLOAD_TTL_SECONDS,
    )
    source = await run_blocking(
        storage_signer.create_signed_download,
        bucket=upload.storage_bucket,
        path=upload.storage_path,
        expires_in_seconds=ttl,
    )
    thumbnail_url = None
    if upload.thumbnail_path:
        try:
            thumbnail = await run_blocking(
                storage_signer.create_signed_download,
                bucket="thumbnails",
                path=_thumbnail_object_path(upload.thumbnail_path),
                expires_in_seconds=ttl,
            )
        except Exception:
            thumbnail = None
        if thumbnail is not None:
            thumbnail_url = thumbnail.url

    return UploadAssetUrlsResponse(
        video_id=upload.id,
        source_video_url=source.url,
        thumbnail_url=thumbnail_url,
        expires_in_seconds=ttl,
    )


def _thumbnail_object_path(thumbnail_path: str) -> str:
    return thumbnail_path.removeprefix("thumbnails/")
