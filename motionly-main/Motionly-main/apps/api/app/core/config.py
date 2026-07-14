import os
from functools import lru_cache
from pathlib import Path

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
    process_role: str = Field(default="api", alias="PROCESS_ROLE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    api_cors_origins: str = Field(default="", alias="API_CORS_ORIGINS")
    api_public_base_url: AnyHttpUrl = Field(
        default="https://api.motionly.invalid",
        alias="API_PUBLIC_BASE_URL",
    )
    supabase_url: AnyHttpUrl | None = Field(default=None, alias="SUPABASE_URL")
    supabase_jwt_secret: str | None = Field(default=None, alias="SUPABASE_JWT_SECRET")
    supabase_service_role_key: str | None = Field(default=None, alias="SUPABASE_SERVICE_ROLE_KEY")
    upload_storage_bucket: str = Field(default="raw-uploads", alias="UPLOAD_STORAGE_BUCKET")
    upload_max_file_size_bytes: int = Field(default=536_870_912, alias="UPLOAD_MAX_FILE_SIZE_BYTES")
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
    retention_generated_asset_days: int = Field(
        default=90, alias="RETENTION_GENERATED_ASSET_DAYS"
    )
    retention_transcript_days: int = Field(default=365, alias="RETENTION_TRANSCRIPT_DAYS")
    retention_analytics_days: int = Field(default=365, alias="RETENTION_ANALYTICS_DAYS")
    retention_export_days: int = Field(default=90, alias="RETENTION_EXPORT_DAYS")
    retention_local_cache_days: int = Field(default=7, alias="RETENTION_LOCAL_CACHE_DAYS")
    media_cache_max_bytes: int = Field(default=10_737_418_240, alias="MEDIA_CACHE_MAX_BYTES")
    readiness_timeout_seconds: float = Field(default=3.0, alias="READINESS_TIMEOUT_SECONDS")
    worker_heartbeat_max_age_seconds: int = Field(
        default=90, alias="WORKER_HEARTBEAT_MAX_AGE_SECONDS"
    )
    metrics_bearer_token: str | None = Field(default=None, alias="METRICS_BEARER_TOKEN")
    render_execute_ffmpeg: bool = Field(default=True, alias="RENDER_EXECUTE_FFMPEG")
    ffmpeg_gpu_enabled: bool = Field(default=False, alias="FFMPEG_GPU_ENABLED")
    ffmpeg_gpu_encoder: str = Field(default="h264_nvenc", alias="FFMPEG_GPU_ENCODER")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
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
    lightning_user_id: str | None = Field(default=None, alias="LIGHTNING_USER_ID")
    lightning_api_key: str | None = Field(default=None, alias="LIGHTNING_API_KEY")
    lightning_teamspace: str | None = Field(default=None, alias="LIGHTNING_TEAMSPACE")
    lightning_visual_deployment: str | None = Field(
        default=None,
        alias="LIGHTNING_VISUAL_DEPLOYMENT",
    )
    lightning_matte_deployment: str | None = Field(
        default=None,
        alias="LIGHTNING_MATTE_DEPLOYMENT",
    )
    lightning_render_deployment: str | None = Field(
        default=None,
        alias="LIGHTNING_RENDER_DEPLOYMENT",
    )
    lightning_scale_url_template: str = Field(
        default=(
            "https://lightning.ai/api/v1/teamspaces/"
            "{teamspace}/deployments/{deployment}"
        ),
        alias="LIGHTNING_SCALE_URL_TEMPLATE",
    )
    lightning_max_replicas: int | None = Field(
        default=None,
        alias="LIGHTNING_MAX_REPLICAS",
    )
    gpu_oom_alert_threshold: int = Field(default=2, alias="GPU_OOM_ALERT_THRESHOLD")
    gpu_long_job_timeout_seconds: int = Field(
        default=1800,
        alias="GPU_LONG_JOB_TIMEOUT_SECONDS",
    )
    rate_limit_per_minute: int = Field(default=120, alias="RATE_LIMIT_PER_MINUTE")
    publish_token_encryption_key: str | None = Field(
        default=None, alias="PUBLISH_TOKEN_ENCRYPTION_KEY"
    )
    admin_api_token: str | None = Field(default=None, alias="ADMIN_API_TOKEN")
    worker_api_token: str | None = Field(default=None, alias="WORKER_API_TOKEN")
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
    pyannote_auth_token: str | None = Field(default=None, alias="PYANNOTE_AUTH_TOKEN")
    wav2vec_emotion_model: str = Field(
        default="superb/wav2vec2-base-superb-er",
        alias="WAV2VEC_EMOTION_MODEL",
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
    visual_blip_model: str = Field(
        default="Salesforce/blip2-opt-2.7b",
        alias="VISUAL_BLIP_MODEL",
    )
    visual_yolo_model: str = Field(default="yolov8n.pt", alias="VISUAL_YOLO_MODEL")
    visual_sample_fps: float = Field(default=2.0, alias="VISUAL_SAMPLE_FPS")
    visual_scene_threshold: float = Field(default=0.42, alias="VISUAL_SCENE_THRESHOLD")
    allow_fixture_providers: bool = Field(default=False, alias="ALLOW_FIXTURE_PROVIDERS")
    music_provider: str = Field(default="licensed_catalog", alias="MUSIC_PROVIDER")
    licensed_music_catalog_table: str = Field(
        default="licensed_music_tracks",
        alias="LICENSED_MUSIC_CATALOG_TABLE",
    )
    edit_planner_provider: str = Field(default="openrouter", alias="EDIT_PLANNER_PROVIDER")
    edit_planner_model: str = Field(default="openrouter/free", alias="EDIT_PLANNER_MODEL")
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
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
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    remotion_enabled: bool = Field(default=False, alias="REMOTION_ENABLED")
    publish_sync_completion: bool = Field(default=False, alias="PUBLISH_SYNC_COMPLETION")
    publish_oauth_state_secret: str | None = Field(default=None, alias="PUBLISH_OAUTH_STATE_SECRET")
    publish_token_refresh_buffer_seconds: int = Field(
        default=300,
        alias="PUBLISH_TOKEN_REFRESH_BUFFER_SECONDS",
    )
    publish_retry_backoff_seconds: int = Field(
        default=60,
        alias="PUBLISH_RETRY_BACKOFF_SECONDS",
    )
    publish_retry_backoff_cap_seconds: int = Field(
        default=900,
        alias="PUBLISH_RETRY_BACKOFF_CAP_SECONDS",
    )
    publish_retry_max_attempts: int = Field(default=3, alias="PUBLISH_RETRY_MAX_ATTEMPTS")
    tiktok_client_key: str | None = Field(default=None, alias="TIKTOK_CLIENT_KEY")
    tiktok_client_secret: str | None = Field(default=None, alias="TIKTOK_CLIENT_SECRET")
    meta_app_id: str | None = Field(default=None, alias="META_APP_ID")
    meta_app_secret: str | None = Field(default=None, alias="META_APP_SECRET")
    meta_graph_api_version: str = Field(default="v22.0", alias="META_GRAPH_API_VERSION")
    google_client_id: str | None = Field(default=None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: str | None = Field(default=None, alias="GOOGLE_CLIENT_SECRET")
    stem_separation_provider: str = Field(default="demucs", alias="STEM_SEPARATION_PROVIDER")
    demucs_model: str = Field(default="htdemucs", alias="DEMUCS_MODEL")
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

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        skip_api_auth = os.getenv("SKIP_API_AUTH")
        if env_flag_enabled(skip_api_auth) and self.app_env not in LOCAL_AUTH_BYPASS_ENVS:
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
            if self.publish_sync_completion:
                raise ValueError(
                    "Security violation: PUBLISH_SYNC_COMPLETION cannot be enabled in "
                    "production environment."
                )
            if self.force_local_media_storage:
                raise ValueError(
                    "Security violation: FORCE_LOCAL_MEDIA_STORAGE cannot be enabled in "
                    "production environment."
                )
        return self

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
        "PUBLISH_TOKEN_ENCRYPTION_KEY",
        "PUBLISH_OAUTH_STATE_SECRET",
        "REDIS_URL",
        "METRICS_BEARER_TOKEN",
        "LIGHTNING_API_KEY",
    ):
        secret_file = os.getenv(f"{name}_FILE")
        if secret_file and not os.getenv(name):
            os.environ[name] = Path(secret_file).read_text(encoding="utf-8").strip()
    
    env_file = os.getenv("SETTINGS_ENV_FILE", ".env")
    if env_file == "none":
        return Settings(_env_file=None)
    return Settings(_env_file=env_file)
