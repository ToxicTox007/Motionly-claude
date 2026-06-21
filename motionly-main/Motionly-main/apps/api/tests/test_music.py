import asyncio
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.music import list_music_tracks, select_music, update_music_regions
from app.api.plans import create_plan
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.music import InMemoryMusicRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.captions import LanguageHint
from app.schemas.music import MusicRegion, MusicRegionsUpdateRequest, MusicSelectRequest
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.edit_planner import FixtureEditPlannerProvider
from app.services.music import FixtureMusicProvider, LicensedCatalogMusicProvider
from app.services.queue import MusicQueue, PlanQueue
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
    asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(broll_density=BrollDensity.MEDIUM),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=plan_repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
    )
    return InMemoryMusicRepository(plan_repository), project_id, workspace_id, user_id


def test_select_music_creates_selection() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()

    response = asyncio.run(
        select_music(
            project_id=project_id,
            payload=MusicSelectRequest(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            plan_repository=repository.plan_repository,
            music_queue=MusicQueue(),
            providers=[FixtureMusicProvider()],
        )
    )

    assert response.status == "ready"
    assert response.selection.music_track is not None
    assert response.selection.music_track.title == "Ambient Luxury Bed"
    assert response.selection.music_volume == 0.25
    assert response.selection.sfx_intensity == 0.25
    assert len(response.selection.regions) == 1
    assert response.selection.regions[0].start_ms == 0
    assert response.selection.regions[0].end_ms > 0


def test_update_music_regions_splits_and_deletes_region() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    selected = asyncio.run(
        select_music(
            project_id=project_id,
            payload=MusicSelectRequest(),
            actor=actor,
            repository=repository,
            plan_repository=repository.plan_repository,
            music_queue=MusicQueue(),
            providers=[FixtureMusicProvider()],
        )
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

    response = asyncio.run(
        select_music(
            project_id=project_id,
            payload=MusicSelectRequest(include_music=False),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            plan_repository=repository.plan_repository,
            music_queue=MusicQueue(),
            providers=[FixtureMusicProvider()],
        )
    )

    assert response.selection.music_track is None
    assert response.selection.music_track_id is None
    assert response.selection.music_volume == 0
    assert response.selection.sfx_intensity == 0


def test_change_music_chooses_alternative_track() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    first = asyncio.run(
        select_music(
            project_id=project_id,
            payload=MusicSelectRequest(mood="clean confident"),
            actor=actor,
            repository=repository,
            plan_repository=repository.plan_repository,
            music_queue=MusicQueue(),
            providers=[FixtureMusicProvider()],
        )
    )
    changed = asyncio.run(
        select_music(
            project_id=project_id,
            payload=MusicSelectRequest(mood="clean confident", force=True),
            actor=actor,
            repository=repository,
            plan_repository=repository.plan_repository,
            music_queue=MusicQueue(),
            providers=[FixtureMusicProvider()],
        )
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

    first = asyncio.run(
        select_music(
            project_id=project_id,
            payload=MusicSelectRequest(),
            actor=actor,
            repository=repository,
            plan_repository=repository.plan_repository,
            music_queue=MusicQueue(),
            providers=[FixtureMusicProvider()],
        )
    )
    second = asyncio.run(
        select_music(
            project_id=project_id,
            payload=MusicSelectRequest(),
            actor=actor,
            repository=repository,
            plan_repository=repository.plan_repository,
            music_queue=MusicQueue(),
            providers=[FixtureMusicProvider()],
        )
    )

    assert second.cached is True
    assert second.selection.id == first.selection.id


def test_select_music_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_music_repository()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            select_music(
                project_id=project_id,
                payload=MusicSelectRequest(),
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
                plan_repository=repository.plan_repository,
                music_queue=MusicQueue(),
                providers=[FixtureMusicProvider()],
            )
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
