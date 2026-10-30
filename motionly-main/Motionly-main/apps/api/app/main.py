import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.admin_inference import (
    build_admin_inference_registry_store,
)
from app.api.admin_inference import (
    router as admin_inference_router,
)
from app.api.admin_music import router as admin_music_router
from app.api.admin_users import router as admin_users_router
from app.api.ai_proposals import router as ai_proposals_router
from app.api.analytics import router as analytics_router
from app.api.api_keys import router as api_keys_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.broll import router as broll_router
from app.api.capabilities import router as capabilities_router
from app.api.captions import router as captions_router
from app.api.clips import router as clips_router
from app.api.creative_variants import router as creative_variants_router
from app.api.editorial_learning import router as editorial_learning_router
from app.api.effects import router as effects_router
from app.api.errors import (
    ApiError,
    api_error_handler,
    request_validation_error_handler,
    unhandled_error_handler,
)
from app.api.export_shares import router as export_shares_router
from app.api.feedback import router as feedback_router
from app.api.health import router as health_router
from app.api.internal_batches import router as internal_batches_router
from app.api.internal_retention import router as internal_retention_router
from app.api.internal_workflows import router as internal_workflows_router
from app.api.layer_jobs import router as layer_jobs_router
from app.api.local_storage import router as local_storage_router
from app.api.localized_publishing import router as localized_publishing_router
from app.api.media import router as media_router
from app.api.model_choice import router as model_choice_router
from app.api.music import router as music_router
from app.api.operations import router as operations_router
from app.api.plans import router as plans_router
from app.api.projects import router as projects_router
from app.api.public_reviews import router as public_reviews_router
from app.api.regeneration import router as regeneration_router
from app.api.renders import router as renders_router
from app.api.reviews import router as reviews_router
from app.api.revision_errors import RevisionConflictError, revision_conflict_error_handler
from app.api.scale import router as scale_router
from app.api.team import router as team_router
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
from app.repositories.api_keys import InMemoryApiKeyRepository, SupabaseApiKeyRepository
from app.repositories.billing import InMemoryBillingRepository, SupabaseBillingRepository
from app.repositories.broll import InMemoryBrollRepository, SupabaseBrollRepository
from app.repositories.captions import InMemoryCaptionRepository, SupabaseCaptionRepository
from app.repositories.editorial_learning import (
    InMemoryEditorialLearningRepository,
    SupabaseEditorialLearningRepository,
)
from app.repositories.effects import InMemoryEffectsRepository, SupabaseEffectsRepository
from app.repositories.export_shares import (
    InMemoryExportShareRepository,
    SupabaseExportShareRepository,
)
from app.repositories.localized_publishing import (
    InMemoryLocalizedPublishingRepository,
    SupabaseLocalizedPublishingRepository,
)
from app.repositories.model_choice import (
    InMemoryModelChoiceRepository,
    SupabaseModelChoiceRepository,
)
from app.repositories.music import InMemoryMusicRepository, SupabaseMusicRepository
from app.repositories.plans import InMemoryPlanRepository, SupabasePlanRepository
from app.repositories.projects import InMemoryProjectRepository, SupabaseProjectRepository
from app.repositories.regeneration import (
    InMemoryRegenerationRepository,
    SupabaseRegenerationRepository,
)
from app.repositories.renders import InMemoryRenderRepository, SupabaseRenderRepository
from app.repositories.reviews import InMemoryReviewRepository, SupabaseReviewRepository
from app.repositories.scale import InMemoryScaleRepository, SupabaseScaleRepository
from app.repositories.team import InMemoryTeamRepository, SupabaseTeamRepository
from app.repositories.uploads import InMemoryUploadRepository, SupabaseUploadRepository
from app.repositories.visual import InMemoryVisualRepository, SupabaseVisualRepository
from app.repositories.workflows import (
    InMemoryWorkflowRepository,
    SupabaseWorkflowRepository,
)
from app.services.admin_user_directory import AdminUserDirectory
from app.services.ai_features import audio_feature_provider as _audio_feature_provider
from app.services.analytics_recovery import AnalyticsRecoveryQueue
from app.services.audio_diarization import GatewayDiarizationProvider
from app.services.audio_ml_providers import (
    PyannoteSpeakerDiarizationProvider,
    Wav2VecEmotionProvider,
    YamnetSoundClassifier,
    YamnetTFLiteSoundClassifier,
)
from app.services.auth_admin import AuthAdminClient
from app.services.billing import PlanFeatureQuotaGuard
from app.services.collaboration import (
    notifier_from_settings as collaboration_notifier_from_settings,
)
from app.services.compute_backend import compute_backend_from_settings
from app.services.generic_media_upload import GenericMediaUploadSessionService
from app.services.inference_runtime import (
    RuntimeHolder,
    build_inference_registry_store,
    build_inference_runtime,
)
from app.services.inference_secrets import SecretResolver
from app.services.local_storage import create_object_storage
from app.services.model_choice import (
    ModelChoiceGatewayResolver,
    build_model_choice_service_for_app,
)
from app.services.model_domain_adapters import (
    GatewayAudioIntelligenceProvider,
    GatewayVisualIntelligenceProvider,
    bundle_revision,
    register_audio_route,
    register_clip_routes,
    register_stem_route,
    register_visual_route,
)
from app.services.model_gateway import (
    ModelCapability,
    ModelOperation,
    route_catalog_fingerprint,
    validate_gateway_operations,
)
from app.services.model_provider_bridges import (
    GatewayEditPlannerProvider,
    GatewayTranscriptionProvider,
)
from app.services.music import LicensedCatalogMusicProvider
from app.services.object_references import ObjectReferenceService
from app.services.operations import MetricsRegistry, OperationsRepository
from app.services.payment_gateway import build_gateway
from app.services.queue import (
    BrollQueue,
    BullMQQueueRegistry,
    EffectsQueue,
    MatteQueue,
    MusicQueue,
    PlanQueue,
    PreprocessQueue,
    QueueRegistry,
    RegenerationQueue,
    RenderQueue,
    ScaleQueue,
    StockAssetEnrichmentQueue,
    TranscriptionQueue,
    VisualQueue,
)
from app.services.remote_audio_emotion import RemoteAudioEmotionProvider
from app.services.scale import (
    DemucsStemSeparator,
    FixtureStemSeparator,
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
from app.services.upload_session import UploadSessionService
from app.services.visual_intelligence import (
    FixtureVisualIntelligenceProvider,
    WorkerOnlyVisualIntelligenceProvider,
)


def _visual_manifest_identity(model_gateway) -> str:
    """A neutral ``clip|blip|yolo`` manifest identity from the registry's local
    visual routes (gpu-local.json, plan Task 14), never a config model-id
    default. Falls back to neutral capability labels when a route is absent."""
    clip, _ = _gateway_route_identity(
        model_gateway, capability="embeddings.image", fallback="visual-embeddings"
    )
    blip, _ = _gateway_route_identity(
        model_gateway, capability="vision.caption", fallback="visual-caption"
    )
    yolo, _ = _gateway_route_identity(
        model_gateway, capability="vision.object_detection", fallback="visual-objects"
    )
    return "|".join((clip, blip, yolo))


def _gateway_route_identity(
    model_gateway, *, capability: str, fallback: str
) -> tuple[str, str]:
    """Model id and revision from the gateway's highest-priority route for a
    capability (plan Task 14: identity comes from the registry, not a code
    default). Returns neutral capability labels when no such route exists."""
    if model_gateway is None:
        return fallback, f"{fallback}-v1"
    candidates = [
        route
        for route in model_gateway.routes
        if route.capability.value == capability and route.enabled
    ]
    if not candidates:
        return fallback, f"{fallback}-v1"
    route = min(candidates, key=lambda item: item.priority)
    return route.model, route.model_revision


def _resolve_video_generation_route(model_gateway) -> object | None:
    """The enabled route serving ``video.generation.submit`` (plan Task 18).

    Generated B-roll is capability-driven: any enabled route whose operations
    include ``GENERATION_VIDEO_SUBMIT`` serves it. ``None`` means the capability
    is unavailable and the API returns ``capability_unavailable`` (503).
    """
    if model_gateway is None:
        return None
    for route in model_gateway.routes:
        if ModelOperation.GENERATION_VIDEO_SUBMIT in route.operations:
            return route
    return None


def _resolve_stem_separation_route(model_gateway) -> object | None:
    """The enabled route serving ``audio.stem_separation`` (plan Task 17).

    Stem jobs are capability-driven: any enabled route whose operations include
    ``AUDIO_STEM_SEPARATION`` serves them — a CPU-local Demucs ``local`` route, a
    remote ``async_job`` template route, or the still-present vendor route.
    ``None`` means the capability is not configured by the registry; the caller
    then falls back to the vendor/Demucs registration (removed in Task 19), and
    the API returns ``capability_unavailable`` (503) when nothing serves it.
    """
    if model_gateway is None:
        return None
    for route in model_gateway.routes:
        if ModelOperation.AUDIO_STEM_SEPARATION in route.operations:
            return route
    return None


def _build_inference_operations_repository(settings, *, metrics_registry=None):
    """A real async-operation repository (plan Task 16A) for app.state.

    Supabase-backed when configured, otherwise the in-memory implementation for
    dev and tests. This is distinct from the cost ledger (OperationsRepository);
    generated B-roll reserves and transitions rows here.
    """
    from app.services.inference_operations import InMemoryInferenceOperationRepository

    supabase_url = settings.supabase_url
    service_key = settings.supabase_service_role_key
    if supabase_url and service_key:
        from app.repositories.inference_operations import (
            SupabaseInferenceOperationRepository,
        )

        return SupabaseInferenceOperationRepository(
            supabase_url=str(supabase_url),
            service_role_key=str(service_key),
            metrics_registry=metrics_registry,
        )
    return InMemoryInferenceOperationRepository()


def create_app() -> FastAPI:
    settings = get_settings()
    _validate_production_settings(settings)
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            rate_limiter = getattr(application.state, "rate_limiter", None)
            redis_client = getattr(rate_limiter, "redis", None)
            if redis_client is not None:
                await redis_client.aclose()
            queue_registry = getattr(application.state, "queue_registry", None)
            close = getattr(queue_registry, "close", None)
            if callable(close):
                await asyncio.to_thread(close)

    app = FastAPI(
        title="Motionly API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production_like else None,
        openapi_url=None if settings.is_production_like else "/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.local_audit_live_effects_gate = local_audit_live_effects_gate_from_settings(settings)
    app.state.launch_feedback = []
    app.state.metrics_registry = MetricsRegistry()
    app.state.collaboration_notifier = collaboration_notifier_from_settings(settings)
    app.state.operations_repository = OperationsRepository(
        supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
        service_role_key=settings.supabase_service_role_key,
    )
    # One registry-driven runtime (plan Task 6, Path B). The registry file is the
    # only source of routes. The remaining vendor-specific register_* calls
    # further down still add routes to this same gateway for now; Tasks 17 and 19
    # remove them.
    # The database registry source (plan Task 20) is additive and off unless
    # AI_REGISTRY_DATABASE_ENABLED. When on, a store supplies the catalog
    # (adapters/models/routes) and a RuntimeHolder reloads atomically when the
    # ai_registry_version counter changes. The holder's accessor keeps
    # app.state.inference_runtime / model_gateway pointing at the live runtime.
    _registry_store = build_inference_registry_store(settings, app.state.metrics_registry)

    # Admin registry store and db: secret opener (plan Tasks 20/21). The admin
    # store carries the AES-256-GCM secret store so the admin API can create and
    # open db: secrets; the catalog store above stays secret-less so ai_secrets
    # is never reachable from worker code. The resolver's db_secret_opener is the
    # ONLY path from a db: ref to a plaintext in the API process, and it lives on
    # app.state.inference_registry_store, never on a worker-reachable object.
    _admin_registry_store = build_admin_inference_registry_store(
        settings, app.state.metrics_registry
    )
    app.state.inference_registry_store = _admin_registry_store
    _db_secret_opener = (
        (lambda name: _admin_registry_store.open_secret(name=name))
        if _admin_registry_store is not None
        else None
    )
    _secret_resolver = SecretResolver(settings=settings, db_secret_opener=_db_secret_opener)

    def _build_runtime() -> object:
        return build_inference_runtime(
            settings,
            metrics_registry=app.state.metrics_registry,
            operations_repository=app.state.operations_repository,
            live_effects_gate=app.state.local_audit_live_effects_gate,
            registry_store=_registry_store,
            secret_resolver=_secret_resolver,
        )

    app.state.inference_runtime_holder = RuntimeHolder(
        build=_build_runtime,
        registry_store=_registry_store,
        reload_interval_s=float(getattr(settings, "ai_registry_reload_interval_s", 30.0)),
    )
    app.state.inference_runtime = app.state.inference_runtime_holder.runtime
    # Dozens of call sites read app.state.model_gateway; keep that attribute.
    app.state.model_gateway = app.state.inference_runtime.gateway
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
    app.state.export_share_repository = (
        SupabaseExportShareRepository(
            render_repository=app.state.render_repository,
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryExportShareRepository(app.state.render_repository)
    )
    app.state.team_repository = (
        SupabaseTeamRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryTeamRepository()
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
    if app.state.model_gateway is not None:
        # Per-plan quotas for remote AI features; routes without a metered
        # budget_feature (every local GPU/CPU route) are unaffected.
        app.state.model_gateway.set_quota_guard(
            PlanFeatureQuotaGuard(
                billing_repository=app.state.billing_repository,
                operations_repository=app.state.operations_repository,
                settings=settings,
            )
        )
    # User model choice (plan Section 2.11, Tasks 30/31). The repository stores
    # the per-workspace/per-project choices; the service resolves them against
    # the live runtime (so a registry reload is honoured) and the workspace's
    # lapsed-aware billing plan; the resolver fills the gateway's preferred route
    # at call time, so API calls and background jobs follow the same choice.
    app.state.model_choice_repository = (
        SupabaseModelChoiceRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryModelChoiceRepository()
    )
    app.state.model_choice_service = build_model_choice_service_for_app(
        app, repository=app.state.model_choice_repository
    )
    if app.state.model_gateway is not None:
        app.state.model_gateway.set_choice_resolver(
            ModelChoiceGatewayResolver(app.state.model_choice_service)
        )
    app.state.api_key_repository = (
        SupabaseApiKeyRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
            metrics_registry=app.state.metrics_registry,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else InMemoryApiKeyRepository()
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
    app.state.payment_gateway = build_gateway(settings)
    storage_supabase_url = None if settings.force_local_media_storage else settings.supabase_url
    storage_service_role_key = (
        None if settings.force_local_media_storage else settings.supabase_service_role_key
    )
    app.state.storage_signer = StorageSigner(
        base_url=str(settings.api_public_base_url),
        supabase_url=str(storage_supabase_url) if storage_supabase_url else None,
        service_role_key=storage_service_role_key,
        # Browsers open signed links on the public origin (N12).
        public_supabase_url=None
        if settings.force_local_media_storage
        else settings.browser_supabase_url,
        # Local-storage links are HMAC-signed with an expiry (B38); the same
        # secret verifies them in app.api.local_storage.
        signing_secret=settings.supabase_jwt_secret,
    )
    app.state.object_storage = create_object_storage(
        root=settings.media_work_dir,
        supabase_url=str(storage_supabase_url) if storage_supabase_url else None,
        service_role_key=storage_service_role_key,
    )
    # Upload metadata (object references, quota reservations, upload sessions)
    # lives in database tables beside uploaded_videos, so it follows the
    # database even when FORCE_LOCAL_MEDIA_STORAGE keeps the bytes on disk.
    metadata_in_database = bool(settings.supabase_url and settings.supabase_service_role_key)
    metadata_supabase_url = str(settings.supabase_url) if metadata_in_database else None
    metadata_service_role_key = settings.supabase_service_role_key if metadata_in_database else None
    app.state.storage_quota_service = StorageQuotaService(
        supabase_url=metadata_supabase_url,
        service_role_key=metadata_service_role_key,
        in_memory=not metadata_in_database,
    )
    app.state.object_reference_service = ObjectReferenceService(
        storage_signer=app.state.storage_signer,
        supabase_url=metadata_supabase_url,
        service_role_key=metadata_service_role_key,
        in_memory=not metadata_in_database,
    )
    upload_session_db = (
        SupabaseRestClient(
            supabase_url=metadata_supabase_url,
            service_role_key=metadata_service_role_key,
            repository="upload_sessions",
            metrics_registry=app.state.metrics_registry,
        )
        if metadata_in_database
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
    # Accounts are created through Auth's admin API (public sign-up is off).
    app.state.auth_admin = (
        AuthAdminClient(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else None
    )
    # Password-reset requests and the admin audit log (Task 12).
    app.state.admin_user_directory = AdminUserDirectory(
        supabase_url=str(settings.supabase_url) if settings.supabase_url else None,
        service_role_key=settings.supabase_service_role_key,
        metrics_registry=app.state.metrics_registry,
    )
    if settings.redis_url:
        app.state.queue_registry = BullMQQueueRegistry(
            redis_url=settings.redis_url,
            bridge_path=settings.bullmq_bridge_path,
            node_command=settings.bullmq_node_command,
            job_attempts=settings.job_attempts,
            bridge_timeout_seconds=settings.bullmq_bridge_timeout_seconds,
        )
    elif settings.is_production_like:
        raise RuntimeError("REDIS_URL is required in production for BullMQ queueing")
    else:
        app.state.queue_registry = QueueRegistry()
    # The GPU capacity observer belongs to the GPU stage; CPU-stage
    # (test_fixture) and GPU-free (GPU_WORKERS_ENABLED=false) deployments have
    # no GPU workers to observe.
    app.state.gpu_scaling_service = (
        compute_backend_from_settings(settings)
        if settings.gpu_stage == "enabled" and settings.gpu_workers_enabled
        else None
    )
    app.state.preprocess_queue = PreprocessQueue(app.state.queue_registry)
    app.state.transcription_queue = TranscriptionQueue(app.state.queue_registry)
    app.state.visual_queue = VisualQueue(app.state.queue_registry)
    app.state.matte_queue = MatteQueue(app.state.queue_registry)
    app.state.plan_queue = PlanQueue(app.state.queue_registry)
    app.state.broll_queue = BrollQueue(app.state.queue_registry)
    app.state.stock_asset_enrichment_queue = StockAssetEnrichmentQueue(app.state.queue_registry)
    app.state.render_queue = RenderQueue(app.state.queue_registry)
    app.state.scale_queue = ScaleQueue(app.state.queue_registry)
    stem_provider_key = settings.stem_separation_provider.strip().lower()
    use_fixture_stems = (
        settings.process_role == "api"
        or stem_provider_key == "fixture"
        or (not settings.is_production_like and settings.allow_fixture_providers)
    )
    if use_fixture_stems:
        app.state.stem_separator = FixtureStemSeparator()
    else:
        app.state.stem_separator = DemucsStemSeparator()
    app.state.stem_gateway_route = None
    if app.state.model_gateway is not None and settings.process_role != "api":
        # Prefer whatever ``audio.stem_separation`` route the gateway already has
        # from the declarative registry (plan Task 17): a CPU-local Demucs route
        # (``cpu-local.json``, Task 14), a remote ``async_job`` template route, or
        # — until Task 19 removes it — the still-present vendor route registered
        # below. Resolving by capability keeps the worker vendor-neutral.
        app.state.stem_gateway_route = _resolve_stem_separation_route(app.state.model_gateway)
        if app.state.stem_gateway_route is None:
            # Local Demucs identity comes from the registry's
            # ``audio.stem_separation`` local route (cpu-local.json, Task 14);
            # this legacy fallback registration uses a neutral capability
            # label so no local model id remains a code default here.
            app.state.stem_gateway_route = register_stem_route(
                app.state.model_gateway,
                separator=app.state.stem_separator,
                model=getattr(app.state.stem_separator, "model", "stem-separation"),
                revision=getattr(
                    app.state.stem_separator, "model_revision", "local-stem-separation-v1"
                ),
            )
    # Generated B-roll (Task 18) submits/polls through whichever enabled route
    # serves ``video.generation.submit`` (ModelOperation.GENERATION_VIDEO_SUBMIT).
    # Resolving by capability keeps the worker and API vendor-neutral: a
    # registry-driven video route populates this the same way.
    app.state.video_generation_route = _resolve_video_generation_route(app.state.model_gateway)
    # The inference runtime's ``operations_repository`` must be a real
    # InferenceOperationRepository (reserve/transition), not the cost ledger.
    # build_inference_runtime receives the cost ledger for gateway cost
    # telemetry, so swap in a dedicated async-operation repository here so
    # app.state.inference_runtime.operations_repository works for Task 18.
    app.state.inference_runtime = replace(
        app.state.inference_runtime,
        operations_repository=_build_inference_operations_repository(
            settings, metrics_registry=app.state.metrics_registry
        ),
    )
    audio_ml_provider_key = settings.audio_ml_provider.strip().lower()

    gateway_has_transcription = bool(
        app.state.model_gateway
        and any(
            route.capability.value == "transcription" for route in app.state.model_gateway.routes
        )
    )
    if gateway_has_transcription:
        # Gateway-only transcription (Task 10): production and dev route through
        # the model gateway. In dev/test the built-in fake registry supplies a
        # transcription route, so a gateway transcription route always exists
        # unless production is misconfigured (which raises below). The model
        # identity is read from the registry transcription route (plan Task 14:
        # no model id remains a code default), not from a config default.
        transcription_model, transcription_revision = _gateway_route_identity(
            app.state.model_gateway, capability="transcription", fallback="transcription"
        )
        app.state.transcription_provider = GatewayTranscriptionProvider(
            gateway=app.state.model_gateway,
            provider_name="gateway",
            model=transcription_model,
            model_revision=transcription_revision,
        )
    elif settings.is_production_like:
        raise RuntimeError("Production transcription requires a configured model gateway route")
    elif settings.local_audit_mode:
        raise RuntimeError(
            "LOCAL_AUDIT_MODE requires transcription via model gateway, "
            "but no gateway transcription route is configured. "
            "Direct provider fallback is blocked to ensure live-effects gate coverage."
        )
    else:
        raise RuntimeError(
            "Transcription requires a configured model gateway route; the direct "
            "provider fallbacks were removed in Task 10 (dev uses the fake route)."
        )
    app.state.audio_ml_providers = (
        {}
        if settings.process_role == "api"
        else _audio_ml_providers(
            provider_key=audio_ml_provider_key,
            settings=settings,
            model_gateway=app.state.model_gateway,
            runtime=getattr(app.state, "inference_runtime", None),
        )
    )
    app.state.audio_intelligence_provider = None
    if app.state.model_gateway is not None and app.state.audio_ml_providers:
        audio_route = register_audio_route(
            app.state.model_gateway,
            providers=app.state.audio_ml_providers,
            # The bundle revision is derived from the constructed providers'
            # own identities (sourced from the registry local routes, plan
            # Task 14), not from config model-id defaults.
            revision=bundle_revision(*_audio_bundle_revision_parts(app.state.audio_ml_providers)),
        )
        app.state.audio_intelligence_provider = GatewayAudioIntelligenceProvider(
            gateway=app.state.model_gateway,
            route=audio_route,
        )
    gateway_has_planning = bool(
        app.state.model_gateway
        and any(route.capability.value == "planning" for route in app.state.model_gateway.routes)
    )
    if gateway_has_planning:
        # Gateway-only planning (Task 10): production and dev route through the
        # model gateway. The dev/test fake registry supplies a planning route, so
        # a gateway planning route always exists unless production is
        # misconfigured (which raises below). The bridge takes only the gateway
        # and typed requests; the planning token quota is recorded and (under
        # AI_QUOTA_SCOPE=feature) enforced by the gateway's PlanFeatureQuotaGuard.
        app.state.edit_planner_provider = GatewayEditPlannerProvider(
            model=route_catalog_fingerprint(
                app.state.model_gateway,
                capability=ModelCapability.PLANNING,
                operation=ModelOperation.PLANNING_CHAT,
            ),
            provider_name="gateway",
            gateway=app.state.model_gateway,
        )
    elif settings.is_production_like:
        raise RuntimeError("Production planning requires a configured model gateway route")
    elif settings.local_audit_mode:
        raise RuntimeError(
            "LOCAL_AUDIT_MODE requires planning via model gateway, "
            "but no gateway planning route is configured. "
            "Direct provider fallback is blocked to ensure live-effects gate coverage."
        )
    else:
        raise RuntimeError(
            "Planning requires a configured model gateway route; the direct "
            "provider fallbacks were removed in Task 10 (dev uses the fake route)."
        )
    visual_provider_key = settings.visual_intelligence_provider.strip().lower()
    # The local visual-manifest identity (CLIP|BLIP|YOLO) comes from the registry
    # ``embeddings``/``vision.caption``/``vision.object_detection`` local routes
    # (gpu-local.json, plan Task 14), not config model-id defaults.
    visual_manifest_model = _visual_manifest_identity(app.state.model_gateway)
    app.state.visual_intelligence_provider = (
        FixtureVisualIntelligenceProvider()
        if visual_provider_key == "fixture"
        else WorkerOnlyVisualIntelligenceProvider(
            model=visual_manifest_model,
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
            revision=bundle_revision(app.state.visual_intelligence_provider.model),
            route_id="control-visual-manifest",
        )
        app.state.visual_gateway_provider = GatewayVisualIntelligenceProvider(
            gateway=app.state.model_gateway,
            route=visual_route,
        )
    app.state.embedding_gateway_provider = None
    # Remote (text/image) embeddings are rewired to capability routes in Tier 2
    # (plan Task 12); Task 19 removed the vendor embeddings wiring. Until then the
    # gateway serves local CLIP embeddings (and, outside production, a hash test
    # fallback); with no route, B-roll ranking falls back to lexical scoring.
    if app.state.model_gateway is not None:
        # CLIP embedding identity comes from the registry ``embeddings.image``
        # local route (gpu-local.json, plan Task 14), not a config model-id
        # default.
        clip_model, clip_revision = _gateway_route_identity(
            app.state.model_gateway,
            capability="embeddings.image",
            fallback="local-clip-embeddings",
        )
        register_clip_routes(
            app.state.model_gateway,
            primary_provider=CompatibleClipEmbeddingProvider(
                model_name=clip_model,
                allow_fallback=False,
                revision=clip_revision,
            ),
            primary_revision=clip_revision,
            # Hash embeddings are a test fixture; production has no fallback.
            fallback_provider=(
                HashClipEmbeddingProvider(model_name=COMPATIBLE_CLIP_MODEL_VERSION)
                if settings.app_env != "production"
                else None
            ),
        )
    if app.state.model_gateway is not None:
        from app.services.model_domain_adapters import GatewayClipEmbeddingProvider

        # Built after route registration: its cache version hashes the routes, so
        # switching providers invalidates cached stock-asset embeddings.
        app.state.embedding_gateway_provider = GatewayClipEmbeddingProvider(
            gateway=app.state.model_gateway
        )

    if settings.app_env == "production":
        # Plan Task 7: production requires only the two startup capabilities
        # (planning.chat and transcription.speech_to_text). Every other
        # capability is optional and validated by the registry startup decision
        # (Section 2.6); it must never block startup here.
        required_operations = [
            ModelOperation.PLANNING_CHAT,
            ModelOperation.TRANSCRIPTION_SPEECH_TO_TEXT,
        ]
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
    # Non-production and the CPU stage serve music from the SAME licensed catalog
    # provider, seeded with generated "Test track" entries (see
    # app.ops.seed_music_catalog, run once at non-prod startup after migrations).
    # There is no bespoke fixture music provider anymore.
    app.state.music_providers = music_providers
    app.state.music_queue = MusicQueue(app.state.queue_registry)
    app.state.effects_queue = EffectsQueue(app.state.queue_registry)
    app.state.regeneration_queue = RegenerationQueue(app.state.queue_registry)

    app.middleware("http")(rate_limit_middleware)
    app.add_exception_handler(Exception, unhandled_error_handler)
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
            "x-motionly-workspace-id",
            "x-request-id",
        ],
    )
    # Registration order makes security headers outermost and request IDs wrap
    # rate limiting, so 429s are logged and correlated like normal responses.
    app.middleware("http")(request_id_middleware)
    app.middleware("http")(security_headers_middleware)
    app.include_router(health_router)
    app.include_router(capabilities_router)
    app.include_router(model_choice_router)
    app.include_router(auth_router)
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
    app.include_router(export_shares_router)
    app.include_router(team_router)
    app.include_router(scale_router)
    app.include_router(billing_router)
    app.include_router(api_keys_router)
    app.include_router(analytics_router)
    app.include_router(editorial_learning_router)
    app.include_router(internal_batches_router)
    app.include_router(internal_retention_router)
    app.include_router(internal_workflows_router)
    app.include_router(local_storage_router)
    app.include_router(admin_router)
    app.include_router(admin_users_router)
    app.include_router(admin_music_router)
    app.include_router(admin_inference_router)
    return app


def _validate_production_settings(settings) -> None:
    # Backward-compatible import surface for callers and tests.
    from app.core.production import validate_production_settings

    validate_production_settings(settings)


def _audio_bundle_revision_parts(providers: dict[str, object]) -> list[str]:
    """Identity parts for the local audio route's bundle revision (plan Task 14).

    Each enabled provider contributes its own model identity (``model_revision``,
    ``revision``, ``model`` or ``model_url`` — whichever it exposes), sourced from
    the registry ``local`` route, never a config model-id default. The parts are
    ordered by feature name so the bundle revision is deterministic, and switching
    a provider (remote, CPU replacement, or a different registry route) changes
    it, which invalidates the capability cache.
    """
    parts: list[str] = []
    for name, provider in sorted(providers.items()):
        identity = (
            getattr(provider, "model_revision", None)
            or getattr(provider, "revision", None)
            or getattr(provider, "model", None)
            or getattr(provider, "model_url", None)
            or ""
        )
        parts.append(f"{name}:{identity}")
    return parts


def _registry_sound_provider(runtime):
    """Sound classifier built from the registry ``audio.sound_classification`` route.

    Reads the local route's ``params.model_path``/``params.weights_sha256`` (plan
    Task 33: the model path and SHA come from the registry route/model record,
    not from settings). Returns ``None`` when no such local route is configured,
    so sound classification degrades instead of being built from a code default.
    """
    if runtime is None:
        return None
    route_records = getattr(runtime, "route_records", None) or {}
    adapter_records = getattr(runtime, "adapter_records", None) or {}
    for route in route_records.values():
        if route.capability.value != "audio.sound_classification":
            continue
        adapter = adapter_records.get(route.adapter_id)
        if adapter is None or adapter.type != "local":
            continue
        params = dict(route.params)
        model_path = params.get("model_path")
        weights_sha256 = params.get("weights_sha256")
        if not isinstance(model_path, str) or not isinstance(weights_sha256, str):
            continue
        return YamnetTFLiteSoundClassifier(
            model_path=model_path, model_sha256=weights_sha256
        )
    return None


def _gateway_diarization_provider(model_gateway):
    """Neutral diarization over any registry ``audio.diarization`` route (Task 17).

    Returns a :class:`GatewayDiarizationProvider` bound to the gateway's first
    ``audio.diarization`` route (a template ``async_job``/``generic_http`` route,
    or a local GPU route), or ``None`` when the gateway carries no such route so
    the caller leaves ``speaker_diarization`` unconfigured. The wrapper itself
    degrades to the transcript heuristic at call time regardless, matching the
    archived vendor wrapper's degrade-instead-of-raise behaviour.
    """
    if model_gateway is None:
        return None
    route = next(
        (
            route
            for route in model_gateway.routes
            if ModelOperation.AUDIO_DIARIZATION in route.operations
        ),
        None,
    )
    if route is None:
        return None
    return GatewayDiarizationProvider(
        gateway=model_gateway,
        model=route.model,
        model_revision=route.model_revision,
    )


def _gateway_emotion_provider(model_gateway):
    """Neutral voice emotion over any registry ``audio.emotion`` route (Task 11).

    Returns a :class:`RemoteAudioEmotionProvider` bound to the gateway's
    highest-priority ``audio.emotion`` route (an ``openai_compatible`` audio-input
    chat route or a local GPU route), or ``None`` when the gateway carries no such
    route. The provider degrades to the transcript estimate at call time, so a
    missing route never fails transcription (plan Section 3).
    """
    if model_gateway is None:
        return None
    route = next(
        (
            route
            for route in model_gateway.routes
            if ModelOperation.AUDIO_PROMPT in route.operations
        ),
        None,
    )
    if route is None:
        return None
    return RemoteAudioEmotionProvider(
        gateway=model_gateway,
        route_id=route.route_id,
        model=route.model,
        model_revision=route.model_revision,
    )


def _audio_ml_providers(
    *, provider_key: str, settings, model_gateway=None, runtime=None
) -> dict[str, object]:
    group_disabled = provider_key in {"disabled", "none", "off"}
    if not group_disabled and provider_key not in {"local_ml", "local", "full"}:
        raise RuntimeError("AUDIO_ML_PROVIDER must be one of local_ml, disabled")
    diarization_key = _audio_feature_provider(
        settings.audio_diarization_provider,
        group_disabled=group_disabled,
    )
    emotion_key = _audio_feature_provider(
        settings.audio_emotion_provider,
        group_disabled=group_disabled,
    )
    sound_key = _audio_feature_provider(
        settings.audio_sound_provider,
        group_disabled=group_disabled,
    )

    providers: dict[str, object] = {}
    # Local model identity comes from the registry's ``local`` route for each
    # capability (gpu-local.json/cpu-local.json, plan Task 14), never a config
    # default. When no such route is configured, a neutral capability label is
    # used so the provider still constructs for availability reporting.
    if emotion_key == "local_ml":
        emotion_model, emotion_revision = _gateway_route_identity(
            model_gateway, capability="audio.emotion", fallback="audio-emotion"
        )
        providers["emotion"] = Wav2VecEmotionProvider(
            model=emotion_model,
            revision=emotion_revision,
        )
    elif not group_disabled:
        # Task 11: with no local emotion model, drive whatever ``audio.emotion``
        # route the registry configured (an openai_compatible audio-input chat
        # route, or a local GPU route) through the neutral Motionly provider. It
        # degrades to the transcript estimate when no such route exists.
        gateway_emotion = _gateway_emotion_provider(model_gateway)
        if gateway_emotion is not None:
            providers["emotion"] = gateway_emotion
    if sound_key == "yamnet_tflite":
        # The YAMNet TFLite model path/sha come from the registry
        # ``audio.sound_classification`` local route (cpu-local.json, plan Task
        # 33), never from settings. A missing route leaves sound unconfigured.
        sound_provider = _registry_sound_provider(runtime)
        if sound_provider is not None:
            providers["sound"] = sound_provider
    elif sound_key == "local_ml":
        sound_model, _sound_revision = _gateway_route_identity(
            model_gateway,
            capability="audio.sound_classification",
            fallback="audio-sound-classification",
        )
        providers["sound"] = YamnetSoundClassifier(model_url=sound_model)
    if diarization_key == "local_ml" and settings.pyannote_auth_token:
        diarization_model, diarization_revision = _gateway_route_identity(
            model_gateway, capability="audio.diarization", fallback="audio-diarization"
        )
        providers["speaker_diarization"] = PyannoteSpeakerDiarizationProvider(
            model=diarization_model,
            auth_token=settings.pyannote_auth_token,
            revision=diarization_revision,
        )
    elif not group_disabled and "speaker_diarization" not in providers:
        # Task 17: with no explicit vendor/local diarization provider, drive
        # whatever ``audio.diarization`` route the registry configured (a
        # template ``async_job``/``generic_http`` route or a local GPU route)
        # through the neutral wrapper. It degrades to the transcript heuristic
        # when no such route exists, so this never blocks audio intelligence.
        gateway_diarization = _gateway_diarization_provider(model_gateway)
        if gateway_diarization is not None:
            providers["speaker_diarization"] = gateway_diarization
    return providers


app = create_app()
