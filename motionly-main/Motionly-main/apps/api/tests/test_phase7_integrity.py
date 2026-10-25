import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.api.feedback import create_launch_feedback
from app.api.plans import _apply_phase5_learning
from app.repositories.analytics import InMemoryAnalyticsRepository
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.editorial_learning import InMemoryEditorialLearningRepository
from app.repositories.renders import RenderJobRecord
from app.repositories.scale import InMemoryScaleRepository
from app.schemas.analytics import AIEditFeedbackEventType, AIEditLayer
from app.schemas.billing import UsageEventType
from app.schemas.broll import StockOrientation, StockProviderKey
from app.schemas.feedback import LaunchFeedbackCreateRequest
from app.schemas.plans import PlanCreateRequest
from app.schemas.renders import CutSegment, RenderJobStatus
from app.schemas.scale import ScaleResourceStatus
from app.services.ai_feedback import record_ai_feedback
from app.services.analytics_recovery import AnalyticsRecoveryQueue
from app.services.outcomes import record_render_success_outcome
from app.services.scale import execute_scale_resource
from app.services.stock_video import ensure_owned_stock_asset
from app.services.storage import StorageSigner


def test_preference_learning_uses_latest_nonempty_feedback_per_layer() -> None:
    workspace_id, project_id, user_id = uuid4(), uuid4(), uuid4()
    learning = InMemoryEditorialLearningRepository()
    learning.set_policy_if_revision(
        workspace_id=workspace_id,
        user_id=user_id,
        memory_enabled=True,
        training_enabled=True,
        policy_version="editorial-consent/v1",
        retention_days=None,
        expected_revision=None,
    )
    older = learning.capture_delta(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        source_event_id=uuid4(),
        layer="cuts",
        delta={"before": {"pace": "normal"}, "after": {"pace": "slow"}},
        normalizer_version="editorial-delta/v1",
        memory_eligible=True,
        training_eligible=True,
    )
    newer = learning.capture_delta(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        source_event_id=uuid4(),
        layer="cuts",
        delta={"before": {"pace": "slow"}, "after": {"pace": "fast"}},
        normalizer_version="editorial-delta/v1",
        memory_eligible=True,
        training_eligible=True,
    )
    assert older is not None and newer is not None
    older = replace(older, created_at=datetime.now(UTC) - timedelta(minutes=2))
    newer = replace(newer, created_at=datetime.now(UTC) - timedelta(minutes=1))
    learning.deltas[older.id] = older
    learning.deltas[newer.id] = newer
    dataset = learning.freeze_dataset(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        minimum_examples=1,
    )
    scale = InMemoryScaleRepository()
    resource = scale.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="model_training",
        name="Preference summary",
        status=ScaleResourceStatus.QUEUED,
        payload={
            "minimum_examples": 1,
            "editorial_dataset_id": str(dataset.id),
            "editorial_user_id": str(user_id),
        },
    )

    completed = execute_scale_resource(
        repository=scale,
        resource_id=resource.id,
        workspace_id=workspace_id,
        settings=None,
        upload_repository=None,
        broll_repository=None,
        visual_provider=None,
        analytics_repository=None,
        editorial_learning_repository=learning,
        stem_separator=None,
    )

    assert completed.result["preferences"]["cuts"] == {"pace": "fast"}


def test_promoted_editorial_memory_is_scoped_to_the_project() -> None:
    workspace_id, first_project_id, second_project_id, user_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    learning = InMemoryEditorialLearningRepository()
    learning.set_policy_if_revision(
        workspace_id=workspace_id,
        user_id=user_id,
        memory_enabled=True,
        training_enabled=True,
        policy_version="editorial-consent/v1",
        retention_days=None,
        expected_revision=None,
    )
    manifest_hashes = {}
    for project_id, pace in ((first_project_id, "fast"), (second_project_id, "slow")):
        learning.capture_delta(
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            source_event_id=uuid4(),
            layer="cuts",
            delta={"before": {"pace": "normal"}, "after": {"pace": pace}},
            normalizer_version="editorial-delta/v1",
            memory_eligible=True,
            training_eligible=True,
        )
        dataset = learning.freeze_dataset(
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            minimum_examples=1,
        )
        learning.promote(
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            dataset_id=dataset.id,
            expected_revision=0,
        )
        manifest_hashes[project_id] = dataset.manifest_hash[:32]

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                scale_repository=InMemoryScaleRepository(),
                editorial_learning_repository=learning,
                settings=SimpleNamespace(editorial_learning_enabled=True),
            )
        )
    )
    first = _apply_phase5_learning(
        payload=PlanCreateRequest(),
        request=request,
        workspace_id=workspace_id,
        project_id=first_project_id,
        user_id=user_id,
    )
    second = _apply_phase5_learning(
        payload=PlanCreateRequest(),
        request=request,
        workspace_id=workspace_id,
        project_id=second_project_id,
        user_id=user_id,
    )

    assert manifest_hashes[first_project_id] in first.prompt
    assert manifest_hashes[second_project_id] not in first.prompt
    assert manifest_hashes[second_project_id] in second.prompt
    assert manifest_hashes[first_project_id] not in second.prompt


