import asyncio
from uuid import UUID, uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.music import list_music_tracks, select_music
from app.api.plans import create_plan
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.music import InMemoryMusicRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.captions import LanguageHint
from app.schemas.music import MusicSelectRequest
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.edit_planner import FixtureEditPlannerProvider
from app.services.music import FixtureMusicProvider
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
