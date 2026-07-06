#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/deploy_lightning_workers.sh [env-file]

Creates or updates Motionly Lightning GPU deployments:
  - motionly-visual-worker
  - motionly-matte-worker
  - motionly-gpu-render-worker

Set LIGHTNING_DEPLOY_SOURCE=studio to deploy directly from a Lightning Studio.
Set LIGHTNING_DEPLOY_SOURCE=image to build and upload Docker images first.

If an env file path is provided, it is sourced before validation.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ $# -eq 1 ]]; then
  env_file="$1"
  if [[ ! -f "$env_file" ]]; then
    echo "env file not found: $env_file" >&2
    exit 1
  fi
  # shellcheck disable=SC1090
  source "$env_file"
fi

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "required command not found: $1" >&2
    exit 127
  fi
}

require_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "required env var is missing: $name" >&2
    exit 1
  fi
}

require_command lightning

if [[ -z "${LIGHTNING_TEAMSPACE:-}" ]]; then
  LIGHTNING_TEAMSPACE="${LIGHTNING_DEFAULT_TEAMSPACE:-motionly/model-deployment-project}"
fi

LIGHTNING_GPU_MACHINE="${LIGHTNING_GPU_MACHINE:-L4}"
LIGHTNING_IMAGE_TAG="${LIGHTNING_IMAGE_TAG:-$(date -u +%Y%m%d%H%M%S)}"
LIGHTNING_GPU_PLATFORM="${LIGHTNING_GPU_PLATFORM:-linux/amd64}"
LIGHTNING_MIN_REPLICAS="${LIGHTNING_MIN_REPLICAS:-0}"
LIGHTNING_MAX_REPLICAS="${LIGHTNING_MAX_REPLICAS:-${GPU_MAX_WORKER_COUNT:-4}}"
LIGHTNING_DEPLOY_SOURCE="${LIGHTNING_DEPLOY_SOURCE:-auto}"
LIGHTNING_STUDIO="${LIGHTNING_STUDIO:-}"
LIGHTNING_STUDIO_PROJECT_PATH="${LIGHTNING_STUDIO_PROJECT_PATH:-/teamspace/studios/this_studio/motionly-gpu}"
LIGHTNING_WORKER_HEALTH_PORT="${LIGHTNING_WORKER_HEALTH_PORT:-8000}"
LIGHTNING_VISUAL_DEPLOYMENT="${LIGHTNING_VISUAL_DEPLOYMENT:-motionly-visual-worker}"
LIGHTNING_MATTE_DEPLOYMENT="${LIGHTNING_MATTE_DEPLOYMENT:-motionly-matte-worker}"
LIGHTNING_RENDER_DEPLOYMENT="${LIGHTNING_RENDER_DEPLOYMENT:-motionly-gpu-render-worker}"
LIGHTNING_GPU_IMAGE_NAME="${LIGHTNING_GPU_IMAGE_NAME:-motionly-lightning-gpu-worker}"
LIGHTNING_PYTHON_IMAGE_NAME="${LIGHTNING_PYTHON_IMAGE_NAME:-motionly-lightning-python-worker}"

APP_ENV="${APP_ENV:-production}"
TRANSCRIPTION_PROVIDER="${TRANSCRIPTION_PROVIDER:-groq}"
TRANSCRIPTION_MODEL="${TRANSCRIPTION_MODEL:-whisper-large-v3-turbo}"
EXPORT_STORAGE_BUCKET="${EXPORT_STORAGE_BUCKET:-exports}"
UPLOAD_STORAGE_BUCKET="${UPLOAD_STORAGE_BUCKET:-raw-uploads}"
RENDER_EXECUTE_FFMPEG="${RENDER_EXECUTE_FFMPEG:-true}"
MODEL_PREFETCH="${MODEL_PREFETCH:-true}"
VISUAL_ANALYSIS_WORKER_CONCURRENCY="${VISUAL_ANALYSIS_WORKER_CONCURRENCY:-1}"
MATTE_WORKER_CONCURRENCY="${MATTE_WORKER_CONCURRENCY:-1}"
GPU_RENDER_WORKER_CONCURRENCY="${GPU_RENDER_WORKER_CONCURRENCY:-1}"

