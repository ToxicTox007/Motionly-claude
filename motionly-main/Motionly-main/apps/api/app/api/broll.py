import time
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
    BrollSlotStatus,
    BrollSwapRequest,
    BrollSwapResponse,
    StockOrientation,
    StockProviderKey,
    UserBrollDeleteResponse,
    UserBrollLibraryResponse,
    UserBrollUploadCompleteResponse,
    UserBrollUploadInitRequest,
    UserBrollUploadInitResponse,
)
from app.services.ai_feedback import record_ai_feedback
from app.services.broll_intelligence import (
    apply_stage4_broll_rules,
    broll_slot_metadata,
    latest_visual_manifest_for_broll,
    visual_manifest_cache_signature,
)
from app.services.local_storage import LocalObjectStorage
from app.services.queue import BrollQueue
from app.services.layer_job_enqueue import (
    build_queued_layer_response_fields,
    prepare_heavy_job_enqueue,
    serialize_layer_job_payload,
)
from app.services.stock_video import (
    StockVideoProvider,
    build_broll_cache_key,
    candidate_shot_type,
    desired_requests,
    ensure_owned_stock_asset,
    keyword_query,
    latency_ms_since,
    score_candidate,
    semantic_match_score,
)
from app.services.storage import StorageSigner

router = APIRouter(prefix="/projects/{project_id}/broll", tags=["B-roll"])

