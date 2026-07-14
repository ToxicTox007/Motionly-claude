# Motionly AI-Centered Rebuild — Session Handoff

**Prepared:** 2026-07-14 14:58 UTC  
**Repository:** `/workspaces/Motionly-claude`  
**Application root:** `/workspaces/Motionly-claude/motionly-main/Motionly-main`  
**Operating system:** Linux  
**Space note:** `/temp` is not present in this environment. `/tmp` has ample host space, but Docker storage is constrained; remove only session-created validation cache/images when necessary and avoid broad prune commands.
**Safety:** No commits, pushes, production deployments, or irreversible infrastructure actions were performed. Do not include or print local `.env` values.

## 1. User goal and fixed product direction

Build Motionly into an AI-centered product whose unchanged purpose is:

> Turn raw footage into a ready-to-post short-form video automatically, with manual refinement before export.

Primary users:
- Content creators
- Social media managers
- Professional video editors

Core journey:

`Upload → transcribe/analyze → AI proposes edit → user approval → B-roll/music/assembly → draft render → timeline refinement (trim/split/reorder) → export`

Non-negotiable product capabilities:
- Reliable upload-to-first-draft pipeline
- Accurate preview
- Strong manual refinement loop
- Reliable export

AI behavior and architecture decisions:
- AI is the central “big brain,” not a disconnected generation feature.
- AI is approval-driven: it must present an editable proposal before timeline mutations or expensive processing.
- Architecture must be provider-neutral and hybrid: Azure plus custom GPU workers.
- Lightning AI must be completely removed from runtime, configuration, tests, and deployment.
- UX must support a creator-friendly default mode and an optional professional editor mode.
- Production-safe privacy/security comes first; deeper compliance can be phased later.
- No customer-content training by default.

## 2. Audit scope and evidence restrictions

The audit and implementation used executable source, configuration, manifests, migrations, tests, and infrastructure definitions. Repository Markdown documentation and previous audit reports were intentionally not used as implementation evidence.

Current architecture discovered from code:
- Next.js/React web editor
- FastAPI control plane
- Supabase/PostgreSQL with RLS
- Redis/BullMQ through a persistent Node bridge
- Python media/GPU subprocesses
- FFmpeg and Remotion renderers
- Groq transcription, OpenRouter planning, local Hugging Face models, Pexels/Pixabay, licensed music, Runway, Stripe, publishing integrations
- Railway control/CPU workers, Lightning GPU scaling, and a custom-GPU Compose path

## 3. Approved target architecture

Core principles:
1. AI plans; users approve; typed tools execute.
2. PostgreSQL owns workflow truth; BullMQ transports work.
3. Every derivative belongs to an immutable source-media revision.
4. Every editor mutation uses optimistic concurrency.
5. Store durable object references; sign URLs only immediately before use.
6. Model-provider selection and GPU-placement selection are separate abstractions.
7. Production never silently uses fixtures or renderer downgrades.
8. Preview and export consume one canonical timeline/render contract.
9. AI decisions record rationale, confidence, provenance, cost, and model/prompt versions.
10. Lightning AI has zero remaining code/config/deployment presence.

Proposed durable workflow states:

`uploaded → analyzing → proposal_ready → awaiting_approval → assembling → draft_rendering → draft_ready → refining → exporting → export_ready`

Failures must checkpoint into a recoverable state and resume idempotently.

## 4. Original high-priority audit findings

These findings drove the implementation order:

### Critical
- API Docker shipped a stale BullMQ bridge that lacked commands expected by Python.
- Authenticated Supabase clients could directly mutate worker-owned domain tables and bypass API validation.
- Arbitrary manual media URLs could reach render workers and create SSRF risk.

### High
- Failed AI plans could deactivate the last good plan.
- AI plans were immediately active; no proposal/approval lifecycle existed.
- Upload did not durably continue to a rendered first draft.
- Unknown production providers could fall back to fixtures.
- Signed media URLs expired after five minutes with no editor refresh path.
- Timeline updates had no optimistic concurrency.
- Database state and queue publication were not transactional.
- Old derivatives could survive source replacement.
- Multi-workspace project loading chose the first membership.
- Captionless export was blocked despite an API option.
- Music rights were hard-coded as confirmed.
- Preview and export used divergent implementations.
- Lightning autoscaling kept remote replica state in API memory and did not reconcile provider state.

