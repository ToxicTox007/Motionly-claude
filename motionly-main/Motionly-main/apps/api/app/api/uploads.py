import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    Actor,
    get_actor,
    get_preprocess_queue,
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
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadReadResponse,
    UploadStatus,
)
from app.services.object_references import ObjectReferenceService
from app.services.offload import run_blocking
from app.services.queue import PreprocessQueue
from app.services.storage import StorageSigner
from app.services.storage_quota import StorageQuotaService
from app.services.upload_session import UploadSession, UploadSessionService
from app.services.upload_validation import validate_upload_preflight

router = APIRouter(prefix="/projects/{project_id}/uploads", tags=["uploads"])
logger = get_logger(__name__)

ActorDep = Annotated[Actor, Depends(get_actor)]
UploadRepositoryDep = Annotated[InMemoryUploadRepository, Depends(get_upload_repository)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]
PreprocessQueueDep = Annotated[PreprocessQueue, Depends(get_preprocess_queue)]
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


async def _ensure_first_draft_workflow(
    *,
    workflow_repository: InMemoryWorkflowRepository | SupabaseWorkflowRepository,
    workspace_id: UUID,
    project_id: UUID,
    uploaded_video_id: UUID,
    source_media_revision: int,
    source_object_reference_id: UUID,
    started_by: UUID,
):
    """Start the sole durable first-draft workflow for a finalized source object."""
    values = {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "uploaded_video_id": uploaded_video_id,
        "source_media_revision": source_media_revision,
        "source_object_reference_id": source_object_reference_id,
        "started_by": started_by,
    }
    ensure_async = getattr(workflow_repository, "ensure_first_draft_v2_async", None)
    if ensure_async is not None:
        workflow, _ = await ensure_async(**values)
    else:
        workflow, _ = await run_blocking(
            workflow_repository.ensure_first_draft_v2,
            **values,
        )
    return workflow


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
        expires_in_seconds=getattr(
            signed_upload,
            "expires_in_seconds",
            settings.upload_signed_url_ttl_seconds,
        ),
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
    workflow_repository: WorkflowRepositoryDep,
) -> UploadCompleteResponse:
    # Kept as an injected compatibility parameter while direct preprocessing is
    # removed. Every accepted source must now be owned by first-draft-v2.
    del preprocess_queue
    state = request.app.state
    workflow_repository = workflow_repository or getattr(state, "workflow_repository", None)
    durable_services_available = all(
        hasattr(state, name)
        for name in ("object_storage", "storage_quota_service", "object_reference_service")
    )
    if workflow_repository is None and getattr(state.settings, "app_env", "production") == "test":
        workflow_repository = InMemoryWorkflowRepository()
    if workflow_repository is None:
        raise ApiError(
            503,
            "workflow_repository_unavailable",
            "Upload completion requires durable workflow orchestration.",
        )
    if (
        not durable_services_available
        and getattr(state.settings, "app_env", "production") != "test"
    ):
        raise ApiError(
            503,
            "durable_upload_services_unavailable",
            "Upload completion requires durable source verification and workflow orchestration.",
        )

    upload = await _repository_call(
        repository,
        "get_upload_for_project_async",
        "get_upload_for_project",
        project_id=project_id,
        video_id=video_id,
        workspace_id=actor.workspace_id,
    )
    authoritative_size = payload.observed_file_size_bytes
    authoritative_hash = None
    object_storage = getattr(request.app.state, "object_storage", None)
    if object_storage is not None:
        metadata = await run_blocking(
            object_storage.inspect_object,
            bucket=upload.storage_bucket,
            path=upload.storage_path,
            compute_sha256=True,
        )
        authoritative_size = metadata.size_bytes
        authoritative_hash = metadata.sha256
        if authoritative_size != upload.file_size_bytes:
            raise ApiError(
                409,
                "upload_size_mismatch",
                "Stored upload size did not match preflight.",
            )

    quota_service: StorageQuotaService | None = getattr(
        request.app.state, "storage_quota_service", None
    )
    reference_service: ObjectReferenceService | None = getattr(
        request.app.state, "object_reference_service", None
    )
    if (
        quota_service is not None
        and reference_service is not None
        and authoritative_size is not None
        and authoritative_hash is not None
    ):
        reservation_id = uuid5(NAMESPACE_URL, f"motionly:legacy-upload:{video_id}")
        reservation_expires_at = upload.legacy_upload_expires_at or upload.created_at + timedelta(
            hours=24
        )
        if (
            upload.legacy_quota_reservation_id is not None
            and upload.legacy_quota_reservation_id != reservation_id
        ):
            raise ApiError(
                409,
                "legacy_upload_reservation_conflict",
                "Legacy upload reservation identity changed.",
            )
        reservation_state = await quota_service.get_reservation_state(reservation_id)
        quota_committed = reservation_state == "committed"
        if reservation_expires_at <= datetime.now(UTC) and not quota_committed:
            raise ApiError(
                409,
                "legacy_upload_expired",
                "This legacy upload can no longer be completed. Start a resumable upload.",
            )

        atomic_claim = getattr(repository, "claim_legacy_upload_atomic_async", None)
        if atomic_claim is not None:
            claimed = await atomic_claim(
                upload=upload,
                reservation_id=reservation_id,
                byte_amount=authoritative_size,
                expires_at=reservation_expires_at,
            )
            if not claimed:
                raise ApiError(
                    409,
                    "upload_completion_conflict",
                    "Upload completion lost a race with cancellation or quota admission.",
                )
        else:
            if upload.legacy_quota_reservation_id is None:
                upload = await _repository_call(
                    repository,
                    "record_legacy_completion_reservation_async",
                    "record_legacy_completion_reservation",
                    upload=upload,
                    reservation_id=reservation_id,
                    expires_at=reservation_expires_at,
                )
            if not quota_committed:
                reservation = await quota_service.reserve(
                    reservation_id=reservation_id,
                    workspace_id=actor.workspace_id,
                    byte_amount=authoritative_size,
                    object_amount=1,
                    expires_at=reservation_expires_at,
                )
                if not reservation.allowed:
                    raise ApiError(
                        409,
                        "storage_quota_exceeded",
                        reservation.reason or "Storage quota would be exceeded.",
                    )

        reference = await reference_service.create_reference(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            storage_bucket=upload.storage_bucket,
            storage_path=upload.storage_path,
            owner_type="uploaded_video",
            owner_id=upload.id,
            object_type="source_video",
            mime_type=upload.mime_type,
            size_bytes=authoritative_size,
            sha256=authoritative_hash,
        )
        atomic_finalize = getattr(
            repository,
            "finalize_legacy_upload_atomic_async",
            None,
        )
        if atomic_finalize is not None:
            finalized = await atomic_finalize(
                upload=upload,
                reservation_id=reservation_id,
                reference_id=reference.id,
                verified_size_bytes=authoritative_size,
                verified_sha256=authoritative_hash,
            )
            if not finalized:
                atomic_cancel = getattr(
                    repository,
                    "cancel_legacy_upload_atomic_async",
                    None,
                )
                if atomic_cancel is not None:
                    await atomic_cancel(
                        upload=upload,
                        reservation_id=reservation_id,
                    )
                await reference_service.mark_deleted(
                    workspace_id=actor.workspace_id,
                    project_id=project_id,
                    reference_id=reference.id,
                )
                raise ApiError(
                    409,
                    "upload_completion_conflict",
                    "Upload completion lost a race with cancellation or expiry.",
                )
            upload = await _repository_call(
                repository,
                "get_upload_for_project_async",
                "get_upload_for_project",
                project_id=project_id,
                video_id=video_id,
                workspace_id=actor.workspace_id,
            )
        else:
            upload = await _repository_call(
                repository,
                "record_verified_source_async",
                "record_verified_source",
                upload=upload,
                source_object_reference_id=reference.id,
                verified_size_bytes=authoritative_size,
                verified_sha256=authoritative_hash,
            )
            if not quota_committed and not await quota_service.commit(reservation_id):
                raise ApiError(
                    409,
                    "quota_reservation_invalid",
                    "Upload quota could not be committed.",
                )
    elif getattr(state.settings, "app_env", "production") == "test":
        # Direct route calls in unit tests do not upload an object to a real
        # backend. Preserve the upload-size invariant and record explicitly
        # synthetic evidence; production always requires the branch above.
        if authoritative_size != upload.file_size_bytes:
            raise ApiError(
                409,
                "upload_size_mismatch",
                "Stored upload size did not match preflight.",
            )
        synthetic_source_id = uuid5(NAMESPACE_URL, f"motionly:test-source:{upload.id}")
        synthetic_hash = hashlib.sha256(
            f"motionly:test-upload:{upload.id}:{authoritative_size}".encode()
        ).hexdigest()
        upload = await _repository_call(
            repository,
            "record_verified_source_async",
            "record_verified_source",
            upload=upload,
            source_object_reference_id=synthetic_source_id,
            verified_size_bytes=authoritative_size,
            verified_sha256=synthetic_hash,
        )

    upload = await _repository_call(
        repository,
        "complete_upload_for_processing_async",
        "complete_upload_for_processing",
        project_id=project_id,
        video_id=video_id,
        workspace_id=actor.workspace_id,
        observed_file_size_bytes=authoritative_size,
    )

    if (
        upload.source_object_reference_id is None
        or upload.verified_size_bytes is None
        or not upload.verified_sha256
    ):
        raise ApiError(
            409,
            "durable_source_verification_missing",
            "Upload completion requires a verified durable source reference.",
        )
    upload = await _repository_call(
        repository,
        "mark_workflow_owned_async",
        "mark_workflow_owned",
        upload=upload,
        workflow_version="first-draft-v2",
    )
    workflow = await _ensure_first_draft_workflow(
        workflow_repository=workflow_repository,
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        uploaded_video_id=upload.id,
        source_media_revision=upload.media_revision,
        source_object_reference_id=upload.source_object_reference_id,
        started_by=actor.user_id,
    )
    return UploadCompleteResponse(
        video_id=upload.id,
        status=upload.status,
        job_id=None,
        workflow_run_id=workflow.id,
        workflow_version=workflow.workflow_version,
        workflow_state=workflow.state.value,
        workflow_status_url=f"/projects/{upload.project_id}/workflows/{workflow.id}",
    )


