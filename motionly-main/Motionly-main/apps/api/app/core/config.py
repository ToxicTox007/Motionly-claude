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
    ffmpeg_gpu_enabled: bool = Field(default=False, alias="FFMPEG_GPU_ENABLED")
    # gpu: GPU-dependent capabilities run on the GPU-stage workers.
    # test_fixture: CPU-stage only; CPU workers serve them with labelled
    # deterministic fixtures. Production refuses test_fixture.
    ml_execution_mode: Literal["gpu", "test_fixture"] = Field(
        default="gpu", alias="ML_EXECUTION_MODE"
    )
    # false: a GPU-free deployment (infra/docker-compose.remote-ai.yml). No GPU
    # worker exists, so the GPU capacity observer, its maintenance job and GPU
    # model prefetch are off, and production requires every feature to select a
    # CPU or remote provider. true (default): today's GPU topology.
    gpu_workers_enabled: bool = Field(default=True, alias="GPU_WORKERS_ENABLED")
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
    # POST /public/exports/resolve: attempts per viewer address, and by everyone
    # together, per hour. Every attempt counts, refused ones included, so the
    # limit also bounds share-token probing.
    export_share_resolve_rate_limit_per_hour: int = Field(
        default=120, ge=1, alias="EXPORT_SHARE_RESOLVE_RATE_LIMIT_PER_HOUR"
    )
    export_share_resolve_global_rate_limit_per_hour: int = Field(
        default=6000, ge=1, alias="EXPORT_SHARE_RESOLVE_GLOBAL_RATE_LIMIT_PER_HOUR"
    )
    # POST /feedback (unauthenticated launch feedback form): submissions per
    # viewer address, and by everyone together, per hour.
    feedback_rate_limit_per_hour: int = Field(
        default=10, ge=1, alias="FEEDBACK_RATE_LIMIT_PER_HOUR"
    )
    feedback_global_rate_limit_per_hour: int = Field(
        default=500, ge=1, alias="FEEDBACK_GLOBAL_RATE_LIMIT_PER_HOUR"
    )
    # POST /public/reviews/*: share-link resolves, comments and decisions per
    # viewer address, and by everyone together, per hour. Every attempt
    # counts, refused ones included, so the limit also bounds token probing.
    public_review_rate_limit_per_hour: int = Field(
        default=240, ge=1, alias="PUBLIC_REVIEW_RATE_LIMIT_PER_HOUR"
    )
    public_review_global_rate_limit_per_hour: int = Field(
        default=10000, ge=1, alias="PUBLIC_REVIEW_GLOBAL_RATE_LIMIT_PER_HOUR"
    )
    # POST /auth/signup: attempts per viewer address, and by everyone together,
    # per hour. Every attempt counts, refused ones included.
    signup_rate_limit_per_hour: int = Field(default=5, ge=1, alias="SIGNUP_RATE_LIMIT_PER_HOUR")
    signup_global_rate_limit_per_hour: int = Field(
        default=100, ge=1, alias="SIGNUP_GLOBAL_RATE_LIMIT_PER_HOUR"
    )
    # POST /auth/password-reset-requests: attempts per viewer, and by everyone
    # together, per hour. Every attempt counts, including those for emails with
    # no account, so the limit also bounds enumeration.
    password_reset_request_rate_limit_per_hour: int = Field(
        default=5, ge=1, alias="PASSWORD_RESET_REQUEST_RATE_LIMIT_PER_HOUR"
    )
    password_reset_request_global_rate_limit_per_hour: int = Field(
        default=100, ge=1, alias="PASSWORD_RESET_REQUEST_GLOBAL_RATE_LIMIT_PER_HOUR"
    )
    # The browser-facing origin reset links point at (``<origin>/auth/confirm``).
    public_origin: AnyHttpUrl | None = Field(default=None, alias="PUBLIC_ORIGIN")
    admin_api_token: str | None = Field(default=None, alias="ADMIN_API_TOKEN")
    worker_api_token: str | None = Field(default=None, alias="WORKER_API_TOKEN")
    worker_api_base_url: str | None = Field(default=None, alias="WORKER_API_BASE_URL")
    admin_user_ids: str = Field(default="", alias="ADMIN_USER_IDS")
    admin_workspace_ids: str = Field(default="", alias="ADMIN_WORKSPACE_IDS")
    # The transcription and planning routes (model id, revision, credentials,
    # provider label) come entirely from the declarative registry (plan Task 26).
    # The former vendor provider-switch settings and their auth keys are replaced
    # by registry routes and secret refs; the display label is the gateway
    # route's provider (or a neutral "gateway" label).
    transcription_debug_retain_audio: bool = Field(
        default=False,
        alias="TRANSCRIPTION_DEBUG_RETAIN_AUDIO",
    )
    audio_ml_provider: str = Field(default="local_ml", alias="AUDIO_ML_PROVIDER")
    # Local diarization needs a Hugging Face auth token for the gated pyannote
    # model; the model id and revision come from the registry (plan Task 14).
    pyannote_auth_token: str | None = Field(default=None, alias="PYANNOTE_AUTH_TOKEN")
    # Per-feature audio switches. Empty inherits AUDIO_ML_PROVIDER (local_ml unless
    # it is disabled); an explicit value wins over the group default.
    audio_diarization_provider: Literal["", "local_ml", "disabled"] = Field(
        default="", alias="AUDIO_DIARIZATION_PROVIDER"
    )
    # Voice emotion: local_ml (wav2vec2 on the ML worker) or disabled. The remote
    # voice-emotion path is rebuilt as a registry capability route in Tier 2 (plan
    # Task 12); its model id, revision, cost and windowing come from that route.
    audio_emotion_provider: Literal["", "local_ml", "disabled"] = Field(
        default="", alias="AUDIO_EMOTION_PROVIDER"
    )
    # Sound events: local_ml (TF Hub YAMNet on the ML worker), yamnet_tflite (CPU
    # YAMNet via MediaPipe in the cpu-vision image), or disabled. Empty follows
    # AUDIO_ML_PROVIDER. The local model ids/revisions come from the registry
    # (plan Task 14); only the CPU weights path and SHA stay here (operator/
    # deployment specific, read by the registry's ``local`` route params).
    audio_sound_provider: Literal["", "local_ml", "yamnet_tflite", "disabled"] = Field(
        default="", alias="AUDIO_SOUND_PROVIDER"
    )
    yamnet_tflite_model_path: str = Field(
        default="/opt/motionly/models/yamnet.tflite", alias="YAMNET_TFLITE_MODEL_PATH"
    )
    yamnet_tflite_model_sha256: str = Field(
        default="4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf",
        alias="YAMNET_TFLITE_MODEL_SHA256",
    )
    # Set by the cpu-vision worker image (Dockerfile.python CPU_VISION_ENABLED=true).
    cpu_vision_enabled: bool = Field(default=False, alias="CPU_VISION_ENABLED")
    # Background matte: local_ml (MediaPipe on the GPU/ML worker) or local_cpu
    # (MediaPipe Tasks selfie segmenter on the cpu-vision worker; no GPU).
    matte_provider: Literal["local_ml", "local_cpu"] = Field(
        default="local_ml", alias="MATTE_PROVIDER"
    )
    matte_cpu_model_path: str = Field(
        default="/opt/motionly/models/selfie_segmenter_landscape.tflite",
        alias="MATTE_CPU_MODEL_PATH",
    )
    matte_cpu_model_sha256: str = Field(
        default="490e9ea734313e0de10fa0cd9e3c6133e36ea4db2b7a49bde9ef019f72796b8e",
        alias="MATTE_CPU_MODEL_SHA256",
    )
    visual_intelligence_provider: str = Field(default="local_ml", alias="VISUAL_PROVIDER")
    # The local CLIP/BLIP/YOLO model ids and revisions come from the registry
    # ``local`` routes (gpu-local.json, plan Task 14), not a code default.
    visual_sample_fps: float = Field(default=2.0, alias="VISUAL_SAMPLE_FPS")
    visual_scene_threshold: float = Field(default=0.42, alias="VISUAL_SCENE_THRESHOLD")
    # VISUAL_PROVIDER=remote: CPU signals on the cpu-vision worker plus remote
    # keyframe enrichment. Face detection uses this pinned BlazeFace model.
    visual_cpu_face_model_path: str = Field(
        default="/opt/motionly/models/blaze_face_full_range.tflite",
        alias="VISUAL_CPU_FACE_MODEL_PATH",
    )
    visual_cpu_face_model_sha256: str = Field(
        default="3698b18f063835bc609069ef052228fbe86d9c9a6dc8dcb7c7c2d69aed2b181b",
        alias="VISUAL_CPU_FACE_MODEL_SHA256",
    )
    # Upper bound on keyframes (one per scene) sent to paid remote APIs per video.
    visual_remote_max_keyframes: int = Field(
        default=24, ge=1, le=120, alias="VISUAL_REMOTE_MAX_KEYFRAMES"
    )
    # Keyframe object detection is enabled by a ``vision.object_detection`` route
    # in the registry (a template route, plan Task 17, or a local route), not by a
    # vendor switch. The route supplies the model id, revision, pricing, batch
    # limits and credentials; objects are reported disabled until one is
    # configured. The former object-detection provider switch and its vendor
    # settings are removed (plan Task 19 item 2).
    # Keyframe captions are rebuilt as a registry capability route in Tier 2 (plan
    # Task 12): the former caption provider switch, model, revision, cost and
    # batch-size settings are removed here (plan Task 26). The route supplies the
    # model id, revision, pricing and credentials; the remote keyframe path
    # reports captions as disabled until that route is configured.
    # Keyframe and text/image embeddings are rebuilt as registry capability
    # routes in Tier 2 (plan Task 12): the former vendor embeddings settings
    # (VISUAL_EMBEDDINGS_PROVIDER, EMBEDDINGS_PROVIDER and the remote embeddings
    # auth key) are removed here (plan Task 26). B-roll ranking falls back to its
    # lexical mode when no embeddings route is configured.
    allow_fixture_providers: bool = Field(default=False, alias="ALLOW_FIXTURE_PROVIDERS")
    music_provider: str = Field(default="licensed_catalog", alias="MUSIC_PROVIDER")
    licensed_music_catalog_table: str = Field(
        default="licensed_music_tracks",
        alias="LICENSED_MUSIC_CATALOG_TABLE",
    )
    # Planning and transcription model ids/revisions come from the registry
    # (plan Task 14/Task 26): only the per-request budget hints stay as settings.
    edit_planner_input_cost_per_million_usd: float = Field(
        default=2.0, ge=0, alias="EDIT_PLANNER_INPUT_COST_PER_MILLION_USD"
    )
    edit_planner_output_cost_per_million_usd: float = Field(
        default=2.0, ge=0, alias="EDIT_PLANNER_OUTPUT_COST_PER_MILLION_USD"
    )
    transcription_estimated_cost_per_request_usd: float = Field(
        default=0.25, ge=0, alias="TRANSCRIPTION_ESTIMATED_COST_PER_REQUEST_USD"
    )
    model_gateway_enabled: bool = Field(default=True, alias="MODEL_GATEWAY_ENABLED")
    model_gateway_monthly_budget_usd: float = Field(
        default=100.0, ge=0, alias="MODEL_GATEWAY_MONTHLY_BUDGET_USD"
    )
    model_gateway_request_budget_usd: float = Field(
        default=5.0, ge=0, alias="MODEL_GATEWAY_REQUEST_BUDGET_USD"
    )
    # JSON object of budget feature -> deployment-wide monthly USD cap, e.g.
    # {"vision_captions": 20, "audio_stems": 15}. Omitted features are uncapped.
    model_gateway_feature_budgets_usd: str = Field(
        default="", alias="MODEL_GATEWAY_FEATURE_BUDGETS_USD"
    )
    model_gateway_failure_threshold: int = Field(
        default=3, ge=1, alias="MODEL_GATEWAY_FAILURE_THRESHOLD"
    )
    model_gateway_circuit_cooldown_seconds: int = Field(
        default=60, ge=1, alias="MODEL_GATEWAY_CIRCUIT_COOLDOWN_SECONDS"
    )
    # Declarative inference registry (plan Task 4, Section 2.4). Tier 1 loads the
    # registry from this JSON file; the database registry arrives in Tier 3.
    ai_registry_file: str | None = Field(default=None, alias="AI_REGISTRY_FILE")
    # When true, a non-commercial licence on any route is an error instead of a
    # warning (plan Section 2.6, Section 2.9).
    ai_block_noncommercial: bool = Field(default=False, alias="AI_BLOCK_NONCOMMERCIAL")
    # Workspace-scoped routes stay rejected until this flag is turned on
    # (plan Section 2.4: workspace BYOK is prepared but disabled).
    ai_workspace_scoped_routes: bool = Field(
        default=False, alias="AI_WORKSPACE_SCOPED_ROUTES"
    )
    # Generic inference secret storage (plan Task 5, Section 2.4). ``file:<name>``
    # refs read ``<AI_SECRETS_DIR>/<name>``; ``bundle:<key>`` refs read a key out
    # of the JSON object at AI_SECRETS_BUNDLE_FILE. Mounted privately (read-only
    # ``/run/secrets`` by default); values are never returned to clients or logged.
    ai_secrets_dir: str = Field(default="/run/secrets", alias="AI_SECRETS_DIR")
    ai_secrets_bundle_file: str | None = Field(default=None, alias="AI_SECRETS_BUNDLE_FILE")
    # Database registry and encrypted secrets (plan Task 20, Section 2.4, Tier 3).
    # The database registry is additive and off by default: the file registry is
    # a complete fallback. When enabled, adapters/models/routes/secrets are read
    # from Supabase and a version counter triggers an atomic runtime reload.
    ai_registry_database_enabled: bool = Field(
        default=False, alias="AI_REGISTRY_DATABASE_ENABLED"
    )
    # How often the runtime holder checks ai_registry_version for a change and
    # reloads (seconds). 0 disables background reload (reload on demand only).
    ai_registry_reload_interval_s: float = Field(
        default=30.0, ge=0, alias="AI_REGISTRY_RELOAD_INTERVAL_S"
    )
    # Deployment KEK for the AES-256-GCM database secret store (decision 3a).
    # ``AI_SECRETS_KEK_PREVIOUS_FILE`` is set only during a bounded rotation
    # window so the store can still decrypt ciphertexts sealed under the prior
    # KEK until the re-encryption CLI rewrites them.
    ai_secrets_kek_file: str | None = Field(default=None, alias="AI_SECRETS_KEK_FILE")
    ai_secrets_kek_previous_file: str | None = Field(
        default=None, alias="AI_SECRETS_KEK_PREVIOUS_FILE"
    )
    stock_video_provider: str = Field(default="pexels_pixabay", alias="STOCK_VIDEO_PROVIDER")
    pexels_api_key: str | None = Field(default=None, alias="PEXELS_API_KEY")
    pixabay_api_key: str | None = Field(default=None, alias="PIXABAY_API_KEY")
    # Payment gateway (demo mock gateway). Billing talks server-to-server to the
    # gateway with a merchant API key, and verifies webhooks with the shared
    # signing secret. INR prices come from app/config/plans.json, not settings.
    mock_gateway_base_url: str | None = Field(default=None, alias="MOCK_GATEWAY_BASE_URL")
    mock_gateway_public_base_url: str | None = Field(
        default=None, alias="MOCK_GATEWAY_PUBLIC_BASE_URL"
    )
    merchant_api_key: str | None = Field(default=None, alias="MERCHANT_API_KEY")
    mock_gateway_webhook_secret: str | None = Field(
        default=None, alias="MOCK_GATEWAY_WEBHOOK_SECRET"
    )
    billing_enabled: bool = Field(default=False, alias="BILLING_ENABLED")
    # Deployment-level billing posture. "disabled" charges nothing and hides the
    # billing UI; "demo" exposes the billing surface backed by a no-charge code
    # path. Production must state it explicitly (see app.core.production). This is
    # distinct from the per-workspace BillingMode (managed/personal) in
    # app.schemas.billing.
    billing_mode: str = Field(default="", alias="BILLING_MODE")
    billing_success_url: AnyHttpUrl | None = Field(default=None, alias="BILLING_SUCCESS_URL")
    billing_cancel_url: AnyHttpUrl | None = Field(default=None, alias="BILLING_CANCEL_URL")
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    remotion_enabled: bool = Field(default=False, alias="REMOTION_ENABLED")
    # Vocal/instrumental stems: ``demucs`` (local CPU/GPU Demucs worker) or
    # ``fixture`` (tests). A remote stem-separation provider is configured through
    # a ``audio.stem_separation`` registry route (plan Task 17), not a code
    # switch. The former remote vendor settings are removed (plan Task 19 item 2).
    stem_separation_provider: str = Field(default="demucs", alias="STEM_SEPARATION_PROVIDER")
    # Local Demucs model id and revision come from the registry ``local`` route
    # (cpu-local.json, plan Task 14), not a code default.
    # Per-plan quotas keyed by Motionly feature, not by provider (Decision B-1).
    # ``video_generation`` and ``planning`` replace the old provider-named quota
    # keys. Path B: no alias for the old names.
    quota_video_generation_free_limit_seconds: float = Field(
        default=0.0,
        alias="QUOTA_VIDEO_GENERATION_FREE_LIMIT_SECONDS",
    )
    quota_video_generation_creator_limit_seconds: float = Field(
        default=60.0,
        alias="QUOTA_VIDEO_GENERATION_CREATOR_LIMIT_SECONDS",
    )
    quota_video_generation_pro_limit_seconds: float = Field(
        default=300.0,
        alias="QUOTA_VIDEO_GENERATION_PRO_LIMIT_SECONDS",
    )
    quota_video_generation_agency_limit_seconds: float = Field(
        default=1200.0,
        alias="QUOTA_VIDEO_GENERATION_AGENCY_LIMIT_SECONDS",
    )

    quota_planning_free_limit_tokens: float = Field(
        default=100000.0,
        alias="QUOTA_PLANNING_FREE_LIMIT_TOKENS",
    )
    quota_planning_creator_limit_tokens: float = Field(
        default=500000.0,
        alias="QUOTA_PLANNING_CREATOR_LIMIT_TOKENS",
    )
    quota_planning_pro_limit_tokens: float = Field(
        default=2000000.0,
        alias="QUOTA_PLANNING_PRO_LIMIT_TOKENS",
    )
    quota_planning_agency_limit_tokens: float = Field(
        default=10000000.0,
        alias="QUOTA_PLANNING_AGENCY_LIMIT_TOKENS",
    )

    # Temporary flag (Decision B-1): ``feature`` (default) sums usage per Motionly
    # feature/category across provider labels; ``provider`` restores per-provider
    # counting and records (but does not enforce) the planning quota. Both modes
    # read the same cost rows. Removed at the Tier 2 gate, no earlier than one
    # full billing period after Tier 1 ships.
    ai_quota_scope: str = Field(default="feature", alias="AI_QUOTA_SCOPE")

    # Per-plan quotas for the remote AI features (metered by the model gateway;
    # local GPU/CPU routes are never metered). See app.services.billing.FEATURE_QUOTAS.
    quota_vision_objects_free_limit_images: float = Field(
        default=120.0,
        ge=0,
        alias="QUOTA_VISION_OBJECTS_FREE_LIMIT_IMAGES",
    )
    quota_vision_objects_creator_limit_images: float = Field(
        default=1000.0,
        ge=0,
        alias="QUOTA_VISION_OBJECTS_CREATOR_LIMIT_IMAGES",
    )
    quota_vision_objects_pro_limit_images: float = Field(
        default=3000.0,
        ge=0,
        alias="QUOTA_VISION_OBJECTS_PRO_LIMIT_IMAGES",
    )
    quota_vision_objects_agency_limit_images: float = Field(
        default=7500.0,
        ge=0,
        alias="QUOTA_VISION_OBJECTS_AGENCY_LIMIT_IMAGES",
    )
    quota_vision_captions_free_limit_images: float = Field(
        default=120.0,
        ge=0,
        alias="QUOTA_VISION_CAPTIONS_FREE_LIMIT_IMAGES",
    )
    quota_vision_captions_creator_limit_images: float = Field(
        default=1000.0,
        ge=0,
        alias="QUOTA_VISION_CAPTIONS_CREATOR_LIMIT_IMAGES",
    )
    quota_vision_captions_pro_limit_images: float = Field(
        default=3000.0,
        ge=0,
        alias="QUOTA_VISION_CAPTIONS_PRO_LIMIT_IMAGES",
    )
    quota_vision_captions_agency_limit_images: float = Field(
        default=7500.0,
        ge=0,
        alias="QUOTA_VISION_CAPTIONS_AGENCY_LIMIT_IMAGES",
    )
    quota_embeddings_free_limit_inputs: float = Field(
        default=1000.0,
        ge=0,
        alias="QUOTA_EMBEDDINGS_FREE_LIMIT_INPUTS",
    )
    quota_embeddings_creator_limit_inputs: float = Field(
        default=8000.0,
        ge=0,
        alias="QUOTA_EMBEDDINGS_CREATOR_LIMIT_INPUTS",
    )
    quota_embeddings_pro_limit_inputs: float = Field(
        default=24000.0,
        ge=0,
        alias="QUOTA_EMBEDDINGS_PRO_LIMIT_INPUTS",
    )
    quota_embeddings_agency_limit_inputs: float = Field(
        default=60000.0,
        ge=0,
        alias="QUOTA_EMBEDDINGS_AGENCY_LIMIT_INPUTS",
    )
    quota_audio_diarization_free_limit_seconds: float = Field(
        default=1800.0,
        ge=0,
        alias="QUOTA_AUDIO_DIARIZATION_FREE_LIMIT_SECONDS",
    )
    quota_audio_diarization_creator_limit_seconds: float = Field(
        default=14400.0,
        ge=0,
        alias="QUOTA_AUDIO_DIARIZATION_CREATOR_LIMIT_SECONDS",
    )
    quota_audio_diarization_pro_limit_seconds: float = Field(
        default=43200.0,
        ge=0,
        alias="QUOTA_AUDIO_DIARIZATION_PRO_LIMIT_SECONDS",
    )
    quota_audio_diarization_agency_limit_seconds: float = Field(
        default=108000.0,
        ge=0,
        alias="QUOTA_AUDIO_DIARIZATION_AGENCY_LIMIT_SECONDS",
    )
    quota_audio_emotion_free_limit_seconds: float = Field(
        default=1800.0,
        ge=0,
        alias="QUOTA_AUDIO_EMOTION_FREE_LIMIT_SECONDS",
    )
    quota_audio_emotion_creator_limit_seconds: float = Field(
        default=14400.0,
        ge=0,
        alias="QUOTA_AUDIO_EMOTION_CREATOR_LIMIT_SECONDS",
    )
    quota_audio_emotion_pro_limit_seconds: float = Field(
        default=43200.0,
        ge=0,
        alias="QUOTA_AUDIO_EMOTION_PRO_LIMIT_SECONDS",
    )
    quota_audio_emotion_agency_limit_seconds: float = Field(
        default=108000.0,
        ge=0,
        alias="QUOTA_AUDIO_EMOTION_AGENCY_LIMIT_SECONDS",
    )
    quota_audio_stems_free_limit_seconds: float = Field(
        default=600.0,
        ge=0,
        alias="QUOTA_AUDIO_STEMS_FREE_LIMIT_SECONDS",
    )
    quota_audio_stems_creator_limit_seconds: float = Field(
        default=3600.0,
        ge=0,
        alias="QUOTA_AUDIO_STEMS_CREATOR_LIMIT_SECONDS",
    )
    quota_audio_stems_pro_limit_seconds: float = Field(
        default=10800.0,
        ge=0,
        alias="QUOTA_AUDIO_STEMS_PRO_LIMIT_SECONDS",
    )
    quota_audio_stems_agency_limit_seconds: float = Field(
        default=36000.0,
        ge=0,
        alias="QUOTA_AUDIO_STEMS_AGENCY_LIMIT_SECONDS",
    )

    max_paid_provider_attempts: int = Field(default=1, alias="MAX_PAID_PROVIDER_ATTEMPTS")

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
            # Custom endpoint and Azure HTTPS/auth rules are enforced by the
            # declarative registry validator (Section 2.6), not env settings
            # (plan Task 26).
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
        "MERCHANT_API_KEY",
        "MOCK_GATEWAY_WEBHOOK_SECRET",
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
