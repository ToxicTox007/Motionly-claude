import pytest
from uuid import uuid4
from datetime import datetime, UTC
from types import SimpleNamespace

from app.api.errors import ApiError
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.repositories.billing import InMemoryBillingRepository, SubscriptionRecord
from app.services.operations import OperationsRepository
from app.services.billing import check_quota
from app.services.scale import FixtureRunwayProvider, _generate_broll, execute_scale_resource
from app.repositories.scale import InMemoryScaleRepository, ScaleResourceStatus, ScaleResourceRecord
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.plans import InMemoryPlanRepository
from app.schemas.broll import BrollGenerateRequest
from app.services.edit_planner import PlannerProviderResult, FixtureEditPlannerProvider
from app.schemas.captions import TranscriptStatus, TranscriptWord
from app.repositories.captions import TranscriptRecord, InMemoryCaptionRepository
from app.repositories.uploads import InMemoryUploadRepository, UploadStatus
from app.schemas.plans import PlanCreateRequest, EditPlan, PlanSummary, EditPlanStyle, EditPlanCut
from app.api.internal_jobs import _execute_job, EnqueuedJob, QueueJobStatus


class SpyRunwayProvider(FixtureRunwayProvider):
    def __init__(self):
        self.initiate_called_count = 0
        self.poll_called_count = 0

    def initiate_generation(self, *, prompt, prompt_image_url, duration_s, ratio):
        self.initiate_called_count += 1
        return super().initiate_generation(
            prompt=prompt,
            prompt_image_url=prompt_image_url,
            duration_s=duration_s,
            ratio=ratio,
        )

    def poll_task(self, task_id):
        self.poll_called_count += 1
        return super().poll_task(task_id)


class SpyPlannerProvider:
    def __init__(self, provider_name="openrouter", model="openrouter/paid-model"):
        self.provider_name = provider_name
        self.model = model
        self.create_plan_called_count = 0

    def create_plan(
        self,
        *,
        transcript,
        payload,
        video_duration_ms,
        visual_manifest=None,
        style_classification=None,
        billing_repository=None,
        operations_repository=None,
    ):
        self.create_plan_called_count += 1
        if billing_repository is not None and operations_repository is not None:
            check_quota(
                workspace_id=transcript.workspace_id,
                provider="openrouter",
                category="tokens",
                billing_repository=billing_repository,
                operations_repository=operations_repository,
                estimated_quantity=2000.0,
            )
        return PlannerProviderResult(
            plan=EditPlan(
                style=EditPlanStyle.CREATOR_INFLUENCER,
                target_duration_s=payload.target_duration_s,
                cuts=[EditPlanCut(start_ms=0, end_ms=1000, reason="test")],
                music_mood="test",
                color_grade="test",
            ),
            provider=self.provider_name,
            model=self.model,
            latency_ms=10,
            cost_estimate_usd=0.004,
            validation_errors=[],
            raw_payload={"usage": {"total_tokens": 2000}},
        )


def test_runway_over_quota_blocks_provider_call():
    workspace_id = uuid4()
    billing_repo = InMemoryBillingRepository()
    billing_repo.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
    )
    operations_repo = OperationsRepository()
    
    # Overlimit: Limit is 60s, record 60s of prior runway usage
    operations_repo.record_cost(
        workspace_id=workspace_id,
        project_id=uuid4(),
        provider="runway",
        category="generated_seconds",
        quantity=60.0,
        unit="second",
        unit_cost_usd=0.0,
        created_at=datetime.now(UTC),
    )

    settings = SimpleNamespace(
        app_env="test",
        quota_runway_creator_limit_seconds=60.0,
    )
    runway_provider = SpyRunwayProvider()
    record = ScaleResourceRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        project_id=uuid4(),
        kind="generated_broll",
        name="test prompt",
        status=ScaleResourceStatus.QUEUED,
        payload={"prompt": "test prompt", "duration_s": 5},
    )

    with pytest.raises(ApiError) as exc_info:
        _generate_broll(
            record=record,
            settings=settings,
            broll_repository=InMemoryBrollRepository(InMemoryPlanRepository(None)),
            runway_provider=runway_provider,
            operations_repository=operations_repo,
            billing_repository=billing_repo,
        )
    assert exc_info.value.status_code == 402
    assert exc_info.value.code == "runway_quota_exceeded"
    assert runway_provider.initiate_called_count == 0


