# Source-Code Audit

Scope: this audit is derived only from executable/source files, manifests, tests, Docker/CI/runtime config, and migrations. I did not use README or other explanatory docs as evidence.

## 1. Executive summary

The codebase appears to be a multi-service video editing and publishing system: upload a source video, transcribe it, analyze visuals, generate an edit plan, render an export, and optionally publish it. That is an inference from the code paths in the web app, FastAPI API, worker processes, renderer, and Supabase schema.

What is real:

- There is a functional Next.js frontend, FastAPI backend, Redis/BullMQ job pipeline, Supabase-backed storage/auth/data layer, and separate worker images.
- Core flows exist in code for projects, uploads, transcription, captions, visual analysis, edit planning, render jobs, exports, billing, analytics, and publishing.
- There are tests for auth, uploads, rendering, Redis queue semantics, visual analysis, and schema/operational integrity.

Biggest blockers:

- Authentication bypass flags exist in runtime code and are not explicitly rejected by production validation.
- There are two migration trees (`supabase/migrations` and `infra/migrations`) with evidence of schema drift and repair migrations, which raises release-order risk.
- The system has multiple execution modes, including local in-process fallbacks and worker-only paths, so code existence does not guarantee the production path is wired end to end.
- The premium Remotion worker uploads rendered files by reading them fully into memory, which is a large-file reliability risk.

## 2. Project identity

Inferred product purpose:

- A collaborative video editing workspace for turning uploaded long-form footage into shorter edited clips with captions, b-roll, music, effects, and rendered exports.
- A publishing system for pushing finished videos to social platforms.

Likely users:

- Creators, editors, and operators managing projects and exports.
- Admin/ops users monitoring queues, costs, retention, and system health.

Likely use cases:

- Upload a source video, inspect transcript/captions, generate or modify an edit plan, render a clip, and download or publish it.
- Review AI-generated analysis and regenerate specific parts of the edit.
- Operate the pipeline, inspect failures, and retry/cancel work.

Evidence:

- [apps/web/app/(app)/projects/[projectId]/page.tsx](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/projects/[projectId]/page.tsx)
- [apps/web/components/editor-client.tsx](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-client.tsx)
- [apps/api/app/api/plans.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/plans.py)
- [apps/api/app/api/renders.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/renders.py)
- [apps/api/app/api/publishing.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/publishing.py)

## 3. Implementation status

| Feature | Status | Notes |
|---|---|---|
| Project CRUD | implemented | Project create/list/update/delete routes and UI exist. |
| Auth/session gating | partial | Supabase auth is wired, but dev/prod bypass flags exist. |
| Upload initiation/completion | implemented | Preflight, register, complete, cancel, and asset URL flows exist. |
| Transcript and captions | implemented | Transcription queueing and caption generation/editing are present. |
| Visual analysis | partial | API, worker, fixture, and local-ML providers exist; runtime depends on worker/provider configuration. |
| Edit plan generation | implemented | Planner, activation, snapshots, restore, timeline compilation, and style classification exist. |
| B-roll workflow | implemented | Library, slots, swapping, search, and generation routes exist. |
| Music workflow | implemented | Selection and region assignment exist. |
| Effects editing | implemented | Effects config API and UI wiring exist. |
| Render/export | implemented | Local FFmpeg render path and premium Remotion path both exist. |
| Publishing | partial | OAuth and publish job code exists, but end-to-end provider setup is environment-dependent. |
| Billing | partial | Stripe/customer portal/webhook code exists; operational status depends on env and deployment. |
| Analytics and ops | implemented | Metrics, cost attribution, worker heartbeat, and provider SLO code exist. |
| Admin controls | implemented | Admin ops, queue retry/cancel, and diagnostics routes exist. |
| Background jobs | implemented | BullMQ registry, workers, internal endpoints, retries, and repeat jobs exist. |
| Storage cleanup/retention | implemented | Retention jobs and deletion paths exist. |
| Multi-tenant security | partial | Membership checks and RLS tests exist, but bypass paths complicate trust. |
| Production readiness | critical defect | Auth bypass, schema drift, and memory-risk paths block a clean readiness claim. |

## 4. Architecture

