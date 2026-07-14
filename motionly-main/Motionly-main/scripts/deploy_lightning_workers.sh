#!/usr/bin/env bash
set -euo pipefail

: "${LIGHTNING_DEPLOY_COMMAND:?Set LIGHTNING_DEPLOY_COMMAND to the deployment executable}"
: "${LIGHTNING_WORKER_IMAGE:?Set LIGHTNING_WORKER_IMAGE to an immutable worker image digest}"
: "${REDIS_URL:?REDIS_URL is required}"
: "${SUPABASE_URL:?SUPABASE_URL is required}"
: "${SUPABASE_SERVICE_ROLE_KEY:?SUPABASE_SERVICE_ROLE_KEY is required}"
: "${WORKER_API_BASE_URL:?WORKER_API_BASE_URL is required}"
: "${WORKER_API_TOKEN:?WORKER_API_TOKEN is required}"

HF_HOME=/var/cache/motionly/huggingface
TORCH_HOME=/var/cache/motionly/torch
XDG_CACHE_HOME=/var/cache/motionly

launch_worker() {
  local role="$1"
  local deployment="$2"
  local queues="$3"
  env \
    APP_ENV=production \
    PROCESS_ROLE=worker \
    LIGHTNING_WORKER_ROLE="$role" \
    WORKER_QUEUES="$queues" \
    PYTHON_MEDIA_WORKER_ENABLED=true \
    ML_WORKER_ENABLED=true \
    MODEL_PREFETCH=true \
    HF_HOME="$HF_HOME" \
    TORCH_HOME="$TORCH_HOME" \
    XDG_CACHE_HOME="$XDG_CACHE_HOME" \
    REDIS_URL="$REDIS_URL" \
    SUPABASE_URL="$SUPABASE_URL" \
    SUPABASE_SERVICE_ROLE_KEY="$SUPABASE_SERVICE_ROLE_KEY" \
    WORKER_API_BASE_URL="$WORKER_API_BASE_URL" \
    WORKER_API_TOKEN="$WORKER_API_TOKEN" \
    "$LIGHTNING_DEPLOY_COMMAND" "$deployment" "$LIGHTNING_WORKER_IMAGE"
}

# Keep these explicit assignments aligned with queue routing contract tests.
WORKER_QUEUES=visual_analysis,transcription
launch_worker visual "${LIGHTNING_VISUAL_DEPLOYMENT:-motionly-visual-worker}" "$WORKER_QUEUES"

WORKER_QUEUES=matte_extraction
launch_worker matte "${LIGHTNING_MATTE_DEPLOYMENT:-motionly-matte-worker}" "$WORKER_QUEUES"

WORKER_QUEUES=render_gpu
launch_worker render "${LIGHTNING_RENDER_DEPLOYMENT:-motionly-gpu-render-worker}" "$WORKER_QUEUES"