def test_runway_under_quota_calls_provider_and_records_usage():
    workspace_id = uuid4()
    project_id = uuid4()
    billing_repo = InMemoryBillingRepository()
    billing_repo.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
    )
    operations_repo = OperationsRepository()

    settings = SimpleNamespace(
        app_env="test",
        media_work_dir="/var/data/test",
        upload_storage_bucket="test-bucket",
        quota_runway_creator_limit_seconds=60.0,
    )
    runway_provider = SpyRunwayProvider()
    record = ScaleResourceRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        project_id=project_id,
        kind="generated_broll",
        name="test prompt",
        status=ScaleResourceStatus.QUEUED,
        payload={"prompt": "test prompt", "duration_s": 5},
    )

    res = _generate_broll(
        record=record,
        settings=settings,
        broll_repository=InMemoryBrollRepository(InMemoryPlanRepository(None)),
        runway_provider=runway_provider,
        operations_repository=operations_repo,
        billing_repository=billing_repo,
    )

    assert runway_provider.initiate_called_count == 1
    assert runway_provider.poll_called_count == 1

    # Check that usage was recorded
    usage = operations_repo.count_usage(
        workspace_id=workspace_id,
        provider="runway",
        category="generated_seconds",
        period_start=datetime(2000, 1, 1, tzinfo=UTC),
        period_end=datetime(2100, 1, 1, tzinfo=UTC),
    )
    assert usage == 5.0


def test_openrouter_over_quota_blocks_provider_call():
    workspace_id = uuid4()
    billing_repo = InMemoryBillingRepository()
    billing_repo.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
    )
    operations_repo = OperationsRepository()

    # Limit is 500k tokens, record 500k of prior OpenRouter tokens usage
    operations_repo.record_cost(
        workspace_id=workspace_id,
        project_id=uuid4(),
        provider="openrouter",
        category="tokens",
        quantity=500000.0,
        unit="token",
        unit_cost_usd=0.0,
        created_at=datetime.now(UTC),
    )

    settings = SimpleNamespace(
        app_env="test",
        quota_openrouter_creator_limit_tokens=500000.0,
    )
    planner_provider = SpyPlannerProvider()
    transcript = TranscriptRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status=TranscriptStatus.READY,
        language="en",
        language_hint="en",
        provider="groq",
        model="whisper",
        confidence=1.0,
        duration_ms=5000,
        words=[TranscriptWord(index=0, text="test", start_ms=0, end_ms=1000, confidence=1.0)],
    )

    with pytest.raises(ApiError) as exc_info:
        planner_provider.create_plan(
            transcript=transcript,
            payload=PlanCreateRequest(prompt="make it nice"),
            video_duration_ms=5000,
            billing_repository=billing_repo,
            operations_repository=operations_repo,
        )
    assert exc_info.value.status_code == 402
    assert exc_info.value.code == "openrouter_quota_exceeded"
    assert planner_provider.create_plan_called_count == 1


def test_openrouter_under_quota_calls_provider_and_records_usage():
    workspace_id = uuid4()
    billing_repo = InMemoryBillingRepository()
    billing_repo.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
    )
    operations_repo = OperationsRepository()

    settings = SimpleNamespace(
        app_env="test",
        quota_openrouter_creator_limit_tokens=500000.0,
        max_paid_provider_attempts=1,
    )
    planner_provider = SpyPlannerProvider()
    transcript = TranscriptRecord(
        id=uuid4(),
        workspace_id=workspace_id,
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status=TranscriptStatus.READY,
        language="en",
        language_hint="en",
        provider="groq",
        model="whisper",
        confidence=1.0,
        duration_ms=5000,
        words=[TranscriptWord(index=0, text="test", start_ms=0, end_ms=1000, confidence=1.0)],
    )

    result = planner_provider.create_plan(
        transcript=transcript,
        payload=PlanCreateRequest(prompt="make it nice"),
        video_duration_ms=5000,
        billing_repository=billing_repo,
        operations_repository=operations_repo,
    )

    assert planner_provider.create_plan_called_count == 1
    assert result.raw_payload["usage"]["total_tokens"] == 2000