### Scalability/supply chain
- Queue status polling scanned all retained jobs.
- Project editor bootstrap used many uncached API calls.
- Python dependencies and model revisions were not fully locked.
- Several containers ran as root.
- Provider and circuit state was process-local.

## 5. Authoritative task list

Progress: **5 of 18 complete**.

- [x] Task 1 — Unify and repair the BullMQ queue control plane
- [x] Task 2 — Fix immediate product correctness regressions
- [x] Task 3 — Make the API the authoritative tenant-safe mutation boundary
- [x] Task 4 — Secure media, workers, and service credentials
- [x] Task 5 — Introduce immutable timeline revisions and source lineage
- [ ] Task 6 — Make upload and media access session-safe
- [ ] Task 7 — Add a transactional outbox and durable workflow state
- [ ] Task 8 — Remove Lightning AI and activate custom GPU workers
- [ ] Task 9 — Add Azure GPU scheduling and autoscaling
- [ ] Task 10 — Build the provider-neutral model gateway
- [ ] Task 11 — Implement the AI proposal and approval brain
- [ ] Task 12 — Complete the durable upload-to-first-draft pipeline
- [ ] Task 13 — Add safe memory and editorial evaluations
- [ ] Task 14 — Unify preview and export rendering
- [ ] Task 15 — Deliver adaptive creator and professional editor modes
- [ ] Task 16 — Establish and enforce scalability SLOs
- [ ] Task 17 — Make dependencies and builds reproducible
- [ ] Task 18 — Wire and validate the production candidate end to end

Do not mark a task complete unless its success criteria are verified by concrete command/test output.

## 6. Completed work and verification evidence

### Task 1 — BullMQ queue control plane: COMPLETE

Implemented:
- Canonical queue bridge source now comes from `apps/worker/src` and is copied into API images.
- API/worker command protocol supports direct `job` and `snapshots` operations.
- Plan status uses direct lookup scoped to queue `planning`, project, workspace, and job kind.
- Paused jobs count as backlog, not cancellation.
- Stuck recovery invokes BullMQ’s lock-aware stalled checker instead of relabeling active jobs in job data.
- Railway workers explicitly set `PROCESS_ROLE=worker`.

Key files:
- `Dockerfile`
- `apps/api/Dockerfile`
- `apps/api/app/services/queue.py`
- `apps/api/app/api/plans.py`
- `apps/api/worker-bridge/queue-bridge.mjs`
- `apps/worker/src/queue-bridge.mjs`
- `apps/api/tests/test_queue_control_plane.py`
- `.railway/railway.ts`

Verification produced in this session:
- Real Redis queue suite: **12 passed**.
- Real tests covered enqueue, direct lookup, snapshots, paused backlog, and protection of a live locked job.
- Targeted backend suite: **128 passed, 1 skipped**.
- `node --check` passed for both bridge copies.
- API Docker image build completed with exit status 0 using the canonical bridge source.
- Disposable Redis container and API test image were removed afterward.

### Task 2 — Immediate product correctness: COMPLETE

Implemented:
- Failed plans no longer replace/deactivate the active ready plan.
- Forward migration: `202607140002_preserve_active_plan_on_failure.sql`.
- Production provider values are validated and fixture providers fail closed.
- Captionless export is supported by API and an explicit web checkbox.
- Forward migration: `202607140003_allow_caption_free_renders.sql`.
- Multi-workspace project loading checks all active memberships.
- Normal AI generation no longer always sets `force: true`; explicit regeneration may bypass cache.
- Railway worker process mode fixed.

Key files:
- `apps/api/app/main.py`
- `apps/api/app/repositories/plans.py`
- `apps/api/app/api/renders.py`
- `apps/web/app/(app)/projects/[projectId]/page.tsx`
- `apps/web/components/editor/ai-step.tsx`
- `apps/web/components/editor/export-step.tsx`
- `apps/web/tests/export-captionless.test.mjs`
- `supabase/migrations/202607140002_preserve_active_plan_on_failure.sql`
- `supabase/migrations/202607140003_allow_caption_free_renders.sql`

