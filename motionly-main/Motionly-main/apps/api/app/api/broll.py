from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.billing import build_billing_status
from app.api.dependencies import (
    Actor,
    get_actor,
    get_billing_repository,
    get_broll_queue,
    get_broll_repository,
    get_stock_asset_enrichment_queue,
    get_stock_video_providers,
    get_storage_signer,
)
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer
from app.schemas.broll import (
    BROLL_SCHEMA_VERSION,
    BrollGenerateRequest,
    BrollGenerateResponse,
    BrollListResponse,
    BrollRegionUpdateRequest,
    BrollRegionUpdateResponse,
    BrollSearchResponse,
    BrollSwapRequest,
    BrollSwapResponse,
    StockOrientation,
    StockProviderKey,
    UserBrollDeleteResponse,
    UserBrollLibraryResponse,
    UserBrollMultipartInitResponse,
    UserBrollUploadCompleteResponse,
    UserBrollUploadInitRequest,
)
from app.schemas.media_uploads import (
    MediaUploadCompleteRequest,
    MediaUploadSessionResponse,
    PartCompleteRequest,
    PartCompleteResponse,
    PartUploadUrl,
)
from app.services.ai_feedback import record_ai_feedback
from app.services.broll_intelligence import (
    apply_stage4_broll_rules,
    latest_visual_manifest_for_broll,
    visual_manifest_cache_signature,
)
from app.services.layer_job_enqueue import (
    build_queued_layer_response_fields,
    prepare_heavy_job_enqueue,
    serialize_layer_job_payload,
)
from app.services.object_references import ObjectReferenceService
from app.services.queue import BrollQueue, StockAssetEnrichmentQueue
from app.services.stock_video import (
    StockVideoCandidate,
    StockVideoProvider,
    build_broll_cache_key,
    build_stock_asset_raw_metadata,
    desired_requests,
    enqueue_stock_asset_enrichment,
    ensure_owned_stock_asset,
    query_embedding,
    score_candidate_details,
)
from app.services.storage import StorageSigner

router = APIRouter(prefix="/projects/{project_id}/broll", tags=["B-roll"])

