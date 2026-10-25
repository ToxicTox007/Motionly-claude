from __future__ import annotations

import re

from app.services.ai_features import gpu_worker_switches
from app.services.inference_runtime import build_inference_runtime
from app.services.inference_validation import InferenceStartupError

ORCHESTRATION_PROCESS_ROLES = {
    "temporal-dispatcher",
    "temporal-worker",
    "workflow-reconciler",
}

# Deployment-level billing posture. Live/Stripe billing is being removed, so the
# only production-allowed values are a fully disabled surface or an explicitly
# labelled no-charge demo surface. Production must state one explicitly.
BILLING_MODES = ("disabled", "demo")


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

    visual_provider_key = settings.visual_intelligence_provider.strip().lower()
    audio_ml_provider_key = settings.audio_ml_provider.strip().lower()
    music_provider_key = settings.music_provider.strip().lower()
    stock_video_provider_key = settings.stock_video_provider.strip().lower().replace(",", "_")

    supported_providers = {
        "VISUAL_PROVIDER": (visual_provider_key, {"local_ml", "remote"}),
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
    _validate_stem_separation_provider(settings)
    _validate_vision_providers(settings)
    _validate_gpu_free_deployment(settings)
    # Inference routes are validated by capability, not by vendor key or provider
    # value (plan Task 7, Section 2.6). ``build_inference_runtime`` loads the
    # declarative registry, applies every Section 2.6 rule and runs the startup
    # decision (``validate_inference_startup``): a required capability
    # (planning.chat, transcription.speech_to_text) without a valid enabled route
    # fails startup; every other capability is optional. A ``provider-managed``
    # revision, unknown pricing, missing remote licence, etc. are warnings logged
    # once at build time. The vendor key checks, provider-value checks, the
    # video-generation requirement, the vendor pricing checks and the vendor
    # revision maps are gone; config.py keeps the vendor SETTINGS (Task 26
    # removes them).
    try:
        build_inference_runtime(settings)
    except InferenceStartupError:
        raise
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f"Invalid production inference registry: {exc}") from exc
    if audio_ml_provider_key in {"disabled", "none", "off"}:
        raise RuntimeError("Production AUDIO_ML_PROVIDER must enable audio intelligence")
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

    if settings.billing_enabled:
        mode = str(getattr(settings, "billing_mode", "") or "").strip().lower()
        if mode == "demo" and (
            not getattr(settings, "mock_gateway_base_url", None)
            or not getattr(settings, "merchant_api_key", None)
            or not getattr(settings, "mock_gateway_webhook_secret", None)
        ):
            raise RuntimeError(
                "MOCK_GATEWAY_BASE_URL, MERCHANT_API_KEY and "
                "MOCK_GATEWAY_WEBHOOK_SECRET are required when BILLING_ENABLED=true "
                "and BILLING_MODE=demo"
            )

    _require_billing_mode(settings)

    if settings.force_local_media_storage:
        raise RuntimeError("FORCE_LOCAL_MEDIA_STORAGE must be false in production")


def _validate_vision_providers(settings: object) -> None:
    """GPU-free matte and visual analysis: pinned CPU models and priced remote routes."""
    matte = str(getattr(settings, "matte_provider", "local_ml") or "").strip().lower()
    if matte == "local_cpu" and not _is_sha256(getattr(settings, "matte_cpu_model_sha256", "")):
        raise RuntimeError("MATTE_CPU_MODEL_SHA256 must be a SHA-256 hex digest in production")
    visual = str(getattr(settings, "visual_intelligence_provider", "") or "").strip().lower()
    if visual != "remote":
        return
    if not _is_sha256(getattr(settings, "visual_cpu_face_model_sha256", "")):
        raise RuntimeError(
            "VISUAL_CPU_FACE_MODEL_SHA256 must be a SHA-256 hex digest in production"
        )
    # Keyframe object detection is enabled by a ``vision.object_detection``
    # registry route (plan Task 17), validated by the declarative registry
    # validator (licence, pricing, immutable revision — Section 2.6); the former
    # vendor object-detection settings and their production check are removed
    # (plan Task 19 item 2).
    # Keyframe captions are rebuilt as a registry capability route in Tier 2
    # (plan Task 12); the former vendor caption settings and their production
    # check are removed in Task 26. The declarative registry validator enforces
    # the route's licence, pricing and immutable revision (Section 2.6).
    # Keyframe and B-roll embeddings are rebuilt as registry capability routes in
    # Tier 2 (plan Task 12); the vendor embeddings settings and their production
    # check are removed in Task 26. The declarative registry validator enforces
    # the route's licence, pricing and immutable revision (Section 2.6).


def _is_sha256(value: object) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "").strip().lower()))


def _validate_gpu_free_deployment(settings: object) -> None:
    """GPU_WORKERS_ENABLED=false: no feature may wait on a GPU worker that never runs."""
    if bool(getattr(settings, "gpu_workers_enabled", True)):
        return
    offenders = gpu_worker_switches(settings)
    if offenders:
        raise RuntimeError(
            "GPU_WORKERS_ENABLED=false needs a CPU or remote provider for every feature; "
            "these still need a GPU worker: " + ", ".join(offenders)
        )


def _validate_stem_separation_provider(settings: object) -> None:
    # Local Demucs is the only code-bound stem provider; a remote provider is
    # configured through a ``audio.stem_separation`` registry route (plan Task
    # 17), validated by the declarative registry validator. The former vendor
    # provider switch and its production check are removed (plan Task 19 item 2).
    provider = str(getattr(settings, "stem_separation_provider", "")).strip().lower()
    if provider not in {"demucs", ""}:
        raise RuntimeError("STEM_SEPARATION_PROVIDER must be demucs in production")


def _require_billing_mode(settings: object) -> None:
    value = str(getattr(settings, "billing_mode", "") or "").strip().lower()
    if not value:
        raise RuntimeError(
            "BILLING_MODE is required in production; set it to one of "
            f"{', '.join(BILLING_MODES)}"
        )
    if value not in BILLING_MODES:
        raise RuntimeError(
            f"BILLING_MODE has unsupported production value {value!r}; "
            f"expected one of {', '.join(BILLING_MODES)}"
        )


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
