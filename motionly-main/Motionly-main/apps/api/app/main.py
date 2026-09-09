from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.ai_proposals import router as ai_proposals_router
from app.api.analytics import router as analytics_router
from app.api.billing import router as billing_router
from app.api.broll import router as broll_router
from app.api.captions import router as captions_router
from app.api.clips import router as clips_router
from app.api.creative_variants import router as creative_variants_router
from app.api.editorial_learning import router as editorial_learning_router
from app.api.effects import router as effects_router
from app.api.errors import ApiError, api_error_handler, request_validation_error_handler
from app.api.feedback import router as feedback_router
from app.api.health import router as health_router
from app.api.internal_batches import router as internal_batches_router
from app.api.internal_jobs import router as internal_jobs_router
from app.api.internal_renders import router as internal_renders_router
from app.api.internal_retention import router as internal_retention_router
from app.api.internal_workflows import router as internal_workflows_router
from app.api.layer_jobs import router as layer_jobs_router
from app.api.local_storage import router as local_storage_router
from app.api.localized_publishing import router as localized_publishing_router
from app.api.media import router as media_router
from app.api.music import router as music_router
from app.api.operations import router as operations_router
from app.api.performance_feedback import router as performance_feedback_router
from app.api.plans import router as plans_router
from app.api.projects import router as projects_router
from app.api.public_reviews import router as public_reviews_router
from app.api.publishing import router as publishing_router
from app.api.regeneration import router as regeneration_router
from app.api.renders import router as renders_router
from app.api.reviews import router as reviews_router
from app.api.revision_errors import RevisionConflictError, revision_conflict_error_handler
from app.api.scale import router as scale_router
from app.api.uploads import router as uploads_router
from app.api.visual import router as visual_router
from app.api.workflows import router as workflows_router
from app.core.config import get_settings
from app.core.local_audit import local_audit_live_effects_gate_from_settings
from app.core.logging import configure_logging
from app.core.middleware import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    rate_limit_middleware,
    request_id_middleware,
    security_headers_middleware,
)
from app.repositories.ai_proposals import (
    InMemoryAIProposalRepository,
    SupabaseAIProposalRepository,
)
from app.repositories.analytics import InMemoryAnalyticsRepository, SupabaseAnalyticsRepository
from app.repositories.billing import InMemoryBillingRepository, SupabaseBillingRepository
from app.repositories.broll import InMemoryBrollRepository, SupabaseBrollRepository
from app.repositories.captions import InMemoryCaptionRepository, SupabaseCaptionRepository
from app.repositories.editorial_learning import (
    InMemoryEditorialLearningRepository,
    SupabaseEditorialLearningRepository,
)
from app.repositories.effects import InMemoryEffectsRepository, SupabaseEffectsRepository
from app.repositories.localized_publishing import (
    InMemoryLocalizedPublishingRepository,
    SupabaseLocalizedPublishingRepository,
)
from app.repositories.music import InMemoryMusicRepository, SupabaseMusicRepository
from app.repositories.performance_feedback import (
    InMemoryPerformanceFeedbackRepository,
    SupabasePerformanceFeedbackRepository,
)
from app.repositories.plans import InMemoryPlanRepository, SupabasePlanRepository
from app.repositories.projects import InMemoryProjectRepository, SupabaseProjectRepository
from app.repositories.publishing import InMemoryPublishingRepository, SupabasePublishingRepository
from app.repositories.regeneration import (
    InMemoryRegenerationRepository,
    SupabaseRegenerationRepository,
)
from app.repositories.renders import InMemoryRenderRepository, SupabaseRenderRepository
from app.repositories.reviews import InMemoryReviewRepository, SupabaseReviewRepository
from app.repositories.scale import InMemoryScaleRepository, SupabaseScaleRepository
from app.repositories.uploads import InMemoryUploadRepository, SupabaseUploadRepository
from app.repositories.visual import InMemoryVisualRepository, SupabaseVisualRepository
from app.repositories.workflows import (
    InMemoryWorkflowRepository,
    SupabaseWorkflowRepository,
)
from app.services.analytics_recovery import AnalyticsRecoveryQueue
from app.services.audio_ml_providers import (
    PyannoteSpeakerDiarizationProvider,
    Wav2VecEmotionProvider,
    YamnetSoundClassifier,
)
from app.services.billing import StripeBillingClient
from app.services.compute_backend import compute_backend_from_settings
from app.services.edit_planner import FixtureEditPlannerProvider, HttpEditPlannerProvider
from app.services.generic_media_upload import GenericMediaUploadSessionService
from app.services.local_storage import create_object_storage
from app.services.model_domain_adapters import (
    GatewayAudioIntelligenceProvider,
    GatewayVisualIntelligenceProvider,
    bundle_revision,
    register_audio_route,
    register_clip_routes,
    register_runway_route,
    register_stem_route,
    register_visual_route,
)
from app.services.model_gateway import (
    ModelCapability,
    ModelOperation,
    route_catalog_fingerprint,
    validate_gateway_operations,
)
from app.services.model_gateway_factory import model_gateway_from_settings
from app.services.model_provider_bridges import (
    GatewayEditPlannerProvider,
    GatewayTranscriptionProvider,
)
from app.services.music import FixtureMusicProvider, LicensedCatalogMusicProvider
from app.services.object_references import ObjectReferenceService
from app.services.operations import MetricsRegistry, OperationsRepository
from app.services.publishing import PublishOAuthClient, SocialPublishingClient
from app.services.queue import (
    BrollQueue,
    BullMQQueueRegistry,
    EffectsQueue,
    MatteQueue,
    MusicQueue,
    PlanQueue,
    PreprocessQueue,
    PublishQueue,
    QueueRegistry,
    RegenerationQueue,
    RenderQueue,
    ScaleQueue,
    StockAssetEnrichmentQueue,
    TranscriptionQueue,
    VisualQueue,
)
from app.services.scale import (
    DemucsStemSeparator,
    FixtureRunwayProvider,
    FixtureStemSeparator,
    RunwayGen3Provider,
)
from app.services.stock_video import (
    COMPATIBLE_CLIP_MODEL_VERSION,
    CompatibleClipEmbeddingProvider,
    FixtureStockVideoProvider,
    HashClipEmbeddingProvider,
    PexelsStockVideoProvider,
    PixabayStockVideoProvider,
)
from app.services.storage import StorageSigner
from app.services.storage_quota import StorageQuotaService
from app.services.supabase import SupabaseRestClient
from app.services.token_cipher import TokenCipher
from app.services.transcription_provider import (
    FixtureTranscriptionProvider,
    WhisperCompatibleHttpProvider,
)
from app.services.upload_session import UploadSessionService
from app.services.visual_intelligence import (
    FixtureVisualIntelligenceProvider,
    WorkerOnlyVisualIntelligenceProvider,
)

