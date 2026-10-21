import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.captions import (
    create_transcription as create_transcription_api,
)
from app.api.captions import (
    generate_captions,
    list_captions,
    update_caption_segments,
    update_caption_track,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.repositories.captions import (
    CAPTION_SEGMENT_COLUMNS,
    CAPTION_TRACK_COLUMNS,
    CAPTION_TRACK_LIST_COLUMNS,
    TRANSCRIPT_COLUMNS,
    TRANSCRIPT_WORD_COLUMNS,
    InMemoryCaptionRepository,
    SupabaseCaptionRepository,
)
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.captions import (
    CaptionGenerateRequest,
    CaptionPresetKey,
    CaptionSegment,
    CaptionSegmentsUpdateRequest,
    CaptionUpdateRequest,
    LanguageHint,
    TranscriptionCreateRequest,
    TranscriptWord,
)
from app.schemas.uploads import UploadStatus
from app.services.ass_captions import caption_track_to_ass
from app.services.caption_chunking import chunk_caption_words
from app.services.caption_presets import CAPTION_SCHEMA_VERSION, get_caption_preset
from app.services.queue import TranscriptionQueue
from app.services.transcription_jobs import run_transcription_for_upload
from app.services.transcription_provider import (
    FixtureTranscriptionProvider,
    normalize_transcription_language,
)


async def create_transcription(**kwargs):
    """Exercise the request enqueue and the direct media-worker execution as two steps."""
    response = await create_transcription_api(**kwargs)
    repository = kwargs["repository"]
    actor = kwargs["actor"]
    project_id = kwargs["project_id"]
    upload = repository.get_active_upload(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    transcript = run_transcription_for_upload(
        upload_repository=repository.upload_repository,
        caption_repository=repository,
        transcription_provider=kwargs["transcription_provider"],
        workspace_id=actor.workspace_id,
        project_id=project_id,
        video_id=upload.id,
        media_work_dir=".motionly/test-storage",
        supabase_url=None,
        service_role_key=None,
        retain_debug_audio=False,
    )
    return response.model_copy(update={"status": transcript.status})


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


def run_generate_captions(
    *,
    project_id: UUID,
    payload: CaptionGenerateRequest,
    actor: Actor,
    repository: InMemoryCaptionRepository,
    transcription_queue: TranscriptionQueue,
) -> object:
    from app.schemas.captions import CaptionGenerateResponse
    from app.services.queue import QueueRegistry
    from tests.worker_job_test_helpers import (
        DummyResponse,
        build_queue_request_state,
        run_enqueued_job,
    )

    registry = QueueRegistry()
    transcription_queue = TranscriptionQueue(registry=registry)
    request = build_queue_request_state(
        registry=registry,
        caption_repository=repository,
    )
    request.app.state.transcription_queue = transcription_queue

    response = DummyResponse()
    captions_response = asyncio.run(
        generate_captions(
            project_id=project_id,
            payload=payload,
            actor=actor,
            repository=repository,
            transcription_queue=transcription_queue,
            request=request,
            response=response,
        )
    )
    if getattr(captions_response, "status", None) == "queued":
        asyncio.run(run_enqueued_job(job_id=captions_response.job_id, request=request))
        tracks = repository.list_caption_tracks(
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        track = sorted(tracks, key=lambda item: item.updated_at, reverse=True)[0]
        captions_response = CaptionGenerateResponse(
            track=track.to_response(),
            job_id=captions_response.job_id,
            status="ready",
        )
    return captions_response



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
    transcript = repository.get_transcript(
        project_id=project_id,
        transcript_id=transcription.transcript_id,
        workspace_id=workspace_id,
    )
    assert transcript.raw_payload["audio_intelligence"]["keywords"]
    assert transcript.raw_payload["audio_intelligence"]["style_signals"]["predicted_style"]
    assert transcript.raw_payload["audio_intelligence"]["speaker_diarization"]["segments"]
    assert transcript.raw_payload["audio_intelligence"]["emotion_pacing"]["windows"]
    assert transcript.raw_payload["audio_intelligence"]["sound_classification"]["labels"]

    captions = run_generate_captions(
        project_id=project_id,
        payload=CaptionGenerateRequest(preset=CaptionPresetKey.BOLD_INFLUENCER),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        transcription_queue=TranscriptionQueue(),
    )

    assert captions.status == "ready"
    assert captions.track.preset == CaptionPresetKey.BOLD_INFLUENCER
    assert captions.track.schema_version == CAPTION_SCHEMA_VERSION
    assert captions.track.segments
    assert all(segment.end_ms - segment.start_ms >= 450 for segment in captions.track.segments)


def test_stage_5_caption_modes_generate_mode_specific_tracks() -> None:
    repository, project_id, workspace_id, user_id = make_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    transcription = asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(language_hint=LanguageHint.ENGLISH),
            request=make_request(),
            actor=actor,
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )
    assert transcription.transcript_id is not None

    captions = run_generate_captions(
        project_id=project_id,
        payload=CaptionGenerateRequest(preset=CaptionPresetKey.EDUCATIONAL_CARD),
        actor=actor,
        repository=repository,
        transcription_queue=TranscriptionQueue(),
    )

    assert captions.track.schema_version == CAPTION_SCHEMA_VERSION
    assert captions.track.preset == CaptionPresetKey.EDUCATIONAL_CARD
    assert captions.track.style.mode == "educational_card"
    assert captions.track.style.animation == "edu_step"
    assert captions.track.style.background_opacity > 0.9
    assert captions.track.segments
    assert all(
        segment.line_count <= captions.track.style.max_lines for segment in captions.track.segments
    )
    assert any(segment.highlight_words for segment in captions.track.segments)

    ass = caption_track_to_ass(segments=captions.track.segments[:1], style=captions.track.style)
    assert "Step 1" in ass
    assert "BackColour" in ass


def test_transcription_preserves_expanded_language_hint() -> None:
    repository, project_id, workspace_id, user_id = make_repository()

    transcription = asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(language_hint=LanguageHint.SPANISH),
            request=make_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )

    assert transcription.transcript_id is not None
    transcript = repository.get_transcript(
        project_id=project_id,
        transcript_id=transcription.transcript_id,
        workspace_id=workspace_id,
    )
    assert transcript.language == "es"
    assert transcript.language_hint == LanguageHint.SPANISH


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
        ("fr", LanguageHint.AUTO, "fr"),
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
    responses = [[track_row], [segment_row]]
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0))

    monkeypatch.setattr("app.repositories.captions.SupabaseRestClient.request", fake_request)

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
    assert calls[0] == (
        "GET",
        "/caption_tracks",
        {
            "params": {
                "id": f"eq.{track_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": CAPTION_TRACK_COLUMNS,
                "limit": "1",
            }
        },
    )
    assert calls[1] == (
        "GET",
        "/caption_segments",
        {
            "params": {
                "caption_track_id": f"eq.{track_id}",
                "project_id": f"eq.{project_id}",
                "workspace_id": f"eq.{workspace_id}",
                "select": CAPTION_SEGMENT_COLUMNS,
                "order": "segment_index.asc",
                "limit": "1000",
            }
        },
    )


