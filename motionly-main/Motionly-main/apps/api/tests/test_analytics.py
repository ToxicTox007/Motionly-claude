import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.api.ai_proposals import decide_proposal
from app.api.analytics import (
    get_ai_edit_analytics,
    get_fine_tuning_dataset,
    record_ai_edit_feedback,
)
from app.api.dependencies import Actor
from app.api.plans import create_plan, update_plan_cuts
from app.repositories.analytics import (
    AI_EDIT_FEEDBACK_EVENT_COLUMNS,
    InMemoryAnalyticsRepository,
    SupabaseAnalyticsRepository,
)
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.ai_proposals import ProposalDecisionRequest, ProposalDecisionType
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
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(editorial_learning_enabled=False))
        )
    )

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
            request=request,
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
            request=request,
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
    # Operational analytics is deliberately not exportable as training data.
    assert dataset.example_count == 0
    assert dataset.examples == []


def test_supabase_analytics_repository_lists_with_explicit_projection(monkeypatch) -> None:
    repository = SupabaseAnalyticsRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(is_success=True, json=lambda: [])

    monkeypatch.setattr("app.repositories.analytics.SupabaseRestClient.request", fake_request)

    assert (
        repository.list_events(
            project_id=project_id,
            workspace_id=workspace_id,
            layer=AIEditLayer.CUTS,
            limit=25,
        )
        == []
    )
    assert calls == [
        (
            "GET",
            "/ai_edit_feedback_events",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "project_id": f"eq.{project_id}",
                    "select": AI_EDIT_FEEDBACK_EVENT_COLUMNS,
                    "order": "created_at.desc",
                    "limit": "25",
                    "layer": "eq.cuts",
                }
            },
        )
    ]


def test_supabase_analytics_async_methods_use_bounded_projections(monkeypatch) -> None:
    repository = SupabaseAnalyticsRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    event_id = uuid4()
    now = "2026-07-06T00:00:00+00:00"
    event_row = {
        "id": str(event_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "edit_plan_id": None,
        "user_id": str(user_id),
        "event_type": "changed",
        "layer": "cuts",
        "decision_id": "cut-1",
        "action": None,
        "source": "manual_editor",
        "ai_payload": {"start_ms": 0},
        "user_payload": {"start_ms": 100},
        "metadata": {},
        "created_at": now,
    }
    responses = [[], [event_row], []]
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(is_success=True, json=lambda: responses.pop(0))

    monkeypatch.setattr(
        "app.repositories.analytics.SupabaseRestClient.async_request",
        fake_async_request,
    )

    event = asyncio.run(
        repository.record_event_async(
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            event_type=AIEditFeedbackEventType.CHANGED,
            layer=AIEditLayer.CUTS,
            source="manual_editor",
            decision_id="cut-1",
            ai_payload={"start_ms": 0},
            user_payload={"start_ms": 100},
        )
    )
    events = asyncio.run(
        repository.list_events_async(
            project_id=project_id,
            workspace_id=workspace_id,
            layer=AIEditLayer.CUTS,
            limit=25,
        )
    )

    assert event.id == event_id
    assert events == []
    assert calls[0] == (
        "GET",
        "/ai_edit_feedback_events",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "event_type": "eq.changed",
                "layer": "eq.cuts",
                "decision_id": "eq.cut-1",
                "select": AI_EDIT_FEEDBACK_EVENT_COLUMNS,
                "limit": "1",
            }
        },
    )
    assert calls[1] == (
        "POST",
        "/ai_edit_feedback_events",
        {
            "params": {"select": AI_EDIT_FEEDBACK_EVENT_COLUMNS},
            "json": [
                {
                    "workspace_id": str(workspace_id),
                    "project_id": str(project_id),
                    "edit_plan_id": None,
                    "user_id": str(user_id),
                    "event_type": "changed",
                    "layer": "cuts",
                    "decision_id": "cut-1",
                    "action": None,
                    "source": "manual_editor",
                    "ai_payload": {"start_ms": 0},
                    "user_payload": {"start_ms": 100},
                    "metadata": {},
                }
            ],
        },
    )
    assert calls[2] == (
        "GET",
        "/ai_edit_feedback_events",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": AI_EDIT_FEEDBACK_EVENT_COLUMNS,
                "order": "created_at.desc",
                "limit": "25",
                "layer": "eq.cuts",
            }
        },
    )