- Frontend: Next.js app router with protected dashboard/project/admin routes, server actions, Supabase SSR auth, and a large client-side editor.
- Backend: FastAPI service with route groups for projects, uploads, captions, visual analysis, plans, b-roll, music, effects, regeneration, renders, publishing, billing, analytics, admin, and internal worker endpoints.
- Database: Supabase Postgres with schema-driven migrations and tests for RLS, job tables, storage metadata, billing, retention, and ops tables.
- Storage: Supabase object storage buckets for raw uploads and exports, plus local storage support for dev.
- Queues/workers: Redis/BullMQ plus multiple worker images for planning, transcription, visual analysis, audio, rendering, publishing, and maintenance.
- External providers: Supabase auth/storage/rest, Stripe, OpenRouter-style planner calls, Remotion, FFmpeg, Google OAuth, and social publishing providers.
- Runtime boundaries: the API sometimes executes in-process fallback jobs for local mode; production worker processes also call back into internal API endpoints and/or write directly to Supabase in some paths.

Evidence:

- [apps/api/app/main.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/main.py)
- [apps/worker/src/worker.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/worker.mjs)
- [infra/docker-compose.gpu.yml](/workspaces/Motionly-claude/motionly-main/Motionly-main/infra/docker-compose.gpu.yml)
- [supabase/config.toml](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/config.toml)

## 5. Tech stack

- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS, Framer Motion, dnd-kit, Sentry, Supabase SSR.
- Backend: Python FastAPI, Uvicorn, Pydantic, HTTPX, Redis, optional audio/visual ML dependencies, ffmpeg-python/OpenCV-style media tooling.
- Worker: Node.js 20, BullMQ, ioredis, Supabase JS, Remotion CLI/runtime, Python subprocess bridges for long-running jobs.
- Renderer: Remotion React renderer with Zod schemas and TypeScript.
- Infrastructure: Docker, Docker Compose, Caddy, Supabase stack, Redis, Postgres, GitHub Actions CI.
- Storage/auth/payments: Supabase Auth/Storage, Stripe, OAuth providers.

Evidence:

- [package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/package.json)
- [apps/web/package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/package.json)
- [apps/api/pyproject.toml](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/pyproject.toml)
- [apps/worker/package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/package.json)
- [apps/renderer/package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/renderer/package.json)

## 6. User/data flow

1. A user signs in through the web app and lands on dashboard/project routes.
2. The frontend fetches workspace-scoped API data with bearer auth and workspace headers.
3. The user creates or selects a project, then uploads a source video.
4. The API registers the upload, stores source media in object storage, and marks upload state.
5. Background jobs probe media, create thumbnails, transcribe audio, and optionally run visual analysis.
6. The edit planner builds a plan from transcript, visual context, and style classification.
7. The editor UI lets the user modify captions, cuts, b-roll, music, and effects.
8. A render job is created, then either a local FFmpeg path or a premium Remotion worker path produces an export.
9. The export is stored in Supabase storage and recorded in the database.
10. Optional publishing flows authenticate to providers and enqueue publish jobs.

This is a real end-to-end shape in code, but individual steps depend on the configured provider path and worker topology.

Evidence:

- [apps/web/lib/client-api.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/client-api.ts)
- [apps/api/app/api/uploads.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/uploads.py)
- [apps/api/app/services/transcription_jobs.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/transcription_jobs.py)
- [apps/api/app/services/visual_jobs.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/visual_jobs.py)
- [apps/api/app/api/plans.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/plans.py)
- [apps/api/app/api/renders.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/renders.py)
- [apps/worker/src/remotion.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/remotion.mjs)

## 7. API surface

Major endpoint groups:

- Projects: create/list/get/update/delete.
- Uploads: init, complete, cancel, active upload, upload metadata, asset URLs.
- Captions/transcripts: transcription create, transcript get, caption generate/get/update.
- Visual: status, analyze, manifest.
- Plans: create/list/snapshots/restore/activate/update cuts/highlights/manual elements/timeline/compile.
- B-roll: init/complete/list/delete/generate/slot/swap/search/region.
- Music: selection, tracks, regions.
- Effects: get/update effect config.
- Regeneration: regenerate project parts.
- Renders: create/bulk create/list/get/cancel/retry/events/download/restore.
- Publishing: OAuth start/callback, connections, publish jobs.
- Billing: status, checkout, customer portal, webhook.
- Analytics: AI edit analytics and fine-tuning dataset.
- Admin/ops/internal: queue management, worker health, retention, batch execution, internal render callbacks.

Auth requirements:

- Most workspace APIs require bearer auth and a workspace header through `get_actor()`.
- Admin routes require an admin identity allowlist.
- Billing webhook uses signature verification.
- Internal worker endpoints are intended for worker/local control-plane access, not public clients.

Evidence:

- [apps/api/app/api/dependencies.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/dependencies.py)
- [apps/api/app/api/admin.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/admin.py)
- [apps/api/app/api/internal_jobs.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/internal_jobs.py)
- [apps/api/app/api/internal_renders.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/internal_renders.py)
- [apps/api/app/api/billing.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/billing.py)

## 8. Data model

Main entities found in migrations/tests/code:

- projects
- uploads / upload assets / upload status
- transcripts
- captions
- edit plans / snapshots / timelines
- b-roll assets and slots
- music tracks / selections / regions
- effects configs
- render jobs / render job events / exports
- publish jobs / publish connections
- billing/customer records
- analytics and cost attribution records
- retention runs
- worker heartbeats / provider SLO tables
- storage deletion queue and related cleanup state

Storage buckets/paths:

- `raw-uploads` for source media.
- `exports` for rendered output.
- Local object storage proxy paths exist for dev.

Security/RLS:

- Supabase RLS policies are tested for membership-based access and service-role bypass behavior.
- Workspace membership is enforced in app code via Supabase-backed membership lookup.

Schema drift:

- There are two migration trees: [supabase/migrations](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/migrations) and [infra/migrations](/workspaces/Motionly-claude/motionly-main/Motionly-main/infra/migrations).
- The code and migrations show enum repair history for edit plan styles and render job status shapes, which indicates prior drift.
- The exact canonical migration source is not provable from code alone.

Evidence:

- [supabase/config.toml](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/config.toml)
- [supabase/tests/phase_0_schema_test.sql](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/tests/phase_0_schema_test.sql)
- [supabase/tests/phase_9_rls_test.sql](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/tests/phase_9_rls_test.sql)
- [packages/shared/src/index.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/packages/shared/src/index.ts)
- [apps/api/app/schemas/plans.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/schemas/plans.py)

## 9. Auth and security

- Web auth uses Supabase SSR and a proxy middleware that protects dashboard, project, billing, and account routes.
- API auth validates Supabase JWTs or, if enabled, falls back to local mock auth / header-based actor identity.
- Workspace authorization is enforced by membership lookup against Supabase.
- Admin access is gated by an allowlist check.
- Signed storage URLs are generated for upload/download access.
- OAuth-based publishing and Google login are supported by code paths and env checks.

Risks:

- `SKIP_AUTH_MIDDLEWARE` and `SKIP_API_AUTH` can bypass normal auth paths.
- Local mock identities and header-driven actor identity are dangerous if accidentally enabled in shared environments.
- Production validation checks many env vars but does not clearly hard-fail on the bypass flags.
- Worker paths sometimes write directly to Supabase with service-role credentials, which is normal for internal jobs but high-risk if exposed.
- Private media and OAuth tokens exist in the system; cleanup, revocation, and secret handling depend on deployment hygiene.

Evidence:

- [apps/web/proxy.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/proxy.ts)
- [apps/web/lib/api-auth.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/api-auth.ts)
- [apps/api/app/services/auth.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/auth.py)
- [apps/api/app/core/config.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/core/config.py)
- [apps/api/app/services/storage.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/storage.py)

## 10. Queues and background jobs

Job creation:

- The API registers queue names for preprocess, transcription, visual analysis, planning, b-roll, render, publish, scale, music, effects, regeneration, and maintenance.
- Render and analysis routes enqueue work via the queue registry.

Execution:

- BullMQ workers consume jobs and dispatch by job kind.
- Some jobs run in Python subprocess executors launched from the Node worker.
- Some local/dev paths can execute via in-process or internal endpoints instead of dedicated workers.

Retries/cancellation/idempotency:

- Queue helpers support retry, cancel, repeat jobs, and dead-letter handling.
- BullMQ job IDs are used for deduplication/idempotency in the queue layer.
- Render completion/failure paths are guarded in the internal worker callback code.

Where work really runs:

- In production, the design expects work to run in separate worker containers.
- The API also contains internal job endpoints and local fallbacks, so not every code path is truly isolated to workers.

Evidence:

- [apps/api/app/services/queue.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/queue.py)
- [apps/worker/src/queue-common.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/queue-common.mjs)
- [apps/worker/src/queue-bridge.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/queue-bridge.mjs)
- [apps/worker/src/worker.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/worker.mjs)
- [apps/api/app/api/internal_jobs.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/internal_jobs.py)

## 11. Reliability, performance, and scaling