ActorDep = Annotated[Actor, Depends(get_actor)]
BrollRepositoryDep = Annotated[InMemoryBrollRepository, Depends(get_broll_repository)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
BrollQueueDep = Annotated[BrollQueue, Depends(get_broll_queue)]
StockAssetEnrichmentQueueDep = Annotated[
    StockAssetEnrichmentQueue, Depends(get_stock_asset_enrichment_queue)
]
StockProvidersDep = Annotated[list[StockVideoProvider], Depends(get_stock_video_providers)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


async def _response_for_slot(repository: InMemoryBrollRepository, slot):
    async_method = getattr(repository, "response_for_slot_async", None)
    if async_method is not None:
        return await async_method(slot)
    return repository.response_for_slot(slot)


def _media_service(request: Request):
    service = getattr(request.app.state, "media_upload_session_service", None)
    if service is None:
        raise ApiError(
            503,
            "media_upload_session_unavailable",
            "B-roll uploads require the server-verified media upload service.",
        )
    return service


def _media_references(request: Request) -> ObjectReferenceService:
    service = getattr(request.app.state, "object_reference_service", None)
    if service is None:
        raise ApiError(503, "object_reference_unavailable", "Media references are unavailable.")
    return service


async def _broll_session_response(session, service) -> MediaUploadSessionResponse:
    pending = [
        PartUploadUrl(
            part_number=number,
            upload_url=signed.url,
            upload_headers=signed.headers,
            expires_in_seconds=signed.expires_in_seconds,
        )
        for number, signed in await service.create_pending_part_urls(session)
    ]
    return MediaUploadSessionResponse(
        session_id=session.id,
        owner_id=session.owner_id,
        status=session.status,
        total_parts=session.part_count,
        completed_parts=session.completed_parts,
        uploaded_bytes=session.uploaded_bytes,
        file_size_bytes=session.declared_size_bytes,
        part_size_bytes=session.part_size_bytes,
        pending_part_urls=pending,
        expires_at=session.expires_at,
        object_reference_id=session.object_reference_id,
        verified_size_bytes=session.verified_size_bytes,
        verified_sha256=session.verified_sha256,
    )


async def _require_owned_broll_session(
    *,
    service,
    repository: InMemoryBrollRepository,
    session_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
):
    """Resolve an owner-bound B-roll session before any mutation."""
    session = await service.inspect(
        session_id=session_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if session.owner_type != "stock_asset" or session.object_type != "broll_asset":
        raise ApiError(
            404, "media_upload_session_not_found", "B-roll upload session was not found."
        )
    asset = await _repository_call(
        repository,
        "get_asset_async",
        "get_asset",
        project_id=project_id,
        workspace_id=workspace_id,
        asset_id=session.owner_id,
    )
    if (
        asset.provider != StockProviderKey.USER_LIBRARY
        or asset.media_upload_session_id != session.id
    ):
        raise ApiError(
            404, "media_upload_session_not_found", "B-roll upload session was not found."
        )
    return session, asset


async def _init_broll_media_upload(
    *,
    project_id: UUID,
    payload: UserBrollUploadInitRequest,
    request: Request,
    actor: Actor,
    repository: InMemoryBrollRepository,
):
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    suffix = Path(payload.filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".m4v"}:
        raise ApiError(
            422, "library_video_type_unsupported", "Upload MP4, MOV, M4V, or WebM video."
        )
    orientation = (
        StockOrientation.PORTRAIT
        if payload.height > payload.width
        else StockOrientation.LANDSCAPE
        if payload.width > payload.height
        else StockOrientation.SQUARE
    )
    # The stock row is the target owner.  It deliberately contains no bucket or
    # path; the generic session and ObjectReference remain the only authority.
    asset = await _repository_call(
        repository,
        "upsert_asset_async",
        "upsert_asset",
        workspace_id=actor.workspace_id,
        project_id=project_id,
        provider=StockProviderKey.USER_LIBRARY,
        provider_asset_id=str(uuid4()),
        title=Path(payload.filename).stem[:160] or "Library clip",
        thumbnail_url=None,
        preview_video_url=None,
        download_url=None,
        license="user_owned",
        width=payload.width,
        height=payload.height,
        duration_ms=payload.duration_ms,
        orientation=orientation,
        score=1.0,
        raw_metadata={"mime_type": payload.mime_type, "original_filename": payload.filename},
        media_state="uploading",
    )
    service = _media_service(request)
    path = f"workspaces/{actor.workspace_id}/projects/{project_id}/broll/{asset.id}{suffix}"
    try:
        session = await service.initiate(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            owner_type="stock_asset",
            owner_id=asset.id,
            object_type="broll_asset",
            final_storage_bucket=request.app.state.settings.upload_storage_bucket,
            final_storage_path=path,
            filename=payload.filename,
            mime_type=payload.mime_type,
            file_size_bytes=payload.file_size_bytes,
            sha256_hash=payload.sha256_hash,
        )
    except Exception:
        await _repository_call(
            repository,
            "mark_library_asset_media_failed_async",
            "mark_library_asset_media_failed",
            asset=asset,
        )
        raise
    try:
        asset = await _repository_call(
            repository,
            "set_library_media_session_async",
            "set_library_media_session",
            asset=asset,
            media_upload_session_id=session.id,
        )
    except Exception:
        # A lost PATCH response can still have linked the owner. Re-read before
        # compensating so we never abort a correctly linked upload.
        try:
            reconciled = await _repository_call(
                repository,
                "get_asset_async",
                "get_asset",
                project_id=project_id,
                workspace_id=actor.workspace_id,
                asset_id=asset.id,
            )
            if reconciled.media_upload_session_id == session.id:
                return reconciled, session
        except Exception:
            pass
        try:
            await service.abort(
                session_id=session.id,
                workspace_id=actor.workspace_id,
                project_id=project_id,
            )
        finally:
            try:
                await _repository_call(
                    repository,
                    "mark_library_asset_media_failed_async",
                    "mark_library_asset_media_failed",
                    asset=asset,
                )
            except Exception:
                # The durable generic session remains terminal/retryable even if the
                # cosmetic owner-state repair must be retried by a later request.
                pass
        raise
    return asset, session


async def _complete_broll_media_upload(
    *,
    project_id: UUID,
    asset_id: UUID,
    sha256_hash: str | None,
    request: Request,
    actor: Actor,
    repository: InMemoryBrollRepository,
) -> UserBrollUploadCompleteResponse:
    asset = await _repository_call(
        repository,
        "get_asset_async",
        "get_asset",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        asset_id=asset_id,
    )
    if asset.provider != StockProviderKey.USER_LIBRARY:
        raise ApiError(409, "library_asset_required", "This is not a user library asset.")
    if asset.media_upload_session_id is None:
        raise ApiError(
            409, "library_upload_session_required", "Start a verified library upload first."
        )
    service = _media_service(request)
    session, _ = await _require_owned_broll_session(
        service=service,
        repository=repository,
        session_id=asset.media_upload_session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    if session.owner_id != asset.id:
        raise ApiError(409, "library_upload_session_invalid", "Library upload session is invalid.")
    completion = await service.complete(
        session_id=session.id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        sha256_hash=sha256_hash,
    )
    session = completion.session
    try:
        asset = await _repository_call(
            repository,
            "mark_library_asset_verified_async",
            "mark_library_asset_verified",
            asset=asset,
            object_reference_id=completion.reference.id,
            media_upload_session_id=session.id,
            verified_size_bytes=session.verified_size_bytes or completion.reference.size_bytes or 0,
            verified_sha256=session.verified_sha256 or completion.reference.sha256 or "",
            storage_quota_reservation_id=session.quota_reservation_id,
        )
    except Exception as exc:
        # Completion is durable and idempotent. If an owner PATCH response was lost,
        # recover its result; otherwise leave the linked session in place so the same
        # completion request can safely reconcile it on retry.
        try:
            reconciled = await _repository_call(
                repository,
                "get_asset_async",
                "get_asset",
                project_id=project_id,
                workspace_id=actor.workspace_id,
                asset_id=asset.id,
            )
            if (
                reconciled.media_state == "verified"
                and reconciled.media_upload_session_id == session.id
                and reconciled.object_reference_id == completion.reference.id
            ):
                return UserBrollUploadCompleteResponse(asset=reconciled.to_response())
        except Exception:
            pass
        raise ApiError(
            503,
            "library_media_reconciliation_required",
            "Media completed, but the library record must be reconciled by retrying.",
        ) from exc
    return UserBrollUploadCompleteResponse(asset=asset.to_response())


@router.post(
    "/library/multipart/init", response_model=UserBrollMultipartInitResponse, status_code=201
)
async def init_user_broll_multipart_upload(
    project_id: UUID,
    payload: UserBrollUploadInitRequest,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
) -> UserBrollMultipartInitResponse:
    asset, session = await _init_broll_media_upload(
        project_id=project_id,
        payload=payload,
        request=request,
        actor=actor,
        repository=repository,
    )
    return UserBrollMultipartInitResponse(
        asset=asset.to_response(),
        session=await _broll_session_response(session, _media_service(request)),
    )


@router.get("/library/multipart/{session_id}", response_model=MediaUploadSessionResponse)
async def get_user_broll_multipart_upload(
    project_id: UUID,
    session_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
) -> MediaUploadSessionResponse:
    service = _media_service(request)
    await _require_owned_broll_session(
        service=service,
        repository=repository,
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    session = await service.resume(
        session_id=session_id, workspace_id=actor.workspace_id, project_id=project_id
    )
    return await _broll_session_response(session, service)


@router.post(
    "/library/multipart/{session_id}/parts/{part_number}/complete",
    response_model=PartCompleteResponse,
)
async def confirm_user_broll_multipart_part(
    project_id: UUID,
    session_id: UUID,
    part_number: int,
    payload: PartCompleteRequest,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
) -> PartCompleteResponse:
    service = _media_service(request)
    await _require_owned_broll_session(
        service=service,
        repository=repository,
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    confirmed = await service.confirm_part(
        session_id=session_id,
        part_number=part_number,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        etag=payload.etag,
    )
    return PartCompleteResponse(
        session_id=confirmed.session_id,
        part_number=confirmed.part_number,
        status="uploaded",
        verified_size_bytes=confirmed.verified_size_bytes,
        completed_parts=confirmed.completed_parts,
        uploaded_bytes=confirmed.uploaded_bytes,
    )


@router.post(
    "/library/multipart/{session_id}/complete", response_model=UserBrollUploadCompleteResponse
)
async def complete_user_broll_multipart_upload(
    project_id: UUID,
    session_id: UUID,
    payload: MediaUploadCompleteRequest,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
) -> UserBrollUploadCompleteResponse:
    service = _media_service(request)
    session, _asset = await _require_owned_broll_session(
        service=service,
        repository=repository,
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    return await _complete_broll_media_upload(
        project_id=project_id,
        asset_id=session.owner_id,
        sha256_hash=payload.sha256_hash,
        request=request,
        actor=actor,
        repository=repository,
    )


@router.get("/library", response_model=UserBrollLibraryResponse)
async def list_user_broll_library(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    storage_signer: StorageSignerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> UserBrollLibraryResponse:
    del storage_signer
    responses = []
    assets = await _repository_call(
        repository,
        "list_library_assets_async",
        "list_library_assets",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    references = _media_references(request)
    for asset in assets:
        response = asset.to_response()
        if asset.media_state == "verified" and asset.object_reference_id is not None:
            signed = await references.get_signed_url(
                workspace_id=actor.workspace_id,
                project_id=project_id,
                reference_id=asset.object_reference_id,
                purpose="browser_download",
                principal_id=actor.user_id,
            )
            response = response.model_copy(
                update={"download_url": signed.url, "preview_video_url": signed.url}
            )
        responses.append(response)
    return UserBrollLibraryResponse(assets=responses)


@router.delete("/library/{asset_id}", response_model=UserBrollDeleteResponse)
async def delete_user_broll_library_asset(
    project_id: UUID,
    asset_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    storage_signer: StorageSignerDep,
) -> UserBrollDeleteResponse:
    del storage_signer
    asset = await _repository_call(
        repository,
        "get_asset_async",
        "get_asset",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        asset_id=asset_id,
    )
    if asset.provider != StockProviderKey.USER_LIBRARY:
        raise ApiError(
            409, "library_asset_required", "Only user-uploaded library assets can be deleted."
        )
    if asset.media_upload_session_id is None:
        raise ApiError(
            409, "library_upload_session_required", "Library media lacks a durable upload session."
        )
    service = _media_service(request)
    session, owner_asset = await _require_owned_broll_session(
        service=service,
        repository=repository,
        session_id=asset.media_upload_session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    if owner_asset.id != asset.id:
        raise ApiError(409, "library_upload_session_invalid", "Library upload session is invalid.")
    if session.status == "completed":
        await service.delete_completed(
            session_id=session.id, workspace_id=actor.workspace_id, project_id=project_id
        )
    else:
        await service.abort(
            session_id=session.id, workspace_id=actor.workspace_id, project_id=project_id
        )
    await _repository_call(
        repository,
        "delete_library_asset_async",
        "delete_library_asset",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        asset_id=asset_id,
    )
    return UserBrollDeleteResponse(asset_id=asset_id)


@router.post("/generate", response_model=BrollGenerateResponse, status_code=202)
async def generate_broll(
    project_id: UUID,
    payload: BrollGenerateRequest,
    request: Request,
    response: Response,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    billing_repository: BillingRepositoryDep,
    broll_queue: BrollQueueDep,
    providers: StockProvidersDep,
    storage_signer: StorageSignerDep = None,  # type: ignore[assignment]
) -> BrollGenerateResponse:
    storage_signer = storage_signer or getattr(request.app.state, "storage_signer", StorageSigner())
    if (
        request.app.state.settings.billing_enabled
        and not build_billing_status(
            actor.workspace_id,
            billing_repository,
        ).entitlements.broll
    ):
        raise ApiError(
            402,
            "broll_plan_required",
            "Upgrade to Creator or higher to generate B-roll.",
        )
    if not providers:
        raise ApiError(
            503,
            "stock_provider_unavailable",
            "No stock video provider is configured for B-roll generation.",
        )
    plan = repository.active_plan(project_id=project_id, workspace_id=actor.workspace_id)
    visual_manifest = latest_visual_manifest_for_broll(
        repository=repository,
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    source_requests = desired_requests(plan.plan, payload.density)
    if not source_requests:
        raise ApiError(422, "broll_requests_empty", "The active edit plan has no B-roll requests.")
    duration_ms = (
        visual_manifest.duration_ms
        if visual_manifest and visual_manifest.duration_ms
        else max(plan.plan.target_duration_s * 1000, max(item.end_ms for item in source_requests))
    )
    requests = apply_stage4_broll_rules(
        requests=source_requests,
        visual_manifest=visual_manifest,
        duration_ms=duration_ms,
    )
    if not requests:
        raise ApiError(
            422,
            "broll_requests_empty",
            "The active edit plan has no B-roll that can be placed safely.",
        )

    embedding_provider = getattr(
        request.app.state,
        "embedding_gateway_provider",
        None,
    )
    cache_key = build_broll_cache_key(
        edit_plan_id=plan.id,
        density=payload.density,
        schema_version=BROLL_SCHEMA_VERSION,
        semantic_context={
            "visual": visual_manifest_cache_signature(visual_manifest),
            "embedding_routes": getattr(
                embedding_provider,
                "cache_version",
                getattr(
                    request.app.state.settings,
                    "visual_clip_model",
                    "openai/clip-vit-base-patch32",
                ),
            ),
        },
    )
    if not payload.force:
        cached = await _repository_call(
            repository,
            "find_cached_slots_async",
            "find_cached_slots",
            project_id=project_id,
            workspace_id=actor.workspace_id,
            cache_key=cache_key,
        )
        if cached and any(bool(slot.metadata.get("embedding_fallback_used")) for slot in cached):
            cached = []
        if cached:
            for slot in cached:
                if slot.selected_asset_id:
                    asset = await _repository_call(
                        repository,
                        "get_asset_async",
                        "get_asset",
                        project_id=project_id,
                        workspace_id=actor.workspace_id,
                        asset_id=slot.selected_asset_id,
                    )
                    ensure_owned_stock_asset(
                        asset=asset,
                        repository=repository,
                        settings=request.app.state.settings,
                        storage_signer=storage_signer,
                    )
            return BrollGenerateResponse(
                slots=[await _response_for_slot(repository, slot) for slot in cached],
                job_id=f"sync-complete--broll--{project_id}--{plan.id}",
                status="ready",
                cached=True,
            )

    prepare_heavy_job_enqueue(request=request)
    job = broll_queue.enqueue_broll(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        edit_plan_id=plan.id,
        job_payload=serialize_layer_job_payload(payload=payload, user_id=actor.user_id),
    )
    return BrollGenerateResponse(
        **build_queued_layer_response_fields(
            project_id=project_id,
            job_id=job.id,
            response=response,
        )
    )


@router.get("", response_model=BrollListResponse)
async def list_broll(
    project_id: UUID,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> BrollListResponse:
    slots = await _repository_call(
        repository,
        "list_slots_async",
        "list_slots",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=limit,
    )
    active_version = max([slot.version for slot in slots], default=None)
    return BrollListResponse(
        slots=[await _response_for_slot(repository, slot) for slot in slots],
        active_version=active_version,
    )


@router.post("/{slot_id}/swap", response_model=BrollSwapResponse)
async def swap_broll(
    project_id: UUID,
    slot_id: UUID,
    payload: BrollSwapRequest,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    storage_signer: StorageSignerDep = None,  # type: ignore[assignment]
    request: Request = None,  # type: ignore[assignment]
) -> BrollSwapResponse:
    request_state = getattr(getattr(request, "app", None), "state", None)
    storage_signer = storage_signer or getattr(request_state, "storage_signer", StorageSigner())
    slot = await _repository_call(
        repository,
        "get_slot_async",
        "get_slot",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        slot_id=slot_id,
    )
    if payload.asset_id is not None:
        asset = await _repository_call(
            repository,
            "get_asset_async",
            "get_asset",
            project_id=project_id,
            workspace_id=actor.workspace_id,
            asset_id=payload.asset_id,
        )
        if asset.provider in {StockProviderKey.PEXELS, StockProviderKey.PIXABAY}:
            if request_state is None:
                raise ApiError(500, "stock_ingest_not_configured", "Stock ingest is unavailable.")
            ensure_owned_stock_asset(
                asset=asset,
                repository=repository,
                settings=request_state.settings,
                storage_signer=storage_signer,
            )
    updated = await _repository_call(
        repository,
        "update_slot_selection_async",
        "update_slot_selection",
        slot=slot,
        asset_id=payload.asset_id,
        disabled=payload.disabled,
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=slot.edit_plan_id,
        event_type=AIEditFeedbackEventType.CHANGED
        if not payload.disabled
        else AIEditFeedbackEventType.REJECTED,
        layer=AIEditLayer.BROLL,
        source="manual_editor",
        decision_id=str(slot_id),
        action="swap_broll",
        ai_payload=(await _response_for_slot(repository, slot)).model_dump(mode="json"),
        user_payload=(await _response_for_slot(repository, updated)).model_dump(mode="json"),
    )
    return BrollSwapResponse(slot=await _response_for_slot(repository, updated))


@router.patch("/{slot_id}/region", response_model=BrollRegionUpdateResponse)
async def update_broll_region(
    project_id: UUID,
    slot_id: UUID,
    payload: BrollRegionUpdateRequest,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> BrollRegionUpdateResponse:
    slot = await _repository_call(
        repository,
        "get_slot_async",
        "get_slot",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        slot_id=slot_id,
    )
    updated = await _repository_call(
        repository,
        "update_slot_region_async",
        "update_slot_region",
        slot=slot,
        start_ms=payload.start_ms,
        end_ms=payload.end_ms,
        disabled=payload.disabled,
    )
    record_ai_feedback(
        request=request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        edit_plan_id=slot.edit_plan_id,
        event_type=AIEditFeedbackEventType.CHANGED
        if not payload.disabled
        else AIEditFeedbackEventType.REJECTED,
        layer=AIEditLayer.BROLL,
        source="manual_editor",
        decision_id=str(slot_id),
        action="update_broll_region",
        ai_payload=(await _response_for_slot(repository, slot)).model_dump(mode="json"),
        user_payload=(await _response_for_slot(repository, updated)).model_dump(mode="json"),
    )
    return BrollRegionUpdateResponse(slot=await _response_for_slot(repository, updated))


@router.get("/{slot_id}/search", response_model=BrollSearchResponse)
async def search_broll_candidates(
    project_id: UUID,
    slot_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    providers: StockProvidersDep,
    stock_asset_enrichment_queue: StockAssetEnrichmentQueueDep,
    query: Annotated[str | None, Query(max_length=160)] = None,
) -> BrollSearchResponse:
    clip_model_name = getattr(
        request.app.state.settings,
        "visual_clip_model",
        "openai/clip-vit-base-patch32",
    )
    slot = await _repository_call(
        repository,
        "get_slot_async",
        "get_slot",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        slot_id=slot_id,
    )
    if not providers:
        raise ApiError(
            503,
            "stock_provider_unavailable",
            "No stock video provider is configured for B-roll search.",
        )
    metadata = slot.metadata
    search_query = (
        query
        or str(metadata.get("semantic_query") or "").strip()
        or " ".join(slot.keywords)
        or slot.intent
    ).strip()
    raw_embedding = metadata.get("semantic_embedding")
    semantic_embedding = (
        [float(value) for value in raw_embedding if isinstance(value, int | float)]
        if isinstance(raw_embedding, list)
        else query_embedding(
            semantic_query=search_query,
            clip_model_name=clip_model_name,
            embedding_provider=getattr(
                request.app.state,
                "embedding_gateway_provider",
                None,
            ),
            workspace_id=actor.workspace_id,
            project_id=project_id,
        )
    )
    target_shot_type = (
        str(metadata.get("shot_type")).strip().lower() if metadata.get("shot_type") else None
    )
    duplicate_provider_asset_ids: set[str] = set()
    used_shot_types: set[str] = set()
    adjacent_embeddings: list[list[float]] = []
    for asset_id in slot.candidate_asset_ids:
        try:
            asset = await _repository_call(
                repository,
                "get_asset_async",
                "get_asset",
                project_id=project_id,
                workspace_id=actor.workspace_id,
                asset_id=asset_id,
            )
            duplicate_provider_asset_ids.add(asset.provider_asset_id)
            raw_shot_type = asset.raw_metadata.get("shot_type")
            if isinstance(raw_shot_type, str) and raw_shot_type.strip():
                used_shot_types.add(raw_shot_type.strip().lower())
            raw_asset_embedding = asset.raw_metadata.get("embedding")
            if isinstance(raw_asset_embedding, list):
                adjacent_embeddings.append(
                    [
                        float(value)
                        for value in raw_asset_embedding
                        if isinstance(value, int | float)
                    ]
                )
        except ApiError:
            continue
    candidates = []
    for provider in providers:
        try:
            candidates = provider.search(
                query=search_query,
                orientation=StockOrientation.PORTRAIT,
                min_width=720,
                min_height=1280,
            )
        except ApiError:
            continue
        if candidates:
            break
    scored_candidates = [
        _persist_and_score_search_candidate(
            candidate=candidate,
            search_query=search_query,
            semantic_embedding=semantic_embedding,
            target_shot_type=target_shot_type,
            slot=slot,
            repository=repository,
            actor=actor,
            project_id=project_id,
            duplicate_provider_asset_ids=duplicate_provider_asset_ids,
            used_shot_types=used_shot_types,
            adjacent_embeddings=adjacent_embeddings,
            clip_model_name=clip_model_name,
            stock_asset_enrichment_queue=stock_asset_enrichment_queue,
            embedding_provider=getattr(
                request.app.state,
                "embedding_gateway_provider",
                None,
            ),
        )
        for candidate in candidates
        if candidate.duration_ms >= 1500
    ]
    scored_candidates = [item for item in scored_candidates if item is not None]
    scored_candidates.sort(key=lambda item: item[0], reverse=True)
    assets = [asset for _score, asset in scored_candidates[:10]]
    updated = await _repository_call(
        repository,
        "add_slot_candidates_async",
        "add_slot_candidates",
        slot=slot,
        asset_ids=[asset.id for asset in assets],
    )
    return BrollSearchResponse(
        slot=await _response_for_slot(repository, updated),
        candidates=[asset.to_response() for asset in assets],
        query=search_query,
    )


def _persist_and_score_search_candidate(
    *,
    candidate: StockVideoCandidate,
    search_query: str,
    semantic_embedding: list[float],
    target_shot_type: str | None,
    slot,
    repository,
    actor,
    project_id: UUID,
    duplicate_provider_asset_ids: set[str],
    used_shot_types: set[str],
    adjacent_embeddings: list[list[float]],
    clip_model_name: str,
    stock_asset_enrichment_queue: StockAssetEnrichmentQueue,
    embedding_provider=None,
):
    score_details = score_candidate_details(
        candidate=candidate,
        slot_duration_ms=slot.end_ms - slot.start_ms,
        duplicate_provider_asset_ids=duplicate_provider_asset_ids,
        semantic_query=search_query,
        semantic_embedding=semantic_embedding,
        target_shot_type=target_shot_type,
        used_shot_types=used_shot_types,
        adjacent_embeddings=adjacent_embeddings,
    )
    asset = repository.upsert_asset(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        provider=candidate.provider,
        provider_asset_id=candidate.provider_asset_id,
        title=candidate.title,
        thumbnail_url=candidate.thumbnail_url,
        preview_video_url=candidate.preview_video_url,
        download_url=candidate.download_url,
        license=candidate.license,
        width=candidate.width,
        height=candidate.height,
        duration_ms=candidate.duration_ms,
        orientation=candidate.orientation,
        score=score_details.total,
        raw_metadata={
            **build_stock_asset_raw_metadata(
                candidate=candidate,
                query=search_query,
                semantic_query=search_query,
                target_shot_type=target_shot_type,
            ),
            "confidence_label": score_details.confidence_label.value,
        },
    )
    enqueue_stock_asset_enrichment(
        queue=stock_asset_enrichment_queue,
        asset=asset,
        clip_model_name=clip_model_name,
    )
    asset = repository.update_asset_metadata(
        asset=asset,
        score=score_details.total,
        raw_metadata={
            "confidence_label": score_details.confidence_label.value,
            "score_breakdown": {
                "semantic": round(score_details.semantic_score, 4),
                "embedding": round(score_details.embedding_score, 4),
                "lexical": round(score_details.lexical_score, 4),
                "orientation": round(score_details.orientation_score, 4),
                "duration": round(score_details.duration_score, 4),
                "diversity": round(score_details.diversity_score, 4),
                "provider_license": round(score_details.provider_license_score, 4),
            },
        },
    )
    return score_details.total, asset