def test_render_success_records_usage_and_acceptance_once() -> None:
    workspace_id, project_id, user_id, plan_id = uuid4(), uuid4(), uuid4(), uuid4()
    billing = InMemoryBillingRepository()
    analytics = InMemoryAnalyticsRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(billing_enabled=True),
                billing_repository=billing,
                analytics_repository=analytics,
            )
        )
    )
    job = _render_job(
        workspace_id=workspace_id,
        project_id=project_id,
        status=RenderJobStatus.COMPLETE,
        manifest={
            "creator_user_id": str(user_id),
            "billing_plan": "free",
            "edit_plan_id": str(plan_id),
            "render_props": {
                "editPlan": {"cuts": [{"start_ms": 0, "end_ms": 1000}]},
                "captions": [{"text": "hello"}],
                "exportSettings": {"format": "mp4"},
            },
        },
    )

    record_render_success_outcome(request=request, job=job)
    record_render_success_outcome(request=request, job=job)

    assert billing.count_usage(
        workspace_id=workspace_id, event_type=UsageEventType.RENDER_SUCCEEDED
    ) == 1
    kept = [event for event in analytics.events.values() if event.event_type.value == "kept"]
    assert {event.layer for event in kept} == {
        AIEditLayer.CUTS,
        AIEditLayer.CAPTIONS,
        AIEditLayer.RENDER,
    }


def test_failed_render_records_no_usage_or_acceptance() -> None:
    billing = InMemoryBillingRepository()
    analytics = InMemoryAnalyticsRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(billing_enabled=True),
                billing_repository=billing,
                analytics_repository=analytics,
            )
        )
    )
    job = _render_job(
        workspace_id=uuid4(),
        project_id=uuid4(),
        status=RenderJobStatus.FAILED,
        manifest={"creator_user_id": str(uuid4()), "billing_plan": "free"},
    )

    record_render_success_outcome(request=request, job=job)

    assert not billing.usage_ledger
    assert not analytics.events


def test_analytics_failures_enter_recoverable_queue() -> None:
    queue = AnalyticsRecoveryQueue()

    class FailingRepository:
        def record_event(self, **_kwargs):
            raise RuntimeError("temporary outage")

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                analytics_repository=FailingRepository(),
                analytics_recovery_queue=queue,
            )
        )
    )
    record_ai_feedback(
        request=request,
        workspace_id=uuid4(),
        project_id=uuid4(),
        user_id=uuid4(),
        event_type=AIEditFeedbackEventType.CHANGED,
        layer=AIEditLayer.CUTS,
        source="test",
        user_payload={"pace": "fast"},
    )
    recovered = InMemoryAnalyticsRepository()

    assert queue.pending_count() == 1
    assert queue.drain(recovered) == 1
    assert queue.pending_count() == 0
    assert len(recovered.events) == 1


def test_selected_stock_video_is_streamed_into_owned_storage(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.services.media_url_policy.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )

    class Stream:
        headers = {"content-type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            assert chunk_size == 1024 * 1024
            yield b"owned-video"

    monkeypatch.setattr(
        "app.services.stock_video.safe_http_stream", lambda *_a, **_k: Stream()
    )
    repository = InMemoryBrollRepository(SimpleNamespace())
    asset = repository.upsert_asset(
        workspace_id=uuid4(),
        project_id=uuid4(),
        provider=StockProviderKey.PEXELS,
        provider_asset_id="123",
        title="Stock",
        thumbnail_url=None,
        preview_video_url="https://media.example.com/preview.mp4",
        download_url="https://media.example.com/video.mp4",
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=4000,
        orientation=StockOrientation.PORTRAIT,
        score=1.0,
        raw_metadata={},
    )

    owned = ensure_owned_stock_asset(
        asset=asset,
        repository=repository,
        settings=SimpleNamespace(upload_storage_bucket="raw-uploads", media_work_dir=str(tmp_path)),
        storage_signer=StorageSigner(base_url="https://api.motionly.test"),
    )

    assert owned.raw_metadata["owned_copy"] is True
    stored = tmp_path / "raw-uploads" / str(owned.raw_metadata["storage_path"])
    assert stored.read_bytes() == b"owned-video"
    assert "provider" not in str(owned.download_url)


def test_feedback_endpoint_persists_without_mail_client() -> None:
    from app.core.middleware import InMemoryRateLimiter

    state = SimpleNamespace(
        settings=SimpleNamespace(supabase_url=None, supabase_service_role_key=None),
        launch_feedback=[],
        rate_limiter=InMemoryRateLimiter(),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers={"user-agent": "test-browser"},
        client=SimpleNamespace(host="203.0.113.7"),
        state=SimpleNamespace(request_id="req_feedback"),
    )

    response = asyncio.run(
        create_launch_feedback(
            payload=LaunchFeedbackCreateRequest(
                category="bug", message="The preview and export differ."
            ),
            request=request,
        )
    )

    assert response.accepted is True
    assert state.launch_feedback[0]["category"] == "bug"


def _render_job(*, workspace_id, project_id, status, manifest):
    return RenderJobRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=uuid4(),
        caption_track_id=uuid4(),
        status=status,
        planner_version="test",
        manifest=manifest,
        keep_segments=[CutSegment(start_ms=0, end_ms=1000, reason="keep")],
    )