async def _delete_legacy_upload_object(request: Request, upload) -> None:
    object_storage = getattr(request.app.state, "object_storage", None)
    if object_storage is not None:
        await run_blocking(
            object_storage.delete_object,
            bucket=upload.storage_bucket,
            path=upload.storage_path,
        )
        return

    storage_signer = getattr(request.app.state, "storage_signer", None)
    if storage_signer is not None:
        await run_blocking(
            storage_signer.delete_object,
            bucket=upload.storage_bucket,
            path=upload.storage_path,
        )


async def _cleanup_legacy_upload_cancellation(request: Request, upload) -> None:
    reservation_id = upload.legacy_quota_reservation_id or uuid5(
        NAMESPACE_URL,
        f"motionly:legacy-upload:{upload.id}",
    )
    quota_service: StorageQuotaService | None = getattr(
        request.app.state, "storage_quota_service", None
    )
    if quota_service is not None:
        reservation_state = await quota_service.get_reservation_state(reservation_id)
        if reservation_state == "committed":
            raise ApiError(
                409,
                "upload_not_cancelable",
                "This upload has been accepted. Retry completion instead of canceling it.",
            )
        if reservation_state == "reserved":
            await quota_service.release_reservation(reservation_id)

    reference_service: ObjectReferenceService | None = getattr(
        request.app.state, "object_reference_service", None
    )
    if reference_service is not None and upload.source_object_reference_id is not None:
        await reference_service.mark_deleted(
            workspace_id=upload.workspace_id,
            project_id=upload.project_id,
            reference_id=upload.source_object_reference_id,
        )
    await _delete_legacy_upload_object(request, upload)


