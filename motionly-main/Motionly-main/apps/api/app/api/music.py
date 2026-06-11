from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

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
    MusicSelectionStatus,
    MusicSelectRequest,
    MusicSelectResponse,
    MusicTrackListResponse,
)
from app.services.music import MusicProvider, MusicTrackCandidate
from app.services.queue import MusicQueue

router = APIRouter(prefix="/projects/{project_id}/music", tags=["Music"])

ActorDep = Annotated[Actor, Depends(get_actor)]
MusicRepositoryDep = Annotated[InMemoryMusicRepository, Depends(get_music_repository)]
PlanRepositoryDep = Annotated[InMemoryPlanRepository, Depends(get_plan_repository)]
MusicProvidersDep = Annotated[list[MusicProvider], Depends(get_music_providers)]
MusicQueueDep = Annotated[MusicQueue, Depends(get_music_queue)]


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
) -> MusicSelectResponse:
    plan = plan_repository.active_plan(project_id=project_id, workspace_id=actor.workspace_id)
    mood = payload.mood or plan.plan.music_mood
    track = None
    music_volume = payload.music_volume if payload.include_music else 0
    sfx_intensity = payload.sfx_intensity if payload.include_music else 0

    if not payload.include_music:
        mood = "none"
    elif payload.music_track_id:
        track = repository.get_track(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            track_id=payload.music_track_id,
        )
    else:
        if not providers:
            raise ApiError(
                503,
                "music_provider_not_configured",
                "Music selection is not configured for production.",
            )
        candidates: list[MusicTrackCandidate] = []
        for provider in providers:
            try:
                candidates = provider.search(mood=mood)
            except ApiError:
                continue
            if candidates:
                break
        if not candidates:
            raise ApiError(
                404,
                "music_track_not_found",
                "No music track was available for the requested mood.",
            )
        if payload.force:
            candidates = candidates_for_change(
                candidates=candidates,
                repository=repository,
                project_id=project_id,
                workspace_id=actor.workspace_id,
                providers=providers,
            )
        candidate = candidates[0]
        track = repository.upsert_track(
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
            raw_metadata={**candidate.raw_metadata, "mood": mood},
        )

    cache_key = build_music_cache_key(
        edit_plan_id=plan.id,
        mood=mood,
        music_volume=music_volume,
        sfx_intensity=sfx_intensity,
        track_id=track.id if track else None,
    )
    job = music_queue.enqueue_music_selection(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        edit_plan_id=plan.id,
    )

    if not payload.force:
        cached = repository.find_cached_selection(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            cache_key=cache_key,
        )
        if cached:
            return MusicSelectResponse(
                selection=repository.response_for_selection(cached),
                job_id=job.id,
                cached=True,
            )

    selection = repository.create_selection(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        edit_plan_id=plan.id,
        status=MusicSelectionStatus.READY,
        music_track_id=track.id if track else None,
        music_volume=music_volume,
        sfx_intensity=sfx_intensity,
        rights_confirmed=payload.rights_confirmed,
        cache_key=cache_key,
    )

    return MusicSelectResponse(
        selection=repository.response_for_selection(selection),
        job_id=job.id,
    )


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
        repository.upsert_track(
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
