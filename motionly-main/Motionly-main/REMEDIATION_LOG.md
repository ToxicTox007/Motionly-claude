# Motionly Production Hardening & Antigravity Remediation Log

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