require_var LIGHTNING_USER_ID
require_var LIGHTNING_API_KEY
require_var LIGHTNING_TEAMSPACE
require_var SUPABASE_URL
require_var SUPABASE_SERVICE_ROLE_KEY
require_var REDIS_URL
require_var WORKER_API_BASE_URL

if [[ -z "${WORKER_API_TOKEN:-}" && -z "${ADMIN_API_TOKEN:-}" ]]; then
  echo "required env var is missing: WORKER_API_TOKEN or ADMIN_API_TOKEN" >&2
  exit 1
fi

require_var GROQ_API_KEY

WORKER_API_TOKEN="${WORKER_API_TOKEN:-${ADMIN_API_TOKEN:-}}"

GPU_IMAGE_LOCAL="${LIGHTNING_GPU_IMAGE_NAME}:${LIGHTNING_IMAGE_TAG}"
PYTHON_IMAGE_LOCAL="${LIGHTNING_PYTHON_IMAGE_NAME}:${LIGHTNING_IMAGE_TAG}"
GPU_IMAGE_REMOTE="${GPU_IMAGE_LOCAL}"
PYTHON_IMAGE_REMOTE="${PYTHON_IMAGE_LOCAL}"

if [[ "$LIGHTNING_DEPLOY_SOURCE" == "auto" ]]; then
  if [[ -n "$LIGHTNING_STUDIO" ]]; then
    LIGHTNING_DEPLOY_SOURCE="studio"
  else
    LIGHTNING_DEPLOY_SOURCE="image"
  fi
fi

if [[ "$LIGHTNING_DEPLOY_SOURCE" != "studio" && "$LIGHTNING_DEPLOY_SOURCE" != "image" ]]; then
  echo "unsupported LIGHTNING_DEPLOY_SOURCE: $LIGHTNING_DEPLOY_SOURCE" >&2
  exit 1
fi

if [[ "$LIGHTNING_DEPLOY_SOURCE" == "image" ]]; then
  require_command docker
fi

build_image() {
  local tag="$1"
  local dockerfile="$2"
  shift 2
  local host_arch
  host_arch="$(uname -m)"
  if [[ "${LIGHTNING_BUILD_STRATEGY:-auto}" == "docker" ]] || {
    [[ "${LIGHTNING_BUILD_STRATEGY:-auto}" == "auto" ]] &&
      [[ "$LIGHTNING_GPU_PLATFORM" == "linux/amd64" ]] &&
      [[ "$host_arch" == "x86_64" ]];
  }; then
    DOCKER_BUILDKIT=0 docker build \
      -t "$tag" \
      -f "$dockerfile" \
      "$@" \
      .
    return
  fi

  docker buildx build \
    --platform "$LIGHTNING_GPU_PLATFORM" \
    --load \
    -t "$tag" \
    -f "$dockerfile" \
    "$@" \
    .
}

upload_image() {
  local tag="$1"
  lightning container upload "$tag" \
    --tag "$LIGHTNING_IMAGE_TAG" \
    --teamspace "$LIGHTNING_TEAMSPACE" \
    --platform "$LIGHTNING_GPU_PLATFORM"
}

deployment_exists() {
  local output
  if ! output="$(lightning deployment inspect "$1" --teamspace "$LIGHTNING_TEAMSPACE" 2>&1)"; then
    return 1
  fi
  if grep -qi "was not found" <<<"$output"; then
    return 1
  fi
  return 0
}

path_mapping_args=()
if [[ -n "${LIGHTNING_CACHE_PATH_MAPPING:-}" ]]; then
  path_mapping_args+=(--path-mapping "$LIGHTNING_CACHE_PATH_MAPPING")
fi
if [[ -n "${LIGHTNING_DATA_PATH_MAPPING:-}" ]]; then
  path_mapping_args+=(--path-mapping "$LIGHTNING_DATA_PATH_MAPPING")
fi

