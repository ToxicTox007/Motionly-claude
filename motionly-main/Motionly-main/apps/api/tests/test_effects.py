import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.dependencies import Actor
from app.api.effects import get_effects, update_effects
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.effects import (
    EFFECTS_CONFIG_COLUMNS,
    InMemoryEffectsRepository,
    SupabaseEffectsRepository,
)
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.schemas.captions import LanguageHint
from app.schemas.effects import (
    AfterEffectsPreset,
    AutoReframeSettings,
    BackgroundRemovalSettings,
    BeatImpactSettings,
    EffectsConfigRequest,
    GradientRampSettings,
    KenBurnsSettings,
    MotionEffectKey,
    MotionKeyframe,
    MotionSettings,
    SpeedRampSettings,
    VisualPresetKey,
)
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.queue import EffectsQueue
from app.services.transcription_provider import FixtureTranscriptionProvider


def make_effects_repository() -> tuple[InMemoryEffectsRepository, UUID, UUID, UUID]:
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
    from tests.worker_job_test_helpers import seed_active_plan

    seed_active_plan(
        plan_repository=plan_repository,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(broll_density=BrollDensity.MEDIUM),
    )
    return InMemoryEffectsRepository(plan_repository), project_id, workspace_id, user_id


def run_update_effects(
    *,
    project_id: UUID,
    payload: EffectsConfigRequest,
    request: object,
    actor: Actor,
    repository: InMemoryEffectsRepository,
    billing_repository: InMemoryBillingRepository,
    plan_repository: InMemoryPlanRepository,
    effects_queue: EffectsQueue,
) -> object:
    from app.schemas.effects import EffectsUpdateResponse
    from tests.worker_job_test_helpers import DummyResponse, run_enqueued_job

    # Ensure queue registry is populated in request
    registry = effects_queue.registry
    request.app.state.queue_registry = registry
    request.app.state.effects_repository = repository
    request.app.state.billing_repository = billing_repository
    request.app.state.plan_repository = plan_repository

    effects_response = asyncio.run(
        update_effects(
            project_id=project_id,
            payload=payload,
            request=request,
            response=DummyResponse(),
            actor=actor,
            repository=repository,
            billing_repository=billing_repository,
            plan_repository=plan_repository,
            effects_queue=effects_queue,
        )
    )
    if getattr(effects_response, "status", None) == "queued":
        asyncio.run(run_enqueued_job(job_id=effects_response.job_id, request=request))
        config = repository.latest_config(project_id=project_id, workspace_id=actor.workspace_id)
        effects_response = EffectsUpdateResponse(
            config=repository.response_for_config(config),
            job_id=effects_response.job_id,
            status="ready",
        )
    return effects_response


def test_update_effects_creates_visual_preset_config() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()

    response = run_update_effects(
        project_id=project_id,
        payload=EffectsConfigRequest(preset=VisualPresetKey.CHAMPAGNE_GOLD),
        request=_request(),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )

    assert response.status == "ready"
    assert response.config.preset == VisualPresetKey.CHAMPAGNE_GOLD
    assert response.config.lut_intensity == 0.75
    assert response.config.preview_after_url is None
    assert response.config.render_time_impact in {"low", "medium", "high"}
    assert response.config.after_effects_project_url is None
    assert response.config.correction_manifest_url is None


def test_supabase_effects_repository_reads_with_explicit_projection(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    caption_repository = InMemoryCaptionRepository(upload_repository)
    plan_repository = InMemoryPlanRepository(caption_repository)
    repository = SupabaseEffectsRepository(
        plan_repository=plan_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    calls = []

    def fake_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(is_success=True, json=lambda: [])

    monkeypatch.setattr("app.repositories.effects.SupabaseRestClient.request", fake_request)

    assert repository.latest_config(project_id=project_id, workspace_id=workspace_id) is None
    assert calls == [
        (
            "GET",
            "/visual_presets",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "project_id": f"eq.{project_id}",
                    "select": EFFECTS_CONFIG_COLUMNS,
                    "order": "updated_at.desc",
                    "limit": "1",
                }
            },
        )
    ]