def test_analytics_api_prefers_async_repository_methods() -> None:
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(editorial_learning_enabled=False))
        )
    )

    class AsyncAnalyticsRepository(InMemoryAnalyticsRepository):
        def __init__(self) -> None:
            super().__init__()
            self.async_calls: list[str] = []

        async def record_event_async(self, **kwargs):
            self.async_calls.append("record-event")
            return self.record_event(**kwargs)

        async def list_events_async(self, **kwargs):
            self.async_calls.append("list-events")
            return self.list_events(**kwargs)

        async def summarize_async(self, **kwargs):
            self.async_calls.append("summarize")
            return self.summarize(**kwargs)

        async def fine_tuning_examples_async(self, **kwargs):
            self.async_calls.append("fine-tuning")
            return self.fine_tuning_examples(**kwargs)

    repository = AsyncAnalyticsRepository()

    asyncio.run(
        record_ai_edit_feedback(
            project_id=project_id,
            payload=AIEditFeedbackEventCreateRequest(
                event_type=AIEditFeedbackEventType.KEPT,
                layer=AIEditLayer.CUTS,
                decision_id="cut-1",
                ai_payload={"start_ms": 0},
                user_payload={"start_ms": 0},
                source="render_confirmation",
            ),
            actor=actor,
            repository=repository,
            request=request,
        )
    )
    asyncio.run(
        get_ai_edit_analytics(
            project_id=project_id,
            actor=actor,
            repository=repository,
            layer=AIEditLayer.CUTS,
            limit=25,
        )
    )
    asyncio.run(
        get_fine_tuning_dataset(
            project_id=project_id,
            actor=actor,
            repository=repository,
            limit=25,
        )
    )

    assert repository.async_calls == ["record-event", "list-events", "summarize"]


def test_plan_suggestions_and_manual_cut_overrides_create_feedback_events() -> None:
    plan_repository, project_id, workspace_id, user_id = make_plan_repository()
    analytics_repository = InMemoryAnalyticsRepository()
    request = request_with_analytics(analytics_repository)
    actor = Actor(user_id=user_id, workspace_id=workspace_id)

    from app.services.queue import QueueRegistry
    from tests.worker_job_test_helpers import (
        DummyResponse,
        build_queue_request_state,
        run_enqueued_job,
    )

    registry = QueueRegistry()
    request = build_queue_request_state(
        registry=registry,
        plan_repository=plan_repository,
        analytics_repository=analytics_repository,
        edit_planner_provider=FixtureEditPlannerProvider(),
    )

    created_resp = asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=12),
            actor=actor,
            repository=plan_repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(registry=registry),
            request=request,
            response=DummyResponse(),
        )
    )
    asyncio.run(run_enqueued_job(job_id=created_resp.job_id, request=request))
    proposal_repository = plan_repository._proposal_repository
    proposal = proposal_repository.get(
        proposal_id=created_resp.proposal_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    decision_payload = ProposalDecisionRequest(
        decision=ProposalDecisionType.APPROVE,
        expected_proposal_revision=proposal.revision,
        expected_base_active_plan_id=proposal.artifacts.base_active_plan_id,
        expected_base_active_plan_revision=proposal.artifacts.base_active_plan_revision,
        idempotency_key=f"analytics-plan-{proposal.id}",
    )
    decision = asyncio.run(
        decide_proposal(
            project_id=project_id,
            proposal_id=proposal.id,
            payload=decision_payload,
            request=request,
            actor=actor,
            repository=proposal_repository,
        )
    )
    assert decision.edit_plan_id is not None
    plan = plan_repository.active_plan(project_id=project_id, workspace_id=workspace_id)
    created = SimpleNamespace(plan=plan)

    suggested = analytics_repository.list_events(
        project_id=project_id,
        workspace_id=workspace_id,
        limit=20,
    )
    replay = asyncio.run(
        decide_proposal(
            project_id=project_id,
            proposal_id=proposal.id,
            payload=decision_payload,
            request=request,
            actor=actor,
            repository=proposal_repository,
        )
    )
    suggested_after_replay = analytics_repository.list_events(
        project_id=project_id,
        workspace_id=workspace_id,
        limit=20,
    )
    assert replay.idempotent_replay is True
    assert len(suggested_after_replay) == len(suggested)
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