@pytest.mark.anyio
async def test_supabase_caption_async_reads_use_bounded_projections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SupabaseCaptionRepository(
        upload_repository=InMemoryUploadRepository(),
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    transcript_id = uuid4()
    track_id = uuid4()
    segment_id = uuid4()
    word_id = uuid4()
    now = "2026-07-06T00:00:00+00:00"
    transcript_row = {
        "id": str(transcript_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "uploaded_video_id": str(uuid4()),
        "status": "ready",
        "language": "en",
        "language_hint": "auto",
        "provider": "fixture",
        "model": "fixture",
        "confidence": 0.95,
        "quality_status": "ok",
        "quality_reasons": [],
        "duration_ms": 1_000,
        "raw_payload": {},
        "error_code": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    word_row = {
        "id": str(word_id),
        "word_index": 0,
        "text": "ship",
        "start_ms": 0,
        "end_ms": 250,
        "confidence": 0.9,
    }
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
        "caption_segments": [
            {
                "id": str(segment_id),
                "caption_track_id": str(track_id),
                "segment_index": 0,
                "start_ms": 0,
                "end_ms": 250,
                "text": "ship",
                "line_count": 1,
                "words": [
                    {
                        "id": str(word_id),
                        "index": 0,
                        "text": "ship",
                        "start_ms": 0,
                        "end_ms": 250,
                        "confidence": 0.9,
                    }
                ],
                "highlight_words": [],
            }
        ],
    }
    responses = [[transcript_row], [word_row], [track_row]]
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0))

    monkeypatch.setattr(
        "app.repositories.captions.SupabaseRestClient.async_request",
        fake_async_request,
    )

    transcript = await repository.latest_transcript_async(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    tracks = await repository.list_caption_tracks_async(
        project_id=project_id,
        workspace_id=workspace_id,
        limit=25,
    )

    assert transcript is not None
    assert transcript.words[0].text == "ship"
    assert tracks[0].id == track_id
    assert tracks[0].segments[0].text == "ship"
    assert calls[0][2]["params"] == {
        "workspace_id": f"eq.{workspace_id}",
        "project_id": f"eq.{project_id}",
        "select": TRANSCRIPT_COLUMNS,
        "order": "updated_at.desc",
        "limit": "1",
    }
    assert calls[1][2]["params"] == {
        "transcript_id": f"eq.{transcript_id}",
        "project_id": f"eq.{project_id}",
        "workspace_id": f"eq.{workspace_id}",
        "select": TRANSCRIPT_WORD_COLUMNS,
        "order": "word_index.asc",
        "limit": "5000",
    }
    assert calls[2][2]["params"] == {
        "workspace_id": f"eq.{workspace_id}",
        "project_id": f"eq.{project_id}",
        "select": CAPTION_TRACK_LIST_COLUMNS,
        "order": "updated_at.desc",
        "limit": "25",
    }
    # B28: tracks and their segments load in ONE embedded query (no calls[3]).
    assert len(calls) == 3


def test_caption_api_prefers_async_repository_methods() -> None:
    request = make_request()
    repository, project_id, workspace_id, user_id = make_repository()

    class AsyncCaptionRepository(InMemoryCaptionRepository):
        def __init__(self, upload_repository):
            super().__init__(upload_repository)
            self.async_calls: list[str] = []

        async def latest_transcript_async(self, **kwargs):
            self.async_calls.append("latest-transcript")
            return self.latest_transcript(**kwargs)

        async def get_transcript_async(self, **kwargs):
            self.async_calls.append("get-transcript")
            return self.get_transcript(**kwargs)

        async def list_caption_tracks_async(self, **kwargs):
            self.async_calls.append("list-tracks")
            return self.list_caption_tracks(**kwargs)

        async def get_caption_track_async(self, **kwargs):
            self.async_calls.append("get-track")
            return self.get_caption_track(**kwargs)

        async def update_caption_track_async(self, **kwargs):
            self.async_calls.append("update-track")
            return self.update_caption_track(**kwargs)

        async def replace_caption_segments_async(self, **kwargs):
            self.async_calls.append("replace-segments")
            return self.replace_caption_segments(**kwargs)

    async_repository = AsyncCaptionRepository(repository.upload_repository)
    transcription = asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(language_hint=LanguageHint.AUTO),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=async_repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )
    captions = run_generate_captions(
        project_id=project_id,
        payload=CaptionGenerateRequest(preset=CaptionPresetKey.BOLD_INFLUENCER),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=async_repository,
        transcription_queue=TranscriptionQueue(),
    )
    asyncio.run(
        list_captions(
            project_id=project_id,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=async_repository,
        )
    )
    asyncio.run(
        update_caption_track(
            project_id=project_id,
            track_id=captions.track.id,
            payload=CaptionUpdateRequest(preset=CaptionPresetKey.BOLD_INFLUENCER),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=async_repository,
            transcription_queue=TranscriptionQueue(),
            request=request,
        )
    )
    asyncio.run(
        update_caption_segments(
            project_id=project_id,
            track_id=captions.track.id,
            payload=CaptionSegmentsUpdateRequest(segments=captions.track.segments),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=async_repository,
            request=request,
        )
    )

    assert transcription.transcript_id is not None
    assert async_repository.async_calls == [
        "latest-transcript",
        "list-tracks",
        "get-track",
        "get-transcript",
        "update-track",
        "get-track",
        "replace-segments",
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


def test_update_caption_segments_persists_timeline_edits() -> None:
    repository, project_id, workspace_id, user_id = make_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(),
            request=make_request(),
            actor=actor,
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )
    captions = run_generate_captions(
        project_id=project_id,
        payload=CaptionGenerateRequest(preset=CaptionPresetKey.BOLD_INFLUENCER),
        actor=actor,
        repository=repository,
        transcription_queue=TranscriptionQueue(),
    )
    first = captions.track.segments[0]
    second = captions.track.segments[1]

    updated = asyncio.run(
        update_caption_segments(
            project_id=project_id,
            track_id=captions.track.id,
            payload=CaptionSegmentsUpdateRequest(
                segments=[
                    second.model_copy(
                        update={
                            "start_ms": second.start_ms + 100,
                            "highlight_words": ["ship"],
                        }
                    ),
                    first.model_copy(update={"end_ms": first.end_ms - 100}),
                ],
            ),
            actor=actor,
            repository=repository,
        )
    )

    saved = repository.get_caption_track(
        project_id=project_id,
        track_id=captions.track.id,
        workspace_id=workspace_id,
    )
    assert [segment.index for segment in updated.track.segments] == [0, 1]
    assert updated.track.segments[0].text == second.text
    assert updated.track.segments[0].highlight_words == ["ship"]
    assert saved.segments[1].end_ms == first.end_ms - 100