base_env_args=(
  -e "APP_ENV=${APP_ENV}"
  -e "SUPABASE_URL=${SUPABASE_URL}"
  -e "SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}"
  -e "REDIS_URL=${REDIS_URL}"
  -e "WORKER_API_BASE_URL=${WORKER_API_BASE_URL}"
  -e "WORKER_API_TOKEN=${WORKER_API_TOKEN}"
  -e "EXPORT_STORAGE_BUCKET=${EXPORT_STORAGE_BUCKET}"
  -e "UPLOAD_STORAGE_BUCKET=${UPLOAD_STORAGE_BUCKET}"
  -e "RENDER_EXECUTE_FFMPEG=${RENDER_EXECUTE_FFMPEG}"
  -e "HF_HOME=/var/cache/motionly/huggingface"
  -e "TORCH_HOME=/var/cache/motionly/torch"
  -e "XDG_CACHE_HOME=/var/cache/motionly"
  -e "PYTHONPATH=${LIGHTNING_STUDIO_PROJECT_PATH}/apps/api"
)

optional_env_args=()
for name in \
  ADMIN_API_TOKEN \
  SUPABASE_JWT_SECRET \
  PUBLISH_TOKEN_ENCRYPTION_KEY \
  METRICS_BEARER_TOKEN \
  SENTRY_DSN \
  GROQ_API_KEY \
  TRANSCRIPTION_PROVIDER \
  TRANSCRIPTION_MODEL \
  PYTHON_EXECUTOR_REQUEST_TIMEOUT_MS \
  PYTHON_EXECUTOR_HEARTBEAT_TIMEOUT_MS \
  PYTHON_EXECUTOR_HEARTBEAT_INTERVAL_MS \
  PYANNOTE_AUTH_TOKEN \
  GPU_WORKER_INSTANCE_TYPE \
  GPU_MAX_CONCURRENT_JOBS_PER_GPU \
  GPU_MEMORY_CEILING_MB \
  GPU_SCALE_UP_QUEUE_DEPTH \
  GPU_SCALE_DOWN_QUEUE_DEPTH \
  GPU_AUTOSCALE_COOLDOWN_SECONDS \
  GPU_MAX_WORKER_COUNT \
  GPU_OOM_ALERT_THRESHOLD \
  GPU_LONG_JOB_TIMEOUT_SECONDS; do
  if [[ -n "${!name:-}" ]]; then
    optional_env_args+=(-e "${name}=${!name}")
  fi
done

deploy_create_or_update() {
  local name="$1"
  local source_ref="$2"
  shift 2

  local update_args=(
    --teamspace "$LIGHTNING_TEAMSPACE"
    --port "$LIGHTNING_WORKER_HEALTH_PORT"
    --machine "$LIGHTNING_GPU_MACHINE"
    --min-replicas "$LIGHTNING_MIN_REPLICAS"
    --max-replicas "$LIGHTNING_MAX_REPLICAS"
    "${path_mapping_args[@]}"
    "${base_env_args[@]}"
    "${optional_env_args[@]}"
    "$@"
  )

  if deployment_exists "$name"; then
    lightning deployment update "$name" "${update_args[@]}"
  else
    local create_args=(
      --autoscale-metric GPU
      --autoscale-threshold 1
      --quantity 1
      "${update_args[@]}"
    )
    if [[ "$LIGHTNING_DEPLOY_SOURCE" == "studio" ]]; then
      create_args=(--studio "$source_ref" "${create_args[@]}")
    else
      create_args=(--image "$source_ref" "${create_args[@]}")
    fi
    lightning deployment create "$name" "${create_args[@]}"
  fi
}

if [[ "$LIGHTNING_DEPLOY_SOURCE" == "studio" ]]; then
  require_var LIGHTNING_STUDIO
  GPU_SOURCE_REF="$LIGHTNING_STUDIO"
  PYTHON_SOURCE_REF="$LIGHTNING_STUDIO"
  VISUAL_COMMAND_ARGS=(--command "bash -lc 'cd \"$LIGHTNING_STUDIO_PROJECT_PATH\" && ./scripts/lightning_start_worker.sh visual'")
  MATTE_COMMAND_ARGS=(--command "bash -lc 'cd \"$LIGHTNING_STUDIO_PROJECT_PATH\" && ./scripts/lightning_start_worker.sh matte'")
  RENDER_COMMAND_ARGS=(--command "bash -lc 'cd \"$LIGHTNING_STUDIO_PROJECT_PATH\" && ./scripts/lightning_start_worker.sh render'")
