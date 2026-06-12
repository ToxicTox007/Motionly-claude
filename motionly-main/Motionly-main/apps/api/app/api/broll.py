import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.billing import build_billing_status
from app.api.dependencies import (
    Actor,
    get_actor,
    get_billing_repository,
    get_broll_queue,
    get_broll_repository,
    get_stock_video_providers,
)
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
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
)
from app.services.queue import BrollQueue
from app.services.stock_video import (
    StockVideoProvider,
    build_broll_cache_key,
    desired_requests,
    keyword_query,
    latency_ms_since,
    score_candidate,
)

router = APIRouter(prefix="/projects/{project_id}/broll", tags=["B-roll"])

ActorDep = Annotated[Actor, Depends(get_actor)]
BrollRepositoryDep = Annotated[InMemoryBrollRepository, Depends(get_broll_repository)]
BillingRepositoryDep = Annotated[InMemoryBillingRepository, Depends(get_billing_repository)]
BrollQueueDep = Annotated[BrollQueue, Depends(get_broll_queue)]
StockProvidersDep = Annotated[list[StockVideoProvider], Depends(get_stock_video_providers)]


@router.post("/generate", response_model=BrollGenerateResponse, status_code=202)
async def generate_broll(
    project_id: UUID,
    payload: BrollGenerateRequest,
    request: Request,
    actor: ActorDep,
    repository: BrollRepositoryDep,
    billing_repository: BillingRepositoryDep,
    broll_queue: BrollQueueDep,
    providers: StockProvidersDep,
) -> BrollGenerateResponse:
    if request.app.state.settings.billing_enabled and not build_billing_status(
        actor.workspace_id,
        billing_repository,
    ).entitlements.broll:
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
    started = time.perf_counter()
    plan = repository.active_plan(project_id=project_id, workspace_id=actor.workspace_id)
    requests = desired_requests(plan.plan, payload.density)
    if not requests:
        raise ApiError(422, "broll_requests_empty", "The active edit plan has no B-roll requests.")

    cache_key = build_broll_cache_key(
        edit_plan_id=plan.id,
        density=payload.density,
        schema_version=BROLL_SCHEMA_VERSION,
    )
    job = broll_queue.enqueue_broll(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        edit_plan_id=plan.id,
    )
    if not payload.force:
        cached = repository.find_cached_slots(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            cache_key=cache_key,
        )
        if cached:
            return BrollGenerateResponse(
                slots=[repository.response_for_slot(slot) for slot in cached],
                job_id=job.id,
                cached=True,
            )

    used_provider_asset_ids: set[str] = set()
    slots: list[dict[str, object]] = []
    for request in requests:
        query = keyword_query(request, plan.style.value)
        slot_duration_ms = request.end_ms - request.start_ms
        ranked_assets = []
        errors: list[str] = []
        for provider in providers:
            try:
                candidates = provider.search(
                    query=query,
                    orientation=StockOrientation.PORTRAIT,
                    min_width=720,
                    min_height=1280,
                )
            except ApiError as exc:
                errors.append(exc.code)
                continue
            for candidate in candidates:
                score = score_candidate(
                    candidate=candidate,
                    slot_duration_ms=slot_duration_ms,
                    duplicate_provider_asset_ids=used_provider_asset_ids,
                )
                if candidate.duration_ms < 1500:
                    continue
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
                    score=score,
                    raw_metadata={
                        **candidate.raw_metadata,
                        "query": query,
                        "latency_ms": latency_ms_since(started),
                    },
                )
                ranked_assets.append((score, asset))
            if ranked_assets:
                break

        ranked_assets.sort(key=lambda item: item[0], reverse=True)
        selected = ranked_assets[0][1] if ranked_assets else None
        if selected:
            used_provider_asset_ids.add(selected.provider_asset_id)
        status = BrollSlotStatus.READY if selected else BrollSlotStatus.FAILED
        slots.append(
            {
                "workspace_id": actor.workspace_id,
                "project_id": project_id,
                "edit_plan_id": plan.id,
                "status": status,
                "start_ms": request.start_ms,
                "end_ms": request.end_ms,
                "keywords": [query, *request.keywords][:8],
                "intent": f"Support transcript moment with {query}",
                "density": (
                    payload.density
                    if payload.density != "from_plan"
                    else plan.broll_density.value
                ),
                "priority": request.priority,
                "selected_asset_id": selected.id if selected else None,
                "candidate_asset_ids": [asset.id for _, asset in ranked_assets[:5]],
                "crossfade_ms": 260,
                "provider": selected.provider if selected else providers[0].provider_key,
                "score": ranked_assets[0][0] if ranked_assets else 0,
                "error_code": (
                    None if selected else (errors[0] if errors else "stock_asset_not_found")
                ),
                "error_message": None if selected else "No usable vertical stock clip was found.",
            }
        )

    records = repository.create_slot_set(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        cache_key=cache_key,
        slots=slots,
    )
    return BrollGenerateResponse(
        slots=[repository.response_for_slot(slot) for slot in records],
        job_id=job.id,
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
) -> BrollSwapResponse:
    slot = repository.get_slot(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        slot_id=slot_id,
    )
    if payload.asset_id is not None:
        repository.get_asset(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            asset_id=payload.asset_id,
        )
    updated = repository.update_slot_selection(
        slot=slot,
        asset_id=payload.asset_id,
        disabled=payload.disabled,
    )
    return BrollSwapResponse(slot=repository.response_for_slot(updated))


@router.patch("/{slot_id}/region", response_model=BrollRegionUpdateResponse)
async def update_broll_region(
    project_id: UUID,
    slot_id: UUID,
    payload: BrollRegionUpdateRequest,
    actor: ActorDep,
    repository: BrollRepositoryDep,
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
    search_query = (query or " ".join(slot.keywords) or slot.intent).strip()
    duplicate_provider_asset_ids: set[str] = set()
    for asset_id in slot.candidate_asset_ids:
        try:
            asset = repository.get_asset(
                project_id=project_id,
                workspace_id=actor.workspace_id,
                asset_id=asset_id,
            )
            duplicate_provider_asset_ids.add(asset.provider_asset_id)
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
            score=score_candidate(
                candidate=candidate,
                slot_duration_ms=slot.end_ms - slot.start_ms,
                duplicate_provider_asset_ids=duplicate_provider_asset_ids,
            ),
            raw_metadata=candidate.raw_metadata,
        )
        for candidate in candidates
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
