import asyncio
from uuid import uuid4

from app.api.clips import get_clip_recommendations
from app.api.dependencies import Actor
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.repositories.visual import InMemoryVisualRepository
from app.schemas.captions import TranscriptWord
from app.schemas.clips import ClipGoal
from app.schemas.visual import CameraMotion, ObjectDetection, SceneSegment, SceneType, VideoManifest
from app.services.clip_finder import find_best_clips


def test_clip_finder_ranks_goal_language_into_every_duration_group() -> None:
    words = _words()
    audio_intelligence = {
        "keywords": [
            {"text": "secret", "score": 0.99},
            {"text": "stop", "score": 0.96},
            {"text": "product", "score": 0.9},
        ],
        "style_signals": {"pace": "fast"},
        "emotion_pacing": {
            "pause_map": [
                {"start_ms": 17_000, "end_ms": 18_100},
                {"start_ms": 36_000, "end_ms": 37_000},
            ],
            "windows": [
                {
                    "start_ms": 18_000,
                    "end_ms": 34_000,
                    "emotion": "excited",
                    "intensity": 0.92,
                }
            ],
        },
    }

    groups, availability, duration_ms = find_best_clips(
        words=words,
        source_duration_ms=90_000,
        goal=ClipGoal.VIRAL_HOOK,
        audio_intelligence=audio_intelligence,
    )

    assert duration_ms == 90_000
    assert availability.transcript is True
    assert availability.audio_intelligence is True
    assert availability.visual_analysis is False
    assert [group.duration_s for group in groups] == [15, 30, 60]
    assert all(group.clips for group in groups)
    assert all(
        [clip.rank for clip in group.clips] == list(range(1, len(group.clips) + 1))
        for group in groups
    )
    assert "stop" in groups[0].clips[0].signals.matched_keywords
    assert groups[0].clips[0].start_ms < groups[0].clips[0].end_ms
    for group in groups:
        for index, left in enumerate(group.clips):
            for right in group.clips[index + 1 :]:
                assert (
                    _overlap_ratio(left.start_ms, left.end_ms, right.start_ms, right.end_ms) < 0.55
                )


def test_clip_finder_respects_authoritative_source_duration() -> None:
    words = [
        *_words(),
        TranscriptWord(
            index=99,
            text="secret",
            start_ms=100_000,
            end_ms=100_250,
            confidence=0.99,
        ),
    ]

    groups, _, source_duration_ms = find_best_clips(
        words=words,
        source_duration_ms=90_000,
        goal=ClipGoal.VIRAL_HOOK,
        audio_intelligence={"keywords": [{"text": "secret", "score": 1}]},
    )

    assert source_duration_ms == 90_000
    assert all(clip.end_ms <= source_duration_ms for group in groups for clip in group.clips)


def test_clip_finder_rejects_sparse_or_silence_heavy_windows() -> None:
    sparse_words = [TranscriptWord(index=0, text="secret", start_ms=0, end_ms=100, confidence=0.9)]
    silence_heavy_words = [
        TranscriptWord(
            index=index,
            text="secret",
            start_ms=index * 200,
            end_ms=index * 200 + 100,
            confidence=0.9,
        )
        for index in range(6)
    ]

    sparse_groups, _, _ = find_best_clips(
        words=sparse_words,
        source_duration_ms=15_000,
        goal=ClipGoal.VIRAL_HOOK,
    )
    silence_heavy_groups, _, _ = find_best_clips(
        words=silence_heavy_words,
        source_duration_ms=15_000,
        goal=ClipGoal.VIRAL_HOOK,
    )

    assert sparse_groups == []
    assert silence_heavy_groups == []


def test_clip_finder_uses_product_visual_context_when_available() -> None:
    groups, availability, _ = find_best_clips(
        words=_words(),
        source_duration_ms=90_000,
        goal=ClipGoal.PRODUCT_MENTION,
        audio_intelligence={"keywords": [{"text": "product", "score": 1}]},
        visual_manifest=_visual_manifest(),
    )

    product_clip = groups[0].clips[0]
    assert availability.visual_analysis is True
    assert "product" in product_clip.transcript_excerpt.lower()
    assert "product" in product_clip.signals.visual_context
    assert any("Visual context supports it" in item for item in product_clip.rationale)


def test_clip_recommendations_endpoint_uses_ready_transcript_without_visual_analysis() -> None:
    upload_repository = InMemoryUploadRepository()
    workspace_id = uuid4()
    project_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="source.mp4",
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=100,
    )
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
        model="fixture-v1",
        confidence=0.94,
        duration_ms=90_000,
        raw_payload={
            "audio_intelligence": {
                "keywords": [{"text": "secret", "score": 0.99}],
                "style_signals": {"pace": "medium"},
            }
        },
        words=_words(),
    )

    response = asyncio.run(
        get_clip_recommendations(
            project_id=project_id,
            actor=Actor(user_id=uuid4(), workspace_id=workspace_id),
            caption_repository=caption_repository,
            visual_repository=InMemoryVisualRepository(upload_repository),
            goal=ClipGoal.VIRAL_HOOK,
            limit_per_duration=2,
        )
    )

    assert response.goal == ClipGoal.VIRAL_HOOK
    assert response.signal_availability.visual_analysis is False
    assert len(response.groups) == 3
    assert all(len(group.clips) <= 2 for group in response.groups)


def _words() -> list[TranscriptWord]:
    phrases = [
        (0, "Today we are talking about a useful creator workflow"),
        (18_200, "Stop making this secret mistake if you want better results"),
        (36_800, "Here is the first step and the framework that explains why it works"),
        (54_000, "This product feature gives every customer a faster demo"),
        (71_000, "Then I realized our story started with one small customer moment"),
    ]
    words: list[TranscriptWord] = []
    index = 0
    for start_ms, phrase in phrases:
        for offset, text in enumerate(phrase.split()):
            word_start = start_ms + offset * 330
            words.append(
                TranscriptWord(
                    index=index,
                    text=text,
                    start_ms=word_start,
                    end_ms=word_start + 250,
                    confidence=0.93,
                )
            )
            index += 1
    return words


def _visual_manifest() -> VideoManifest:
    return VideoManifest(
        duration_ms=90_000,
        provider="fixture",
        scenes=[
            SceneSegment(
                index=0,
                start_ms=0,
                end_ms=35_000,
                representative_timestamp_ms=10_000,
                scene_type=SceneType.TALKING_HEAD,
                camera_motion=CameraMotion.STATIC,
                visual_importance_score=0.35,
            ),
            SceneSegment(
                index=1,
                start_ms=35_000,
                end_ms=65_000,
                representative_timestamp_ms=50_000,
                scene_type=SceneType.PRODUCT,
                camera_motion=CameraMotion.PUSH_IN,
                visual_importance_score=0.96,
                objects=[
                    ObjectDetection(
                        label="product",
                        confidence=0.95,
                        box={"x": 0, "y": 0, "width": 1, "height": 1},
                    )
                ],
            ),
            SceneSegment(
                index=2,
                start_ms=65_000,
                end_ms=90_000,
                representative_timestamp_ms=76_000,
                scene_type=SceneType.TALKING_HEAD,
                camera_motion=CameraMotion.STATIC,
                visual_importance_score=0.4,
            ),
        ],
    )


def _overlap_ratio(left_start: int, left_end: int, right_start: int, right_end: int) -> float:
    overlap = max(0, min(left_end, right_end) - max(left_start, right_start))
    shortest = min(left_end - left_start, right_end - right_start)
    return overlap / shortest if shortest else 0
