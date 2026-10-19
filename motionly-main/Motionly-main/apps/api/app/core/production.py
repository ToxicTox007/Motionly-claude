from __future__ import annotations

from app.services.model_gateway import (
    ModelCapability,
    ModelOperation,
    is_immutable_model_revision,
)
from app.services.model_gateway_factory import model_gateway_from_settings

ORCHESTRATION_PROCESS_ROLES = {
    "temporal-dispatcher",
    "temporal-worker",
    "workflow-reconciler",
}


def validate_production_settings(settings: object) -> None:
    if settings.app_env != "production":
        return
    if getattr(settings, "ml_execution_mode", "gpu") != "gpu":
        raise RuntimeError(
            "ML_EXECUTION_MODE=test_fixture is not allowed in production; "
            "GPU-dependent capabilities must run on the GPU workers"
        )

    process_role = str(settings.process_role).strip().lower()
    deployment_mode = str(settings.temporal_deployment_mode).strip().lower()
    if process_role not in ORCHESTRATION_PROCESS_ROLES:
        # The API and the Python workers sign links that browsers open.
        _require_public_supabase_url(settings)
    temporal_roles = {
        "api": ("api", "motionly-api"),
        "temporal-dispatcher": ("dispatcher", "motionly-dispatcher"),
        "temporal-worker": ("worker", "motionly-temporal-worker"),
        "workflow-reconciler": ("reconciler", "motionly-reconciler"),
    }
    temporal_required = process_role in ORCHESTRATION_PROCESS_ROLES or (
        process_role == "api" and deployment_mode == "self_hosted"
    )
    if temporal_required:
        required_orchestration = {
            "SUPABASE_URL": settings.supabase_url,
            "SUPABASE_JWT_SECRET": settings.supabase_jwt_secret,
            "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
            "TEMPORAL_ADDRESS": settings.temporal_address,
            "TEMPORAL_NAMESPACE": settings.temporal_namespace,
            "TEMPORAL_TASK_QUEUE": settings.temporal_task_queue,
        }
        if deployment_mode == "cloud":
            required_orchestration["TEMPORAL_API_KEY"] = settings.temporal_api_key
        elif deployment_mode == "self_hosted":
            if str(settings.temporal_address or "").strip().lower() != "temporal:7233":
                raise RuntimeError(
                    "Self-hosted Temporal must use the private Docker service temporal:7233"
                )
            expected_role, expected_identity = temporal_roles[process_role]
            required_orchestration.update(
                {
                    "TEMPORAL_SERVER_ROOT_CA_PATH": settings.temporal_server_root_ca_path,
                    "TEMPORAL_CLIENT_CERT_PATH": settings.temporal_client_cert_path,
                    "TEMPORAL_CLIENT_PRIVATE_KEY_PATH": settings.temporal_client_private_key_path,
                    "TEMPORAL_SERVER_NAME": settings.temporal_server_name,
                    "TEMPORAL_CLIENT_ROLE": settings.temporal_client_role,
                    "TEMPORAL_IDENTITY": settings.temporal_identity,
                }
            )
            if not settings.temporal_tls_enabled:
                raise RuntimeError(
                    "TEMPORAL_TLS_ENABLED must be true in production self-hosted mode"
                )
            if str(settings.temporal_server_name or "").strip() != "temporal":
                raise RuntimeError("TEMPORAL_SERVER_NAME must be temporal for self-hosted mTLS")
            if str(settings.temporal_client_role or "").strip() != expected_role:
                raise RuntimeError("TEMPORAL_CLIENT_ROLE does not match the process role")
            if str(settings.temporal_identity or "").strip() != expected_identity:
                raise RuntimeError("TEMPORAL_IDENTITY does not match the certificate-bound role")
        else:
            raise RuntimeError("TEMPORAL_DEPLOYMENT_MODE must be cloud or self_hosted")
        missing_orchestration = [
            name
            for name, value in required_orchestration.items()
            if value is None or not str(value).strip()
        ]
        if missing_orchestration:
            raise RuntimeError(
                "Missing production orchestration environment variables: "
                f"{', '.join(missing_orchestration)}"
            )
        if deployment_mode == "cloud" and not settings.temporal_tls_enabled:
            raise RuntimeError("TEMPORAL_TLS_ENABLED must be true in production cloud mode")
        if process_role == "temporal-worker":
            if settings.redis_url is None or not str(settings.redis_url).strip():
                raise RuntimeError(
                    "REDIS_URL is required for the production temporal-worker enqueue activity"
                )
            missing_internal_api = [
                name
                for name, value in {
                    "WORKER_API_BASE_URL": settings.worker_api_base_url,
                    "WORKER_API_TOKEN": settings.worker_api_token,
                }.items()
                if value is None or not str(value).strip()
            ]
            if missing_internal_api:
                raise RuntimeError(
                    "Missing production Temporal proposal activity variables: "
                    + ", ".join(missing_internal_api)
                )
        return

    transcription_provider_key = settings.transcription_provider.strip().lower()
    edit_planner_provider_key = settings.edit_planner_provider.strip().lower()
    visual_provider_key = settings.visual_intelligence_provider.strip().lower()
    audio_ml_provider_key = settings.audio_ml_provider.strip().lower()
    music_provider_key = settings.music_provider.strip().lower()
    stock_video_provider_key = settings.stock_video_provider.strip().lower().replace(",", "_")

    supported_providers = {
        "TRANSCRIPTION_PROVIDER": (transcription_provider_key, {"groq", "openai_compatible"}),
        "EDIT_PLANNER_PROVIDER": (
            edit_planner_provider_key,
            {"openrouter", "openai_compatible"},
        ),
        "VISUAL_PROVIDER": (visual_provider_key, {"local_ml"}),
        "AUDIO_ML_PROVIDER": (
            audio_ml_provider_key,
            {"local_ml", "local", "full", "disabled", "none", "off"},
        ),
        "MUSIC_PROVIDER": (music_provider_key, {"licensed_catalog"}),
        "STOCK_VIDEO_PROVIDER": (
            stock_video_provider_key,
            {"pexels", "pixabay", "pexels_pixabay"},
        ),
    }
    for name, (configured, supported) in supported_providers.items():
        if configured not in supported:
            raise RuntimeError(
                f"{name} has unsupported production value {configured!r}; "
                f"expected one of {', '.join(sorted(supported))}"
            )

    if settings.allow_fixture_providers:
        raise RuntimeError("ALLOW_FIXTURE_PROVIDERS must be false in production")
    if not getattr(settings, "render_execute_ffmpeg", True):
        # Without FFmpeg, renders write a short black test clip (fixtures/registry.json).
        raise RuntimeError("RENDER_EXECUTE_FFMPEG must be true in production")
    if settings.stem_separation_provider != "demucs":
        raise RuntimeError("STEM_SEPARATION_PROVIDER must be demucs in production")
    explicit_gateway_routes = bool(str(getattr(settings, "model_gateway_routes", "") or "").strip())
    if not explicit_gateway_routes and not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is required for the legacy transcription route")
    if not explicit_gateway_routes and not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for the legacy planning route")
    try:
        production_gateway = model_gateway_from_settings(settings)
    except ValueError as exc:
        raise RuntimeError(f"Invalid production model gateway configuration: {exc}") from exc
    if production_gateway is None:
        raise RuntimeError("MODEL_GATEWAY_ENABLED must be true in production")
    uncosted_external_routes = [
        route.route_id
        for route in production_gateway.routes
        if route.adapter in {"azure", "openai_compatible", "openrouter", "groq"}
        and route.input_cost_per_million_usd == 0
        and route.output_cost_per_million_usd == 0
        and route.fixed_cost_usd == 0
        and not route.explicitly_free
    ]
    if uncosted_external_routes:
        raise RuntimeError(
            "Production external model routes require explicit pricing or "
            f"explicitly_free=true: {', '.join(sorted(uncosted_external_routes))}"
        )
    if any(
        route.capability == ModelCapability.PLANNING and route.explicitly_free
        for route in production_gateway.routes
    ):
        raise RuntimeError("Production planning routes cannot be explicitly free")
    configured_operations = {
        operation for route in production_gateway.routes for operation in route.operations
    }
    required_configured = {
        ModelCapability.PLANNING: ModelOperation.PLANNING_CHAT,
        ModelCapability.TRANSCRIPTION: ModelOperation.TRANSCRIPTION_SPEECH_TO_TEXT,
    }
    missing_capabilities = [
        capability
        for capability, operation in required_configured.items()
        if operation not in configured_operations
    ]
    if missing_capabilities:
        names = ", ".join(sorted(item.value for item in missing_capabilities))
        raise RuntimeError(f"Production model gateway is missing capabilities: {names}")
    if audio_ml_provider_key in {"disabled", "none", "off"}:
        raise RuntimeError("Production AUDIO_ML_PROVIDER must enable audio intelligence")
    if not settings.runway_api_key:
        raise RuntimeError("RUNWAY_API_KEY is required for generation capability routing")
    if settings.runway_estimated_cost_per_request_usd <= 0:
        raise RuntimeError("RUNWAY_ESTIMATED_COST_PER_REQUEST_USD must be positive in production")
    pinned_revisions = {
        "PYANNOTE_MODEL_REVISION": settings.pyannote_model_revision,
        "WAV2VEC_EMOTION_MODEL_REVISION": settings.wav2vec_emotion_model_revision,
        "VISUAL_CLIP_MODEL_REVISION": settings.visual_clip_model_revision,
        "VISUAL_BLIP_MODEL_REVISION": settings.visual_blip_model_revision,
        "VISUAL_YOLO_MODEL_REVISION": settings.visual_yolo_model_revision,
        "DEMUCS_MODEL_REVISION": settings.demucs_model_revision,
        "RUNWAY_MODEL_REVISION": settings.runway_model_revision,
    }
    for name, revision in pinned_revisions.items():
        if not is_immutable_model_revision(str(revision)):
            raise RuntimeError(f"{name} must be an immutable model revision")
    if settings.edit_planner_model == "openrouter/free" or settings.edit_planner_model.endswith(
        ":free"
    ):
        raise RuntimeError("Production planning requires a pinned non-aggregator model")
    if music_provider_key == "licensed_catalog" and (
        not settings.supabase_url or not settings.supabase_service_role_key
    ):
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for licensed music catalog"
        )
    if stock_video_provider_key in {"pexels", "pexels_pixabay"} and not settings.pexels_api_key:
        raise RuntimeError("PEXELS_API_KEY is required when STOCK_VIDEO_PROVIDER includes pexels")
    if stock_video_provider_key in {"pixabay", "pexels_pixabay"} and not settings.pixabay_api_key:
        raise RuntimeError("PIXABAY_API_KEY is required when STOCK_VIDEO_PROVIDER includes pixabay")

    if settings.process_role != "api":
        required_worker = {
            "SUPABASE_URL": settings.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
            "REDIS_URL": settings.redis_url,
            "WORKER_API_TOKEN": settings.worker_api_token,
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
        "WORKER_API_TOKEN": settings.worker_api_token,
        "METRICS_BEARER_TOKEN": settings.metrics_bearer_token,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing production environment variables: {', '.join(missing)}")

    if not settings.cors_origin_list:
        raise RuntimeError("API_CORS_ORIGINS is required in production")
    _reject_local_url("API_PUBLIC_BASE_URL", str(settings.api_public_base_url))
    for origin in settings.cors_origin_list:
        _reject_local_url("API_CORS_ORIGINS", origin)

    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required in production for BullMQ queueing")

    if settings.billing_enabled and (
        not settings.stripe_secret_key or not settings.stripe_webhook_secret
    ):
        raise RuntimeError("Stripe settings are required when BILLING_ENABLED=true")

    if settings.force_local_media_storage:
        raise RuntimeError("FORCE_LOCAL_MEDIA_STORAGE must be false in production")


def _reject_local_url(name: str, value: str) -> None:
    lowered = value.lower()
    if any(host in lowered for host in ("localhost", "127.0.0.1", "0.0.0.0", ".invalid")):
        raise RuntimeError(f"{name} must be a real deployed HTTPS URL in production")


def _require_public_supabase_url(settings: object) -> None:
    value = getattr(settings, "supabase_public_url", None)
    if not value:
        raise RuntimeError(
            "SUPABASE_PUBLIC_URL is required in production (Supabase as browsers reach it)"
        )
    if not str(value).lower().startswith("https://"):
        raise RuntimeError("SUPABASE_PUBLIC_URL must be a real deployed HTTPS URL in production")
    _reject_local_url("SUPABASE_PUBLIC_URL", str(value))