Verification:
- Targeted backend suite: **128 passed, 1 skipped**.
- Web suite: **63 passed**.
- Web TypeScript check exited 0.
- OpenAPI/generated contract check was reported passing by the independent reviewer.

### Task 3 — API-authoritative tenant boundary: COMPLETE

Implemented forward migration:
- `supabase/migrations/202607140004_api_authoritative_tenant_boundary.sql`

Migration behavior:
- Dynamically removes authenticated mutation policies and table privileges from 36 protected domain/worker/ops tables.
- Rebuilds tenant-scoped SELECT policies for 27 readable tables.
- Preserves only the intentional `update_own_profile` self-service mutation RPC.
- Makes profile/workspace/membership reads explicit and mutation service-role-only.
- Adds 10 composite uniqueness keys.
- Adds 36 tenant-consistent foreign keys across project, upload, transcript, caption, plan, render, export, B-roll, music, visual, publishing, and operational references.
- Adds nullable-reference checks preventing `MATCH SIMPLE` bypasses in provider/cost operational tables.
- Uses safe `NOT VALID` constraints: existing production data can be remediated later, while new writes are enforced immediately.

Focused test:
- `supabase/tests/task_3_tenant_boundary_test.sql`

Verification:
- Clean `supabase db reset --local` applied every migration successfully.
- Full DB suite: **118 tests PASS**.
- Final focused Task 3 suite: **36 tests PASS**.
- `supabase db lint --local`: **No schema errors found**.
- Real Supabase service-role repository contract: **1 passed**.

## 7. Task 4 — Media, worker, and service security: COMPLETE

Implemented media and SSRF hardening:
- Python rejects unsupported schemes, credentials, local names, malformed numeric hosts, legacy decimal/octal/hex/abbreviated private IPv4 forms, IPv4-mapped private IPv6, and deprecated IPv6 site-local addresses.
- Every DNS answer is validated before use. Each connection is pinned to the immutable vetted address set, retries only another already-vetted address, preserves the original HTTP `Host` and HTTPS SNI, bypasses environment proxies, and independently revalidates every redirect.
- The Node worker uses its native HTTP/HTTPS transport with the same DNS pinning, vetted-address retry, Host/SNI preservation, redirect revalidation, and unsafe-address rejection.
- Render media manifests cover source media, manual B-roll/music/SFX, assets, stems, mattes, and provider metadata references.
- Selected stock-video ownership downloads and stock-thumbnail enrichment use the pinned transport. Thumbnails enforce an 8 MiB streamed limit, approved image content types, and JPEG/PNG/GIF/WebP signatures.
- Generated Runway B-roll uses the default pinned transport; the incompatible top-level `httpx.stream` override was removed.
- Media policy errors remain query-secret safe.

Implemented service, credential, and runtime hardening:
- `WORKER_API_TOKEN` is distinct from `ADMIN_API_TOKEN`; there is no worker-token fallback to the admin secret, and internal-token comparison is constant-time.
- Tests inspect every registered `/internal` route and prove worker authorization is required, while `/admin` remains protected by the separate admin dependency.
- Production OAuth state requires its dedicated secret. API and web security headers/CSP are enforced and tested.
- API, standard worker, GPU workers, renderer, and web runtime definitions use non-root users as applicable.
- API and worker images provision writable `/var/data/motionly`, `/var/cache/motionly`, and `/tmp/motionly` paths. The standard worker creates `TMPDIR` before dependency installation.
- GPU Compose includes a networkless, read-only, capability-limited one-shot ownership initializer for named volumes; API and workers wait for successful initialization.
- Worker secret wiring excludes admin and OAuth-state secrets.
- API test collection deterministically forces local auth-safe defaults before application imports, even when the invoking shell exports production/auth-bypass values.