- The premium Remotion path reads the exported file into memory before upload, which is a large-file OOM risk.
- The system has multiple subprocess and media-processing paths, so ffmpeg/ML jobs can be CPU, GPU, and disk heavy.
- Local/proxy storage flows can hide object-storage failures if not exercised in production mode.
- There is cleanup/retention code, but it depends on job scheduling and environment wiring.
- API, queue, and worker separation is good for scale, but the current code still contains fallback paths that complicate observability and failure isolation.
- Some provider integrations are best-effort or fixture-backed, which reduces certainty about production latency and error behavior.

Evidence:

- [apps/worker/src/remotion.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/remotion.mjs)
- [apps/api/app/services/preprocessing.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/preprocessing.py)
- [apps/api/app/services/transcription_jobs.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/transcription_jobs.py)
- [apps/api/app/services/retention.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/retention.py)
- [apps/api/app/api/health.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/health.py)

## 12. Deployment steps

Practical deployment procedure derived from manifests, Dockerfiles, compose, and scripts:

1. Provision required services: Postgres/Supabase, Redis, object storage, web, API, and the worker fleet.
2. Set service-specific env vars:
   - Web: Supabase URL/keys, auth-related envs, API base URL, Google OAuth envs if used.
   - API: Supabase URL/JWT secret/service role key, Redis URL, admin token, storage signing vars, provider keys, billing keys, render/ML flags, and worker-facing URLs.
   - Worker: Redis URL, Supabase URL/service role key, FFmpeg/Chromium/Remotion paths, ML enablement flags, and provider credentials.
   - Database/infra: Postgres credentials, storage credentials, and any Supabase gateway/auth config.
3. Build the apps using the repo scripts in [package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/package.json) and per-package manifests.
4. Apply database migrations before starting app traffic.
   - Caution: the repo contains both [supabase/migrations](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/migrations) and [infra/migrations](/workspaces/Motionly-claude/motionly-main/Motionly-main/infra/migrations); pick one canonical source and keep the other in sync.
5. Create storage buckets and policies for `raw-uploads` and `exports`.
6. Start Redis and the queue consumers.
7. Start the API, then the worker fleet, then the web app.
8. Configure external providers:
   - Stripe for billing.
   - OAuth providers for publishing and sign-in.
   - Supabase auth/storage/rest.
   - Any planner/ML provider keys used by the edit/visual stack.
9. Run smoke tests:
   - auth login/callback,
   - project create/list,
   - upload init/complete,
   - transcript/caption fetch,
   - plan create,
   - render create,
   - queue status/heartbeat,
   - export download URL,
   - admin/ops page if allowed.
10. Roll out in this order:
   - migrations,
   - infra/services,
   - API,
   - workers,
   - web,
   - external webhook registration.
11. Rollback/recovery:
   - revert app images first,
   - keep DB migrations forward-only,
   - restore from backups using the provided dump/restore verification scripts,
   - drain or reset problematic queues.
12. Production hardening checklist:
   - disable all auth bypass flags,
   - verify RLS and service-role boundaries,
   - confirm storage signing and cleanup,
   - validate webhook signatures,
   - confirm worker health and retention jobs,
   - confirm no local/mock providers are enabled,
   - check memory headroom for render uploads.

Deployment-readiness rating: not ready for an unguarded production launch yet.

Evidence:

- [apps/api/Dockerfile](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/Dockerfile)
- [apps/worker/Dockerfile](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/Dockerfile)
- [apps/worker/Dockerfile.gpu](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/Dockerfile.gpu)
- [infra/docker-compose.gpu.yml](/workspaces/Motionly-claude/motionly-main/Motionly-main/infra/docker-compose.gpu.yml)
- [scripts/check_forward_migrations.sh](/workspaces/Motionly-claude/motionly-main/Motionly-main/scripts/check_forward_migrations.sh)
- [scripts/verify_backup_restore.sh](/workspaces/Motionly-claude/motionly-main/Motionly-main/scripts/verify_backup_restore.sh)

## 13. Testing quality

What exists:

- API tests for admin controls, uploads, visual analysis caching/versioning, security behavior, integrity checks, and a golden media pipeline.
- Worker tests for routing, Redis contract semantics, and control-plane isolation.
- Renderer tests for timeline/time conversion and output encoding.
- Web tests for admin auth and editor flow behavior.
- CI runs linting, type checking, tests, Supabase database checks, Redis contract tests, browser journey tests, and prelaunch gates.

What these tests prove:

- Core request/queue/auth mechanics are covered.
- Some media/render paths are exercised with golden tests.
- RLS and job-contract assumptions are tested.

What is missing:

- Strong proof that external providers work in production credentials and network conditions.
- Full end-to-end proof for GPU/Remotion/prod publisher/billing flows.
- Explicit negative tests ensuring auth-bypass flags are rejected in production.
- Load/failure injection for large renders, queue backlogs, and provider outages.

Evidence:

- [apps/api/tests](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/tests)
- [apps/worker/tests](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/tests)
- [apps/renderer/tests](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/renderer/tests)
- [apps/web/tests](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/tests)
- [.github/workflows/phase-0-safety.yml](/workspaces/Motionly-claude/motionly-main/Motionly-main/.github/workflows/phase-0-safety.yml)

## 14. Dead code, duplication, placeholders, and drift

- Duplicate migration history exists in two trees, which is a structural drift risk.
- The admin ops UI contains at least one explicit placeholder label for cost tracking even though backend cost code exists.
- There are fixture/local-only providers alongside worker-only providers and real providers, which makes feature names and runtime behavior easy to misread.
- Some code paths exist only for local or internal execution and are not public API features.
- Style enum history shows legacy and canonical values coexisting, which is a sign of repair/migration drift rather than a clean single-source schema.

Evidence:

- [apps/web/app/(app)/admin/ops/page.tsx](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/app/(app)/admin/ops/page.tsx)
- [apps/api/app/services/visual_intelligence.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/visual_intelligence.py)
- [packages/shared/src/index.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/packages/shared/src/index.ts)
- [supabase/migrations](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/migrations)
- [infra/migrations](/workspaces/Motionly-claude/motionly-main/Motionly-main/infra/migrations)

## 15. Prioritized risk register

### P0

- Auth bypass flags can disable security if misconfigured.
  - Impact: full workspace/auth compromise in a deployed environment if bypass envs are set.
  - Evidence: [apps/web/proxy.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/proxy.ts), [apps/web/lib/api-auth.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/api-auth.ts), [apps/api/app/services/auth.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/auth.py), [apps/api/app/core/config.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/core/config.py)
  - Required fix: hard fail in production for any bypass flag; make local mock auth impossible to enable in deploy configs.

### P1

- Schema drift / dual migration trees.
  - Impact: release breakage, enum/status mismatch, and uncertain deploy order.
  - Evidence: [supabase/migrations](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/migrations), [infra/migrations](/workspaces/Motionly-claude/motionly-main/Motionly-main/infra/migrations), [supabase/tests/phase_0_schema_test.sql](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/tests/phase_0_schema_test.sql)
  - Required fix: choose one canonical migration source, archive or mirror the other, and enforce a single forward-only pipeline.
- Premium Remotion export upload reads whole files into memory.
  - Impact: OOM risk on large renders, worker crashes, and retry storms.
  - Evidence: [apps/worker/src/remotion.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/remotion.mjs)
  - Required fix: stream uploads instead of buffering entire exports.
- Mixed in-process/local/worker execution paths.
  - Impact: production behavior can differ from tested worker behavior.
  - Evidence: [apps/api/app/api/internal_jobs.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/internal_jobs.py), [apps/api/app/services/queue.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/queue.py)
  - Required fix: eliminate fallback execution in production and make worker boundaries explicit.

### P2

- Placeholder/admin UX text can mislead operators.
- External provider reliability is under-proven from code alone.
- Some cleanup and retention behavior depends on scheduled execution and environment wiring.

## 16. Recommended technical direction

- Consolidate the platform into one canonical migration history and one canonical job execution model.
- Remove or hard-disable all auth bypasses outside local dev.
- Make storage and render uploads stream-based end to end.
- Prefer explicit worker-only execution for heavy jobs; keep the API as a control plane, not a fallback runtime.
- Treat provider integrations as bounded adapters with contract tests and failure-mode tests.
- Make admin/ops and placeholder states visually and programmatically distinct from real production metrics.

## 17. Operational readiness checklist

- Confirm production env rejects `SKIP_API_AUTH`, `SKIP_AUTH_MIDDLEWARE`, and mock providers.
- Confirm `raw-uploads` and `exports` buckets exist with the expected policies.
- Confirm Redis, Postgres/Supabase, and worker pools are reachable.
- Confirm all required secrets are set through secure secret mechanisms, not plaintext env files.
- Confirm worker images include FFmpeg/Chromium/Remotion paths where needed.
- Confirm queue retries, cancellation, and dead-letter workflows are observable.
- Confirm billing and webhook signatures are validated.
- Confirm backup and restore procedures are tested against the active schema.
- Confirm retention jobs are scheduled and actually execute.
- Confirm large export sizes stay within worker memory headroom.