GROQ_TRANSCRIPTION_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENROUTER_CHAT_COMPLETIONS_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def create_app() -> FastAPI:
    settings = get_settings()
    _validate_production_settings(settings)
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Motionly API",
        version="0.1.0",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.local_audit_live_effects_gate = local_audit_live_effects_gate_from_settings(
        settings
    )
    app.state.launch_feedback = []
    app.state.metrics_registry = MetricsRegistry()
    app.state.operations_repository = OperationsRepository(
        supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
        service_role_key=settings.supabase_service_role_key,
    )
    app.state.model_gateway = model_gateway_from_settings(
        settings,
        metrics_registry=app.state.metrics_registry,
        operations_repository=app.state.operations_repository,
        live_effects_gate=app.state.local_audit_live_effects_gate,
    )
    app.state.workflow_repository = (
        SupabaseWorkflowRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryWorkflowRepository()
    )
    app.state.project_repository = (
        SupabaseProjectRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryProjectRepository()
    )
    upload_repository = (
        SupabaseUploadRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryUploadRepository()
    )
    app.state.upload_repository = upload_repository
    app.state.caption_repository = (
        SupabaseCaptionRepository(
            upload_repository=upload_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryCaptionRepository(upload_repository)
    )
    app.state.localized_publishing_repository = (
        SupabaseLocalizedPublishingRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryLocalizedPublishingRepository()
    )
    app.state.render_repository = (
        SupabaseRenderRepository(
            caption_repository=app.state.caption_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryRenderRepository(app.state.caption_repository)
    )
    token_encryption_key = settings.publish_token_encryption_key
    if not token_encryption_key:
        if settings.app_env == "production":
            raise RuntimeError("PUBLISH_TOKEN_ENCRYPTION_KEY is required in production")
        token_encryption_key = "motionly-local-publish-token-key-change-me"
    app.state.publish_token_cipher = TokenCipher(token_encryption_key)
    app.state.publishing_repository = (
        SupabasePublishingRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            token_cipher=app.state.publish_token_cipher,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryPublishingRepository()
    )
    app.state.plan_repository = (
        SupabasePlanRepository(
            caption_repository=app.state.caption_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryPlanRepository(app.state.caption_repository)
    )
    app.state.proposal_repository = (
        SupabaseAIProposalRepository(
            plan_repository=app.state.plan_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryAIProposalRepository(plan_repository=app.state.plan_repository)
    )
    app.state.review_repository = (
        SupabaseReviewRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryReviewRepository()
    )
    app.state.broll_repository = (
        SupabaseBrollRepository(
            plan_repository=app.state.plan_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryBrollRepository(app.state.plan_repository)
    )
    app.state.music_repository = (
        SupabaseMusicRepository(
            plan_repository=app.state.plan_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryMusicRepository(app.state.plan_repository)
    )
    app.state.effects_repository = (
        SupabaseEffectsRepository(
            plan_repository=app.state.plan_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryEffectsRepository(app.state.plan_repository)
    )
    app.state.regeneration_repository = (
        SupabaseRegenerationRepository(
            plan_repository=app.state.plan_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryRegenerationRepository(app.state.plan_repository)
    )
    app.state.billing_repository = (
        SupabaseBillingRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryBillingRepository()
    )
    app.state.analytics_repository = (
        SupabaseAnalyticsRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryAnalyticsRepository()
    )
    app.state.analytics_recovery_queue = AnalyticsRecoveryQueue(settings.redis_url)
    app.state.editorial_learning_repository = (
        SupabaseEditorialLearningRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            default_retention_days=settings.editorial_learning_retention_days,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryEditorialLearningRepository(
            default_retention_days=settings.editorial_learning_retention_days
        )
    )
    app.state.performance_feedback_repository = (
        SupabasePerformanceFeedbackRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            default_retention_days=settings.performance_feedback_retention_days,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryPerformanceFeedbackRepository(
            default_retention_days=settings.performance_feedback_retention_days
        )
    )
    app.state.scale_repository = (
        SupabaseScaleRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryScaleRepository()
    )
    app.state.visual_repository = (
        SupabaseVisualRepository(
            upload_repository=upload_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryVisualRepository(upload_repository)
    )
    app.state.plan_repository.visual_repository = app.state.visual_repository
    app.state.stripe_billing_client = StripeBillingClient(api_key=settings.stripe_secret_key)
    storage_supabase_url = None if settings.force_local_media_storage else settings.supabase_url
    storage_service_role_key = (
        None if settings.force_local_media_storage else settings.supabase_service_role_key
    )
    app.state.storage_signer = StorageSigner(
        base_url=str(settings.api_public_base_url),
        supabase_url=str(storage_supabase_url) if storage_supabase_url else None,
        service_role_key=storage_service_role_key,
    )
    app.state.object_storage = create_object_storage(
        root=settings.media_work_dir,
        supabase_url=str(storage_supabase_url) if storage_supabase_url else None,
        service_role_key=storage_service_role_key,
    )
    task6_uses_supabase = bool(storage_supabase_url and storage_service_role_key)
    app.state.storage_quota_service = StorageQuotaService(
        supabase_url=str(storage_supabase_url) if storage_supabase_url else None,
        service_role_key=storage_service_role_key,
        in_memory=not task6_uses_supabase,
    )
    app.state.object_reference_service = ObjectReferenceService(
        storage_signer=app.state.storage_signer,
        supabase_url=str(storage_supabase_url) if storage_supabase_url else None,
        service_role_key=storage_service_role_key,
        in_memory=not task6_uses_supabase,
    )
    upload_session_db = (
        SupabaseRestClient(
            supabase_url=str(storage_supabase_url),
            service_role_key=storage_service_role_key,
            repository="upload_sessions",
            metrics_registry=app.state.metrics_registry,
        )
        if task6_uses_supabase
        else None
    )
    app.state.upload_session_service = UploadSessionService(
        storage_signer=app.state.storage_signer,
        upload_repository=app.state.upload_repository,
        object_storage=app.state.object_storage,
        quota_service=app.state.storage_quota_service,
        object_reference_service=app.state.object_reference_service,
        db_client=upload_session_db,
    )
    app.state.media_upload_session_service = GenericMediaUploadSessionService(
        storage_signer=app.state.storage_signer,
        object_storage=app.state.object_storage,
        quota_service=app.state.storage_quota_service,
        object_reference_service=app.state.object_reference_service,
        db_client=upload_session_db,
    )
    app.state.rate_limiter = (
        RedisRateLimiter(settings.redis_url) if settings.redis_url else InMemoryRateLimiter()
    )
    if settings.redis_url:
        app.state.queue_registry = BullMQQueueRegistry(
            redis_url=settings.redis_url,
            bridge_path=settings.bullmq_bridge_path,
            node_command=settings.bullmq_node_command,
            job_attempts=settings.job_attempts,
            bridge_timeout_seconds=settings.bullmq_bridge_timeout_seconds,
        )
    elif settings.app_env == "production":
        raise RuntimeError("REDIS_URL is required in production for BullMQ queueing")
    else:
        app.state.queue_registry = QueueRegistry()
    app.state.gpu_scaling_service = compute_backend_from_settings(settings)
    app.state.preprocess_queue = PreprocessQueue(app.state.queue_registry)
    app.state.transcription_queue = TranscriptionQueue(app.state.queue_registry)
    app.state.visual_queue = VisualQueue(app.state.queue_registry)
    app.state.matte_queue = MatteQueue(app.state.queue_registry)
    app.state.plan_queue = PlanQueue(app.state.queue_registry)
    app.state.broll_queue = BrollQueue(app.state.queue_registry)
    app.state.stock_asset_enrichment_queue = StockAssetEnrichmentQueue(app.state.queue_registry)
    app.state.render_queue = RenderQueue(app.state.queue_registry)
    app.state.publish_queue = PublishQueue(app.state.queue_registry)
    app.state.scale_queue = ScaleQueue(app.state.queue_registry)
    app.state.social_publishing_client = SocialPublishingClient(
        meta_graph_api_version=settings.meta_graph_api_version
    )
    app.state.publish_oauth_client = PublishOAuthClient(settings)
    app.state.stem_separator = (
        FixtureStemSeparator()
        if settings.process_role == "api"
        or settings.stem_separation_provider == "fixture"
        or (settings.app_env != "production" and settings.allow_fixture_providers)
        else DemucsStemSeparator()
    )
    app.state.stem_gateway_route = None
    if app.state.model_gateway is not None and settings.process_role != "api":
        app.state.stem_gateway_route = register_stem_route(
            app.state.model_gateway,
            separator=app.state.stem_separator,
            model=settings.demucs_model,
            revision=settings.demucs_model_revision,
        )
    app.state.runway_provider = (
        RunwayGen3Provider(api_key=settings.runway_api_key, model=settings.runway_model)
        if settings.runway_api_key
        else FixtureRunwayProvider()
    )
    app.state.runway_gateway_route = None
    if app.state.model_gateway is not None and settings.runway_api_key:
        app.state.runway_gateway_route = register_runway_route(
            app.state.model_gateway,
            provider=app.state.runway_provider,
            model=settings.runway_model,
            revision=settings.runway_model_revision,
            fixed_cost_usd=settings.runway_estimated_cost_per_request_usd,
            endpoint_host="api.runwayml.com",
            requires_live_effects_gate=True,
        )
    transcription_provider_key = settings.transcription_provider.strip().lower()
    edit_planner_provider_key = settings.edit_planner_provider.strip().lower()
    audio_ml_provider_key = settings.audio_ml_provider.strip().lower()

    gateway_has_transcription = bool(
        app.state.model_gateway
        and any(
            route.capability.value == "transcription" for route in app.state.model_gateway.routes
        )
    )
    if transcription_provider_key in {"groq", "openai_compatible"} and gateway_has_transcription:
        app.state.transcription_provider = GatewayTranscriptionProvider(
            gateway=app.state.model_gateway,
            provider_name=transcription_provider_key,
            model=settings.transcription_model,
            model_revision=settings.transcription_model_revision,
        )
    elif settings.app_env == "production":
        raise RuntimeError("Production transcription requires a configured model gateway route")
    elif settings.local_audit_mode:
        raise RuntimeError(
            "LOCAL_AUDIT_MODE requires transcription via model gateway, "
            "but no gateway transcription route is configured. "
            "Direct provider fallback is blocked to ensure live-effects gate coverage."
        )
    elif transcription_provider_key in {"groq", "openai_compatible"} and settings.groq_api_key:
        app.state.transcription_provider = WhisperCompatibleHttpProvider(
            endpoint=GROQ_TRANSCRIPTION_ENDPOINT,
            api_key=settings.groq_api_key,
            model=settings.transcription_model,
            provider_name="groq",
        )
    else:
        app.state.transcription_provider = FixtureTranscriptionProvider(
            model=settings.transcription_model
        )
    app.state.audio_ml_providers = (
        {}
        if settings.process_role == "api"
        else _audio_ml_providers(provider_key=audio_ml_provider_key, settings=settings)
    )
    app.state.audio_intelligence_provider = None
    if app.state.model_gateway is not None and app.state.audio_ml_providers:
        audio_route = register_audio_route(
            app.state.model_gateway,
            providers=app.state.audio_ml_providers,
            revision=bundle_revision(
                settings.pyannote_model_revision,
                settings.wav2vec_emotion_model_revision,
                settings.yamnet_model_url,
            ),
        )
        app.state.audio_intelligence_provider = GatewayAudioIntelligenceProvider(
            gateway=app.state.model_gateway,
            route=audio_route,
        )
    gateway_has_planning = bool(
        app.state.model_gateway
        and any(route.capability.value == "planning" for route in app.state.model_gateway.routes)
    )
    if edit_planner_provider_key in {"openrouter", "openai_compatible"} and gateway_has_planning:
        app.state.edit_planner_provider = GatewayEditPlannerProvider(
            endpoint=OPENROUTER_CHAT_COMPLETIONS_ENDPOINT,
            api_key=settings.openrouter_api_key or "gateway-managed",
            model=route_catalog_fingerprint(
                app.state.model_gateway,
                capability=ModelCapability.PLANNING,
                operation=ModelOperation.PLANNING_CHAT,
            ),
            provider_name=edit_planner_provider_key,
            gateway=app.state.model_gateway,
        )
    elif settings.app_env == "production":
        raise RuntimeError("Production planning requires a configured model gateway route")
    elif settings.local_audit_mode:
        raise RuntimeError(
            "LOCAL_AUDIT_MODE requires planning via model gateway, "
            "but no gateway planning route is configured. "
            "Direct provider fallback is blocked to ensure live-effects gate coverage."
        )
    elif (
        edit_planner_provider_key in {"openrouter", "openai_compatible"}
        and settings.openrouter_api_key
    ):
        app.state.edit_planner_provider = HttpEditPlannerProvider(
            endpoint=OPENROUTER_CHAT_COMPLETIONS_ENDPOINT,
            api_key=settings.openrouter_api_key,
            model=settings.edit_planner_model,
            provider_name="openrouter",
        )
    else:
        app.state.edit_planner_provider = FixtureEditPlannerProvider(
            model=settings.edit_planner_model
        )
    visual_provider_key = settings.visual_intelligence_provider.strip().lower()
    app.state.visual_intelligence_provider = (
        FixtureVisualIntelligenceProvider()
        if visual_provider_key == "fixture"
        else WorkerOnlyVisualIntelligenceProvider(
            model="|".join(
                (
                    settings.visual_clip_model,
                    settings.visual_blip_model,
                    settings.visual_yolo_model,
                )
            ),
        )
    )
    app.state.visual_gateway_provider = None
    if app.state.model_gateway is not None and (
        settings.app_env != "production"
        or getattr(app.state.visual_intelligence_provider, "provider_name", "") == "fixture_visual"
    ):
        visual_route = register_visual_route(
            app.state.model_gateway,
            provider=app.state.visual_intelligence_provider,
            model=app.state.visual_intelligence_provider.model,
            revision=bundle_revision(
                settings.visual_clip_model_revision,
                settings.visual_blip_model_revision,
                settings.visual_yolo_model_revision,
            ),
            route_id="control-visual-manifest",
        )
        app.state.visual_gateway_provider = GatewayVisualIntelligenceProvider(
            gateway=app.state.model_gateway,
            route=visual_route,
        )
    app.state.embedding_gateway_provider = None
    if app.state.model_gateway is not None:
        register_clip_routes(
            app.state.model_gateway,
            primary_provider=CompatibleClipEmbeddingProvider(
                model_name=settings.visual_clip_model,
                allow_fallback=False,
                revision=settings.visual_clip_model_revision,
            ),
            primary_revision=settings.visual_clip_model_revision,
            fallback_provider=HashClipEmbeddingProvider(model_name=COMPATIBLE_CLIP_MODEL_VERSION),
        )
        from app.services.model_domain_adapters import GatewayClipEmbeddingProvider

        app.state.embedding_gateway_provider = GatewayClipEmbeddingProvider(
            gateway=app.state.model_gateway
        )

    if settings.app_env == "production":
        required_operations = [
            ModelOperation.PLANNING_CHAT,
            ModelOperation.TRANSCRIPTION_SPEECH_TO_TEXT,
            ModelOperation.EMBEDDINGS_TEXT,
            ModelOperation.EMBEDDINGS_IMAGE,
            ModelOperation.GENERATION_VIDEO_SUBMIT,
            ModelOperation.GENERATION_VIDEO_POLL,
        ]
        if settings.process_role != "api":
            required_operations.append(ModelOperation.AUDIO_STEM_SEPARATION)
            if app.state.audio_intelligence_provider is not None:
                required_operations.append(ModelOperation.AUDIO_INTELLIGENCE)
        validate_gateway_operations(
            app.state.model_gateway,
            required=required_operations,
            runtime=f"{settings.process_role} runtime",
        )

    stock_video_provider = settings.stock_video_provider.replace(",", "_")
    stock_video_providers = []
    if stock_video_provider in {"pexels", "pexels_pixabay"} and settings.pexels_api_key:
        stock_video_providers.append(PexelsStockVideoProvider(api_key=settings.pexels_api_key))
    if stock_video_provider in {"pixabay", "pexels_pixabay"} and settings.pixabay_api_key:
        stock_video_providers.append(PixabayStockVideoProvider(api_key=settings.pixabay_api_key))
    if settings.app_env != "production" or settings.allow_fixture_providers:
        stock_video_providers.append(FixtureStockVideoProvider())
    app.state.stock_video_providers = stock_video_providers
    music_providers = []
    if (
        settings.music_provider == "licensed_catalog"
        and settings.supabase_url
        and settings.supabase_service_role_key
    ):
        music_providers.append(
            LicensedCatalogMusicProvider(
                supabase_url=str(settings.supabase_url),
                service_role_key=settings.supabase_service_role_key,
                table_name=settings.licensed_music_catalog_table,
            )
        )
    if settings.app_env != "production" or settings.allow_fixture_providers:
        music_providers.append(FixtureMusicProvider())
    app.state.music_providers = music_providers
    app.state.music_queue = MusicQueue(app.state.queue_registry)
    app.state.effects_queue = EffectsQueue(app.state.queue_registry)
    app.state.regeneration_queue = RegenerationQueue(app.state.queue_registry)

    app.middleware("http")(request_id_middleware)
    app.middleware("http")(rate_limit_middleware)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(RevisionConflictError, revision_conflict_error_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "authorization",
            "content-type",
            "x-motionly-user-id",
            "x-motionly-workspace-id",
            "x-request-id",
        ],
    )
    app.middleware("http")(security_headers_middleware)
    app.include_router(health_router)
    app.include_router(operations_router)
    app.include_router(feedback_router)
    app.include_router(projects_router)
    app.include_router(uploads_router)
    app.include_router(workflows_router)
    app.include_router(media_router)
    app.include_router(captions_router)
    app.include_router(localized_publishing_router)
    app.include_router(clips_router)
    app.include_router(creative_variants_router)
    app.include_router(visual_router)
    app.include_router(plans_router)
    app.include_router(ai_proposals_router)
    app.include_router(reviews_router)
    app.include_router(public_reviews_router)
    app.include_router(layer_jobs_router)
    app.include_router(broll_router)
    app.include_router(music_router)
    app.include_router(effects_router)
    app.include_router(regeneration_router)
    app.include_router(renders_router)
    app.include_router(publishing_router)
    app.include_router(performance_feedback_router)
    app.include_router(scale_router)
    app.include_router(billing_router)
    app.include_router(analytics_router)
    app.include_router(editorial_learning_router)
    app.include_router(internal_jobs_router)
    app.include_router(internal_batches_router)
    app.include_router(internal_retention_router)
    app.include_router(internal_renders_router)
    app.include_router(internal_workflows_router)
    app.include_router(local_storage_router)
    app.include_router(admin_router)
    return app


def _validate_production_settings(settings) -> None:
    # Backward-compatible import surface for callers and tests.
    from app.core.production import validate_production_settings

    validate_production_settings(settings)


def _audio_ml_providers(*, provider_key: str, settings) -> dict[str, object]:
    if provider_key in {"disabled", "none", "off"}:
        return {}
    if provider_key not in {"local_ml", "local", "full"}:
        raise RuntimeError("AUDIO_ML_PROVIDER must be one of local_ml, disabled")

    providers: dict[str, object] = {
        "emotion": Wav2VecEmotionProvider(
            model=settings.wav2vec_emotion_model,
            revision=settings.wav2vec_emotion_model_revision,
        ),
        "sound": YamnetSoundClassifier(model_url=settings.yamnet_model_url),
    }
    if settings.pyannote_auth_token:
        providers["speaker_diarization"] = PyannoteSpeakerDiarizationProvider(
            model=settings.pyannote_model,
            auth_token=settings.pyannote_auth_token,
            revision=settings.pyannote_model_revision,
        )
    return providers


app = create_app()