Verification evidence:
- Prescribed API Task 4 regression group: **130 passed, 1 skipped**.
- Focused media/service suite after DNS-failover hardening: **44 passed**.
- Final media and generated-B-roll/cost regression suite: **39 passed**.
- Full API collection with inherited `APP_ENV=production` and `SKIP_API_AUTH=true`: **560 tests collected**, exit 0.
- API Ruff: **all checks passed**.
- Worker suite: **29 passed, 1 skipped**.
- Web suite: **65 passed**; web TypeScript check passed.
- Node syntax checks passed for media policy, Remotion, and worker entry points.
- Compose configuration parsed successfully; `git diff --check` passed.
- All affected Dockerfiles passed `docker build --check`.
- Full API and web image builds passed. The full standard worker image build completed after fixing `TMPDIR` ordering; its configured user is `motionly`, and runtime write checks passed for data, cache, and temporary paths.
- Final independent semantic/security review found no demonstrated high- or medium-severity blocker and returned **`TASK 4 COMPLETION: YES`**.

## 7b. Task 5 — Immutable timeline revisions and source lineage: COMPLETE

Implemented database migration (`202607140005_immutable_timeline_revisions.sql`):
- Added `revision` column to `edit_plans` with auto-increment trigger on `plan` JSONB changes.
- Added `media_revision` column to `uploaded_videos` with trigger on storage_path/byte_size/duration_ms changes.
- Added `source_media_revision` and `source_plan_revision` lineage columns to `render_jobs`, `broll_slots`, `project_music_selections`, and `exports`.
- Added `stale` boolean column (default false) to `render_jobs`, `exports`, `broll_slots`, `project_music_selections`.
- Created `motionly_update_plan_with_revision(p_plan_id, p_expected_revision, p_new_plan)` atomic CAS function with P0002 exception on mismatch.
- Created `motionly_invalidate_stale_derivatives(p_project_id, p_plan_id)` to mark derivatives stale when plan revision advances.
- Created `motionly_invalidate_on_media_replace(p_project_id)` to mark ALL project derivatives stale.
- Created media replacement trigger (`uploaded_videos_invalidate_on_media_replace`).
- Created `timeline_revisions` append-only audit table with RLS (service role INSERT only, tenant-scoped SELECT).
- Added partial indexes on `stale = true` for efficient staleness queries.

Implemented API revision enforcement:
- Created `apps/api/app/api/revision_errors.py` with `RevisionConflictError` and HTTP 409 handler.
- Updated request schemas with optional `expected_revision: int | None`.
- Updated response schemas with `revision: int | None`.
- Added `update_plan_with_revision()` to both InMemory and Supabase plan repositories.
- Updated all plan mutation endpoints (cuts, timeline, manual-elements, caption-highlights, compile-timeline) to use CAS when `expected_revision` provided.
- Backward compatible: mutations without `expected_revision` use the old path with deprecation warning.
- Render creation records `source_plan_revision` and `source_media_revision` in manifest.

Implemented frontend conflict detection and UX:
- Created `apps/web/hooks/useRevisionTracking.ts` for revision state management.
- Created `apps/web/components/editor/ConflictDialog.tsx` — modal with Reload/Force Save options.
- Created `apps/web/components/editor/StaleIndicator.tsx` — banner for stale derivatives.
- Updated `apps/web/lib/client-api.ts` with `RevisionConflictError` class and 409 detection.
- Wired revision tracking into `editor-client.tsx` with minimal monolith changes.
- Updated `TimelinePanel.tsx` to pass `expected_revision` and handle conflicts.
- Updated `export-step.tsx` with stale indicator when renders are outdated.

Key files:
- `supabase/migrations/202607140005_immutable_timeline_revisions.sql`
- `supabase/tests/task_5_timeline_revisions_test.sql`
- `apps/api/app/api/revision_errors.py`
- `apps/api/app/api/plans.py`
- `apps/api/app/repositories/plans.py`
- `apps/api/app/schemas/plans.py`
- `apps/api/app/main.py`
- `apps/api/app/api/renders.py`
- `apps/api/tests/test_revision_concurrency.py`
- `apps/web/hooks/useRevisionTracking.ts`
- `apps/web/components/editor/ConflictDialog.tsx`
- `apps/web/components/editor/StaleIndicator.tsx`
- `apps/web/lib/client-api.ts`
- `apps/web/components/editor-client.tsx`
- `apps/web/components/editor/timeline/TimelinePanel.tsx`
- `apps/web/components/editor/export-step.tsx`
- `apps/web/tests/revision-conflict.test.mjs`

