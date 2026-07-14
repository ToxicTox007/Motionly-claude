#!/usr/bin/env bash
set -euo pipefail

case "${LIGHTNING_WORKER_ROLE:-visual}" in
  visual)
    WORKER_QUEUES="${WORKER_QUEUES:-visual_analysis,transcription}"
    ;;
  matte)
    WORKER_QUEUES="${WORKER_QUEUES:-matte_extraction}"
    ;;
  render)
    WORKER_QUEUES="${WORKER_QUEUES:-render_gpu}"
    ;;
  *)
    echo "LIGHTNING_WORKER_ROLE must be visual, matte, or render" >&2
    exit 64
    ;;
esac

export WORKER_QUEUES
export APP_ENV="${APP_ENV:-production}"
export PROCESS_ROLE="${PROCESS_ROLE:-worker}"
export PYTHON_MEDIA_WORKER_ENABLED="${PYTHON_MEDIA_WORKER_ENABLED:-true}"
export ML_WORKER_ENABLED="${ML_WORKER_ENABLED:-true}"
export HF_HOME="${HF_HOME:-/var/cache/motionly/huggingface}"
export TORCH_HOME="${TORCH_HOME:-/var/cache/motionly/torch}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/var/cache/motionly}"

: "${REDIS_URL:?REDIS_URL is required}"
: "${SUPABASE_URL:?SUPABASE_URL is required}"
: "${SUPABASE_SERVICE_ROLE_KEY:?SUPABASE_SERVICE_ROLE_KEY is required}"

cd /app
python -m app.workers.prefetch_models
exec node /app/apps/worker/src/worker.mjs --loop