ActorDep = Annotated[Actor, Depends(get_actor)]
BrollRepositoryDep = Annotated[InMemoryBrollRepository, Depends(get_broll_repository)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
BrollQueueDep = Annotated[BrollQueue, Depends(get_broll_queue)]
StockProvidersDep = Annotated[list[StockVideoProvider], Depends(get_stock_video_providers)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]


@router.post("/library/init", response_model=UserBrollUploadInitResponse, status_code=201)
async def init_user_broll_upload(
    project_id: UUID,
    payload: UserBrollUploadInitRequest,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    storage_signer: StorageSignerDep,
) -> UserBrollUploadInitResponse:
    repository.get_project_for_workspace(project_id=project_id, workspace_id=actor.workspace_id)
    suffix = Path(payload.filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".m4v"}:
        raise ApiError(
            422,
            "library_video_type_unsupported",
            "Upload MP4, MOV, M4V, or WebM video.",
        )
    asset_key = uuid4()
    bucket = request.app.state.settings.upload_storage_bucket
    path = f"workspaces/{actor.workspace_id}/projects/{project_id}/broll/{asset_key}{suffix}"
    signed = storage_signer.create_signed_upload(
        bucket=bucket,
        path=path,
        mime_type=payload.mime_type,
        expires_in_seconds=request.app.state.settings.upload_signed_url_ttl_seconds,
    )
    orientation = (
        StockOrientation.PORTRAIT
        if payload.height > payload.width
        else StockOrientation.LANDSCAPE
        if payload.width > payload.height
        else StockOrientation.SQUARE
    )
    asset = repository.upsert_asset(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        provider=StockProviderKey.USER_LIBRARY,
        provider_asset_id=str(asset_key),
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
        raw_metadata={
            "status": "uploading",
            "storage_bucket": bucket,
            "storage_path": path,
            "mime_type": payload.mime_type,
            "file_size_bytes": payload.file_size_bytes,
            "original_filename": payload.filename,
        },
    )
    return UserBrollUploadInitResponse(
        asset=asset.to_response(),
        signed_upload_url=signed.url,
        upload_headers=signed.headers,
        expires_in_seconds=request.app.state.settings.upload_signed_url_ttl_seconds,
    )


@router.post("/library/{asset_id}/complete", response_model=UserBrollUploadCompleteResponse)
async def complete_user_broll_upload(
    project_id: UUID,
    asset_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    storage_signer: StorageSignerDep,
) -> UserBrollUploadCompleteResponse:
    asset = repository.get_asset(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        asset_id=asset_id,
    )
    if asset.provider != StockProviderKey.USER_LIBRARY:
        raise ApiError(409, "library_asset_required", "This is not a user library asset.")
    bucket = str(
        asset.raw_metadata.get("storage_bucket") or request.app.state.settings.upload_storage_bucket
    )
    path = str(asset.raw_metadata.get("storage_path") or "")
    download = storage_signer.create_signed_download(
        bucket=bucket,
        path=path,
        expires_in_seconds=request.app.state.settings.upload_signed_url_ttl_seconds,
    )
    asset = repository.mark_library_asset_ready(asset=asset, download_url=download.url)
    return UserBrollUploadCompleteResponse(asset=asset.to_response())


@router.get("/library", response_model=UserBrollLibraryResponse)
async def list_user_broll_library(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    storage_signer: StorageSignerDep,
) -> UserBrollLibraryResponse:
    ttl = request.app.state.settings.upload_signed_url_ttl_seconds
    responses = []
    for asset in repository.list_library_assets(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    ):
        response = asset.to_response()
        path = str(asset.raw_metadata.get("storage_path") or "")
        bucket = str(
            asset.raw_metadata.get("storage_bucket")
            or request.app.state.settings.upload_storage_bucket
        )
        if path and asset.raw_metadata.get("status") == "ready":
            url = storage_signer.create_signed_download(
                bucket=bucket,
                path=path,
                expires_in_seconds=ttl,
            ).url
            response = response.model_copy(update={"download_url": url, "preview_video_url": url})
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
    asset = repository.get_asset(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        asset_id=asset_id,
    )
    bucket = str(asset.raw_metadata.get("storage_bucket") or "")
    path = str(asset.raw_metadata.get("storage_path") or "")
    if bucket and path:
        if storage_signer.supabase_url:
            storage_signer.delete_object(bucket=bucket, path=path)
        else:
            LocalObjectStorage(root=request.app.state.settings.media_work_dir).object_path(
                bucket=bucket,
                path=path,
            ).unlink(missing_ok=True)
    repository.delete_library_asset(
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
    storage_signer = storage_signer or getattr(
        request.app.state, "storage_signer", StorageSigner()
    )
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

    cache_key = build_broll_cache_key(
        edit_plan_id=plan.id,
        density=payload.density,
        schema_version=BROLL_SCHEMA_VERSION,
        semantic_context=visual_manifest_cache_signature(visual_manifest),
    )
    if not payload.force:
        cached = repository.find_cached_slots(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            cache_key=cache_key,
        )
        if cached:
            for slot in cached:
                if slot.selected_asset_id:
                    asset = repository.get_asset(
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
                slots=[repository.response_for_slot(slot) for slot in cached],
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
) -> BrollListResponse:
    slots = repository.list_slots(project_id=project_id, workspace_id=actor.workspace_id)
    active_version = max([slot.version for slot in slots], default=None)
    return BrollListResponse(
        slots=[repository.response_for_slot(slot) for slot in slots],
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
    slot = repository.get_slot(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        slot_id=slot_id,
    )
    if payload.asset_id is not None:
        asset = repository.get_asset(
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
    updated = repository.update_slot_selection(
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
        ai_payload=repository.response_for_slot(slot).model_dump(mode="json"),
        user_payload=repository.response_for_slot(updated).model_dump(mode="json"),
    )
    return BrollSwapResponse(slot=repository.response_for_slot(updated))


@router.patch("/{slot_id}/region", response_model=BrollRegionUpdateResponse)
async def update_broll_region(
    project_id: UUID,
    slot_id: UUID,
    payload: BrollRegionUpdateRequest,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    request: Request = None,  # type: ignore[assignment]
) -> BrollRegionUpdateResponse:
    slot = repository.get_slot(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        slot_id=slot_id,
    )
    updated = repository.update_slot_region(
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
        ai_payload=repository.response_for_slot(slot).model_dump(mode="json"),
        user_payload=repository.response_for_slot(updated).model_dump(mode="json"),
    )
    return BrollRegionUpdateResponse(slot=repository.response_for_slot(updated))


@router.get("/{slot_id}/search", response_model=BrollSearchResponse)
async def search_broll_candidates(
    project_id: UUID,
    slot_id: UUID,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    providers: StockProvidersDep,
    query: Annotated[str | None, Query(max_length=160)] = None,
) -> BrollSearchResponse:
    slot = repository.get_slot(
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
        [float(value) for value in raw_embedding if isinstance(value, (int, float))]
        if isinstance(raw_embedding, list)
        else []
    )
    target_shot_type = (
        str(metadata.get("shot_type")).strip().lower() if metadata.get("shot_type") else None
    )
    duplicate_provider_asset_ids: set[str] = set()
    used_shot_types: set[str] = set()
    for asset_id in slot.candidate_asset_ids:
        try:
            asset = repository.get_asset(
                project_id=project_id,
                workspace_id=actor.workspace_id,
                asset_id=asset_id,
            )
            duplicate_provider_asset_ids.add(asset.provider_asset_id)
            raw_shot_type = asset.raw_metadata.get("shot_type")
            if isinstance(raw_shot_type, str) and raw_shot_type.strip():
                used_shot_types.add(raw_shot_type.strip().lower())
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
        (
            score_candidate(
                candidate=candidate,
                slot_duration_ms=slot.end_ms - slot.start_ms,
                duplicate_provider_asset_ids=duplicate_provider_asset_ids,
                semantic_query=search_query,
                semantic_embedding=semantic_embedding,
                target_shot_type=target_shot_type,
                used_shot_types=used_shot_types,
            ),
            candidate,
        )
        for candidate in candidates
        if candidate.duration_ms >= 1500
    ]
    scored_candidates.sort(key=lambda item: item[0], reverse=True)
    assets = [
        repository.upsert_asset(
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
            score=score,
            raw_metadata={
                **candidate.raw_metadata,
                "query": search_query,
                "semantic_query": search_query,
                "target_shot_type": target_shot_type,
            },
        )
        for score, candidate in scored_candidates[:10]
    ]
    updated = repository.add_slot_candidates(
        slot=slot,
        asset_ids=[asset.id for asset in assets],
    )
    return BrollSearchResponse(
        slot=repository.response_for_slot(updated),
        candidates=[asset.to_response() for asset in assets],
        query=search_query,
    )