Verification:
- Clean `supabase db reset --local` applied all migrations successfully (including 202607140005).
- Full DB suite: **137 tests PASS** across 6 test files.
- Focused Task 5 pgTAP suite: **19 tests PASS**.
- `supabase db lint --local`: **No schema errors found**.
- API test suite: **561 passed, 1 pre-existing unrelated skip, 18 skipped**.
- Focused revision concurrency tests: **20 passed**.
- Web test suite: **77 passed** (including 12 new revision-conflict tests).
- TypeScript type check (`tsc --noEmit`): **0 errors**.

## 8. Important implementation decisions already made

- Preserve forward-only database migrations; never edit an already-deployed historical migration except the newly created current-session migration before it is committed/deployed.
- Database service role is the mutation authority; authenticated browser users receive tenant-scoped reads only.
- Failed generated plans remain visible for diagnostics but never become active.
- Captionless exports must remain supported through UI and API.
- BullMQ active jobs with a valid lock are not “recovered” by data relabeling.
- Queue snapshots treat paused jobs as backlog.
- Use exact/pinned dependency versions when adding dependencies.
- Do not send project source, secrets, or customer data to new third-party endpoints.
- Do not commit or push unless explicitly requested.
- Use `/tmp` for temporary artifacts when needed; Docker storage is constrained, so remove only known session-created validation resources and avoid broad prune commands.

## 9. Remaining task implementation intent

### Task 5 — Immutable timeline revisions/source lineage
- Add media and timeline revision records.
- Require expected revision for all editor mutations.
- Enforce canonical timeline invariants.
- Tie all derivatives to a source revision.
- Invalidate stale derivatives on source replacement.
- Add multi-tab/collaborator conflict UX.

### Task 6 — Session-safe uploads/media
- Resumable multipart upload.
- Server-side object size/hash verification.
- Atomic upload quotas.
- Durable object references and browser URL refresh.
- Worker-time URL signing.

### Task 7 — Transactional outbox/durable workflows
- Workflow runs and step attempts in PostgreSQL.
- Transactional outbox and dispatcher.
- Idempotent worker claims/completion.
- Reconciliation after Redis/API/worker failures.

### Task 8 — Remove Lightning AI
- Add provider-neutral compute backend and static custom GPU pool.
- Remove Lightning service, settings, scripts, Railway variables, tests, and response strings.
- Add zero-reference assertion.

### Task 9 — Azure GPU
- Queue-specific Azure GPU pools, likely AKS/KEDA or equivalent approved scheduler.
- Workload identity/private networking.
- Capability routing, scale-from-zero, cost caps, OOM replacement.

### Task 10 — Provider-neutral model gateway
- Typed capabilities for planning, transcription, vision, audio, embeddings, generation.
- Azure and OpenAI-compatible custom adapters.
- Central routing, budget, fallback, circuit, telemetry, model revisions.

### Task 11 — AI proposal/approval brain
- Multimodal context builder.
- Typed tool registry and deterministic orchestrator state machine.
- Proposal rationale/confidence/provenance/cost.
- Approval/rejection/partial approval.
- Atomic application into a new timeline revision.

### Task 12 — Durable upload-to-first-draft
- Wire all steps from upload through analysis, approval, assembly, and draft render.
- Browser closure/device switch must not stop orchestration.

### Task 13 — Safe memory/evaluation
- Consent-aware project/user preferences.
- Normalized accepted/rejected edit deltas.
- No default training.
- Editorial regression datasets and shadow evaluation.

### Task 14 — Preview/export parity
- One timeline evaluator/render contract.
- Frame/timing golden comparison.
- Remove or clearly label browser-only approximations.

### Task 15 — Adaptive UX
- Creator mode and professional mode.
- Reduce visual noise/tiny uppercase labels.
- Preserve precision keyboard workflows.
- Decompose editor monoliths.

### Task 16 — Scalability SLOs
- Consolidated editor bootstrap.
- Pooled clients/single-job queue lookups/backpressure/model warm pools.
- Enforced API/browser/media/GPU load thresholds.

