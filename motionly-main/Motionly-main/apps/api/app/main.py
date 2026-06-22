from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.analytics import router as analytics_router
from app.api.billing import router as billing_router
from app.api.broll import router as broll_router
from app.api.captions import router as captions_router
from app.api.effects import router as effects_router
from app.api.errors import ApiError, api_error_handler, request_validation_error_handler
from app.api.health import router as health_router
from app.api.internal_jobs import router as internal_jobs_router
from app.api.internal_renders import router as internal_renders_router
from app.api.internal_retention import router as internal_retention_router
from app.api.local_storage import router as local_storage_router
from app.api.music import router as music_router
from app.api.plans import router as plans_router
from app.api.projects import router as projects_router
from app.api.publishing import router as publishing_router
from app.api.regeneration import router as regeneration_router
from app.api.renders import router as renders_router
from app.api.scale import router as scale_router
from app.api.uploads import router as uploads_router
from app.api.visual import router as visual_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    rate_limit_middleware,
    request_id_middleware,
)
from app.repositories.analytics import InMemoryAnalyticsRepository, SupabaseAnalyticsRepository
from app.repositories.billing import InMemoryBillingRepository, SupabaseBillingRepository
from app.repositories.broll import InMemoryBrollRepository, SupabaseBrollRepository
from app.repositories.captions import InMemoryCaptionRepository, SupabaseCaptionRepository
from app.repositories.effects import InMemoryEffectsRepository, SupabaseEffectsRepository
from app.repositories.music import InMemoryMusicRepository, SupabaseMusicRepository
from app.repositories.plans import InMemoryPlanRepository, SupabasePlanRepository
from app.repositories.projects import InMemoryProjectRepository, SupabaseProjectRepository
from app.repositories.publishing import InMemoryPublishingRepository, SupabasePublishingRepository
from app.repositories.regeneration import (
    InMemoryRegenerationRepository,
    SupabaseRegenerationRepository,
)
from app.repositories.renders import InMemoryRenderRepository, SupabaseRenderRepository
from app.repositories.scale import InMemoryScaleRepository, SupabaseScaleRepository
from app.repositories.uploads import InMemoryUploadRepository, SupabaseUploadRepository
from app.repositories.visual import InMemoryVisualRepository, SupabaseVisualRepository
from app.services.audio_ml_providers import (
    PyannoteSpeakerDiarizationProvider,
    Wav2VecEmotionProvider,
    YamnetSoundClassifier,
)
from app.services.billing import StripeBillingClient
from app.services.edit_planner import FixtureEditPlannerProvider, HttpEditPlannerProvider
from app.services.music import FixtureMusicProvider, LicensedCatalogMusicProvider
from app.services.publishing import PublishOAuthClient, SocialPublishingClient
from app.services.queue import (
    BrollQueue,
    BullMQQueueRegistry,
    EffectsQueue,
    MusicQueue,
    PlanQueue,
    PreprocessQueue,
    PublishQueue,
    QueueRegistry,
    RegenerationQueue,
    RenderQueue,
    ScaleQueue,
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
    FixtureStockVideoProvider,
    PexelsStockVideoProvider,
    PixabayStockVideoProvider,
)
from app.services.storage import StorageSigner
from app.services.token_cipher import TokenCipher
from app.services.transcription_provider import (
    FixtureTranscriptionProvider,
    WhisperCompatibleHttpProvider,
)
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
    app.state.project_repository = (
        SupabaseProjectRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryProjectRepository()
    )
    upload_repository = (
        SupabaseUploadRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
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
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryCaptionRepository(upload_repository)
    )
    app.state.render_repository = (
        SupabaseRenderRepository(
            caption_repository=app.state.caption_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
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
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryPublishingRepository()
    )
    if isinstance(app.state.publishing_repository, SupabasePublishingRepository):
        app.state.publishing_repository.encrypt_legacy_tokens()
    app.state.plan_repository = (
        SupabasePlanRepository(
            caption_repository=app.state.caption_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryPlanRepository(app.state.caption_repository)
    )
    app.state.broll_repository = (
        SupabaseBrollRepository(
            plan_repository=app.state.plan_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryBrollRepository(app.state.plan_repository)
    )
    app.state.music_repository = (
        SupabaseMusicRepository(
            plan_repository=app.state.plan_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryMusicRepository(app.state.plan_repository)
    )
    app.state.effects_repository = (
        SupabaseEffectsRepository(
            plan_repository=app.state.plan_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
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
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryBillingRepository()
    )
    app.state.analytics_repository = (
        SupabaseAnalyticsRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryAnalyticsRepository()
    )
    app.state.scale_repository = (
        SupabaseScaleRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryScaleRepository()
    )
    app.state.visual_repository = (
        SupabaseVisualRepository(
            upload_repository=upload_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryVisualRepository(upload_repository)
    )
    app.state.plan_repository.visual_repository = app.state.visual_repository
    app.state.stripe_billing_client = StripeBillingClient(api_key=settings.stripe_secret_key)
    app.state.storage_signer = StorageSigner(
        base_url=str(settings.api_public_base_url),
        supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
        service_role_key=settings.supabase_service_role_key,
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
        )
    elif settings.app_env == "production":
        raise RuntimeError("REDIS_URL is required in production for BullMQ queueing")
    else:
        app.state.queue_registry = QueueRegistry()
    app.state.preprocess_queue = PreprocessQueue(app.state.queue_registry)
    app.state.transcription_queue = TranscriptionQueue(app.state.queue_registry)
    app.state.visual_queue = VisualQueue(app.state.queue_registry)
    app.state.plan_queue = PlanQueue(app.state.queue_registry)
    app.state.broll_queue = BrollQueue(app.state.queue_registry)
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
    app.state.runway_provider = (
        RunwayGen3Provider(api_key=settings.runway_api_key, model=settings.runway_model)
        if settings.runway_api_key
        else FixtureRunwayProvider()
    )
    transcription_provider_key = settings.transcription_provider.strip().lower()
    edit_planner_provider_key = settings.edit_planner_provider.strip().lower()
    audio_ml_provider_key = settings.audio_ml_provider.strip().lower()

    app.state.transcription_provider = (
        WhisperCompatibleHttpProvider(
            endpoint=GROQ_TRANSCRIPTION_ENDPOINT,
            api_key=settings.groq_api_key,
            model=settings.transcription_model,
            provider_name="groq",
        )
        if transcription_provider_key in {"groq", "openai_compatible"} and settings.groq_api_key
        else FixtureTranscriptionProvider(model=settings.transcription_model)
    )
    app.state.audio_ml_providers = (
        {}
        if settings.process_role == "api"
        else _audio_ml_providers(provider_key=audio_ml_provider_key, settings=settings)
    )
    app.state.edit_planner_provider = (
        HttpEditPlannerProvider(
            endpoint=OPENROUTER_CHAT_COMPLETIONS_ENDPOINT,
            api_key=settings.openrouter_api_key,
            model=settings.edit_planner_model,
            provider_name="openrouter",
        )
        if (
            edit_planner_provider_key in {"openrouter", "openai_compatible"}
            and settings.openrouter_api_key
        )
        else FixtureEditPlannerProvider(model=settings.edit_planner_model)
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "authorization",
            "content-type",
            "x-motionly-user-id",
            "x-motionly-workspace-id",
            "x-request-id",
        ],
    )
    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(uploads_router)
    app.include_router(captions_router)
    app.include_router(visual_router)
    app.include_router(plans_router)
    app.include_router(broll_router)
    app.include_router(music_router)
    app.include_router(effects_router)
    app.include_router(regeneration_router)
    app.include_router(renders_router)
    app.include_router(publishing_router)
    app.include_router(scale_router)
    app.include_router(billing_router)
    app.include_router(analytics_router)
    app.include_router(internal_jobs_router)
    app.include_router(internal_retention_router)
    app.include_router(internal_renders_router)
    app.include_router(local_storage_router)
    app.include_router(admin_router)
    return app


def _validate_production_settings(settings) -> None:
    if settings.app_env != "production":
        return

    if settings.process_role != "api":
        required_worker = {
            "SUPABASE_URL": settings.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
            "REDIS_URL": settings.redis_url,
        }
        missing_worker = [name for name, value in required_worker.items() if not value]
        if missing_worker:
            raise RuntimeError(
                f"Missing production worker environment variables: {', '.join(missing_worker)}"
            )
        return

    required = {
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_JWT_SECRET": settings.supabase_jwt_secret,
        "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
        "SENTRY_DSN": settings.sentry_dsn,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing production environment variables: {', '.join(missing)}")

    if not settings.cors_origin_list:
        raise RuntimeError("API_CORS_ORIGINS is required in production")
    _reject_local_url("API_PUBLIC_BASE_URL", str(settings.api_public_base_url))
    for origin in settings.cors_origin_list:
        _reject_local_url("API_CORS_ORIGINS", origin)

    if not settings.admin_api_token:
        raise RuntimeError("ADMIN_API_TOKEN is required in production for worker access")
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required in production for BullMQ queueing")

    if settings.billing_enabled and (
        not settings.stripe_secret_key or not settings.stripe_webhook_secret
    ):
        raise RuntimeError("Stripe settings are required when BILLING_ENABLED=true")

    if settings.allow_fixture_providers:
        raise RuntimeError("ALLOW_FIXTURE_PROVIDERS must be false in production")
    if settings.stem_separation_provider != "demucs":
        raise RuntimeError("STEM_SEPARATION_PROVIDER must be demucs in production")

    transcription_provider_key = settings.transcription_provider.strip().lower()
    edit_planner_provider_key = settings.edit_planner_provider.strip().lower()
    audio_ml_provider_key = settings.audio_ml_provider.strip().lower()

    if transcription_provider_key == "fixture":
        raise RuntimeError("TRANSCRIPTION_PROVIDER cannot be fixture in production")
    if transcription_provider_key in {"groq", "openai_compatible"} and not settings.groq_api_key:
        raise RuntimeError(
            "GROQ_API_KEY is required when TRANSCRIPTION_PROVIDER uses the Groq-compatible path"
        )
    if edit_planner_provider_key == "fixture":
        raise RuntimeError("EDIT_PLANNER_PROVIDER cannot be fixture in production")
    if (
        edit_planner_provider_key in {"openrouter", "openai_compatible"}
        and not settings.openrouter_api_key
    ):
        raise RuntimeError(
            "OPENROUTER_API_KEY is required when EDIT_PLANNER_PROVIDER uses the OpenRouter path"
        )
    if settings.music_provider == "fixture":
        raise RuntimeError("MUSIC_PROVIDER cannot be fixture in production")
    if settings.visual_intelligence_provider.strip().lower() == "fixture":
        raise RuntimeError("VISUAL_PROVIDER cannot be fixture in production")
    if audio_ml_provider_key == "fixture":
        raise RuntimeError("AUDIO_ML_PROVIDER cannot be fixture in production")
    if settings.music_provider == "licensed_catalog" and (
        not settings.supabase_url or not settings.supabase_service_role_key
    ):
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for licensed music catalog"
        )
    stock_video_provider = settings.stock_video_provider.replace(",", "_")
    if stock_video_provider == "fixture":
        raise RuntimeError("STOCK_VIDEO_PROVIDER cannot be fixture in production")
    if stock_video_provider in {"pexels", "pexels_pixabay"} and not settings.pexels_api_key:
        raise RuntimeError("PEXELS_API_KEY is required when STOCK_VIDEO_PROVIDER includes pexels")
    if stock_video_provider in {"pixabay", "pexels_pixabay"} and not settings.pixabay_api_key:
        raise RuntimeError("PIXABAY_API_KEY is required when STOCK_VIDEO_PROVIDER includes pixabay")


def _reject_local_url(name: str, value: str) -> None:
    lowered = value.lower()
    if any(host in lowered for host in ("localhost", "127.0.0.1", "0.0.0.0", ".invalid")):
        raise RuntimeError(f"{name} must be a real deployed HTTPS URL in production")


def _audio_ml_providers(*, provider_key: str, settings) -> dict[str, object]:
    if provider_key in {"disabled", "none", "off"}:
        return {}
    if provider_key not in {"local_ml", "local", "full"}:
        raise RuntimeError("AUDIO_ML_PROVIDER must be one of local_ml, disabled")

    providers: dict[str, object] = {
        "emotion": Wav2VecEmotionProvider(model=settings.wav2vec_emotion_model),
        "sound": YamnetSoundClassifier(model_url=settings.yamnet_model_url),
    }
    if settings.pyannote_auth_token:
        providers["speaker_diarization"] = PyannoteSpeakerDiarizationProvider(
            model=settings.pyannote_model,
            auth_token=settings.pyannote_auth_token,
        )
    return providers


app = create_app()