## 18. Evidence map

Important source paths used for this audit:

- [package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/package.json)
- [apps/web/package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/package.json)
- [apps/api/pyproject.toml](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/pyproject.toml)
- [apps/worker/package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/package.json)
- [apps/renderer/package.json](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/renderer/package.json)
- [apps/api/app/main.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/main.py)
- [apps/web/proxy.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/proxy.ts)
- [apps/web/lib/api-auth.ts](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/api-auth.ts)
- [apps/web/components/editor-client.tsx](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-client.tsx)
- [apps/api/app/api/projects.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/projects.py)
- [apps/api/app/api/uploads.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/uploads.py)
- [apps/api/app/api/captions.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/captions.py)
- [apps/api/app/api/visual.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/visual.py)
- [apps/api/app/api/plans.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/plans.py)
- [apps/api/app/api/renders.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/renders.py)
- [apps/api/app/api/publishing.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/publishing.py)
- [apps/api/app/api/billing.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/billing.py)
- [apps/api/app/services/auth.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/auth.py)
- [apps/api/app/services/queue.py](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/queue.py)
- [apps/worker/src/worker.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/worker.mjs)
- [apps/worker/src/remotion.mjs](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/remotion.mjs)
- [supabase/config.toml](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/config.toml)
- [supabase/tests/phase_0_schema_test.sql](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/tests/phase_0_schema_test.sql)
- [supabase/tests/phase_9_rls_test.sql](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/tests/phase_9_rls_test.sql)
- [infra/docker-compose.gpu.yml](/workspaces/Motionly-claude/motionly-main/Motionly-main/infra/docker-compose.gpu.yml)
- [.github/workflows/phase-0-safety.yml](/workspaces/Motionly-claude/motionly-main/Motionly-main/.github/workflows/phase-0-safety.yml)

## 19. Final assessment

This is not a toy repo. The codebase contains a substantial, coherent product architecture with real UI, API, jobs, storage, and deployment artifacts. However, source evidence does not support a production-ready claim yet.

The main reasons are security bypass risk, schema/migration drift, runtime-path ambiguity, and a few reliability hazards in heavy media processing. The system looks close to a deployable platform, but it still needs consolidation and hardening before I would treat it as operationally safe.

## 20. Expanded analysis

This appendix gives a deeper, section-by-section pass over the same source evidence. It is intentionally more opinionated about blockers, probable failure modes, and where the code overstates what is actually wired end to end.

### 20.1 Executive summary, expanded

The repo is a real product system, not scaffolding. The web app, API, worker fleet, renderer, storage integration, and Supabase-backed schema all point at a concrete workflow: ingest a source video, transform it into an edited output, and optionally publish it. The main distinction is between code that exists and code that is proven to work under the production deployment path.

The biggest practical blocker is not one missing feature, but the combination of several cross-cutting risks:

- Authentication can be bypassed in code paths that are meant to simplify local development.
- Multiple schema histories and repair migrations imply the database shape has not been stable.
- Heavy media work is split across API fallbacks, queue workers, Python subprocesses, and direct Supabase writes, which makes production behavior sensitive to environment flags.
- The premium render upload path is not stream-safe.

The net result is that the codebase looks feature-complete at the API/UI level, but operational completeness is not yet demonstrated by source alone.

### 20.2 Project identity, expanded

Inferred purpose:

- Primary use: turn uploaded videos into edited social-ready clips with AI-assisted structure.
- Secondary use: manage teams, billing, and job operations around that content pipeline.
- Tertiary use: publish finished assets to connected external platforms.

What the code suggests about users:

- Creators or editors use the dashboard and project editor.
- Operators use the admin/ops pages, queue tools, and health endpoints.
- The presence of billing, publishing, and analytics indicates the system is intended for multi-workspace SaaS use, not just a single-user local app.

What is not proven:

- A complete creator journey in production with real credentials and real media through every provider.
- That every named UI feature maps to a stable backend implementation rather than a partial or local-only code path.

### 20.3 Implementation status, expanded

The overall implementation pattern is uneven. The most mature areas are project CRUD, uploads, captions, plans, and renders, because those have clear route definitions, state models, and UI wiring. The least trustworthy areas are those that depend on environment-specific providers or background workers.

Feature-level nuance:

