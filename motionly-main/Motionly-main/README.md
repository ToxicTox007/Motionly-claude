# Motionly

Motionly is a full-stack video production platform built with a monorepo architecture.
It includes:
- `apps/web` – a Next.js frontend application
- `apps/worker` – a Node.js queue worker and optional Remotion renderer
- `apps/api` – a Python FastAPI backend service
- `packages/shared` – shared TypeScript code for the frontend/worker packages
- `infra` – SQL migration scripts for database and Supabase schema
- `supabase` – Supabase project configuration

## Project Overview

The repository is organized as a pnpm workspace with Turbo for task orchestration.
The main `package.json` exposes root-level developer commands.

### Core components
- `apps/web`: React / Next.js application using Supabase auth and the public API.
- `apps/worker`: background worker using BullMQ and Redis, optionally runs premium Remotion rendering.
- `apps/api`: FastAPI backend service for business logic, storage signing, billing, auth, and render orchestration.
- `infra/migrations`: database schema and migration SQL.
- `supabase`: Supabase project configuration files.

## Prerequisites

Before running locally, install:
- Node.js 20+
- pnpm 9+
- Python 3.12+
- Redis (for BullMQ queue)
- A Supabase database or PostgreSQL instance (recommended)
- Docker, if you want containerized deployment

## Setup

From the repository root (`motionly-main/Motionly-main`):

```bash
cd /workspaces/Motionly-claude/motionly-main/Motionly-main
pnpm install
```

Copy environment variables:

```bash
cp .env.example .env
```

Then update `.env` with your values:
- `NEXT_PUBLIC_APP_URL`
- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_JWT_SECRET`
- `SUPABASE_PROJECT_REF`
- `REDIS_URL`
- `ADMIN_API_TOKEN`
- Any provider API keys such as `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`

Optional deployment and render configuration:
- `BILLING_ENABLED` / `PERSONAL_USE_MODE`
- `REMOTION_ENABLED=true` to enable premium rendering
- `RENDER_TEMP_DIR` and `MEDIA_WORK_DIR` for local media storage

## Local Development

### Start the frontend

```bash
cd apps/web
pnpm dev
```

The frontend runs on `http://localhost:3000` by default.

### Start the worker

```bash
cd apps/worker
pnpm dev
```

The worker is responsible for queue processing and render tasks.

### Start the API

The API is a Python FastAPI service.

```bash
cd apps/api
python -m pip install --upgrade pip
python -m pip install -e .
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.

### Turbo monorepo commands

The workspace root includes shared scripts for common tasks:

```bash
pnpm dev       # Run development mode for the monorepo
pnpm build     # Build packages and apps
pnpm lint      # Run lint across workspace
pnpm typecheck # Run TypeScript type checks
pnpm test      # Run tests (workspace-aware)
```

> Note: `apps/api` is a Python service and may not be fully covered by `pnpm dev`. Run it separately for local Python API development.

## Build

### Build the web app

```bash
cd apps/web
pnpm build
```

### Build the worker

```bash
cd apps/worker
pnpm build
```

### Build the API container

From the repository root:

```bash
docker build -f apps/api/Dockerfile -t motionly-api .
```

### Build the worker container

From the repository root:

```bash
docker build -f apps/worker/Dockerfile -t motionly-worker .
```

## Deployment

### Deploy Web

The web app can be deployed like any Next.js application.
After building:

```bash
cd apps/web
pnpm start
```

Or deploy to a hosting provider that supports Next.js.

### Deploy API

Use the API Dockerfile if deploying in a container-aware environment:

```bash
docker run --env-file .env -p 8000:8000 motionly-api
```

### Deploy Worker

Use the worker Dockerfile for the queue and renderer:

```bash
docker run --env-file .env motionly-worker
```

If you enable `REMOTION_ENABLED=true`, the worker container will also support premium rendering via Chromium.

## Database and Supabase

The project expects a Supabase-compatible PostgreSQL backend.

- Apply SQL migrations from `infra/migrations`
- Configure Supabase credentials in `.env`
- Use `SUPABASE_SERVICE_ROLE_KEY` for server-side API access
- Use `SUPABASE_ANON_KEY` for frontend authentication

## Important Environment Variables

Shared runtime:
- `APP_ENV`
- `LOG_LEVEL`

Frontend runtime:
- `NEXT_PUBLIC_APP_URL`
- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

API runtime:
- `API_CORS_ORIGINS`
- `API_PUBLIC_BASE_URL`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_JWT_SECRET`

Queue and render:
- `REDIS_URL`
- `UPLOAD_STORAGE_BUCKET`
- `EXPORT_STORAGE_BUCKET`
- `JOB_ATTEMPTS`
- `WORKER_CONCURRENCY`
- `RENDER_WORKER_CONCURRENCY`
- `REMOTION_ENABLED`

AI provider settings:
- `TRANSCRIPTION_PROVIDER`
- `TRANSCRIPTION_MODEL`
- `OPENROUTER_API_KEY`
- `GROQ_API_KEY`
- `PEXELS_API_KEY`
- `PIXABAY_API_KEY`

## Notes

- `apps/worker` uses BullMQ and Redis for job scheduling.
- `apps/api` is a Python package using FastAPI and `uvicorn`.
- `apps/web` uses Next.js 16 and Supabase for auth and API integration.
- `packages/shared` holds shared TypeScript utilities used by the frontend and worker.

## Troubleshooting

- If the frontend cannot connect, verify `NEXT_PUBLIC_API_BASE_URL` and CORS origins.
- If the worker cannot enqueue jobs, verify `REDIS_URL` and `SUPABASE` credentials.
- If render jobs fail, set `REMOTION_ENABLED` and ensure Chromium is available in the worker image.

## Useful commands

```bash
pnpm install
pnpm dev
pnpm build
pnpm lint
pnpm typecheck
pnpm test
```

## License

This repository does not include a license file by default. Add a license if you intend to publish or distribute the project.
