import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.api.analytics import (
    get_ai_edit_analytics,
    get_fine_tuning_dataset,
    record_ai_edit_feedback,
)
from app.api.dependencies import Actor
from app.api.plans import create_plan, update_plan_cuts
from app.repositories.analytics import InMemoryAnalyticsRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.analytics import (
    AIEditFeedbackEventCreateRequest,
    AIEditFeedbackEventType,
    AIEditLayer,
)
from app.schemas.captions import LanguageHint
from app.schemas.plans import EditPlanStyle, PlanCreateRequest, PlanCutsUpdateRequest
from app.schemas.uploads import UploadStatus
from app.services.edit_planner import FixtureEditPlannerProvider
from app.services.queue import PlanQueue
from app.services.transcription_provider import FixtureTranscriptionProvider


def test_ai_edit_analytics_summarizes_events_and_builds_fine_tuning_examples() -> None:
    repository = InMemoryAnalyticsRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    asyncio.run(
        record_ai_edit_feedback(
            project_id=project_id,
            payload=AIEditFeedbackEventCreateRequest(
                event_type=AIEditFeedbackEventType.KEPT,
                layer=AIEditLayer.CUTS,
                decision_id="cut-1",
                ai_payload={"start_ms": 0, "end_ms": 1200},
                user_payload={"start_ms": 0, "end_ms": 1200},
                source="render_confirmation",
            ),
            actor=actor,
            repository=repository,
        )
    )
    asyncio.run(
        record_ai_edit_feedback(
            project_id=project_id,
            payload=AIEditFeedbackEventCreateRequest(
                event_type=AIEditFeedbackEventType.CHANGED,
                layer=AIEditLayer.CUTS,
                decision_id="cut-2",
                ai_payload={"start_ms": 1200, "end_ms": 2600},
                user_payload={"start_ms": 1400, "end_ms": 2400},
                source="manual_editor",
            ),
            actor=actor,
            repository=repository,
        )
    )

    analytics = asyncio.run(
        get_ai_edit_analytics(
            project_id=project_id,
            actor=actor,
            repository=repository,
        )
    )
    cuts = next(layer for layer in analytics.summary.layers if layer.layer == AIEditLayer.CUTS)
    assert analytics.summary.total_events == 2
    assert cuts.kept == 1
    assert cuts.changed == 1
    assert cuts.keep_rate == 0.5

    dataset = asyncio.run(
        get_fine_tuning_dataset(
            project_id=project_id,
            actor=actor,
            repository=repository,
        )
    )
    assert dataset.example_count == 2
    assert {example.signal.value for example in dataset.examples} == {"positive", "correction"}


def test_plan_suggestions_and_manual_cut_overrides_create_feedback_events() -> None:
    plan_repository, project_id, workspace_id, user_id = make_plan_repository()
    analytics_repository = InMemoryAnalyticsRepository()
    request = request_with_analytics(analytics_repository)
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    created = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=12),
            actor=actor,
            repository=plan_repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
            request=request,
        )
    )
    suggested = analytics_repository.list_events(
        project_id=project_id,
        workspace_id=workspace_id,
        limit=20,
    )
    assert any(
        event.event_type == AIEditFeedbackEventType.AI_SUGGESTED and event.layer == AIEditLayer.CUTS
        for event in suggested
    )

    changed_cuts = [
        cut.model_copy(update={"end_ms": max(cut.start_ms + 500, cut.end_ms - 250)})
        for cut in created.plan.plan.cuts
    ]
    asyncio.run(
        update_plan_cuts(
            project_id=project_id,
            plan_id=created.plan.id,
            payload=PlanCutsUpdateRequest(cuts=changed_cuts),
            actor=actor,
            repository=plan_repository,
            request=request,
        )
    )

    events = analytics_repository.list_events(
        project_id=project_id,
        workspace_id=workspace_id,
        layer=AIEditLayer.CUTS,
        limit=20,
    )
    assert any(event.event_type == AIEditFeedbackEventType.CHANGED for event in events)
    examples = analytics_repository.fine_tuning_examples(
        project_id=project_id,
        workspace_id=workspace_id,
    )
    assert any(example.signal.value == "correction" for example in examples)


def make_plan_repository() -> tuple[InMemoryPlanRepository, UUID, UUID, UUID]:
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
        model="whisper-1",
        confidence=0.95,
        duration_ms=12_000,
        raw_payload={},
        words=FixtureTranscriptionProvider()
        .transcribe(audio_path=None, language_hint=LanguageHint.AUTO)
        .words,
    )
    return InMemoryPlanRepository(caption_repository), project_id, workspace_id, user_id


def request_with_analytics(repository: InMemoryAnalyticsRepository):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(analytics_repository=repository),
        ),
    )
