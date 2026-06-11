import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.captions import create_transcription, generate_captions
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.repositories.captions import InMemoryCaptionRepository, SupabaseCaptionRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.captions import (
    CaptionGenerateRequest,
    CaptionPresetKey,
    CaptionSegment,
    LanguageHint,
    TranscriptionCreateRequest,
    TranscriptWord,
)
from app.schemas.uploads import UploadStatus
from app.services.caption_chunking import chunk_caption_words
from app.services.caption_presets import CAPTION_SCHEMA_VERSION, get_caption_preset
from app.services.queue import TranscriptionQueue
from app.services.transcription_provider import (
    FixtureTranscriptionProvider,
    normalize_transcription_language,
)


def make_request() -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    media_work_dir=".motionly/test-storage",
                    transcription_debug_retain_audio=False,
                )
            )
        )
    )


def make_repository() -> tuple[InMemoryCaptionRepository, UUID, UUID, UUID]:
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
    upload.metadata.has_audio = True
    upload.status = UploadStatus.READY
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    return InMemoryCaptionRepository(upload_repository), project_id, workspace_id, user_id


def test_transcription_and_caption_generation_happy_path() -> None:
    repository, project_id, workspace_id, user_id = make_repository()

    transcription = asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(language_hint=LanguageHint.ENGLISH),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )

    assert transcription.status == "ready"
    assert transcription.transcript_id is not None

    captions = asyncio.run(
        generate_captions(
            project_id=project_id,
            payload=CaptionGenerateRequest(preset=CaptionPresetKey.BOLD_INFLUENCER),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            transcription_queue=TranscriptionQueue(),
        )
    )

    assert captions.status == "ready"
    assert captions.track.preset == CaptionPresetKey.BOLD_INFLUENCER
    assert captions.track.schema_version == 1
    assert captions.track.segments
    assert all(segment.end_ms - segment.start_ms >= 450 for segment in captions.track.segments)


def test_transcription_requires_ready_source_video() -> None:
    repository, project_id, workspace_id, user_id = make_repository()
    active_upload = repository.get_active_upload(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    active_upload.status = UploadStatus.PROCESSING

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            create_transcription(
                project_id=project_id,
                payload=TranscriptionCreateRequest(language_hint=LanguageHint.ENGLISH),
                request=make_request(),
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=repository,
                transcription_provider=FixtureTranscriptionProvider(),
                transcription_queue=TranscriptionQueue(),
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "source_video_not_ready"


@pytest.mark.parametrize(
    ("provider_language", "hint", "expected"),
    [
        ("english", LanguageHint.AUTO, "en"),
        ("en-US", LanguageHint.AUTO, "en"),
        ("hindi", LanguageHint.AUTO, "hi"),
        ("hi-IN", LanguageHint.AUTO, "hi"),
        ("", LanguageHint.ENGLISH, "en"),
        ("fr", LanguageHint.AUTO, "unknown"),
    ],
)
def test_provider_language_is_normalized_for_storage(
    provider_language: str,
    hint: LanguageHint,
    expected: str,
) -> None:
    assert normalize_transcription_language(provider_language, hint) == expected


def test_supabase_caption_repository_reads_tracks_with_segments(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    repository = SupabaseCaptionRepository(
        upload_repository=upload_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    transcript_id = uuid4()
    track_id = uuid4()
    segment_id = uuid4()
    now = "2026-06-03T07:00:00+00:00"
    track_row = {
        "id": str(track_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "transcript_id": str(transcript_id),
        "preset": CaptionPresetKey.BOLD_INFLUENCER.value,
        "schema_version": CAPTION_SCHEMA_VERSION,
        "style": get_caption_preset(CaptionPresetKey.BOLD_INFLUENCER).style.model_dump(
            mode="json",
        ),
        "hide_filler_words": False,
        "created_at": now,
        "updated_at": now,
    }
    segment_row = {
        "id": str(segment_id),
        "caption_track_id": str(track_id),
        "segment_index": 0,
        "start_ms": 120,
        "end_ms": 640,
        "text": "ship it",
        "line_count": 1,
        "words": [
            {
                "index": 0,
                "text": "ship",
                "start_ms": 120,
                "end_ms": 340,
                "confidence": 0.91,
            },
            {
                "index": 1,
                "text": "it",
                "start_ms": 360,
                "end_ms": 640,
                "confidence": 0.9,
            },
        ],
    }
    responses = [
        [track_row],
        [segment_row],
    ]

    def fake_get(*_args, **_kwargs):
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0))

    monkeypatch.setattr("app.repositories.captions.httpx.get", fake_get)

    track = repository.get_caption_track(
        project_id=project_id,
        track_id=track_id,
        workspace_id=workspace_id,
    )

    assert track.id == track_id
    assert track.segments == [
        CaptionSegment(
            id=segment_id,
            index=0,
            start_ms=120,
            end_ms=640,
            text="ship it",
            line_count=1,
            words=[
                TranscriptWord(
                    index=0,
                    text="ship",
                    start_ms=120,
                    end_ms=340,
                    confidence=0.91,
                ),
                TranscriptWord(
                    index=1,
                    text="it",
                    start_ms=360,
                    end_ms=640,
                    confidence=0.9,
                ),
            ],
        )
    ]


def test_caption_chunking_is_pause_and_punctuation_aware() -> None:
    repository, project_id, workspace_id, user_id = make_repository()
    asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )
    transcript = repository.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    assert transcript is not None

    segments = chunk_caption_words(transcript.words)

    assert len(segments) >= 2
    assert all(1 <= len(segment.words) <= 4 for segment in segments)
    assert all(segment.line_count <= 2 for segment in segments)


def test_transcription_marks_video_without_audio_unavailable() -> None:
    repository, project_id, workspace_id, user_id = make_repository()
    upload = repository.get_active_upload(project_id=project_id, workspace_id=workspace_id)
    upload.metadata.has_audio = False

    response = asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )

    assert response.status == "captions_unavailable"


def test_caption_generation_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_repository()
    asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            generate_captions(
                project_id=project_id,
                payload=CaptionGenerateRequest(),
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
                transcription_queue=TranscriptionQueue(),
            )
        )

    assert exc_info.value.status_code in {403, 404}
