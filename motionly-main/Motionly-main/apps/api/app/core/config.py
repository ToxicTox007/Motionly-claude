from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: str = Field(default="production", alias="APP_ENV")
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
    upload_sync_processing: bool = Field(default=False, alias="UPLOAD_SYNC_PROCESSING")
    render_sync_completion: bool = Field(default=False, alias="RENDER_SYNC_COMPLETION")
    render_execute_ffmpeg: bool = Field(default=True, alias="RENDER_EXECUTE_FFMPEG")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    bullmq_bridge_path: str | None = Field(default=None, alias="BULLMQ_BRIDGE_PATH")
    bullmq_node_command: str = Field(default="node", alias="BULLMQ_NODE_COMMAND")
    job_attempts: int = Field(default=3, alias="JOB_ATTEMPTS")
    job_backoff_ms: int = Field(default=30000, alias="JOB_BACKOFF_MS")
    worker_concurrency: int = Field(default=3, alias="WORKER_CONCURRENCY")
    render_worker_concurrency: int = Field(default=1, alias="RENDER_WORKER_CONCURRENCY")
    rate_limit_per_minute: int = Field(default=120, alias="RATE_LIMIT_PER_MINUTE")
    admin_api_token: str | None = Field(default=None, alias="ADMIN_API_TOKEN")
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
    personal_use_mode: bool = Field(default=True, alias="PERSONAL_USE_MODE")
    billing_success_url: AnyHttpUrl | None = Field(default=None, alias="BILLING_SUCCESS_URL")
    billing_cancel_url: AnyHttpUrl | None = Field(default=None, alias="BILLING_CANCEL_URL")
    billing_portal_return_url: AnyHttpUrl | None = Field(
        default=None,
        alias="BILLING_PORTAL_RETURN_URL",
    )
    sentry_dsn: str | None = Field(default=None, alias="SENTRY_DSN")
    remotion_enabled: bool = Field(default=False, alias="REMOTION_ENABLED")

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.api_cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def should_complete_renders_synchronously(self) -> bool:
        return self.render_sync_completion

    @property
    def should_process_uploads_synchronously(self) -> bool:
        return self.upload_sync_processing

    @property
    def admin_workspace_id_list(self) -> set[str]:
        return {
            workspace_id.strip().lower()
            for workspace_id in self.admin_workspace_ids.split(",")
            if workspace_id.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