def test_supabase_effects_async_reads_use_bounded_projections(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    caption_repository = InMemoryCaptionRepository(upload_repository)
    plan_repository = InMemoryPlanRepository(caption_repository)
    repository = SupabaseEffectsRepository(
        plan_repository=plan_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(is_success=True, json=lambda: [])

    monkeypatch.setattr(
        "app.repositories.effects.SupabaseRestClient.async_request",
        fake_async_request,
    )

    assert (
        asyncio.run(
            repository.latest_config_async(
                project_id=project_id,
                workspace_id=workspace_id,
            )
        )
        is None
    )
    assert (
        asyncio.run(
            repository.find_cached_config_async(
                project_id=project_id,
                workspace_id=workspace_id,
                cache_key="effects-cache-key",
            )
        )
        is None
    )
    assert calls == [
        (
            "GET",
            "/visual_presets",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "project_id": f"eq.{project_id}",
                    "select": EFFECTS_CONFIG_COLUMNS,
                    "order": "updated_at.desc",
                    "limit": "1",
                }
            },
        ),
        (
            "GET",
            "/visual_presets",
            {
                "params": {
                    "workspace_id": f"eq.{workspace_id}",
                    "project_id": f"eq.{project_id}",
                    "cache_key": "eq.effects-cache-key",
                    "select": EFFECTS_CONFIG_COLUMNS,
                    "order": "updated_at.desc",
                    "limit": "1",
                }
            },
        ),
    ]


def test_effects_api_prefers_async_repository_methods() -> None:
    base_repository, project_id, workspace_id, user_id = make_effects_repository()

    class AsyncEffectsRepository(InMemoryEffectsRepository):
        def __init__(self, plan_repository):
            super().__init__(plan_repository)
            self.async_calls: list[str] = []

        async def latest_config_async(self, **kwargs):
            self.async_calls.append("latest-config")
            return self.latest_config(**kwargs)

        async def find_cached_config_async(self, **kwargs):
            self.async_calls.append("find-cache")
            return self.find_cached_config(**kwargs)

    repository = AsyncEffectsRepository(base_repository.plan_repository)
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    payload = EffectsConfigRequest(preset=VisualPresetKey.OLD_MONEY)

    run_update_effects(
        project_id=project_id,
        payload=payload,
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )
    cached = run_update_effects(
        project_id=project_id,
        payload=payload,
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )
    latest = asyncio.run(
        get_effects(project_id=project_id, actor=actor, repository=repository)
    )

    assert cached.cached is True
    assert latest is not None
    assert repository.async_calls == ["find-cache", "find-cache", "latest-config"]


def test_update_effects_reuses_cached_config() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    payload = EffectsConfigRequest(preset=VisualPresetKey.OLD_MONEY)

    first = run_update_effects(
        project_id=project_id,
        payload=payload,
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )
    second = run_update_effects(
        project_id=project_id,
        payload=payload,
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )

    assert second.cached is True
    assert second.config.id == first.config.id


def test_update_effects_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()

    with pytest.raises(ApiError) as exc_info:
        run_update_effects(
            project_id=project_id,
            payload=EffectsConfigRequest(preset=VisualPresetKey.CLEAN_FOUNDER),
            request=_request(),
            actor=Actor(user_id=user_id, workspace_id=uuid4()),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=repository.plan_repository,
            effects_queue=EffectsQueue(),
        )

    assert exc_info.value.status_code in {403, 404}


def test_update_effects_accepts_background_gradient_and_ae_handoff() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()
    billing_repository = InMemoryBillingRepository()
    billing_repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.PRO,
        status=SubscriptionStatus.ACTIVE,
    )

    response = run_update_effects(
        project_id=project_id,
        payload=EffectsConfigRequest(
            preset=VisualPresetKey.CHAMPAGNE_GOLD,
            motion_effects=[
                MotionEffectKey.BROLL_SLIDE_IN,
                MotionEffectKey.GRADIENT_RAMP,
            ],
            background_removal=BackgroundRemovalSettings(
                enabled=True,
                apply_to_broll=True,
                feather_px=24,
            ),
            gradient_ramp=GradientRampSettings(
                enabled=True,
                start_color="#111827",
                end_color="#f59e0b",
                opacity=0.35,
            ),
            after_effects_presets=[
                AfterEffectsPreset(name="Fast Vertical Cleanup", intensity=0.7)
            ],
        ),
        request=_request(),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=billing_repository,
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )

    assert response.config.background_removal.enabled is True
    assert response.config.background_removal.apply_to_broll is True
    assert response.config.gradient_ramp.enabled is True
    assert MotionEffectKey.BROLL_SLIDE_IN in response.config.motion_effects
    assert MotionEffectKey.GRADIENT_RAMP not in response.config.motion_effects
    assert response.config.after_effects_presets[0].name == "Fast Vertical Cleanup"


