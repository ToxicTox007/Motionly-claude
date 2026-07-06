import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.music import (
    get_music_selection,
    list_music_tracks,
    select_music,
    update_music_regions,
)
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.music import (
    MUSIC_SELECTION_COLUMNS,
    MUSIC_TRACK_COLUMNS,
    InMemoryMusicRepository,
    SupabaseMusicRepository,
)
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.captions import LanguageHint
from app.schemas.music import MusicRegion, MusicRegionsUpdateRequest, MusicSelectRequest
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.music import FixtureMusicProvider, LicensedCatalogMusicProvider
from app.services.queue import MusicQueue
from app.services.transcription_provider import FixtureTranscriptionProvider


def make_music_repository() -> tuple[InMemoryMusicRepository, UUID, UUID, UUID]:
    upload_repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=1000,
    )
    upload.status = UploadStatus.READY
    upload.metadata.duration_ms = 12_000
    upload.metadata.has_audio = True
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    caption_repository = InMemoryCaptionRepository(upload_repository)
    caption_repository.create_transcript(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status="ready",
        language="en",
        language_hint="auto",
        provider="fixture",
        model="fixture",
        confidence=0.95,
        duration_ms=12_000,
        raw_payload={},
        words=FixtureTranscriptionProvider()
        .transcribe(audio_path=None, language_hint=LanguageHint.AUTO)
        .words,
    )
    plan_repository = InMemoryPlanRepository(caption_repository)
    from tests.worker_job_test_helpers import seed_active_plan

    seed_active_plan(
        plan_repository=plan_repository,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(broll_density=BrollDensity.MEDIUM),
    )
    return InMemoryMusicRepository(plan_repository), project_id, workspace_id, user_id


def run_select_music(
    *,
    project_id: UUID,
    payload: MusicSelectRequest,
    actor: Actor,
    repository: InMemoryMusicRepository,
    providers: list[FixtureMusicProvider],
) -> object:
    from app.schemas.music import MusicSelectResponse
    from app.services.queue import QueueRegistry
    from tests.worker_job_test_helpers import (
        DummyResponse,
        build_queue_request_state,
        run_enqueued_job,
    )

    registry = QueueRegistry()
    request = build_queue_request_state(
        registry=registry,
        music_repository=repository,
        plan_repository=repository.plan_repository,
        music_providers=providers,
    )
    music_response = asyncio.run(
        select_music(
            project_id=project_id,
            payload=payload,
            actor=actor,
            repository=repository,
            plan_repository=repository.plan_repository,
            music_queue=MusicQueue(registry=registry),
            providers=providers,
            request=request,
            response=DummyResponse(),
        )
    )
    if getattr(music_response, "status", None) == "queued":
        asyncio.run(run_enqueued_job(job_id=music_response.job_id, request=request))
        selection = repository.latest_selection(
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        music_response = MusicSelectResponse(
            selection=repository.response_for_selection(selection),
            job_id=music_response.job_id,
            status="ready",
        )
    return music_response


def test_select_music_creates_selection() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()

    response = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        providers=[FixtureMusicProvider()],
    )

    assert response.status == "ready"
    assert response.selection.music_track is not None
    assert response.selection.music_track.title == "Ambient Luxury Bed"
    assert response.selection.music_volume == 0.25
    assert response.selection.sfx_intensity == 0.25
    assert len(response.selection.regions) == 1
    assert response.selection.regions[0].start_ms == 0
    assert response.selection.regions[0].end_ms > 0


