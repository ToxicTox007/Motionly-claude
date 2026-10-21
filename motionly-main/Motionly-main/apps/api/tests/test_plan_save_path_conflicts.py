"""One conflict test per plan save path: a stale ``expected_revision`` on every
plan-content route raises :class:`RevisionConflictError` (surfaced as a 409
``collaboration_revision_conflict``). Complements test_revision_concurrency.py
(which already covers the cuts path) so every save path is exercised.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.dependencies import Actor
from app.api.revision_errors import RevisionConflictError
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.captions import LanguageHint
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.transcription_provider import FixtureTranscriptionProvider

STALE_REVISION = 999


def _make_request(plan_repository):
    from app.services.queue import QueueRegistry

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env="test", billing_enabled=False, redis_url=None
                ),
                queue_registry=QueueRegistry(),
                plan_repository=plan_repository,
                operations_repository=None,
                broll_repository=None,
                music_repository=None,
                effects_repository=None,
            ),
        ),
    )


@pytest.fixture
def active_plan():
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()

    uploads = InMemoryUploadRepository()
    uploads.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = uploads.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=1000,
    )
    upload.status = UploadStatus.READY
    upload.metadata.duration_ms = 7000
    upload.metadata.width = 1920
    upload.metadata.height = 1080
    upload.metadata.fps = 30
    upload.metadata.codec = "h264"
    upload.metadata.orientation = "landscape"
    upload.metadata.has_audio = True
    uploads.attach_active_video(project_id=project_id, video_id=upload.id)

    captions = InMemoryCaptionRepository(uploads)
    captions.create_transcript(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status="ready",
        language="en",
        language_hint="auto",
        provider="fixture",
        model="whisper-1",
        confidence=0.95,
        duration_ms=7000,
        raw_payload={},
        words=FixtureTranscriptionProvider()
        .transcribe(audio_path=None, language_hint=LanguageHint.AUTO)
        .words,
    )
    plans = InMemoryPlanRepository(captions)

    from tests.worker_job_test_helpers import seed_active_plan

    seed_active_plan(
        plan_repository=plans,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(target_duration_s=10, broll_density=BrollDensity.NONE),
    )
    record = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    env = SimpleNamespace(
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        plan_repository=plans,
    )
    return env, record


def _actor(env):
    return Actor(user_id=env.user_id, workspace_id=env.workspace_id)


def test_caption_highlights_conflict(active_plan):
    from app.api.plans import update_plan_caption_highlights
    from app.schemas.plans import CaptionHighlight, PlanCaptionHighlightsUpdateRequest

    env, record = active_plan
    payload = PlanCaptionHighlightsUpdateRequest(
        caption_highlights=[CaptionHighlight(timestamp_ms=500, word="hi", effect="pulse")],
        expected_revision=STALE_REVISION,
    )
    with pytest.raises(RevisionConflictError) as exc:
        asyncio.run(
            update_plan_caption_highlights(
                project_id=env.project_id,
                plan_id=record.id,
                payload=payload,
                actor=_actor(env),
                repository=env.plan_repository,
                request=_make_request(env.plan_repository),
            )
        )
    assert exc.value.expected == STALE_REVISION
    assert exc.value.actual == 1


def test_manual_elements_conflict(active_plan):
    from app.api.plans import update_plan_manual_elements
    from app.schemas.plans import ManualElement, PlanManualElementsUpdateRequest

    env, record = active_plan
    payload = PlanManualElementsUpdateRequest(
        manual_elements=[
            ManualElement(
                id="text-1",
                type="text",
                name="Title",
                zone="canvas",
                start_ms=0,
                end_ms=3000,
                properties={"content": "Hello"},
            )
        ],
        expected_revision=STALE_REVISION,
    )
    with pytest.raises(RevisionConflictError) as exc:
        asyncio.run(
            update_plan_manual_elements(
                project_id=env.project_id,
                plan_id=record.id,
                payload=payload,
                actor=_actor(env),
                repository=env.plan_repository,
                request=_make_request(env.plan_repository),
            )
        )
    assert exc.value.expected == STALE_REVISION


def test_compile_timeline_conflict(active_plan):
    from app.api.plans import compile_project_timeline

    env, record = active_plan
    with pytest.raises(RevisionConflictError) as exc:
        asyncio.run(
            compile_project_timeline(
                project_id=env.project_id,
                plan_id=record.id,
                actor=_actor(env),
                repository=env.plan_repository,
                request=_make_request(env.plan_repository),
                expected_revision=STALE_REVISION,
            )
        )
    assert exc.value.expected == STALE_REVISION
