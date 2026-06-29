# GPU and Media Worker Runbook

## Worker Types and Queues

- `visual_analysis`: GPU visual intelligence. Concurrency defaults to `VISUAL_ANALYSIS_WORKER_CONCURRENCY=1`.
- `matte_extraction`: MediaPipe selfie segmentation and FFmpeg matte encoding. Concurrency defaults to `MATTE_WORKER_CONCURRENCY=1`.
- `render_gpu`: GPU render jobs. Concurrency is controlled by `GPU_RENDER_WORKER_CONCURRENCY`.
- Standard domain queues such as `transcription`, `planning`, `stock_search`, and `provider_calls` must not run matte extraction.

Matte extraction is intentionally isolated because it is expensive and can OOM GPU workers. It must run only on GPU/media worker deployments with `WORKER_QUEUES=matte_extraction`.

## Resource Limits

- Deployment target: `GPU_WORKER_INSTANCE_TYPE`, default `g5.xlarge`.
- Max concurrent jobs per GPU: `GPU_MAX_CONCURRENT_JOBS_PER_GPU`, default `1`.
- GPU memory ceiling: `GPU_MEMORY_CEILING_MB`, default `16000`.
- Matte concurrency: `MATTE_WORKER_CONCURRENCY`, default `1`.
- Visual concurrency: `VISUAL_ANALYSIS_WORKER_CONCURRENCY`, default `1`.
- Long job timeout: `GPU_LONG_JOB_TIMEOUT_SECONDS`, default `1800`.

Keep matte and visual workers at concurrency `1` unless load tests show enough memory headroom for the selected model set.

## Autoscaling Policy

The testable policy lives in `apps/api/app/services/gpu_autoscaling.py`.

- Scale up when GPU queue depth is at or above `GPU_SCALE_UP_QUEUE_DEPTH`, default `4`.
- Scale down when queue depth is at or below `GPU_SCALE_DOWN_QUEUE_DEPTH`, default `1`.
- Cooldown is `GPU_AUTOSCALE_COOLDOWN_SECONDS`, default `300`.
- Max worker count is `GPU_MAX_WORKER_COUNT`, default `4`.
- OOM alert threshold is `GPU_OOM_ALERT_THRESHOLD`, default `2`.

Production autoscaling should use the sum of waiting jobs in `matte_extraction`, `visual_analysis`, and `render_gpu`, with per-queue dashboards so matte backlog cannot hide planning/transcription health.

## Matte Cache and Recovery

Matte records are stored in `public.segmentation_mattes` with status:

- `pending`
- `processing`
- `completed`
- `failed`
- `skipped`

The cache key is derived from upload ID, source file SHA-256, segmentation settings, and model version. Pending records use a provisional key to collapse duplicate requests before the worker computes the source hash.

If a matte job fails:

1. Confirm the project can still transcribe, plan, and render without alpha matte.
2. Inspect `segmentation_mattes.error_code` and worker logs for `matte_job_failed`.
3. Retry by re-saving effects with background removal and `force=true`, or by requeueing the failed matte record after confirming the source file is available.
4. If CUDA OOM repeats above `GPU_OOM_ALERT_THRESHOLD`, lower `MATTE_WORKER_CONCURRENCY`, increase instance memory, or temporarily disable background removal.

## Rollback

Rollback is safe because render props include `segmentationMatteStatus`. If matte status is pending, failed, skipped, or unavailable, render falls back to no alpha matte instead of trying to resolve a missing asset.