def test_supabase_music_repository_reads_with_explicit_projections(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    caption_repository = InMemoryCaptionRepository(upload_repository)
    plan_repository = InMemoryPlanRepository(caption_repository)
    repository = SupabaseMusicRepository(
        plan_repository=plan_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    track_id = uuid4()
    now = "2026-07-05T00:00:00+00:00"
    track_row = {
        "id": str(track_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "provider": "fixture",
        "provider_track_id": "ambient-1",
        "title": "Ambient Bed",
        "license": "royalty_free",
        "duration_ms": 12000,
        "mood_tags": ["ambient"],
        "bpm": 92,
        "energy": 0.4,
        "vocals": False,
        "loopable": True,
        "beat_map": [0.0, 1.0],
        "raw_metadata": {},
        "created_at": now,
    }
    responses = [[], [track_row]]
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(is_success=True, json=lambda: responses.pop(0))

    monkeypatch.setattr("app.repositories.music.SupabaseRestClient.request", fake_request)

    assert repository.latest_selection(project_id=project_id, workspace_id=workspace_id) is None
    track = repository.get_track(
        project_id=project_id,
        workspace_id=workspace_id,
        track_id=track_id,
    )

    assert track.id == track_id
    assert calls[0] == (
        "GET",
        "/project_music_selections",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": MUSIC_SELECTION_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            }
        },
    )
    assert calls[1] == (
        "GET",
        "/music_tracks",
        {
            "params": {
                "id": f"eq.{track_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": MUSIC_TRACK_COLUMNS,
                "limit": "1",
            }
        },
    )


def test_supabase_music_async_reads_use_bounded_projections(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    caption_repository = InMemoryCaptionRepository(upload_repository)
    plan_repository = InMemoryPlanRepository(caption_repository)
    repository = SupabaseMusicRepository(
        plan_repository=plan_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    track_id = uuid4()
    selection_id = uuid4()
    plan_id = uuid4()
    now = "2026-07-06T00:00:00+00:00"
    track_row = {
        "id": str(track_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "provider": "fixture",
        "provider_track_id": "ambient-1",
        "title": "Ambient Bed",
        "license": "royalty_free",
        "duration_ms": 12000,
        "mood_tags": ["ambient"],
        "bpm": 92,
        "energy": 0.4,
        "vocals": False,
        "loopable": True,
        "beat_map": [0.0, 1.0],
        "raw_metadata": {},
        "created_at": now,
    }
    selection_row = {
        "id": str(selection_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "edit_plan_id": str(plan_id),
        "version": 1,
        "schema_version": 1,
        "status": "ready",
        "music_track_id": str(track_id),
        "music_volume": 0.25,
        "sfx_intensity": 0.25,
        "rights_confirmed": True,
        "cache_key": "music-cache-key",
        "regions": [],
        "created_at": now,
        "updated_at": now,
    }
    responses = [[selection_row], [selection_row], [track_row]]
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(is_success=True, json=lambda: responses.pop(0))

    monkeypatch.setattr(
        "app.repositories.music.SupabaseRestClient.async_request",
        fake_async_request,
    )

    latest = asyncio.run(
        repository.latest_selection_async(
            project_id=project_id,
            workspace_id=workspace_id,
        )
    )
    cached = asyncio.run(
        repository.find_cached_selection_async(
            project_id=project_id,
            workspace_id=workspace_id,
            cache_key="music-cache-key",
        )
    )
    track = asyncio.run(
        repository.get_track_async(
            project_id=project_id,
            workspace_id=workspace_id,
            track_id=track_id,
        )
    )

    assert latest is not None
    assert cached is not None
    assert track.id == track_id
    assert calls[0] == (
        "GET",
        "/project_music_selections",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": MUSIC_SELECTION_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            }
        },
    )
    assert calls[1] == (
        "GET",
        "/project_music_selections",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "cache_key": "eq.music-cache-key",
                "select": MUSIC_SELECTION_COLUMNS,
                "order": "updated_at.desc",
                "limit": "1",
            }
        },
    )
    assert calls[2] == (
        "GET",
        "/music_tracks",
        {
            "params": {
                "id": f"eq.{track_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": MUSIC_TRACK_COLUMNS,
                "limit": "1",
            }
        },
    )


def test_music_api_prefers_async_repository_methods() -> None:
    base_repository, project_id, workspace_id, user_id = make_music_repository()

    class AsyncMusicRepository(InMemoryMusicRepository):
        def __init__(self, plan_repository):
            super().__init__(plan_repository)
            self.async_calls: list[str] = []

        async def latest_selection_async(self, **kwargs):
            self.async_calls.append("latest-selection")
            return self.latest_selection(**kwargs)

        async def find_cached_selection_async(self, **kwargs):
            self.async_calls.append("find-cache")
            return self.find_cached_selection(**kwargs)

        async def update_regions_async(self, **kwargs):
            self.async_calls.append("update-regions")
            return self.update_regions(**kwargs)

        async def upsert_track_async(self, **kwargs):
            self.async_calls.append("upsert-track")
            return self.upsert_track(**kwargs)

        async def response_for_selection_async(self, selection):
            self.async_calls.append("response-for-selection")
            return self.response_for_selection(selection)

    repository = AsyncMusicRepository(base_repository.plan_repository)
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    first = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(),
        actor=actor,
        repository=repository,
        providers=[FixtureMusicProvider()],
    )
    cached = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(),
        actor=actor,
        repository=repository,
        providers=[FixtureMusicProvider()],
    )
    asyncio.run(
        get_music_selection(
            project_id=project_id,
            actor=actor,
            repository=repository,
        )
    )
    asyncio.run(
        update_music_regions(
            project_id=project_id,
            payload=MusicRegionsUpdateRequest(regions=first.selection.regions),
            actor=actor,
            repository=repository,
        )
    )
    asyncio.run(
        list_music_tracks(
            project_id=project_id,
            actor=actor,
            repository=repository,
            plan_repository=repository.plan_repository,
            providers=[FixtureMusicProvider()],
            mood="clean confident",
        )
    )

    assert cached.cached is True
    assert repository.async_calls == [
        "find-cache",
        "find-cache",
        "response-for-selection",
        "latest-selection",
        "response-for-selection",
        "latest-selection",
        "update-regions",
        "response-for-selection",
        "upsert-track",
    ]


def test_update_music_regions_splits_and_deletes_region() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    selected = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(),
        actor=actor,
        repository=repository,
        providers=[FixtureMusicProvider()],
    )
    original = selected.selection.regions[0]

    split = asyncio.run(
        update_music_regions(
            project_id=project_id,
            payload=MusicRegionsUpdateRequest(
                regions=[
                    MusicRegion(id=original.id, start_ms=original.start_ms, end_ms=3000),
                    MusicRegion(id="music-second", start_ms=3000, end_ms=original.end_ms),
                ]
            ),
            actor=actor,
            repository=repository,
        )
    )

    assert len(split.regions) == 2
    assert split.regions[0].end_ms == 3000
    assert split.regions[1].start_ms == 3000

    deleted = asyncio.run(
        update_music_regions(
            project_id=project_id,
            payload=MusicRegionsUpdateRequest(regions=[split.regions[1]]),
            actor=actor,
            repository=repository,
        )
    )

    assert len(deleted.regions) == 1
    assert deleted.regions[0].start_ms == 3000