else
  echo "Building Lightning GPU image: $GPU_IMAGE_LOCAL"
  build_image "$GPU_IMAGE_LOCAL" apps/worker/Dockerfile.gpu

  echo "Building Lightning Python media image: $PYTHON_IMAGE_LOCAL"
  build_image "$PYTHON_IMAGE_LOCAL" apps/worker/Dockerfile.python

  echo "Uploading $GPU_IMAGE_LOCAL to Lightning"
  upload_image "$GPU_IMAGE_LOCAL"

  echo "Uploading $PYTHON_IMAGE_LOCAL to Lightning"
  upload_image "$PYTHON_IMAGE_LOCAL"

  GPU_SOURCE_REF="$GPU_IMAGE_REMOTE"
  PYTHON_SOURCE_REF="$PYTHON_IMAGE_REMOTE"
  VISUAL_COMMAND_ARGS=()
  MATTE_COMMAND_ARGS=()
  RENDER_COMMAND_ARGS=()
fi

echo "Deploying $LIGHTNING_VISUAL_DEPLOYMENT"
deploy_create_or_update \
  "$LIGHTNING_VISUAL_DEPLOYMENT" \
  "$GPU_SOURCE_REF" \
  "${VISUAL_COMMAND_ARGS[@]}" \
  -e "ML_WORKER_ENABLED=true" \
  -e "PYTHON_MEDIA_WORKER_ENABLED=true" \
  -e "MODEL_PREFETCH=${MODEL_PREFETCH}" \
  -e "WORKER_QUEUES=visual_analysis,transcription" \
  -e "WORKER_CONCURRENCY=1" \
  -e "VISUAL_ANALYSIS_WORKER_CONCURRENCY=${VISUAL_ANALYSIS_WORKER_CONCURRENCY}"

echo "Deploying $LIGHTNING_MATTE_DEPLOYMENT"
deploy_create_or_update \
  "$LIGHTNING_MATTE_DEPLOYMENT" \
  "$GPU_SOURCE_REF" \
  "${MATTE_COMMAND_ARGS[@]}" \
  -e "ML_WORKER_ENABLED=true" \
  -e "MODEL_PREFETCH=${MODEL_PREFETCH}" \
  -e "WORKER_QUEUES=matte_extraction" \
  -e "WORKER_CONCURRENCY=1" \
  -e "MATTE_WORKER_CONCURRENCY=${MATTE_WORKER_CONCURRENCY}"

echo "Deploying $LIGHTNING_RENDER_DEPLOYMENT"
deploy_create_or_update \
  "$LIGHTNING_RENDER_DEPLOYMENT" \
  "$PYTHON_SOURCE_REF" \
  "${RENDER_COMMAND_ARGS[@]}" \
  -e "PYTHON_MEDIA_WORKER_ENABLED=true" \
  -e "WORKER_QUEUES=render_gpu" \
  -e "WORKER_CONCURRENCY=1" \
  -e "GPU_RENDER_WORKER_CONCURRENCY=${GPU_RENDER_WORKER_CONCURRENCY}" \
  -e "FFMPEG_GPU_ENABLED=true" \
  -e "FFMPEG_GPU_ENCODER=h264_nvenc"

cat <<EOF
Lightning GPU worker deployment flow completed.
Teamspace: ${LIGHTNING_TEAMSPACE}
Deploy source: ${LIGHTNING_DEPLOY_SOURCE}
$(if [[ "$LIGHTNING_DEPLOY_SOURCE" == "image" ]]; then echo "Image tag: ${LIGHTNING_IMAGE_TAG}"; else echo "Studio: ${LIGHTNING_STUDIO}"; fi)
Deployments:
  - ${LIGHTNING_VISUAL_DEPLOYMENT}
  - ${LIGHTNING_MATTE_DEPLOYMENT}
  - ${LIGHTNING_RENDER_DEPLOYMENT}
EOF