def test_runway_retry_resumes_polling_without_calling_provider_again():
    workspace_id = uuid4()
    project_id = uuid4()
    billing_repo = InMemoryBillingRepository()
    billing_repo.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
    )
    operations_repo = OperationsRepository()
    scale_repo = InMemoryScaleRepository()

    settings = SimpleNamespace(
        app_env="test",
        media_work_dir="/var/data/test",
        upload_storage_bucket="test-bucket",
    )
    runway_provider = SpyRunwayProvider()

    # Pre-populate record with existing task ID
    record = scale_repo.create(
        workspace_id=workspace_id,
        project_id=project_id,
        kind="generated_broll",
        name="test prompt",
        status=ScaleResourceStatus.QUEUED,
        payload={"prompt": "test prompt", "duration_s": 5, "runway_task_id": "fixture-test-prompt"},
    )

    res = _generate_broll(
        record=record,
        settings=settings,
        broll_repository=InMemoryBrollRepository(InMemoryPlanRepository(None)),
        runway_provider=runway_provider,
        operations_repository=operations_repo,
        billing_repository=billing_repo,
        repository=scale_repo,
    )

    # Initiate should NOT have been called, only poll
    assert runway_provider.initiate_called_count == 0
    assert runway_provider.poll_called_count == 1
    assert res["task_id"] == "fixture-test-prompt"


def test_planner_retry_attempts_cap():
    workspace_id = uuid4()
    project_id = uuid4()
    billing_repo = InMemoryBillingRepository()
    billing_repo.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
    )
    operations_repo = OperationsRepository()
    plan_repo = InMemoryPlanRepository(None)

    settings = SimpleNamespace(
        app_env="test",
        max_paid_provider_attempts=1,
    )

    app_state = SimpleNamespace(
        settings=settings,
        plan_repository=plan_repo,
        edit_planner_provider=SpyPlannerProvider(provider_name="openrouter"),
        operations_repository=operations_repo,
        billing_repository=billing_repo,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=app_state))

    upload_repo = InMemoryUploadRepository()
    upload_repo.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repo.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=1000,
    )
    upload.status = UploadStatus.READY
    upload.metadata.duration_ms = 5000
    upload_repo.attach_active_video(project_id=project_id, video_id=upload.id)

    caption_repo = InMemoryCaptionRepository(upload_repo)
    transcript = caption_repo.create_transcript(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status="ready",
        language="en",
        language_hint="auto",
        provider="groq",
        model="whisper",
        confidence=1.0,
        duration_ms=5000,
        raw_payload={},
        words=[TranscriptWord(index=0, text="test", start_ms=0, end_ms=1000, confidence=1.0)],
    )
    plan_repo = InMemoryPlanRepository(caption_repo)
    app_state.plan_repository = plan_repo

    # Attempt = 0, under max (1), should pass
    job_0 = EnqueuedJob(
        id="job-0",
        kind="edit_plan",
        attempts=0,
        max_attempts=3,
        workspace_id=workspace_id,
        project_id=project_id,
        resource_id=transcript.id,
        render_engine='{"prompt": "nice", "style": null, "target_duration_s": 35, "broll_density": "medium", "feedback": "", "style_profile_id": null, "saved_template_id": null, "user_id": null}',
    )

    res_0 = _execute_job(job=job_0, request=request)
    assert res_0["status"] == "ready"

    # Attempt = 1, >= max (1), should raise ApiError(429)
    job_1 = EnqueuedJob(
        id="job-1",
        kind="edit_plan",
        attempts=1,
        max_attempts=3,
        workspace_id=workspace_id,
        project_id=project_id,
        resource_id=transcript.id,
        render_engine='{"prompt": "nice", "style": null, "target_duration_s": 35, "broll_density": "medium", "feedback": "", "style_profile_id": null, "saved_template_id": null, "user_id": null}',
    )

    with pytest.raises(ApiError) as exc_info:
        _execute_job(job=job_1, request=request)
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "edit_planner_retry_limit_exceeded"