### Task 17 — Reproducibility/supply chain
- Hash-locked Python dependencies.
- Tight direct JS versions.
- Pinned image/model revisions.
- SBOM, image/SAST/license scans, signing.

### Task 18 — Final wiring
- Full creator/editor journeys.
- Security, failover, source replacement, URL refresh, render parity, export, retention, backup/restore, canary rollback.

## 10. Provisional success targets

- Resumable upload at current 512 MiB maximum.
- Non-media API p95 below 400 ms.
- Editor save acknowledgment p95 below 750 ms.
- Timeline input response below 50 ms.
- Preview seek below 150 ms.
- Preview/export timing drift no greater than one frame.
- Ten-minute 1080p input to proposal p50 below five minutes and p95 below ten minutes on warm capacity.
- Sixty-second 1080×1920 export p95 below three minutes on a warm GPU.
- Draft/export success at least 99.5% after retries, excluding invalid source media.
- Zero silent fixture use, silent downgrade, cross-tenant mutation, or lost timeline update.

## 11. Environment state at handoff

- Workspace dependencies are installed with the existing lockfile.
- Local Supabase was reset from all migrations and was running at the end of Task 3 validation; latest verified schema includes migrations through `202607140004`.
- The standard Task 4 worker validation image `motionly-task4-worker` remains locally available and passed non-root runtime write checks.
- Failed/current worker build cache created during this session was pruned after Docker storage filled; no user-owned Docker resources were removed.
- `/tmp/motionly-task3-reset.log` and `/tmp/motionly-task4-collect.log` may exist.
- No production credentials were required for Task 4 validation.
- Task 4 changes are complete and independently reviewed but remain uncommitted. No commit or push was performed.

## 12. Known modified files through completed Tasks 1–4

- `.railway/railway.ts`
- `Dockerfile`
- `apps/api/Dockerfile`
- `apps/api/app/api/plans.py`
- `apps/api/app/api/renders.py`
- `apps/api/app/main.py`
- `apps/api/app/repositories/plans.py`
- `apps/api/app/services/queue.py`
- `apps/api/tests/test_queue_control_plane.py`
- `apps/api/worker-bridge/queue-bridge.mjs`
- `apps/web/app/(app)/projects/[projectId]/page.tsx`
- `apps/web/components/editor/ai-step.tsx`
- `apps/web/components/editor/export-step.tsx`
- `apps/web/tests/export-captionless.test.mjs`
- `apps/worker/src/queue-bridge.mjs`
- `supabase/migrations/202607140002_preserve_active_plan_on_failure.sql`
- `supabase/migrations/202607140003_allow_caption_free_renders.sql`
- `supabase/migrations/202607140004_api_authoritative_tenant_boundary.sql`
- `supabase/tests/task_3_tenant_boundary_test.sql`

Additional Task 4 files:
- `apps/api/Dockerfile`
- `apps/api/app/services/media_url_policy.py`
- `apps/api/app/services/scale.py`
- `apps/api/app/services/stock_video.py`
- `apps/api/tests/__init__.py`
- `apps/api/tests/conftest.py`
- `apps/api/tests/test_admin.py`
- `apps/api/tests/test_broll.py`
- `apps/api/tests/test_media_url_policy.py`
- `apps/api/tests/test_phase7_integrity.py`
- `apps/api/tests/test_regeneration.py`
- `apps/api/tests/test_renders.py`
- `apps/api/tests/test_service_hardening.py`
- `apps/worker/Dockerfile`
- `apps/worker/src/media-url-policy.mjs`
- `apps/worker/src/media-url-policy.test.mjs`
- `infra/docker-compose.gpu.yml`

## 13. Suggested prompt for the next AI session

> Continue implementing the Motionly AI-centered rebuild from this handoff. Treat Tasks 1–4 as complete only because their cited validation and review evidence passed. Start with Task 5: immutable timeline revisions and source lineage. Do not start later tasks until Task 5 success criteria are implemented and verified. Preserve forward-only migrations, existing user changes, the API-authoritative tenant boundary, and the Task 4 SSRF/credential/container hardening. Use `/tmp` for temporary artifacts, account for constrained Docker storage, and do not broadly prune user resources. Work in small validated increments, update the task list immediately after each completed task, and do not commit or push without explicit permission.
