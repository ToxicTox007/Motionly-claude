from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.dependencies import (
    Actor,
    get_actor,
    get_music_providers,
    get_music_queue,
    get_music_repository,
    get_plan_repository,
)
from app.api.errors import ApiError
from app.repositories.music import InMemoryMusicRepository
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.music import (
    MusicRegionsUpdateRequest,
    MusicSelectionResponse,
    MusicSelectRequest,
    MusicSelectResponse,
    MusicTrackListResponse,
)
from app.services.layer_job_enqueue import (
    build_queued_layer_response_fields,
    prepare_heavy_job_enqueue,
    serialize_layer_job_payload,
)
from app.services.music import MusicProvider, MusicTrackCandidate
from app.services.queue import MusicQueue

router = APIRouter(prefix="/projects/{project_id}/music", tags=["Music"])

ActorDep = Annotated[Actor, Depends(get_actor)]
MusicRepositoryDep = Annotated[InMemoryMusicRepository, Depends(get_music_repository)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]
MusicProvidersDep = Annotated[list[MusicProvider], Depends(get_music_providers)]
MusicQueueDep = Annotated[MusicQueue, Depends(get_music_queue)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


async def _response_for_selection(
    repository: InMemoryMusicRepository,
    selection,
) -> MusicSelectionResponse:
    async_method = getattr(repository, "response_for_selection_async", None)
    if async_method is not None:
        return await async_method(selection)
    return repository.response_for_selection(selection)


@router.get("/selection", response_model=MusicSelectionResponse | None)
async def get_music_selection(
    project_id: UUID,
    actor: ActorDep,
    repository: MusicRepositoryDep,
) -> MusicSelectionResponse | None:
    selection = await _repository_call(
        repository,
        "latest_selection_async",
        "latest_selection",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    return await _response_for_selection(repository, selection) if selection else None


def build_music_cache_key(
    *,
    edit_plan_id: UUID,
    mood: str,
    music_volume: float,
    sfx_intensity: float,
    track_id: UUID | None,
) -> str:
    return (
        f"plan:{edit_plan_id}:mood:{mood or 'default'}:"
        f"vol:{music_volume:.3f}:sfx:{sfx_intensity:.3f}:track:{track_id or 'auto'}"
    )


@router.post("/select", response_model=MusicSelectResponse, status_code=202)
async def select_music(
    project_id: UUID,
    payload: MusicSelectRequest,
    actor: ActorDep,
    repository: MusicRepositoryDep,
    plan_repository: PlanRepositoryDep,
    music_queue: MusicQueueDep,
    providers: MusicProvidersDep,
    request: Request,
    response: Response,
) -> MusicSelectResponse:
    plan = plan_repository.active_plan(project_id=project_id, workspace_id=actor.workspace_id)
    mood = payload.mood or plan.plan.music_mood
    music_volume = payload.music_volume if payload.include_music else 0
    sfx_intensity = payload.sfx_intensity if payload.include_music else 0
    if not payload.include_music:
        mood = "none"
    elif not payload.music_track_id and not providers:
        raise ApiError(
            503,
            "music_provider_not_configured",
            "Music selection is not configured for production.",
        )

    track_id = payload.music_track_id
    cache_key = build_music_cache_key(
        edit_plan_id=plan.id,
        mood=mood,
        music_volume=music_volume,
        sfx_intensity=sfx_intensity,
        track_id=track_id,
    )
    if not payload.force:
        cached = await _repository_call(
            repository,
            "find_cached_selection_async",
            "find_cached_selection",
            project_id=project_id,
            workspace_id=actor.workspace_id,
            cache_key=cache_key,
        )
        if cached:
            return MusicSelectResponse(
                selection=await _response_for_selection(repository, cached),
                job_id=f"sync-complete--music--{project_id}--{plan.id}",
                status="ready",
                cached=True,
            )

    prepare_heavy_job_enqueue(request=request)
    job = music_queue.enqueue_music_selection(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        edit_plan_id=plan.id,
        job_payload=serialize_layer_job_payload(payload=payload, user_id=actor.user_id),
    )
    return MusicSelectResponse(
        **build_queued_layer_response_fields(
            project_id=project_id,
            job_id=job.id,
            response=response,
        )
    )


@router.patch("/regions", response_model=MusicSelectionResponse)
async def update_music_regions(
    project_id: UUID,
    payload: MusicRegionsUpdateRequest,
    actor: ActorDep,
    repository: MusicRepositoryDep,
) -> MusicSelectionResponse:
    selection = await _repository_call(
        repository,
        "latest_selection_async",
        "latest_selection",
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    if selection is None:
        raise ApiError(
            404,
            "music_selection_not_found",
            "No music selection exists for this project.",
        )
    updated = await _repository_call(
        repository,
        "update_regions_async",
        "update_regions",
        project_id=project_id,
        workspace_id=actor.workspace_id,
        selection_id=selection.id,
        regions=payload.regions,
    )
    return await _response_for_selection(repository, updated)


@router.get("/tracks", response_model=MusicTrackListResponse)
async def list_music_tracks(
    project_id: UUID,
    actor: ActorDep,
    repository: MusicRepositoryDep,
    plan_repository: PlanRepositoryDep,
    providers: MusicProvidersDep,
    mood: Annotated[str | None, Query(max_length=160)] = None,
) -> MusicTrackListResponse:
    plan = plan_repository.active_plan(project_id=project_id, workspace_id=actor.workspace_id)
    search_mood = mood or plan.plan.music_mood
    if not providers:
        raise ApiError(
            503,
            "music_provider_not_configured",
            "Music selection is not configured for production.",
        )
    candidates: list[MusicTrackCandidate] = []
    for provider in providers:
        try:
            candidates = provider.search(mood=search_mood, max_results=8)
        except ApiError:
            continue
        if candidates:
            break
    tracks = [
        await _repository_call(
            repository,
            "upsert_track_async",
            "upsert_track",
            workspace_id=actor.workspace_id,
            project_id=project_id,
            provider=candidate.provider,
            provider_track_id=candidate.provider_track_id,
            title=candidate.title,
            license=candidate.license,
            duration_ms=candidate.duration_ms,
            mood_tags=candidate.mood_tags,
            bpm=candidate.bpm,
            energy=candidate.energy,
            vocals=candidate.vocals,
            loopable=candidate.loopable,
            beat_map=candidate.beat_map,
            raw_metadata={**candidate.raw_metadata, "mood": search_mood},
        )
        for candidate in candidates
    ]
    return MusicTrackListResponse(
        tracks=[track.to_response() for track in tracks],
        mood=search_mood,
    )


def candidates_for_change(
    *,
    candidates: list[MusicTrackCandidate],
    repository: InMemoryMusicRepository,
    project_id: UUID,
    workspace_id: UUID,
    providers: list[MusicProvider],
) -> list[MusicTrackCandidate]:
    latest = repository.latest_selection(project_id=project_id, workspace_id=workspace_id)
    if not latest or not latest.music_track_id:
        return candidates
    latest_track = repository.get_track(
        project_id=project_id,
        workspace_id=workspace_id,
        track_id=latest.music_track_id,
    )
    alternatives = [
        candidate
        for candidate in candidates
        if candidate.provider_track_id != latest_track.provider_track_id
    ]
    if alternatives:
        return alternatives
    for provider in providers:
        fallback = [
            candidate
            for candidate in provider.search(mood="", max_results=8)
            if candidate.provider_track_id != latest_track.provider_track_id
        ]
        if fallback:
            return fallback
    return candidates
