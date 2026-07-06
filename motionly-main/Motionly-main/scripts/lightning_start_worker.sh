#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:-}"
ROOT_DIR="${LIGHTNING_STUDIO_PROJECT_PATH:-/teamspace/studios/this_studio/motionly-gpu}"
HEALTH_PORT="${LIGHTNING_WORKER_HEALTH_PORT:-8000}"

if [[ -z "$ROLE" ]]; then
  echo "usage: scripts/lightning_start_worker.sh <visual|matte|render>" >&2
  exit 2
fi

cd "$ROOT_DIR"

export PYTHON_EXECUTOR_REQUEST_TIMEOUT_MS="${PYTHON_EXECUTOR_REQUEST_TIMEOUT_MS:-900000}"
export PYTHON_EXECUTOR_HEARTBEAT_TIMEOUT_MS="${PYTHON_EXECUTOR_HEARTBEAT_TIMEOUT_MS:-120000}"
export PYTHON_EXECUTOR_HEARTBEAT_INTERVAL_MS="${PYTHON_EXECUTOR_HEARTBEAT_INTERVAL_MS:-30000}"

case "$ROLE" in
  visual)
    export ML_WORKER_ENABLED="${ML_WORKER_ENABLED:-true}"
    export PYTHON_MEDIA_WORKER_ENABLED="${PYTHON_MEDIA_WORKER_ENABLED:-true}"
    export MODEL_PREFETCH="${MODEL_PREFETCH:-true}"
    export WORKER_QUEUES="${WORKER_QUEUES:-visual_analysis,transcription}"
    export WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"
    export VISUAL_ANALYSIS_WORKER_CONCURRENCY="${VISUAL_ANALYSIS_WORKER_CONCURRENCY:-1}"
    ;;
  matte)
    export ML_WORKER_ENABLED="${ML_WORKER_ENABLED:-true}"
    export PYTHON_MEDIA_WORKER_ENABLED="${PYTHON_MEDIA_WORKER_ENABLED:-true}"
    export MODEL_PREFETCH="${MODEL_PREFETCH:-true}"
    export WORKER_QUEUES="${WORKER_QUEUES:-matte_extraction}"
    export WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"
    export MATTE_WORKER_CONCURRENCY="${MATTE_WORKER_CONCURRENCY:-1}"
    ;;
  render)
    export PYTHON_MEDIA_WORKER_ENABLED="${PYTHON_MEDIA_WORKER_ENABLED:-true}"
    export WORKER_QUEUES="${WORKER_QUEUES:-render_gpu}"
    export WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"
    export GPU_RENDER_WORKER_CONCURRENCY="${GPU_RENDER_WORKER_CONCURRENCY:-1}"
    export FFMPEG_GPU_ENABLED="${FFMPEG_GPU_ENABLED:-true}"
    export FFMPEG_GPU_ENCODER="${FFMPEG_GPU_ENCODER:-h264_nvenc}"
    ;;
  *)
    echo "unsupported worker role: $ROLE" >&2
    exit 2
    ;;
esac

if command -v corepack >/dev/null 2>&1; then
  corepack enable >/dev/null 2>&1 || true
fi

if ! command -v pnpm >/dev/null 2>&1 && command -v corepack >/dev/null 2>&1; then
  corepack prepare pnpm@9.15.4 --activate >/dev/null 2>&1 || true
fi

api_extra=""
case "$ROLE" in
  visual | matte)
    api_extra="[visual-ml]"
    ;;
esac

if ! python -c 'import pydantic_settings' >/dev/null 2>&1; then
  echo "Installing API Python dependencies..."
  python -m pip install --no-cache-dir --upgrade pip
  python -m pip install --no-cache-dir --upgrade --force-reinstall "numpy>=1.26.4,<2.0"
  python -m pip install --no-cache-dir "./apps/api${api_extra}"
fi

# Ensure worker dependencies are actually available before starting the loop.
if ! node --input-type=module -e 'await import("bullmq")' >/dev/null 2>&1; then
  echo "Installing worker Node.js dependencies..."
  if command -v pnpm >/dev/null 2>&1; then
    pnpm install --prod --filter @motionly/worker...
  else
    (cd apps/worker && npm install --omit=dev)
  fi
fi

python -m http.server "$HEALTH_PORT" --bind 0.0.0.0 >/tmp/motionly-health.log 2>&1 &
HEALTH_SERVER_PID=$!
cleanup() {
  if kill -0 "$HEALTH_SERVER_PID" >/dev/null 2>&1; then
    kill "$HEALTH_SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

if [[ "${MODEL_PREFETCH}" == "true" ]]; then
  python -m app.workers.prefetch_models
fi
cd apps/worker
exec node src/worker.mjs --loop