def test_update_effects_persists_stage_six_motion_design() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()
    billing_repository = InMemoryBillingRepository()
    billing_repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.PRO,
        status=SubscriptionStatus.ACTIVE,
    )

    response = run_update_effects(
        project_id=project_id,
        payload=EffectsConfigRequest(
            preset=VisualPresetKey.DUBAI_INFLUENCER,
            motion_effects=[MotionEffectKey.KEN_BURNS],
            motion=MotionSettings(
                keyframes=[
                    MotionKeyframe(at_ms=0, scale=1, x_pct=50, y_pct=50),
                    MotionKeyframe(at_ms=900, scale=1.12, x_pct=62, y_pct=46),
                ],
                ken_burns=KenBurnsSettings(enabled=True, intensity=0.12),
                speed_ramp=SpeedRampSettings(enabled=True, peak_speed=1.6),
                beat_impact=BeatImpactSettings(enabled=True, shake_px=16),
                auto_reframe=AutoReframeSettings(
                    enabled=True,
                    target_aspect="9:16",
                    subject_x_pct=64,
                    subject_y_pct=44,
                ),
            ),
        ),
        request=_request(),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=billing_repository,
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )

    assert response.config.schema_version == 3
    assert MotionEffectKey.KEN_BURNS in response.config.motion_effects
    assert MotionEffectKey.SPEED_RAMP in response.config.motion_effects
    assert MotionEffectKey.BEAT_IMPACT in response.config.motion_effects
    assert response.config.motion.ken_burns.enabled is True
    assert response.config.motion.speed_ramp.peak_speed == 1.6
    assert response.config.motion.beat_impact.shake_px == 16
    assert response.config.motion.auto_reframe.subject_x_pct == 64
    assert len(response.config.motion.keyframes) == 2
    assert response.config.effect_count_limit_per_30s >= 8


def test_reset_effects_uses_preset_defaults_as_single_source_of_truth() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()

    response = run_update_effects(
        project_id=project_id,
        payload=EffectsConfigRequest(
            preset=VisualPresetKey.OLD_MONEY,
            lut_intensity=1,
            motion_effects=[MotionEffectKey.GLITCH, MotionEffectKey.GRADIENT_RAMP],
            gradient_ramp=GradientRampSettings(enabled=False),
            reset_to_defaults=True,
        ),
        request=_request(billing_enabled=False),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )

    assert response.config.preset == VisualPresetKey.OLD_MONEY
    assert response.config.lut_intensity == 0.72
    assert response.config.texture.grain == 0.2
    assert response.config.motion_effects == [MotionEffectKey.MOTION_BLUR]
    assert response.config.gradient_ramp.enabled is True
    assert response.config.gradient_ramp.start_color == "#1f2933"


def test_update_effects_allows_local_qa_when_billing_is_disabled() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()

    response = run_update_effects(
        project_id=project_id,
        payload=EffectsConfigRequest(
            preset=VisualPresetKey.VOGUE_LUXURY,
            motion_effects=[MotionEffectKey.FLASH],
            gradient_ramp=GradientRampSettings(enabled=True),
        ),
        request=_request(billing_enabled=False),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )

    assert response.status == "ready"
    assert response.config.gradient_ramp.enabled is True


def test_update_effects_requires_pro_when_billing_is_enabled() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()

    with pytest.raises(ApiError) as exc_info:
        run_update_effects(
            project_id=project_id,
            payload=EffectsConfigRequest(
                preset=VisualPresetKey.VOGUE_LUXURY,
                motion_effects=[MotionEffectKey.FLASH],
            ),
            request=_request(billing_enabled=True),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            plan_repository=repository.plan_repository,
            effects_queue=EffectsQueue(),
        )

    assert exc_info.value.status_code == 402
    assert exc_info.value.code == "premium_effects_plan_required"


def test_update_effects_persists_per_clip_motion_effects() -> None:
    repository, project_id, workspace_id, user_id = make_effects_repository()
    billing_repository = InMemoryBillingRepository()
    billing_repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.PRO,
        status=SubscriptionStatus.ACTIVE,
    )

    response = run_update_effects(
        project_id=project_id,
        payload=EffectsConfigRequest(
            preset=VisualPresetKey.CLEAN_FOUNDER,
            motion=MotionSettings(
                clip_effects={
                    "cut-0": [MotionEffectKey.ZOOM_PUNCH, MotionEffectKey.KEN_BURNS],
                    "broll-slot-1": [MotionEffectKey.BROLL_SLIDE_IN],
                },
            ),
        ),
        request=_request(),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=billing_repository,
        plan_repository=repository.plan_repository,
        effects_queue=EffectsQueue(),
    )

    assert response.config.motion.clip_effects == {
        "cut-0": [MotionEffectKey.ZOOM_PUNCH, MotionEffectKey.KEN_BURNS],
        "broll-slot-1": [MotionEffectKey.BROLL_SLIDE_IN],
    }
    assert response.config.render_time_impact in {"low", "medium", "high"}


def _request(*, billing_enabled: bool = True):
    from app.services.queue import QueueRegistry
    from tests.worker_job_test_helpers import build_queue_request_state

    registry = QueueRegistry()
    return build_queue_request_state(
        registry=registry,
        settings=SimpleNamespace(
            app_env="test",
            billing_enabled=billing_enabled,
            redis_url=None,
            admin_api_token="worker-secret",
        ),
    )