@router.post("/{video_id}/cancel", response_model=UploadReadResponse)
async def cancel_upload(
    project_id: UUID,
    video_id: UUID,
    request: Request,
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
    reservation_id = upload.legacy_quota_reservation_id or uuid5(
        NAMESPACE_URL,
        f"motionly:legacy-upload:{video_id}",
    )
    atomic_cancel = getattr(repository, "cancel_legacy_upload_atomic_async", None)
    if upload.status == UploadStatus.FAILED:
        if upload.error_code == "upload_canceled":
            if atomic_cancel is not None:
                await atomic_cancel(upload=upload, reservation_id=reservation_id)
                await _delete_legacy_upload_object(request, upload)
            else:
                # A previous request may have persisted cancellation before one
                # of its idempotent cleanup steps completed.
                await _cleanup_legacy_upload_cancellation(request, upload)
        return upload.to_response()
    if upload.status in {UploadStatus.PROCESSING, UploadStatus.READY}:
        raise ApiError(
            409,
            "upload_not_cancelable",
            "This upload is already being processed and can no longer be canceled.",
        )

    if atomic_cancel is not None:
        canceled = await atomic_cancel(upload=upload, reservation_id=reservation_id)
        if not canceled:
            raise ApiError(
                409,
                "upload_not_cancelable",
                "This upload has been accepted and can no longer be canceled.",
            )
        upload = await _repository_call(
            repository,
            "get_upload_for_project_async",
            "get_upload_for_project",
            project_id=project_id,
            video_id=video_id,
            workspace_id=actor.workspace_id,
        )
        await _delete_legacy_upload_object(request, upload)
        return upload.to_response()

    quota_service: StorageQuotaService | None = getattr(
        request.app.state, "storage_quota_service", None
    )
    if (
        quota_service is not None
        and await quota_service.get_reservation_state(reservation_id) == "committed"
    ):
        raise ApiError(
            409,
            "upload_not_cancelable",
            "This upload has been accepted. Retry completion instead of canceling it.",
        )

    upload = await _repository_call(
        repository,
        "mark_failed_async",
        "mark_failed",
        upload=upload,
        error_code="upload_canceled",
        error_message="Upload canceled before the source video was completed.",
    )
    await _cleanup_legacy_upload_cancellation(request, upload)
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
