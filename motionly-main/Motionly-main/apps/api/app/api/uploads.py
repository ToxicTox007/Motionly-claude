from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    Actor,
    get_actor,
    get_storage_signer,
    get_upload_repository,
    get_upload_session_service,
    get_workflow_repository,
)
from app.api.errors import ApiError
from app.core.logging import get_logger
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.workflows import InMemoryWorkflowRepository, SupabaseWorkflowRepository
from app.schemas.multipart_uploads import (
    MultipartCompleteRequest,
    MultipartCompleteResponse,
    MultipartUploadInitRequest,
    MultipartUploadInitResponse,
    PartCompleteRequest,
    PartCompleteResponse,
    PartUploadUrl,
    UploadSessionResponse,
)
from app.schemas.uploads import (
    UploadAssetUrlsResponse,
    UploadReadResponse,
    UploadStatus,
)
from app.services.object_references import ObjectReferenceService
from app.services.offload import run_blocking
from app.services.storage import StorageSigner
from app.services.upload_session import UploadSession, UploadSessionService

router = APIRouter(prefix="/projects/{project_id}/uploads", tags=["uploads"])
logger = get_logger(__name__)

ActorDep = Annotated[Actor, Depends(get_actor)]
UploadRepositoryDep = Annotated[InMemoryUploadRepository, Depends(get_upload_repository)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]
UploadSessionServiceDep = Annotated[UploadSessionService, Depends(get_upload_session_service)]
WorkflowRepositoryDep = Annotated[
    InMemoryWorkflowRepository | SupabaseWorkflowRepository,
    Depends(get_workflow_repository),
]


async def _multipart_session_response(
    session: UploadSession,
    service: UploadSessionService,
) -> UploadSessionResponse:
    pending_urls = []
    for part_number, signed in await service.create_pending_part_urls(session):
        pending_urls.append(
            PartUploadUrl(
                part_number=part_number,
                upload_url=signed.url,
                upload_headers=signed.headers,
                expires_in_seconds=signed.expires_in_seconds,
            )
        )
    return UploadSessionResponse(
        session_id=session.id,
        video_id=session.uploaded_video_id,
        status=session.status,
        total_parts=session.part_count,
        completed_parts=session.completed_parts,
        uploaded_bytes=session.uploaded_bytes,
        file_size_bytes=session.declared_size_bytes,
        part_size_bytes=session.part_size_bytes,
        pending_part_urls=pending_urls,
        expires_at=session.expires_at,
        verified_size_bytes=session.verified_size_bytes,
        verified_sha256=session.verified_sha256,
        source_reference_id=session.source_reference_id,
    )


@router.post("/multipart/init", response_model=MultipartUploadInitResponse, status_code=201)
async def init_multipart_upload(
    project_id: UUID,
    payload: MultipartUploadInitRequest,
    request: Request,
    actor: ActorDep,
    service: UploadSessionServiceDep,
) -> MultipartUploadInitResponse:
    settings = request.app.state.settings
    session = await service.initiate(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        filename=payload.filename,
        mime_type=payload.mime_type,
        file_size_bytes=payload.file_size_bytes,
        storage_bucket=settings.upload_storage_bucket,
        max_file_size_bytes=settings.upload_max_file_size_bytes,
        max_active_uploads=settings.upload_max_concurrent_per_workspace,
        sha256_hash=payload.sha256_hash,
    )
    part_urls = [
        PartUploadUrl(
            part_number=part_number,
            upload_url=signed.url,
            upload_headers=signed.headers,
            expires_in_seconds=signed.expires_in_seconds,
        )
        for part_number, signed in await service.create_pending_part_urls(session)
    ]
    return MultipartUploadInitResponse(
        video_id=session.uploaded_video_id,
        session_id=session.id,
        status=session.status,
        total_parts=session.part_count,
        part_size_bytes=session.part_size_bytes,
        part_urls=part_urls,
        expires_at=session.expires_at,
        warnings=session.warnings,
    )


