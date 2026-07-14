# Motionly AI-Centered Rebuild — Session Handoff

**Prepared:** 2026-07-14 14:58 UTC  
**Repository:** `/workspaces/Motionly-claude`  
**Application root:** `/workspaces/Motionly-claude/motionly-main/Motionly-main`  
**Operating system:** Linux  
**Space note:** If large builds/tests need more space, use `/temp` on `/dev/sdb1` for temporary artifacts and caches.  
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

Progress: **3 of 18 complete**.

- [x] Task 1 — Unify and repair the BullMQ queue control plane
- [x] Task 2 — Fix immediate product correctness regressions
- [x] Task 3 — Make the API the authoritative tenant-safe mutation boundary
- [ ] Task 4 — Secure media, workers, and service credentials **(PARTIAL/UNVERIFIED WORK EXISTS; NEXT)**
- [ ] Task 5 — Introduce immutable timeline revisions and source lineage
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

## 7. Current partial Task 4 state — DO NOT ASSUME COMPLETE

The Task 4 sub-agent pipeline was interrupted after it had written substantial changes. These changes have **not yet been reviewed or validated by the parent session**. Treat the worktree as partial and potentially broken.

### Partial media/SSRF work detected

New/changed areas include:
- `apps/api/app/services/media_url_policy.py`
- `apps/api/tests/test_media_url_policy.py`
- `apps/api/app/services/render_jobs.py`
- `apps/api/app/services/stock_video.py`
- `apps/api/app/services/scale.py`
- `apps/api/app/api/renders.py`
- `apps/worker/src/media-url-policy.mjs`
- `apps/worker/src/media-url-policy.test.mjs`
- `apps/worker/src/remotion.mjs`

Intended behavior appears to include:
- Reject private, loopback, link-local, reserved, credential-bearing, and unsupported outbound media URLs.
- Validate DNS results.
- Preserve strict `supabase://` and fixture behavior where allowed.
- Revalidate media in both API and worker layers.
- Guard redirect/download paths.

### Partial service-hardening work detected

New/changed areas include:
- `apps/api/app/core/config.py`
- `apps/api/app/core/middleware.py`
- `apps/api/app/main.py`
- Internal API routes such as `internal_jobs.py` and `internal_renders.py`
- `apps/api/tests/test_service_hardening.py`
- Worker `queue-common.mjs`, `worker.mjs`, and related tests
- `.railway/railway.ts`
- `infra/docker-compose.gpu.yml`
- `.env.example` files
- `apps/web/next.config.ts`
- `apps/web/playwright.config.mjs`
- `apps/web/tests/service-hardening.test.mjs`
- Likely Dockerfiles/non-root runtime changes; inspect before relying on them.

Intended behavior appears to include:
- New distinct `WORKER_API_TOKEN`.
- No fallback from worker token to `ADMIN_API_TOKEN`.
- Constant-time internal token comparison.
- Separate worker secret wiring in Railway/Compose.
- Production OAuth-state secret requirements.
- API/web security headers and CSP.
- Non-root worker/web containers.

### Exact next action for Task 4

1. Inspect all Task 4 changes before editing further.
2. Run focused tests separately to avoid long tool calls:

```bash
cd /workspaces/Motionly-claude/motionly-main/Motionly-main/apps/api
python -m pytest -q tests/test_media_url_policy.py
python -m pytest -q tests/test_service_hardening.py
python -m pytest -q \
  tests/test_internal_jobs.py \
  tests/test_phase6_worker_queue.py \
  tests/test_renders.py \
  tests/test_broll.py \
  tests/test_phase7_integrity.py
python -m ruff check app tests
```

```bash
cd /workspaces/Motionly-claude/motionly-main/Motionly-main/apps/worker
pnpm test
```

```bash
cd /workspaces/Motionly-claude/motionly-main/Motionly-main/apps/web
pnpm test
pnpm typecheck
```

3. Check JS syntax explicitly:

```bash
cd /workspaces/Motionly-claude/motionly-main/Motionly-main
node --check apps/worker/src/media-url-policy.mjs
node --check apps/worker/src/remotion.mjs
node --check apps/worker/src/worker.mjs
```

4. Review all internal routes to prove every worker-only route uses `WORKER_API_TOKEN`, while `/admin` still uses admin authorization.
5. Test SSRF bypasses: decimal/octal-like IPv4 forms, IPv4-mapped IPv6, DNS returning mixed public/private results, redirects to private targets, manual audio/video URLs, and secret query redaction.
6. Build affected containers. If space is tight, set applicable temporary/cache directories under `/temp` on `/dev/sdb1`; do not broadly prune Docker data belonging to the user.
7. Obtain an independent security review. Only then mark Task 4 complete.

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
- Use `/temp` for large temporary data if workspace disk pressure appears.

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

- Workspace dependencies were installed with `pnpm install --frozen-lockfile`.
- Local Supabase was reset from all migrations and was running at the end of Task 3 validation.
- Latest verified database state includes migrations through `202607140004`.
- A temporary Redis Docker container used for queue contracts was removed.
- The temporary API validation image was removed.
- Docker build cache/layers may remain; do not run broad destructive prune commands without permission.
- `/tmp/motionly-task3-reset.log` may exist.
- No production credentials are required for Tasks 1–3 validation.
- Task 4 partial changes may not compile; validate before continuing.

## 12. Known modified files through completed Tasks 1–3

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

This list does not include every partial Task 4 file; use the Task 4 section above and inspect the worktree.

## 13. Suggested prompt for the next AI session

> Continue implementing the Motionly AI-centered rebuild from this handoff. Treat Tasks 1–3 as complete only because their cited tests passed. Task 4 has substantial interrupted, unverified changes: inspect and validate them first, fix all failures/security-review findings, and only then mark Task 4 complete. Preserve forward-only migrations, existing user changes, and the API-authoritative tenant boundary. Use `/temp` on `/dev/sdb1` for large temporary artifacts if needed. Work in small validated increments, update the task list immediately after each completed task, do not commit/push without explicit permission, and do not claim overall completion until all 18 tasks have concrete verification evidence.