def test_list_music_tracks_returns_clickable_options() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()

    response = asyncio.run(
        list_music_tracks(
            project_id=project_id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            plan_repository=repository.plan_repository,
            providers=[FixtureMusicProvider()],
            mood="clean confident",
        )
    )

    assert response.mood == "clean confident"
    assert response.tracks
    assert response.tracks[0].title == "Clean Confident Groove"
    assert response.tracks[0].beat_map


def test_select_music_can_persist_no_music() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()

    response = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(include_music=False),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        providers=[FixtureMusicProvider()],
    )

    assert response.selection.music_track is None
    assert response.selection.music_track_id is None
    assert response.selection.music_volume == 0
    assert response.selection.sfx_intensity == 0


def test_change_music_chooses_alternative_track() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    first = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(mood="clean confident"),
        actor=actor,
        repository=repository,
        providers=[FixtureMusicProvider()],
    )
    changed = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(mood="clean confident", force=True),
        actor=actor,
        repository=repository,
        providers=[FixtureMusicProvider()],
    )

    assert first.selection.music_track is not None
    assert changed.selection.music_track is not None
    assert (
        changed.selection.music_track.provider_track_id
        != first.selection.music_track.provider_track_id
    )


def test_select_music_reuses_cached_selection() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    first = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(),
        actor=actor,
        repository=repository,
        providers=[FixtureMusicProvider()],
    )
    second = run_select_music(
        project_id=project_id,
        payload=MusicSelectRequest(),
        actor=actor,
        repository=repository,
        providers=[FixtureMusicProvider()],
    )

    assert second.cached is True
    assert second.selection.id == first.selection.id


def test_select_music_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()

    with pytest.raises(ApiError) as exc_info:
        run_select_music(
            project_id=project_id,
            payload=MusicSelectRequest(),
            actor=Actor(user_id=user_id, workspace_id=uuid4()),
            repository=repository,
            providers=[FixtureMusicProvider()],
        )

    assert exc_info.value.status_code in {403, 404}


def test_licensed_catalog_provider_returns_real_audio_metadata(monkeypatch) -> None:
    def fake_get(*_args, **_kwargs):
        return httpx.Response(
            200,
            json=[
                {
                    "provider_track_id": "catalog-clean-1",
                    "title": "Licensed Clean Bed",
                    "license": "motionly_catalog_standard",
                    "duration_ms": 60_000,
                    "mood_tags": ["clean", "confident"],
                    "bpm": 92,
                    "energy": 0.55,
                    "vocals": False,
                    "loopable": True,
                    "beat_map": [0, 650, 1300],
                    "audio_bucket": "licensed-music",
                    "audio_path": "clean/catalog-clean-1.m4a",
                    "raw_metadata": {"catalog": "internal"},
                    "active": True,
                }
            ],
        )

    monkeypatch.setattr("app.services.music.httpx.get", fake_get)
    provider = LicensedCatalogMusicProvider(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )

    tracks = provider.search(mood="clean confident")

    assert tracks[0].provider_track_id == "catalog-clean-1"
    assert tracks[0].provider == "licensed_catalog"
    assert tracks[0].raw_metadata["audio_bucket"] == "licensed-music"
    assert tracks[0].raw_metadata["audio_path"] == "clean/catalog-clean-1.m4a"