@router.get("/multipart/{session_id}", response_model=UploadSessionResponse)
async def get_multipart_upload(
    project_id: UUID,
    session_id: UUID,
    actor: ActorDep,
    service: UploadSessionServiceDep,
) -> UploadSessionResponse:
    session = await service.resume(
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    return await _multipart_session_response(session, service)


@router.post(
    "/multipart/{session_id}/parts/{part_number}/complete",
    response_model=PartCompleteResponse,
)
async def confirm_multipart_part(
    project_id: UUID,
    session_id: UUID,
    part_number: int,
    payload: PartCompleteRequest,
    actor: ActorDep,
    service: UploadSessionServiceDep,
) -> PartCompleteResponse:
    confirmation = await service.confirm_part(
        session_id=session_id,
        part_number=part_number,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        etag=payload.etag,
    )
    return PartCompleteResponse(
        session_id=confirmation.session_id,
        part_number=confirmation.part_number,
        status="uploaded",
        verified_size_bytes=confirmation.verified_size_bytes,
        completed_parts=confirmation.completed_parts,
        uploaded_bytes=confirmation.uploaded_bytes,
    )


@router.post(
    "/multipart/{session_id}/complete",
    response_model=MultipartCompleteResponse,
)
async def complete_multipart_upload(
    project_id: UUID,
    session_id: UUID,
    payload: MultipartCompleteRequest,
    actor: ActorDep,
    service: UploadSessionServiceDep,
    workflow_repository: WorkflowRepositoryDep,
) -> MultipartCompleteResponse:
    completion = await service.complete(
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        sha256_hash=payload.sha256_hash,
    )
    ensure_async = getattr(workflow_repository, "ensure_first_draft_v2_async", None)
    ensure_values = {
        "workspace_id": completion.upload.workspace_id,
        "project_id": completion.upload.project_id,
        "uploaded_video_id": completion.upload.id,
        "source_media_revision": completion.upload.media_revision,
        "source_object_reference_id": completion.reference.id,
        "started_by": actor.user_id,
    }
    if ensure_async is not None:
        workflow, _ = await ensure_async(**ensure_values)
    else:
        workflow, _ = await run_blocking(
            workflow_repository.ensure_first_draft_v2,
            **ensure_values,
        )
    return MultipartCompleteResponse(
        session_id=completion.session.id,
        video_id=completion.upload.id,
        status=completion.session.status,
        source_reference_id=completion.reference.id,
        verified_size_bytes=completion.session.verified_size_bytes or 0,
        verified_sha256=completion.session.verified_sha256 or "",
        job_id=None,
        workflow_run_id=workflow.id,
        workflow_version=workflow.workflow_version,
        workflow_state=workflow.state.value,
        workflow_status_url=f"/projects/{project_id}/workflows/{workflow.id}",
    )


@router.post("/multipart/{session_id}/abort", response_model=UploadSessionResponse)
async def abort_multipart_upload(
    project_id: UUID,
    session_id: UUID,
    actor: ActorDep,
    service: UploadSessionServiceDep,
) -> UploadSessionResponse:
    session = await service.abort(
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    return await _multipart_session_response(session, service)


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return await run_blocking(getattr(repository, sync_name), **kwargs)


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
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
    source_reference_id = None
    thumbnail_reference_id = None
    source_url = None
    thumbnail_url = None
    reference_service: ObjectReferenceService | None = getattr(
        request.app.state, "object_reference_service", None
    )
    if reference_service is not None:
        if upload.source_object_reference_id is not None:
            source_reference_id = upload.source_object_reference_id
        else:
            source_reference = await reference_service.create_reference(
                workspace_id=actor.workspace_id,
                project_id=project_id,
                storage_bucket=upload.storage_bucket,
                storage_path=upload.storage_path,
                owner_type="uploaded_video",
                owner_id=upload.id,
                object_type="source_video",
                mime_type=upload.mime_type,
                size_bytes=upload.verified_size_bytes or upload.file_size_bytes,
                sha256=upload.verified_sha256,
            )
            source_reference_id = source_reference.id
            upload = await _repository_call(
                repository,
                "attach_source_reference_async",
                "attach_source_reference",
                upload=upload,
                source_object_reference_id=source_reference.id,
            )
        signed_source = await reference_service.get_signed_url(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            reference_id=source_reference_id,
            purpose="browser_preview",
            principal_id=actor.user_id,
        )
        source_url = signed_source.url
        expires_at = signed_source.expires_at
    else:
        source = await run_blocking(
            storage_signer.create_signed_download,
            bucket=upload.storage_bucket,
            path=upload.storage_path,
            expires_in_seconds=ttl,
        )
        source_url = source.url

    if upload.thumbnail_path:
        thumbnail_path = _thumbnail_object_path(upload.thumbnail_path)
        if reference_service is not None:
            try:
                thumbnail_reference = await reference_service.create_reference(
                    workspace_id=actor.workspace_id,
                    project_id=project_id,
                    storage_bucket="thumbnails",
                    storage_path=thumbnail_path,
                    owner_type="uploaded_video",
                    owner_id=upload.id,
                    object_type="thumbnail",
                    mime_type="image/jpeg",
                )
                signed_thumbnail = await reference_service.get_signed_url(
                    workspace_id=actor.workspace_id,
                    project_id=project_id,
                    reference_id=thumbnail_reference.id,
                    purpose="browser_preview",
                    principal_id=actor.user_id,
                )
            except Exception:
                logger.warning(
                    "upload_thumbnail_signing_failed",
                    extra={"video_id": str(upload.id)},
                )
            else:
                thumbnail_url = signed_thumbnail.url
                thumbnail_reference_id = thumbnail_reference.id
        else:
            try:
                thumbnail = await run_blocking(
                    storage_signer.create_signed_download,
                    bucket="thumbnails",
                    path=thumbnail_path,
                    expires_in_seconds=ttl,
                )
            except Exception:
                thumbnail = None
            if thumbnail is not None:
                thumbnail_url = thumbnail.url

    return UploadAssetUrlsResponse(
        video_id=upload.id,
        source_video_url=source_url,
        thumbnail_url=thumbnail_url,
        expires_in_seconds=ttl,
        expires_at=expires_at,
        source_reference_id=source_reference_id,
        thumbnail_reference_id=thumbnail_reference_id,
    )


def _thumbnail_object_path(thumbnail_path: str) -> str:
    return thumbnail_path.removeprefix("thumbnails/")