def test_update_caption_track_persists_custom_style() -> None:
    repository, project_id, workspace_id, user_id = make_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(),
            request=make_request(),
            actor=actor,
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )
    captions = run_generate_captions(
        project_id=project_id,
        payload=CaptionGenerateRequest(preset=CaptionPresetKey.BOLD_INFLUENCER),
        actor=actor,
        repository=repository,
        transcription_queue=TranscriptionQueue(),
    )
    custom_style = captions.track.style.model_copy(
        update={
            "font_family": "Inter Tight",
            "text_color": "#fffbeb",
            "accent_color": "#22c55e",
            "position_x_pct": 48,
            "position_y_pct": 72,
        }
    )

    updated = asyncio.run(
        update_caption_track(
            project_id=project_id,
            track_id=captions.track.id,
            payload=CaptionUpdateRequest(style=custom_style),
            actor=actor,
            repository=repository,
            transcription_queue=TranscriptionQueue(),
        )
    )

    assert updated.track.style.font_family == "Inter Tight"
    assert updated.track.style.position_x_pct == 48


def test_update_caption_segments_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    asyncio.run(
        create_transcription(
            project_id=project_id,
            payload=TranscriptionCreateRequest(),
            request=make_request(),
            actor=actor,
            repository=repository,
            transcription_provider=FixtureTranscriptionProvider(),
            transcription_queue=TranscriptionQueue(),
        )
    )
    captions = run_generate_captions(
        project_id=project_id,
        payload=CaptionGenerateRequest(),
        actor=actor,
        repository=repository,
        transcription_queue=TranscriptionQueue(),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            update_caption_segments(
                project_id=project_id,
                track_id=captions.track.id,
                payload=CaptionSegmentsUpdateRequest(segments=captions.track.segments),
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
            )
        )

    assert exc_info.value.status_code in {403, 404}


def test_update_caption_segments_validates_ranges() -> None:
    with pytest.raises(ValueError):
        CaptionSegmentsUpdateRequest(
            segments=[
                CaptionSegment(index=0, start_ms=1000, end_ms=900, text="bad", line_count=1),
            ],
        )


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
    assert response.transcript_id is not None
    transcript = repository.get_transcript(
        project_id=project_id,
        transcript_id=response.transcript_id,
        workspace_id=workspace_id,
    )
    audio_intelligence = transcript.raw_payload["audio_intelligence"]
    assert audio_intelligence["sound_classification"]["labels"][0]["label"] == "no_audio"
    assert audio_intelligence["speaker_diarization"]["speaker_count"] == 0


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
        run_generate_captions(
            project_id=project_id,
            payload=CaptionGenerateRequest(),
            actor=Actor(user_id=user_id, workspace_id=uuid4()),
            repository=repository,
            transcription_queue=TranscriptionQueue(),
        )

    assert exc_info.value.status_code in {403, 404}
