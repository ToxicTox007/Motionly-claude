# Motionly Production Hardening & Antigravity Remediation Log

## Phase 1 / Task 1.1 — Kill Auth Bypass Flags

- Date: 2026-06-24
- Executor: Antigravity / Implementer
- Files touched:
  - [config.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/core/config.py#L141-L151)
  - [auth.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/auth.py#L27-L38)
  - [env.ts](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/env.ts#L44-L54)
  - [test_config.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/tests/test_config.py)
  - [config.test.mjs](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/tests/config.test.mjs)
- Audit finding resolved: Production auth bypass / workspace compromise via SKIP_AUTH_MIDDLEWARE, SKIP_API_AUTH, or mock tokens.
- What changed: Added Pydantic config validation in API to block startup if APP_ENV is production and SKIP_API_AUTH is enabled. Hardened verify_supabase_jwt as defense-in-depth to verify settings environment. Added env assertions in Next.js web application to throw errors if SKIP_AUTH_MIDDLEWARE is enabled in production.
- Verification performed: `pytest tests/test_config.py` and `node --test tests/config.test.mjs`
- Verification output / evidence:
  ```
  tests/test_config.py .........                                           [100%]
  ========================= 9 passed, 1 warning in 1.75s =========================
  
  ✔ production mode + SKIP_AUTH_MIDDLEWARE=true fails validation (111.738407ms)
  ✔ production mode + NEXT_PUBLIC_SKIP_AUTH_MIDDLEWARE=true fails validation (155.95195ms)
  ✔ production mode + normal auth configuration passes validation (109.589405ms)
  ✔ local/development mode with bypass enabled passes validation (115.946069ms)
  ℹ tests 4
  ℹ suites 0
  ℹ pass 4
  ℹ fail 0
  ℹ cancelled 0
  ℹ skipped 0
  ℹ todo 0
  ℹ duration_ms 609.453959
  ```
- Remaining caveats: None. Unused auth bypass flags referenced in plan (MOCK_AUTH, LOCAL_AUTH) are confirmed absent.

## Phase 2 / Task 2.1 — Async Planner Fast Path (Backend)

- Date: 2026-06-24
- Executor: Antigravity / Main Agent / Implementer
- Files touched:
  - [apps/api/app/api/plans.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/plans.py#L68-L270)
  - [apps/api/app/api/internal_jobs.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/internal_jobs.py#L274-L510)
  - [apps/api/app/schemas/plans.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/schemas/plans.py#L283-L297)
  - [apps/api/app/services/queue.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/queue.py#L560-L572)
  - [apps/api/tests/test_plans.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/tests/test_plans.py#L56-L113)
- Audit finding resolved: AI/intelligence audit flags synchronous OpenRouter edit-planner calls as a P0 thread-blocking risk.
- What changed: Converted `POST /plans` cache-miss route to serialize request options and enqueue them via `PlanQueue`. Created `GET /plans/jobs/{job_id}` status polling endpoint querying BullMQ and DB fallback states. Implemented the `"edit_plan"` job execution handler in `internal_jobs._execute_job` to run planner provider synchronously within the background worker process. Added unit tests covering async enqueuing, thread isolation, and job failure/success mapping.
- Verification performed:
  `pytest apps/api/tests/test_plans.py`
  `pytest apps/api/tests/test_internal_jobs.py`
  `pytest apps/api/tests/test_queue_backends.py`
- Verification output / evidence:
  ```
  apps/api/tests/test_plans.py ...........................                 [100%]
  ======================== 27 passed, 1 warning in 0.95s =========================
  apps/api/tests/test_internal_jobs.py .....                               [ 62%]
  apps/api/tests/test_queue_backends.py ...                                [100%]
  ========================= 8 passed, 1 warning in 0.84s =========================
  ```
- Remaining caveats: None. Frontend polling implementation has been completed and verified.

## Phase 2 / Task 2.4 — Async Planner Fast Path (Frontend)

- Date: 2026-06-24
- Executor: Antigravity / Main Agent / Implementer
- Files touched:
  - [apps/web/lib/client-api.ts](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/lib/client-api.ts#L143-L185)
  - [apps/web/components/editor/ai-step.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ai-step.tsx#L169-L225)
  - [apps/web/components/editor-client.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor-client.tsx#L662-L699)
  - [apps/web/components/editor/ScalePanel.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/ScalePanel.tsx#L160-L225)
  - [apps/web/tests/editor-polling.test.mjs](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/tests/editor-polling.test.mjs#L1-L49)
- Audit finding resolved: Client-side components block UI state or fail when `/plans` API responds with `202 Accepted` and status `queued` instead of immediate synchronous generation.
- What changed: Added `pollPlanJob` helper in `client-api.ts` to poll enqueued jobs. Integrated polling inside the prompt step (`ai-step.tsx`), editor timeline smart action (`editor-client.tsx`), template applications, and reference style analysis buttons in the Scale panel (`ScalePanel.tsx`). On success, the frontend loads the updated plans list and retrieves the corresponding plan payload.
- Verification performed:
  `npm run test` in `apps/web` (includes static analysis validation tests in `editor-polling.test.mjs` checking all enqueued polling calls).
  `npm run typecheck` to verify TypeScript compile checks.
- Verification output / evidence:
  ```
  ✔ rejects every authenticated identity when no admin allowlist is configured (0.948319ms)
  ✔ rejects an authenticated non-admin even when another identity is allowed (0.345303ms)
  ✔ accepts an explicitly allowed user (0.162221ms)
  ✔ accepts an explicitly allowed workspace (0.124325ms)
  ✔ processing screen refreshes after a direct page load (5.449046ms)
  ✔ undo and redo are serialized per workspace and project (7.120379ms)
  ✔ saved templates include and apply caption and effects settings (3.986913ms)
  ✔ ai-step.tsx implements enqueued async plan polling correctly (7.602115ms)
  ✔ editor-client.tsx implements enqueued async plan polling correctly (2.451333ms)
  ✔ ScalePanel.tsx template and style applications implement enqueued async plan polling correctly (4.568488ms)
  ✔ editor labels describe the implemented behavior (8.767997ms)
  ✔ shell, dashboard, and feedback use live application paths (5.87551ms)
  ✔ shared stock provider type includes Runway (2.917145ms)
  ✔ editor output playhead maps through removed source gaps (0.957312ms)
  ✔ clip-boundary end timing stays on the ending source clip (0.187538ms)
  ℹ tests 15
  ℹ suites 0
  ℹ pass 15
  ℹ fail 0
  ℹ cancelled 0
  ℹ skipped 0
  ℹ todo 0
  ℹ duration_ms 726.654432
  ```
- Remaining caveats: None.

## Phase 3 / Task 3.1 — Stream Render Uploads

- Date: 2026-06-24
- Executor: Antigravity / Main Agent / Implementer
- Files touched:
  - [apps/worker/src/remotion.mjs](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/remotion.mjs#L19-L219)
  - [apps/worker/src/storage-streaming.test.mjs](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/storage-streaming.test.mjs)
- Audit finding resolved: Large Remotion exports must upload without loading the whole file into memory (preventing worker OOM on large videos).
- What changed: Imported `createReadStream` from `"node:fs"` and replaced buffer-based whole-file reading (`await readFile(localPath)`) inside `uploadToStorage` with streaming uploads via `createReadStream(localPath)`. Added static analysis code-contract test `storage-streaming.test.mjs` ensuring buffering/readFile isn't reintroduced to the upload path.
- Verification performed:
  `pnpm --filter @motionly/worker test`
- Verification output / evidence:
  ```
  ✔ workers execute media without the API job runner (14.934436ms)
  ﹣ real Redis preserves BullMQ retry, terminal failure, and job-id idempotency (0.773334ms) # set RUN_REDIS_CONTRACTS=true
  ✔ Remotion export upload uses streaming and avoids full-file buffering (5.216944ms)
  ✔ media work is dispatched directly and never through the API job runner (5.470348ms)
  ✔ standard worker defaults to the isolated Remotion queue (4.577479ms)
  ℹ tests 5
  ℹ suites 0
  ℹ pass 4
  ℹ fail 0
  ℹ cancelled 0
  ℹ skipped 1
  ℹ todo 0
  ℹ duration_ms 660.506807
  Worker queue config is not set; build check only.
  ```
- Remaining caveats: None. Supabase storage JS SDK is verified to support standard Node.js readable streams.

## Phase 4 / Task 4.5 — Real background segmentation (Option A)

- Date: 2026-06-28
- Executor: Cursor / Implementer
- Files touched:
  - [segmentation_matte.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/segmentation_matte.py)
  - [visual_intelligence.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/visual_intelligence.py#L267-L420)
  - [visual.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/schemas/visual.py#L80-L88)
  - [renders.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/api/renders.py#L627-L660)
  - [ffmpeg_render.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/ffmpeg_render.py#L365-L470)
  - [render_jobs.py](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api/app/services/render_jobs.py#L431-L470)
  - [VideoScene.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/renderer/src/components/VideoScene.tsx#L128-L310)
  - [remotion.mjs](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker/src/remotion.mjs#L340-L620)
  - [look-tab.tsx](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web/components/editor/tabs/look-tab.tsx#L1093-L1100)
- Audit finding resolved: Fake CSS ellipse / FFmpeg boxblur presented as background removal; no ML segmentation masks in render pipeline.
- What changed: Added MediaPipe Selfie Segmentation matte generation during GPU visual analysis, stored matte video in object storage, wired `segmentationMatte` into render props, composited real masks in Remotion (alpha-key pre-pass) and FFmpeg (`movie` + `alphamerge`), updated UI copy to “Subject segmentation”, and removed legacy ellipse fallback when a matte is present.
- Verification performed: `pytest tests/test_segmentation_matte.py tests/test_ffmpeg_render.py::test_ffmpeg_renderer_uses_segmentation_matte_instead_of_boxblur` and `node --test apps/web/tests/phase7-product-integrity.test.mjs`
- Verification output / evidence:
  ```
  tests/test_segmentation_matte.py .                                         [100%]
  tests/test_ffmpeg_render.py .                                              [100%]
  2 passed, 1 warning in 1.04s

  ✔ editor labels describe the implemented behavior
  ℹ tests 3 / pass 3
  ```
- Remaining caveats: MediaPipe Selfie Segmentation is a lightweight stopgap (best for centered speakers). B-roll still uses legacy ellipse when `apply_to_broll` is enabled. Matte retention is not yet tied to upload delete triggers. Remotion golden `segmentation_off_center` scenario still pending GPU worker validation in CI media-goldens job.

## Phase 5 — Consolidate Migration Trees

- Date: 2026-06-28
- Executor: Cursor / Implementer
- Canonical migration source: supabase/migrations
- Files touched:
  - [supabase/migrations/](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/supabase/migrations/) (replaced symlink with 40 real SQL files)
  - [infra/migrations_legacy/](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/infra/migrations_legacy/)
  - [infra/migrations_legacy/README.md](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/infra/migrations_legacy/README.md)
  - [scripts/check_forward_migrations.sh](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/scripts/check_forward_migrations.sh)
  - [infra/docker-compose.gpu.yml](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/infra/docker-compose.gpu.yml#L27)
  - [README.md](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/README.md)
- Migrations ported: None. Both trees were byte-identical (40/40 files). `supabase/migrations` had been a symlink to `../infra/migrations`; it is now a real directory with the same content.
- Non-canonical tree handling: `infra/migrations/` moved to `infra/migrations_legacy/` with README stating audit/history-only use; no new migrations may be added there.
- Scripts/CI updated:
  - `scripts/check_forward_migrations.sh` now guards `supabase/migrations` (forward-only) and fails on new `.sql` in `infra/migrations_legacy` or recreated `infra/migrations`.
  - `infra/docker-compose.gpu.yml` Postgres init mount updated to `../supabase/migrations`.
  - `README.md` database section updated to reference `supabase/migrations`.
  - `.github/workflows/phase-0-safety.yml` unchanged (already uses `supabase db reset --local` + `check_forward_migrations.sh`).
- Enum/status normalization: No backfill migration added. Legacy `running`/`succeeded` render statuses are mapped at read time in `apps/api/app/repositories/renders.py`; fresh bootstrap enum shape is established by existing repair migrations (`202606120004`, `202606220001`) and validated by `phase_0_schema_test.sql`.
- Verification performed:
  - `bash scripts/check_forward_migrations.sh origin/main`
  - `npx supabase@2.104.0 db reset --local`
  - `npx supabase@2.104.0 test db`
- Verification output / evidence:
  ```
  Migration history is forward-only.

  Finished supabase db reset on branch main.
  (40 migrations applied from supabase/migrations/)

  phase_0_schema_test.sql .. ok
  phase_9_rls_test.sql ..... ok
  All tests successful.
  Files=2, Tests=69, Result: PASS
  ```
- Remaining caveats: Migration filenames still carry duplicate phase labels (e.g. two `phase_4_*`, two `phase_5_*`) and `phase_0_schema_repair` runs late by timestamp; week_01 vs week_04 `render_job_status` enum definitions remain compensated only by downstream repair migrations. Audit docs (`PROJECT_CODE_AUDIT.md`, `source-audit.md`) still mention `infra/migrations` and were not updated in this phase. Staging deploy hook sends `migration_strategy: forward-only` but does not name the migration directory in-repo.

