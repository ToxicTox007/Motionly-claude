# Lightning GPU Worker Deployment

Use [scripts/deploy_lightning_workers.sh](/workspaces/Motionly-claude/motionly-main/Motionly-main/scripts/deploy_lightning_workers.sh:1) to create or update the three Lightning deployments used for GPU queues:

- `motionly-visual-worker`
- `motionly-matte-worker`
- `motionly-gpu-render-worker`

The script supports two deployment sources:

- preferred: `LIGHTNING_DEPLOY_SOURCE=studio` with `LIGHTNING_STUDIO=<studio-name>`
- fallback: `LIGHTNING_DEPLOY_SOURCE=image` to build and upload Docker images

The Studio flow is the better production path when the GPU environment is already prepared inside Lightning. It avoids rebuilding CUDA-heavy images on the local machine and starts deployments from the Studio filesystem with [scripts/lightning_start_worker.sh](/workspaces/Motionly-claude/motionly-main/Motionly-main/scripts/lightning_start_worker.sh:1).

The script expects environment variables instead of checked-in secrets.

Required for Lightning auth:

- `LIGHTNING_USER_ID`
- `LIGHTNING_API_KEY`

Required for Motionly runtime:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `REDIS_URL`
- `WORKER_API_BASE_URL`
- `WORKER_API_TOKEN` or `ADMIN_API_TOKEN`
- `GROQ_API_KEY`

Useful optional values:

- `LIGHTNING_DEPLOY_SOURCE`
- `LIGHTNING_STUDIO`
- `LIGHTNING_STUDIO_PROJECT_PATH`
- `LIGHTNING_WORKER_HEALTH_PORT`
- `LIGHTNING_TEAMSPACE`
- `LIGHTNING_GPU_MACHINE`
- `LIGHTNING_MIN_REPLICAS`
- `LIGHTNING_MAX_REPLICAS`
- `LIGHTNING_CACHE_PATH_MAPPING`
- `LIGHTNING_DATA_PATH_MAPPING`
- `SUPABASE_JWT_SECRET`
- `PUBLISH_TOKEN_ENCRYPTION_KEY`
- `SENTRY_DSN`
- `PYANNOTE_AUTH_TOKEN`

The queue layout matches the repo runbook in [gpu-media-workers.md](/workspaces/Motionly-claude/motionly-main/Motionly-main/docs/ops/gpu-media-workers.md:1):

- visual deployment: `WORKER_QUEUES=visual_analysis,transcription`
- matte deployment: `WORKER_QUEUES=matte_extraction`
- GPU render deployment: `WORKER_QUEUES=render_gpu`

Concurrency is pinned to `1` for the GPU queues unless overridden explicitly.

Example:

```bash
export LIGHTNING_USER_ID=...
export LIGHTNING_API_KEY=...
export LIGHTNING_DEPLOY_SOURCE=studio
export LIGHTNING_STUDIO=motionly-gpu-studio
export LIGHTNING_TEAMSPACE=motionly/model-deployment-project
export SUPABASE_URL=...
export SUPABASE_SERVICE_ROLE_KEY=...
export REDIS_URL=...
export WORKER_API_BASE_URL=...
export ADMIN_API_TOKEN=...
export GROQ_API_KEY=...

scripts/deploy_lightning_workers.sh
```
