import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_AUTH_BYPASS_ENVS = {"development", "local"}


def env_flag_enabled(value: str | None) -> bool:
    return bool(value and value.lower() in ("true", "1", "yes", "on"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: str = Field(default="production", alias="APP_ENV")
    skip_api_auth: bool = Field(default=False, alias="SKIP_API_AUTH")
    process_role: str = Field(default="api", alias="PROCESS_ROLE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    api_cors_origins: str = Field(default="", alias="API_CORS_ORIGINS")
    api_public_base_url: AnyHttpUrl = Field(
        default="https://api.motionly.invalid",
        alias="API_PUBLIC_BASE_URL",
    )
    supabase_url: AnyHttpUrl | None = Field(default=None, alias="SUPABASE_URL")
    # Supabase as browsers reach it (``<public origin>/supabase``). Signed links
    # handed to browsers use it; the API and workers call SUPABASE_URL.
    supabase_public_url: AnyHttpUrl | None = Field(default=None, alias="SUPABASE_PUBLIC_URL")
    supabase_jwt_secret: str | None = Field(default=None, alias="SUPABASE_JWT_SECRET")
    supabase_service_role_key: str | None = Field(default=None, alias="SUPABASE_SERVICE_ROLE_KEY")
    upload_storage_bucket: str = Field(default="raw-uploads", alias="UPLOAD_STORAGE_BUCKET")
    upload_max_file_size_bytes: int = Field(
        default=536_870_912,
        gt=0,
        le=536_870_912,
        alias="UPLOAD_MAX_FILE_SIZE_BYTES",
    )
    upload_max_concurrent_per_workspace: int = Field(
        default=3,
        alias="UPLOAD_MAX_CONCURRENT_PER_WORKSPACE",
    )
    upload_signed_url_ttl_seconds: int = Field(default=900, alias="UPLOAD_SIGNED_URL_TTL_SECONDS")
    export_storage_bucket: str = Field(default="exports", alias="EXPORT_STORAGE_BUCKET")
    export_signed_url_ttl_seconds: int = Field(default=300, alias="EXPORT_SIGNED_URL_TTL_SECONDS")
    media_work_dir: str = Field(default="/var/data/motionly/storage", alias="MEDIA_WORK_DIR")
    force_local_media_storage: bool = Field(default=False, alias="FORCE_LOCAL_MEDIA_STORAGE")
    retention_source_days: int = Field(default=365, alias="RETENTION_SOURCE_DAYS")
    retention_generated_asset_days: int = Field(default=90, alias="RETENTION_GENERATED_ASSET_DAYS")
    retention_transcript_days: int = Field(default=365, alias="RETENTION_TRANSCRIPT_DAYS")
    retention_analytics_days: int = Field(default=365, alias="RETENTION_ANALYTICS_DAYS")
    editorial_learning_enabled: bool = Field(default=False, alias="EDITORIAL_LEARNING_ENABLED")
    editorial_learning_policy_version: str = Field(
        default="editorial-consent/v1",
        min_length=1,
        max_length=80,
        alias="EDITORIAL_LEARNING_POLICY_VERSION",
    )
    editorial_learning_retention_days: int = Field(
        default=90, ge=1, le=365, alias="EDITORIAL_LEARNING_RETENTION_DAYS"
    )
    editorial_learning_minimum_examples: int = Field(
        default=100, ge=1, le=10_000, alias="EDITORIAL_LEARNING_MINIMUM_EXAMPLES"
    )
    editorial_learning_shadow_sample_rate: float = Field(
        default=0, ge=0, le=1, alias="EDITORIAL_LEARNING_SHADOW_SAMPLE_RATE"
    )
    retention_export_days: int = Field(default=90, alias="RETENTION_EXPORT_DAYS")
    retention_local_cache_days: int = Field(default=7, alias="RETENTION_LOCAL_CACHE_DAYS")
    media_cache_max_bytes: int = Field(default=10_737_418_240, alias="MEDIA_CACHE_MAX_BYTES")
    readiness_timeout_seconds: float = Field(default=3.0, alias="READINESS_TIMEOUT_SECONDS")
    health_diagnostics_cache_ttl_seconds: int = Field(
        default=30,
        ge=0,
        le=300,
        alias="HEALTH_DIAGNOSTICS_CACHE_TTL_SECONDS",
    )
    health_diagnostics_rate_limit_per_minute: int = Field(
        default=10,
        ge=1,
        le=120,
        alias="HEALTH_DIAGNOSTICS_RATE_LIMIT_PER_MINUTE",
    )
    worker_heartbeat_max_age_seconds: int = Field(
        default=90, alias="WORKER_HEARTBEAT_MAX_AGE_SECONDS"
    )
    metrics_bearer_token: str | None = Field(default=None, alias="METRICS_BEARER_TOKEN")
    render_execute_ffmpeg: bool = Field(default=True, alias="RENDER_EXECUTE_FFMPEG")
    ffmpeg_gpu_enabled: bool = Field(default=False, alias="FFMPEG_GPU_ENABLED")
    # gpu: GPU-dependent capabilities run on the GPU-stage workers.
    # test_fixture: CPU-stage only; CPU workers serve them with labelled
    # deterministic fixtures. Production refuses test_fixture.
    ml_execution_mode: Literal["gpu", "test_fixture"] = Field(
        default="gpu", alias="ML_EXECUTION_MODE"
    )
    ffmpeg_gpu_encoder: str = Field(default="h264_nvenc", alias="FFMPEG_GPU_ENCODER")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    temporal_address: str | None = Field(default=None, alias="TEMPORAL_ADDRESS")
    temporal_deployment_mode: Literal["cloud", "self_hosted"] = Field(
        default="cloud", alias="TEMPORAL_DEPLOYMENT_MODE"
    )
    temporal_namespace: str | None = Field(default=None, alias="TEMPORAL_NAMESPACE")
    temporal_api_key: str | None = Field(default=None, alias="TEMPORAL_API_KEY")
    temporal_task_queue: str | None = Field(default=None, alias="TEMPORAL_TASK_QUEUE")
    temporal_tls_enabled: bool = Field(default=True, alias="TEMPORAL_TLS_ENABLED")
    temporal_server_root_ca_path: str | None = Field(
        default=None, alias="TEMPORAL_SERVER_ROOT_CA_PATH"
    )
    temporal_client_cert_path: str | None = Field(default=None, alias="TEMPORAL_CLIENT_CERT_PATH")
    temporal_client_private_key_path: str | None = Field(
        default=None, alias="TEMPORAL_CLIENT_PRIVATE_KEY_PATH"
    )
    temporal_server_name: str | None = Field(default=None, alias="TEMPORAL_SERVER_NAME")
    temporal_client_role: str | None = Field(default=None, alias="TEMPORAL_CLIENT_ROLE")
    temporal_identity: str | None = Field(default=None, alias="TEMPORAL_IDENTITY")
    temporal_dispatcher_id: str | None = Field(default=None, alias="TEMPORAL_DISPATCHER_ID")
    temporal_max_concurrent_activities: int = Field(
        default=100,
        ge=1,
        le=1000,
        alias="TEMPORAL_MAX_CONCURRENT_ACTIVITIES",
    )
    temporal_max_concurrent_workflow_tasks: int = Field(
        default=100,
        ge=1,
        le=1000,
        alias="TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS",
    )
    workflow_dispatcher_batch_size: int = Field(
        default=25,
        ge=1,
        le=100,
        alias="WORKFLOW_DISPATCHER_BATCH_SIZE",
    )
    workflow_dispatcher_lease_seconds: int = Field(
        default=60,
        ge=5,
        le=900,
        alias="WORKFLOW_DISPATCHER_LEASE_SECONDS",
    )
    workflow_dispatcher_poll_interval_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        alias="WORKFLOW_DISPATCHER_POLL_INTERVAL_SECONDS",
    )
    workflow_dispatcher_backoff_initial_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=300.0,
        alias="WORKFLOW_DISPATCHER_BACKOFF_INITIAL_SECONDS",
    )
    workflow_dispatcher_backoff_max_seconds: float = Field(
        default=60.0,
        ge=1.0,
        le=3600.0,
        alias="WORKFLOW_DISPATCHER_BACKOFF_MAX_SECONDS",
    )
    workflow_reconciler_interval_seconds: int = Field(
        default=30,
        ge=5,
        le=3600,
        alias="WORKFLOW_RECONCILER_INTERVAL_SECONDS",
    )
    workflow_reconciler_staleness_seconds: int = Field(
        default=300,
        ge=30,
        le=86_400,
        alias="WORKFLOW_RECONCILER_STALENESS_SECONDS",
    )
    workflow_reconciler_batch_size: int = Field(
        default=50,
        ge=1,
        le=100,
        alias="WORKFLOW_RECONCILER_BATCH_SIZE",
    )
    workflow_step_lease_seconds: int = Field(
        default=3600,
        ge=5,
        le=3600,
        alias="WORKFLOW_STEP_LEASE_SECONDS",
    )
    workflow_step_result_max_bytes: int = Field(
        default=262_144,
        ge=1024,
        le=1_048_576,
        alias="WORKFLOW_STEP_RESULT_MAX_BYTES",
    )
    bullmq_bridge_path: str | None = Field(default=None, alias="BULLMQ_BRIDGE_PATH")
    bullmq_node_command: str = Field(default="node", alias="BULLMQ_NODE_COMMAND")
    bullmq_bridge_timeout_seconds: float = Field(
        default=10.0,
        alias="BULLMQ_BRIDGE_TIMEOUT_SECONDS",
    )
    job_attempts: int = Field(default=3, alias="JOB_ATTEMPTS")
    job_backoff_ms: int = Field(default=30000, alias="JOB_BACKOFF_MS")
    worker_concurrency: int = Field(default=3, alias="WORKER_CONCURRENCY")
    render_worker_concurrency: int = Field(default=1, alias="RENDER_WORKER_CONCURRENCY")
    gpu_worker_instance_type: str = Field(default="g5.xlarge", alias="GPU_WORKER_INSTANCE_TYPE")
    gpu_max_concurrent_jobs_per_gpu: int = Field(
        default=1,
        alias="GPU_MAX_CONCURRENT_JOBS_PER_GPU",
    )
    gpu_memory_ceiling_mb: int = Field(default=16_000, alias="GPU_MEMORY_CEILING_MB")
    matte_worker_concurrency: int = Field(default=1, alias="MATTE_WORKER_CONCURRENCY")
    visual_worker_concurrency: int = Field(default=1, alias="VISUAL_ANALYSIS_WORKER_CONCURRENCY")
    gpu_scale_up_queue_depth: int = Field(default=4, alias="GPU_SCALE_UP_QUEUE_DEPTH")
    gpu_scale_down_queue_depth: int = Field(default=1, alias="GPU_SCALE_DOWN_QUEUE_DEPTH")
    gpu_autoscale_cooldown_seconds: int = Field(
        default=300,
        alias="GPU_AUTOSCALE_COOLDOWN_SECONDS",
    )
    gpu_max_worker_count: int = Field(default=4, alias="GPU_MAX_WORKER_COUNT")
    # Static Compose GPU pool capacity. This is the only supported production
    # compute placement; it never calls a remote orchestration API.
    gpu_pool_visual_replicas: int = Field(default=1, alias="GPU_POOL_VISUAL_REPLICAS")
    gpu_pool_matte_replicas: int = Field(default=1, alias="GPU_POOL_MATTE_REPLICAS")
    gpu_pool_render_replicas: int = Field(default=1, alias="GPU_POOL_RENDER_REPLICAS")
    gpu_oom_alert_threshold: int = Field(default=2, alias="GPU_OOM_ALERT_THRESHOLD")
    gpu_long_job_timeout_seconds: int = Field(
        default=1800,
        alias="GPU_LONG_JOB_TIMEOUT_SECONDS",
    )
    rate_limit_per_minute: int = Field(default=120, alias="RATE_LIMIT_PER_MINUTE")
    admin_api_token: str | None = Field(default=None, alias="ADMIN_API_TOKEN")
    worker_api_token: str | None = Field(default=None, alias="WORKER_API_TOKEN")
    worker_api_base_url: str | None = Field(default=None, alias="WORKER_API_BASE_URL")
    admin_user_ids: str = Field(default="", alias="ADMIN_USER_IDS")
    admin_workspace_ids: str = Field(default="", alias="ADMIN_WORKSPACE_IDS")
    transcription_provider: str = Field(default="groq", alias="TRANSCRIPTION_PROVIDER")
    transcription_model: str = Field(default="whisper-large-v3-turbo", alias="TRANSCRIPTION_MODEL")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    transcription_debug_retain_audio: bool = Field(
        default=False,
        alias="TRANSCRIPTION_DEBUG_RETAIN_AUDIO",
    )
    audio_ml_provider: str = Field(default="local_ml", alias="AUDIO_ML_PROVIDER")
    pyannote_model: str = Field(
        default="pyannote/speaker-diarization-3.1",
        alias="PYANNOTE_MODEL",
    )
    pyannote_model_revision: str = Field(
        default="84fd25912480287da0247647c3d2b4853cb3ee5d",
        alias="PYANNOTE_MODEL_REVISION",
    )
    pyannote_auth_token: str | None = Field(default=None, alias="PYANNOTE_AUTH_TOKEN")
    wav2vec_emotion_model: str = Field(
        default="superb/wav2vec2-base-superb-er",
        alias="WAV2VEC_EMOTION_MODEL",
    )
    wav2vec_emotion_model_revision: str = Field(
        default="441a7599c3b22107314dcbd9166621c5c83f2cc5",
        alias="WAV2VEC_EMOTION_MODEL_REVISION",
    )
    yamnet_model_url: str = Field(
        default="https://tfhub.dev/google/yamnet/1",
        alias="YAMNET_MODEL_URL",
    )
    visual_intelligence_provider: str = Field(default="local_ml", alias="VISUAL_PROVIDER")
    visual_clip_model: str = Field(
        default="openai/clip-vit-base-patch32",
        alias="VISUAL_CLIP_MODEL",
    )
    visual_clip_model_revision: str = Field(
        default="3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
        alias="VISUAL_CLIP_MODEL_REVISION",
    )
    visual_blip_model: str = Field(
        default="Salesforce/blip2-opt-2.7b",
        alias="VISUAL_BLIP_MODEL",
    )
    visual_blip_model_revision: str = Field(
        default="59a1ef6c1e5117b3f65523d1c6066825bcf315e3",
        alias="VISUAL_BLIP_MODEL_REVISION",
    )
    visual_yolo_model: str = Field(
        default="https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt",
        alias="VISUAL_YOLO_MODEL",
    )
    visual_yolo_model_revision: str = Field(
        default="ultralytics-assets-v8.3.0",
        alias="VISUAL_YOLO_MODEL_REVISION",
    )
    visual_sample_fps: float = Field(default=2.0, alias="VISUAL_SAMPLE_FPS")
    visual_scene_threshold: float = Field(default=0.42, alias="VISUAL_SCENE_THRESHOLD")
    allow_fixture_providers: bool = Field(default=False, alias="ALLOW_FIXTURE_PROVIDERS")
    music_provider: str = Field(default="licensed_catalog", alias="MUSIC_PROVIDER")
    licensed_music_catalog_table: str = Field(
        default="licensed_music_tracks",
        alias="LICENSED_MUSIC_CATALOG_TABLE",
    )
    edit_planner_provider: str = Field(default="openrouter", alias="EDIT_PLANNER_PROVIDER")
    edit_planner_model: str = Field(
        default="openai/gpt-4.1-mini",
        alias="EDIT_PLANNER_MODEL",
    )
    edit_planner_model_revision: str = Field(
        default="openai/gpt-4.1-mini-2025-04-14",
        alias="EDIT_PLANNER_MODEL_REVISION",
    )
    edit_planner_input_cost_per_million_usd: float = Field(
        default=2.0, ge=0, alias="EDIT_PLANNER_INPUT_COST_PER_MILLION_USD"
    )
    edit_planner_output_cost_per_million_usd: float = Field(
        default=2.0, ge=0, alias="EDIT_PLANNER_OUTPUT_COST_PER_MILLION_USD"
    )
    transcription_model_revision: str = Field(
        default="whisper-large-v3-turbo-2025-02-01",
        alias="TRANSCRIPTION_MODEL_REVISION",
    )
    transcription_estimated_cost_per_request_usd: float = Field(
        default=0.25, ge=0, alias="TRANSCRIPTION_ESTIMATED_COST_PER_REQUEST_USD"
    )
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    model_gateway_enabled: bool = Field(default=True, alias="MODEL_GATEWAY_ENABLED")
    model_gateway_routes: str = Field(default="", alias="MODEL_GATEWAY_ROUTES")
    model_gateway_monthly_budget_usd: float = Field(
        default=100.0, ge=0, alias="MODEL_GATEWAY_MONTHLY_BUDGET_USD"
    )
    model_gateway_request_budget_usd: float = Field(
        default=5.0, ge=0, alias="MODEL_GATEWAY_REQUEST_BUDGET_USD"
    )
    model_gateway_failure_threshold: int = Field(
        default=3, ge=1, alias="MODEL_GATEWAY_FAILURE_THRESHOLD"
    )
    model_gateway_circuit_cooldown_seconds: int = Field(
        default=60, ge=1, alias="MODEL_GATEWAY_CIRCUIT_COOLDOWN_SECONDS"
    )
    model_gateway_openai_base_url: AnyHttpUrl | None = Field(
        default=None, alias="MODEL_GATEWAY_OPENAI_BASE_URL"
    )
    model_gateway_openai_api_key: str | None = Field(
        default=None, alias="MODEL_GATEWAY_OPENAI_API_KEY"
    )
    azure_openai_endpoint: AnyHttpUrl | None = Field(default=None, alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: str | None = Field(default=None, alias="AZURE_OPENAI_API_KEY")
    azure_openai_api_version: str = Field(
        default="2025-01-01-preview", alias="AZURE_OPENAI_API_VERSION"
    )
    stock_video_provider: str = Field(default="pexels_pixabay", alias="STOCK_VIDEO_PROVIDER")
    pexels_api_key: str | None = Field(default=None, alias="PEXELS_API_KEY")
    pixabay_api_key: str | None = Field(default=None, alias="PIXABAY_API_KEY")
    stripe_secret_key: str | None = Field(default=None, alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str | None = Field(default=None, alias="STRIPE_WEBHOOK_SECRET")
    stripe_price_creator: str | None = Field(default=None, alias="STRIPE_PRICE_CREATOR")
    stripe_price_pro: str | None = Field(default=None, alias="STRIPE_PRICE_PRO")
    stripe_price_agency: str | None = Field(default=None, alias="STRIPE_PRICE_AGENCY")
    billing_enabled: bool = Field(default=False, alias="BILLING_ENABLED")
    billing_success_url: AnyHttpUrl | None = Field(default=None, alias="BILLING_SUCCESS_URL")
    billing_cancel_url: AnyHttpUrl | None = Field(default=None, alias="BILLING_CANCEL_URL")
    billing_portal_return_url: AnyHttpUrl | None = Field(
        default=None,
        alias="BILLING_PORTAL_RETURN_URL",
    )
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    remotion_enabled: bool = Field(default=False, alias="REMOTION_ENABLED")
    stem_separation_provider: str = Field(default="demucs", alias="STEM_SEPARATION_PROVIDER")
    demucs_model: str = Field(default="htdemucs", alias="DEMUCS_MODEL")
    demucs_model_revision: str = Field(default="demucs==4.1.0", alias="DEMUCS_MODEL_REVISION")
    quota_runway_free_limit_seconds: float = Field(
        default=0.0,
        alias="QUOTA_RUNWAY_FREE_LIMIT_SECONDS",
    )
    quota_runway_creator_limit_seconds: float = Field(
        default=60.0,
        alias="QUOTA_RUNWAY_CREATOR_LIMIT_SECONDS",
    )
    quota_runway_pro_limit_seconds: float = Field(
        default=300.0,
        alias="QUOTA_RUNWAY_PRO_LIMIT_SECONDS",
    )
    quota_runway_agency_limit_seconds: float = Field(
        default=1200.0,
        alias="QUOTA_RUNWAY_AGENCY_LIMIT_SECONDS",
    )

    quota_openrouter_free_limit_tokens: float = Field(
        default=100000.0,
        alias="QUOTA_OPENROUTER_FREE_LIMIT_TOKENS",
    )
    quota_openrouter_creator_limit_tokens: float = Field(
        default=500000.0,
        alias="QUOTA_OPENROUTER_CREATOR_LIMIT_TOKENS",
    )
    quota_openrouter_pro_limit_tokens: float = Field(
        default=2000000.0,
        alias="QUOTA_OPENROUTER_PRO_LIMIT_TOKENS",
    )
    quota_openrouter_agency_limit_tokens: float = Field(
        default=10000000.0,
        alias="QUOTA_OPENROUTER_AGENCY_LIMIT_TOKENS",
    )

    max_paid_provider_attempts: int = Field(default=1, alias="MAX_PAID_PROVIDER_ATTEMPTS")

    runway_api_key: str | None = Field(default=None, alias="RUNWAY_API_KEY")
    runway_model: str = Field(default="gen3a_turbo", alias="RUNWAY_MODEL")
    runway_model_revision: str = Field(
        default="gen3a_turbo-2024-11-06",
        alias="RUNWAY_MODEL_REVISION",
    )
    runway_estimated_cost_per_request_usd: float = Field(
        default=0.5,
        gt=0,
        alias="RUNWAY_ESTIMATED_COST_PER_REQUEST_USD",
    )

    # Local-audit controls remain inert unless LOCAL_AUDIT_MODE is explicitly true.
    # The authorization value is loaded only from its mounted *_FILE secret below.
    local_audit_mode: bool = Field(default=False, alias="LOCAL_AUDIT_MODE")
    local_audit_external_effects_enabled: bool = Field(
        default=False,
        alias="LOCAL_AUDIT_EXTERNAL_EFFECTS_ENABLED",
    )
    local_audit_external_authorization: str | None = Field(
        default=None,
        alias="LOCAL_AUDIT_EXTERNAL_AUTHORIZATION",
    )
    local_audit_external_authorization_file: str | None = Field(
        default=None,
        alias="LOCAL_AUDIT_EXTERNAL_AUTHORIZATION_FILE",
    )
    local_audit_target: str = Field(default="", alias="LOCAL_AUDIT_TARGET")
    local_audit_run_id: str = Field(default="", alias="LOCAL_AUDIT_RUN_ID")
    local_audit_provider_accounts: str = Field(
        default="",
        alias="LOCAL_AUDIT_PROVIDER_ACCOUNTS",
    )
    local_audit_approved_domains: str = Field(
        default="",
        alias="LOCAL_AUDIT_APPROVED_DOMAINS",
    )
    local_audit_max_per_run_spend_usd: float | None = Field(
        default=None,
        alias="LOCAL_AUDIT_MAX_PER_RUN_SPEND_USD",
    )
    local_audit_max_total_spend_usd: float | None = Field(
        default=None,
        alias="LOCAL_AUDIT_MAX_TOTAL_SPEND_USD",
    )
    local_audit_remote_cleanup_owner: str = Field(
        default="",
        alias="LOCAL_AUDIT_REMOTE_CLEANUP_OWNER",
    )
    local_audit_remote_cleanup_reference: str = Field(
        default="",
        alias="LOCAL_AUDIT_REMOTE_CLEANUP_REFERENCE",
    )
    local_audit_remote_cleanup_policy: str = Field(
        default="",
        alias="LOCAL_AUDIT_REMOTE_CLEANUP_POLICY",
    )

    @property
    def gpu_stage(self) -> Literal["enabled", "not_enabled"]:
        """Whether GPU-dependent capabilities are real (GPU stage) or fixtures."""
        return "enabled" if self.ml_execution_mode == "gpu" else "not_enabled"

    @property
    def api_auth_bypass_enabled(self) -> bool:
        """Whether the explicitly configured local-only API auth bypass is active."""
        return self.skip_api_auth and self.app_env in LOCAL_AUTH_BYPASS_ENVS

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if (
            self.workflow_dispatcher_backoff_initial_seconds
            > self.workflow_dispatcher_backoff_max_seconds
        ):
            raise ValueError(
                "WORKFLOW_DISPATCHER_BACKOFF_INITIAL_SECONDS cannot exceed "
                "WORKFLOW_DISPATCHER_BACKOFF_MAX_SECONDS."
            )
        if self.workflow_reconciler_interval_seconds > self.workflow_reconciler_staleness_seconds:
            raise ValueError(
                "WORKFLOW_RECONCILER_INTERVAL_SECONDS cannot exceed "
                "WORKFLOW_RECONCILER_STALENESS_SECONDS."
            )
        if self.skip_api_auth and self.app_env not in LOCAL_AUTH_BYPASS_ENVS:
            raise ValueError(
                "Security violation: SKIP_API_AUTH can only be enabled when "
                "APP_ENV is exactly 'development' or 'local'."
            )
        if not self.supabase_jwt_secret and self.app_env not in LOCAL_AUTH_BYPASS_ENVS:
            raise ValueError(
                "Security violation: SUPABASE_JWT_SECRET is required outside local development."
            )
        if (
            self.supabase_jwt_secret
            and self.app_env not in LOCAL_AUTH_BYPASS_ENVS
            and (not self.supabase_url or not self.supabase_service_role_key)
        ):
            raise ValueError(
                "Security violation: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required "
                "outside local development to verify workspace membership."
            )
        if self.app_env == "production":
            if self.force_local_media_storage:
                raise ValueError(
                    "Security violation: FORCE_LOCAL_MEDIA_STORAGE cannot be enabled in "
                    "production environment."
                )
            if self.model_gateway_openai_base_url:
                if not str(self.model_gateway_openai_base_url).startswith("https://"):
                    raise ValueError(
                        "Security violation: MODEL_GATEWAY_OPENAI_BASE_URL must use HTTPS."
                    )
                if not self.model_gateway_openai_api_key:
                    raise ValueError(
                        "Security violation: MODEL_GATEWAY_OPENAI_API_KEY is required in "
                        "production when a custom endpoint is configured."
                    )
            if self.azure_openai_endpoint:
                if not str(self.azure_openai_endpoint).startswith("https://"):
                    raise ValueError("Security violation: AZURE_OPENAI_ENDPOINT must use HTTPS.")
                if not self.azure_openai_api_key:
                    raise ValueError(
                        "Security violation: AZURE_OPENAI_API_KEY is required in production "
                        "when Azure OpenAI is configured."
                    )
        return self

    @property
    def browser_supabase_url(self) -> str | None:
        """Base URL for browser-facing Supabase links; SUPABASE_URL if unset."""
        value = self.supabase_public_url or self.supabase_url
        return str(value).rstrip("/") if value else None

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.api_cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def admin_workspace_id_list(self) -> set[str]:
        return {
            workspace_id.strip().lower()
            for workspace_id in self.admin_workspace_ids.split(",")
            if workspace_id.strip()
        }

    @property
    def admin_user_id_list(self) -> set[str]:
        return {
            user_id.strip().lower() for user_id in self.admin_user_ids.split(",") if user_id.strip()
        }


@lru_cache
def get_settings() -> Settings:
    for name in (
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "ADMIN_API_TOKEN",
        "WORKER_API_TOKEN",
        "REDIS_URL",
        "TEMPORAL_API_KEY",
        "METRICS_BEARER_TOKEN",
        "MODEL_GATEWAY_OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "RUNWAY_API_KEY",
    ):
        secret_file = os.getenv(f"{name}_FILE")
        if secret_file and not os.getenv(name):
            os.environ[name] = Path(secret_file).read_text(encoding="utf-8").strip()

    # The local-audit authorization must come from the mounted file even if a
    # process inherited a similarly named environment variable.
    local_audit_authorization_file = os.getenv("LOCAL_AUDIT_EXTERNAL_AUTHORIZATION_FILE")
    if local_audit_authorization_file:
        os.environ["LOCAL_AUDIT_EXTERNAL_AUTHORIZATION"] = (
            Path(local_audit_authorization_file).read_text(encoding="utf-8").strip()
        )

    env_file = os.getenv("SETTINGS_ENV_FILE", ".env")
    if env_file == "none":
        return Settings(_env_file=None)
    return Settings(_env_file=env_file)