- Upload and transcription are implemented, but they depend on media probing, storage access, and queue execution.
- Visual analysis is implemented as an API surface, but its runtime can switch between fixture, worker-only, and local-ML providers.
- Publishing exists, but provider setup and OAuth configuration determine whether it actually operates.
- Billing exists, but the source evidence is mostly wiring and webhook handling rather than full lifecycle validation.
- Admin/ops exists, but some UI text and operational flows still signal placeholder status.

The main pattern is: control plane code is generally present; production execution confidence is much lower.

### 20.4 Architecture, expanded

The architecture is service-oriented but not cleanly isolated:

- The Next.js web app is the human-facing control plane.
- FastAPI is the API/control plane and also hosts some internal job endpoints.
- BullMQ/Redis is the queue system, but not every execution path stays in workers.
- Supabase is used for auth, database, storage, and some direct service-role operations.
- The worker fleet is heterogeneous: Node workers orchestrate jobs, while Python subprocesses perform CPU/ML/media work.

The key architectural tension is between explicit boundaries and escape hatches. There are enough escape hatches for local development and internal execution that production separation can be accidentally weakened if configuration is wrong.

### 20.5 Tech stack, expanded

The stack is broad and therefore operationally expensive:

- Frontend stack complexity comes from Next.js app-router behavior, server actions, client-side editor state, Supabase SSR, and animation-heavy UI pieces.
- Backend complexity comes from FastAPI, Redis, Supabase REST/service-role access, provider adapters, and media-processing helpers.
- Worker complexity comes from BullMQ, Node process orchestration, Python media/ML executors, ffmpeg, Chromium, and Remotion.

That breadth is not inherently bad, but it raises the cost of deployment and debugging. A regression may originate in the web layer, the API, the queue layer, the worker image, or a provider credential problem.

### 20.6 User/data flow, expanded

The expected journey is clear:

1. Authenticate.
2. Create/select a project.
3. Upload source media.
4. Let preprocessing/transcription/visual analysis run.
5. Generate or adjust a plan.
6. Render a result.
7. Export or publish it.

What makes this fragile is the number of required state transitions. Each transition depends on a previous state being exactly right:

- Upload must complete before preprocessing can succeed.
- Transcript must be ready before plan generation.
- Some render paths need plan or transcript data plus active upload context.
- Premium render execution requires the correct worker and storage configuration.

In practice, these dependencies mean one broken step can make the whole user journey look partially functional but actually stall.

### 20.7 API surface, expanded

The API is wide and mostly coherent, but its surface area tells you where production risk lives:

- Workspace APIs need consistent auth and membership checks.
- Internal APIs are operationally sensitive because they can execute control-plane logic.
- Render, billing, and publishing endpoints expose user-impacting side effects.
- Admin routes expose queue and job controls that can amplify mistakes if access control fails.

The biggest API concern is not missing routes. It is the coexistence of public routes, admin routes, and worker/internal routes inside the same service with multiple authorization modes.

### 20.8 Data model, expanded

The data model supports a full media workflow, but the schema history suggests it was assembled and repaired across phases:

- Entities for uploads, transcripts, plans, renders, exports, and publish jobs line up with the user flow.
- Ops tables for heartbeats, SLOs, retention, and queue state show the platform is meant to be run, not just used.
- Storage buckets for source media and exports fit the product shape.

The main data-model problem is drift. The evidence shows enum evolution and repair migrations, which means code, tests, and schema may have been reconciled after inconsistencies rather than designed from a single stable contract.

### 20.9 Auth and security, expanded

This is the highest-risk area.

Probable blockers:

- Development bypass flags are implemented in the same codebase as production auth.
- Local/mock identities can be returned when the wrong environment flags are present.
- Some API paths can rely on header-provided workspace/user IDs when JWT configuration is absent in non-production.
- Worker and service-role operations are powerful enough that a misrouted request can become a data access problem.

The security model is not absent, but it is too permissive for comfort until bypasses are made impossible in production images and deploy manifests.

### 20.10 Queues and background jobs, expanded

The queue system is a real part of the platform, not decorative code. It is also a major source of failure modes:

- Job deduplication depends on BullMQ semantics and job IDs.
- Some jobs are expected to run as repeat/daily maintenance tasks.
- Worker dispatch spans multiple job kinds, so a bug in routing can break a whole class of tasks.
- The API can participate in job execution in local mode, which makes prod-vs-dev behavior diverge.

Probable problems:

- Job retries may mask repeated upstream provider failures if idempotency is incomplete.
- Cancel/retry semantics can leave partially persisted artifacts if cleanup is not uniform.
- Direct writes from workers into Supabase create a second mutation channel outside the API transaction path.

### 20.11 Reliability, performance, and scaling, expanded

The system can scale in principle because it separates control plane and workers, but several implementation choices fight that goal:

- Media work is inherently heavyweight.
- Some paths spawn subprocesses and read/write temp files.
- The premium render upload path buffers whole files.
- A lot of runtime behavior depends on service-level environment flags rather than purely on code shape.

Probable scaling bottlenecks:

- Large exports may trigger worker memory pressure.
- Rendering and ML jobs may compete for CPU, disk, and GPU resources.
- Provider latency or transient failures can cascade into queue backlog.

### 20.12 Deployment steps, expanded

A realistic deployment sequence is more constrained than a normal web app:

- Database and storage shape must exist before traffic.
- Redis and worker pools must be live before job-heavy features work.
- API and web deploys need to agree on auth and workspace headers.
- Billing, OAuth, and provider secrets need to be configured before their UI controls are meaningful.

What makes deployment risky is that the system has multiple active subsystems that can appear healthy independently while the end-to-end user journey still fails. The smoke tests therefore need to cover auth, upload, transcript readiness, plan generation, render creation, export retrieval, and admin/ops visibility.

### 20.13 Testing quality, expanded

The test suite is better than average for a system of this shape. It covers:

- Security edges.
- Upload validation.
- Queue and worker contracts.
- Render/media golden paths.
- RLS and operational schema checks.

The gap is depth across real providers and real deployment modes. The tests show that the team knows what can break, but they do not fully prove that production credentials, external services, and large-scale workloads all behave correctly together.

### 20.14 Dead code, duplication, placeholders, and drift, expanded

This area is not just cleanup work. It has operational significance.

Examples of drift-like signals:

- Two migration trees exist.
- Legacy and canonical style values coexist.
- The admin UI contains placeholder wording in at least one spot.
- Local fixture providers remain alongside production providers.

Those are signs that the codebase has been through several iterations and still carries old assumptions forward. In a media pipeline, stale assumptions often become broken jobs or incorrect UI state rather than obvious compile errors.

### 20.15 Prioritized risk register, expanded

Additional probable blockers beyond the earlier P0/P1 list:

- Internal worker endpoints can become an accidental public dependency if routed incorrectly.
- If the service-role key or Supabase config is wrong, membership and storage access can fail in ways that look like product bugs.
- Queue backlogs can hide provider failures until user-facing latency becomes unacceptable.
- Retention or cleanup jobs failing quietly can inflate storage and create inconsistent state.

The highest-leverage fixes are still the same: eliminate bypasses, consolidate migrations, and make heavy work strictly worker-owned.

### 20.16 Recommended technical direction, expanded

The architectural direction should be:

- One schema history.
- One production auth model.
- One worker-owned path for heavy jobs.
- Streaming uploads for large artifacts.
- Explicit provider adapters with hard failure behavior.

If those are in place, the remaining work becomes ordinary hardening instead of structural uncertainty.

### 20.17 Operational readiness checklist, expanded

Beyond the earlier checklist, the operational team should verify:

- Production images cannot start with mock auth or fixture providers enabled.
- Redis job queues are visible and alerting.
- Render memory consumption stays bounded under worst-case files.
- Retention and backup jobs are observable and tested.
- Admin actions are logged and permission-gated.
- Supabase and storage credentials rotate cleanly without breaking the app.

### 20.18 Evidence map, expanded

The most important source folders for understanding the system are:

- [apps/web](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web)
- [apps/api](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api)
- [apps/worker](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker)
- [apps/renderer](/workspaces/Motionly-claude/motionly-main/Motionly-main/apps/renderer)
- [supabase](/workspaces/Motionly-claude/motionly-main/Motionly-main/supabase)
- [infra](/workspaces/Motionly-claude/motionly-main/Motionly-main/infra)
- [.github/workflows/phase-0-safety.yml](/workspaces/Motionly-claude/motionly-main/Motionly-main/.github/workflows/phase-0-safety.yml)

Those are the subsystems you would inspect first when diagnosing production failures.

### 20.19 Final assessment, expanded

The codebase is materially real and closer to a product than a prototype. It already includes substantial functionality, tests, and deployment artifacts. The reason I still would not call it production-ready is that the source shows too many ways for runtime behavior to diverge from the intended secure, worker-driven, forward-migrated model.

In short: the product shape is real, the implementation breadth is real, but the operational guarantees are not yet strong enough to treat the system as stable without further consolidation and hardening.
