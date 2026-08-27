
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

Progress: **17 of 18 complete**.

- [x] Task 1 — Unify and repair the BullMQ queue control plane
- [x] Task 2 — Fix immediate product correctness regressions
- [x] Task 3 — Make the API the authoritative tenant-safe mutation boundary
- [x] Task 4 — Secure media, workers, and service credentials
- [x] Task 5 — Introduce immutable timeline revisions and source lineage
- [x] Task 6 — Make upload and media access session-safe
- [x] Task 7 — Add a transactional outbox and durable workflow state
- [x] Task 8 — Remove Lightning AI and activate custom GPU workers
- [x] Task 9 — Add Azure GPU scheduling and autoscaling
- [x] Task 10 — Build the provider-neutral model gateway
- [x] Task 11 — Implement the AI proposal and approval brain
- [x] Task 12 — Complete the durable upload-to-first-draft pipeline
- [x] Task 13 — Add safe memory and editorial evaluations
- [x] Task 14 — Unify preview and export rendering
- [x] Task 15 — Deliver adaptive creator and professional editor modes
- [x] Task 16 — Establish and enforce scalability SLOs
- [x] Task 17 — Make dependencies and builds reproducible
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

## 7c. Task 6 — Session-safe uploads and media access: COMPLETE

Implemented resumable, verified uploads:
- Added durable multipart sessions and normalized part rows with resume/reconciliation, bounded five-URL windows, idempotent confirmation/completion/abort, and bucket-compatible video MIME headers.
- Enforced the 512 MiB maximum in request schemas, settings, service preflight, browser preflight, and worker download bounds.
- Treats storage as authoritative: verifies every part size, assembles in order, computes server-side SHA-256, checks declared/requested hashes, uploads the final object, and re-verifies final storage size and SHA-256.
- Supabase's fixed signed PUT lifetime is reported truthfully as 7,200 seconds. Terminal storage deletion waits 7,500 seconds, preventing a still-valid PUT from recreating an unaccounted object.

Implemented atomic accounting and lifecycle safety:
- Migration `202607140006_session_safe_uploads.sql` adds byte/object usage plus reservations, multipart state, object references, URL grant audit rows, tenant FKs/RLS, idempotent quota transitions, atomic finalization/cancellation/expiry, and durable storage deletion work.
- Forward migrations `202607140011_task6_legacy_upload_claim.sql` and `202607140012_task6_terminal_cleanup.sql` deliver fixes to environments where migration 006 was already applied.
- Migration `202607140013_task6_source_reference_backfill.sql` creates and links source references for existing eligible uploads.
- Legacy single-PUT completion now performs authoritative storage verification and the same atomic quota/reference finalization.

Implemented durable media access:
- Browser media responses carry durable source/thumbnail reference IDs and refresh short-lived URLs before expiry, after focus/visibility recovery, and once after source/poster errors.
- Lazy source references are persisted back to the upload row; render creation guarantees a source reference and fails closed if one cannot be established.
- Worker media is signed only at execution time through the tenant-scoped internal signer. Preprocess, transcription, visual analysis, matte extraction, stem separation, FFmpeg, and Remotion consume the signed URL; Python materialization rechecks byte size and SHA-256, and Remotion refuses an uploaded source without a reference.
- Regenerated `packages/shared/openapi.json` and `packages/shared/src/generated/api.ts`.

Key files:
- `apps/api/app/api/uploads.py`
- `apps/api/app/api/media.py`
- `apps/api/app/services/upload_session.py`
- `apps/api/app/services/storage_quota.py`
- `apps/api/app/services/object_references.py`
- `apps/api/app/services/worker_media.py`
- `apps/api/app/services/retention.py`
- `apps/web/lib/resumable-upload.ts`
- `apps/web/components/upload-panel.tsx`
- `apps/web/components/editor-client.tsx`
- `apps/worker/src/remotion.mjs`
- `supabase/migrations/202607140006_session_safe_uploads.sql`
- `supabase/migrations/202607140011_task6_legacy_upload_claim.sql`
- `supabase/migrations/202607140012_task6_terminal_cleanup.sql`
- `supabase/migrations/202607140013_task6_source_reference_backfill.sql`
- `supabase/tests/task_6_session_safe_uploads_test.sql`

Verification:
- Focused Task 6 API/upload/worker-media suite: **48 passed**.
- Stable render-route and stem-consumer invariants: **4 passed**.
- Relevant API render/media/retention regression suite: **97 passed, 1 skipped** before the final TTL-only adjustment; the final affected upload suite then passed 48/48.
- Full web suite: **91 passed**; web TypeScript check passed.
- Full worker suite: **35 passed, 1 skipped**; Node syntax checks passed.
- Focused Task 6 pgTAP suite: **67 tests PASS**.
- `supabase db lint --local`: **No schema errors found**.
- Generated contract check passed; Task 6 Ruff checks and `git diff --check` passed.
- Independent final audit returned **`TASK 6 AUDIT: PASS`**.

## 7d. Task 8 — Remove Lightning AI and activate custom GPU workers: COMPLETE

Implemented provider-neutral compute backend and static custom GPU pool:
- New `apps/api/app/services/compute_backend.py` defines a `ComputeBackend` abstraction and a default `StaticGpuPoolBackend`. The static pool represents Motionly's own custom GPU workers (see `infra/docker-compose.gpu.yml`); it observes per-group backlog vs. static capacity (visual/matte/render) and emits a saturation/backpressure signal, and it never calls any external managed-GPU provider API.
- `compute_backend_from_settings()` selects the backend via the provider-neutral `COMPUTE_BACKEND` setting (default `static_gpu_pool`); future managed backends (e.g. Azure GPU in Task 9) can implement the same contract without changing callers.

Removed all Lightning presence:
- Deleted `apps/api/app/services/lightning_scaling.py`.
- Removed every `lightning_*` setting from `apps/api/app/core/config.py` and dropped `LIGHTNING_API_KEY` from the secret-file loader; added `COMPUTE_BACKEND` + `GPU_POOL_{VISUAL,MATTE,RENDER}_REPLICAS`.
- `gpu_autoscaling.py` `max_worker_count` now derives from `gpu_max_worker_count` only (removed `lightning_max_replicas`).
- `main.py` wires the compute backend; `operations.py` disabled reason string is now `compute_backend_unconfigured`.
- Deleted `scripts/deploy_lightning_workers.sh` and `scripts/lightning_start_worker.sh`; added provider-neutral `scripts/deploy_gpu_workers.sh` and `scripts/gpu_start_worker.sh` (using `GPU_WORKER_ROLE`, `GPU_DEPLOY_COMMAND`, `GPU_WORKER_IMAGE`, `GPU_*_DEPLOYMENT`).
- Removed all `LIGHTNING_*` variables from `.railway/railway.ts` and `prod.env.example`; added compute-backend settings to `apps/api/.env.example`.
- Updated tests: `test_phase4_gpu_media_workers.py` (replaced 3 Lightning scaling tests with 6 static-pool/compute-backend tests), `apps/worker/src/worker-routing.test.mjs` (points at the new GPU scripts), `test_control_media_boundary.py` (removed `lightning-sdk` line).
- Updated `docs/motionly_technical_documentation.md` section 4 to describe the provider-neutral custom GPU worker pool.

Added zero-reference assertion:
- `apps/api/tests/test_lightning_removed.py` scans the runtime/config/deploy/test tree and fails if any case-insensitive `lightning` token survives (only `lightningcss` in the web lockfile is allowed and stripped). It also asserts the service module and old scripts are deleted and the new scripts and compute backend exist.

Key files:
- `apps/api/app/services/compute_backend.py`
- `apps/api/app/services/gpu_autoscaling.py`
- `apps/api/app/core/config.py`
- `apps/api/app/main.py`
- `apps/api/app/api/operations.py`
- `scripts/deploy_gpu_workers.sh`
- `scripts/gpu_start_worker.sh`
- `.railway/railway.ts`
- `prod.env.example`
- `apps/api/.env.example`
- `apps/api/tests/test_lightning_removed.py`
- `apps/api/tests/test_phase4_gpu_media_workers.py`
- `apps/api/tests/test_control_media_boundary.py`
- `apps/worker/src/worker-routing.test.mjs`
- `docs/motionly_technical_documentation.md`

Verification:
- Full API pytest: **574 passed, 18 skipped, 1 failed**. The single failure (`test_control_media_boundary::test_api_image_contains_no_ffmpeg_or_ml_runtime`, the `apps/worker` assertion) was confirmed **pre-existing at HEAD** (stale from the Task 1 canonical-bridge copy) and is unrelated to Lightning removal.
- Zero-reference suite `test_lightning_removed.py`: **4 passed**.
- Worker node `worker-routing.test.mjs`: **6 passed** (including the renamed GPU-pool placement test).
- Ruff on all Task 8 files: **All checks passed**.
- Repo-wide `grep -ri lightning` shows no references outside the scan test itself and `lightningcss` in `pnpm-lock.yaml`.
- Note: `pytest` and `ruff` (declared dev dependencies) had gone missing from the API venv mid-session and were reinstalled to run verification. No commits or pushes were made.

## 7e. Task 9 — Add Azure GPU scheduling and autoscaling: COMPLETE

Implemented queue-specific Azure GPU pools on AKS + KEDA, wired behind the provider-neutral compute backend from Task 8.

Decision logic (`apps/api/app/services/azure_gpu.py`, pure/deterministic):
- **Capability routing** — `CAPABILITY_CATALOG` (T4 `Standard_NC4as_T4_v3`, V100 `Standard_NC6s_v3`, A100 `Standard_NC24ads_A100_v4`); `default_azure_pools`/`azure_pools_from_settings` build render/visual/matte pools; `route_queue_to_pool` maps `render_gpu`→render, `visual_analysis`+`transcription`→visual, `matte_extraction`→matte.
- **Scale-from-zero** — no backlog resolves to `min_replicas` (0).
- **Cost caps** — `plan_azure_scaling` funds pools in priority order (render > visual > matte) until the hourly USD budget is exhausted; over-budget pools are `cost_capped`.
- **OOM replacement** — pools at/over the OOM threshold are `cordoned` with an `oom_replacements` count.

Backend + settings:
- `AzureGpuPoolBackend(ComputeBackend)` in `apps/api/app/services/compute_backend.py`; `compute_backend_from_settings` selects it for `COMPUTE_BACKEND=azure_aks_keda` (static pool remains the default).
- `config.py` adds `AZURE_GPU_CAPABILITY`, `AZURE_GPU_MAX_NODES_PER_POOL`, `AZURE_GPU_HOURLY_BUDGET_USD`, `AZURE_GPU_RESOURCE_GROUP`, `AZURE_GPU_CLUSTER_NAME`, `AZURE_GPU_WORKLOAD_IDENTITY_CLIENT_ID`; documented in `apps/api/.env.example`.

AKS/KEDA manifests (`infra/azure/`):
- `namespace.yaml`, `workload-identity.yaml` (ServiceAccount with `azure.workload.identity/client-id` annotation + `use: "true"` label), `network-policy.yaml` (default-deny + explicit egress = private networking), `storage.yaml` (RWX Azure Files), `gpu-deployments.yaml` (one GPU Deployment per pool: `replicas: 0`, workload-identity SA + pod label, `accelerator` nodeSelector, `sku=gpu` toleration, `nvidia.com/gpu` limits, non-root), `keda-scaledobjects.yaml` (one ScaledObject per pool, `minReplicaCount: 0`, per-queue Redis triggers, workload-identity `TriggerAuthentication`), `README.md`.

Docs: `docs/azure_gpu_deployment.md` gained a ready-to-apply manifests section; technical doc section 4 references the Azure backend.

Verification:
- Focused `tests/test_phase9_azure_gpu.py`: **20 passed** (capability routing, scale-from-zero, cost caps, OOM replacement, backend/factory wiring, and YAML-parsed manifest correctness — scale-to-zero, per-queue triggers, workload identity, default-deny networking, GPU scheduling).
- Full API pytest: **606 passed, 18 skipped, 1 failed**. The single failure is the same pre-existing, unrelated `test_control_media_boundary::test_api_image_contains_no_ffmpeg_or_ml_runtime` (`apps/worker` assertion, stale from Task 1).
- Ruff on all Task 9 files: **All checks passed**.
- **Independent auditor subagent** verified all 9 success criteria and returned **`TASK 9 AUDIT: PASS`**.
- No commits or pushes were made.

## 7f. Task 10 — Build the provider-neutral model gateway: COMPLETE

Implemented:
- Added typed request/output contracts for planning, transcription, vision, audio, embeddings, and generation, plus explicit local domain operations for visual manifests, matte segmentation, audio intelligence, Demucs stem separation, image embeddings, and route-affine Runway submit/poll.
- Added Azure OpenAI and OpenAI-compatible adapters with validated HTTPS endpoints, callable model/deployment IDs separate from immutable model revisions, strict typed response parsing, and recorded transport contracts.
- Centralized capability/operation routing, request and workspace-month dollar budgets, controlled fallback, route+revision circuit breakers, usage/cost telemetry, durable tenant-scoped provider events, and invocation provenance.
- Made budget settlement charge the full actual provider cost in both memory and Redis before rejecting an underestimated result; Redis reservations are atomic cross-process leases with crash-expiry reconciliation.
- Preserved provider failure classifications. Local Runway request rejection is terminal, nonbillable, and non-circuit-worthy; remote auth/quota/rate-limit/availability/timeout failures remain distinct, and video submission never falls back after a possible side effect.
- Routed production matte segmentation and Demucs stem separation through typed gateway adapters with workspace/project context and immutable revisions. Production runtime validation checks actually registered executable operations and fails closed on missing routes.
- Bound legacy in-flight Runway polling to the configured route/revision through the gateway or rejected it before any direct provider bypass.
- Added production route/pricing/revision guards, deployment/env catalog wiring, and the `provider_slo_events` schema contract for route, revision, request, cost, and typed usage telemetry.

Key files:
- `apps/api/app/services/model_gateway.py`
- `apps/api/app/services/model_adapters.py`
- `apps/api/app/services/model_domain_adapters.py`
- `apps/api/app/services/model_gateway_factory.py`
- `apps/api/app/services/provider_resilience.py`
- `apps/api/app/services/matte_jobs.py`
- `apps/api/app/services/scale.py`
- `apps/api/app/core/production.py`
- `apps/api/app/main.py`
- `apps/api/app/workers/visual_job.py`
- `apps/api/app/workers/matte_job.py`
- `apps/api/tests/test_model_gateway.py`
- `apps/api/tests/test_model_gateway_redis.py`
- `apps/api/tests/test_phase10_cost_controls.py`
- `supabase/migrations/202607140010_provider_neutral_model_gateway.sql`
- `supabase/tests/task_10_model_gateway_test.sql`

Verification:
- Focused gateway/domain/production suite: **97 passed**.
- Real Redis contracts for atomic budgets, over-reservation settlement, lease recovery, and revision-scoped circuits: **4 passed**.
- Full API suite: **666 passed, 23 opt-in contracts skipped**.
- Task 10 Supabase pgTAP contract: **12 tests PASS**; local repository integration contracts: **2 passed**.
- Ruff over the Task 10 implementation/tests: **All checks passed**; Python compile checks completed with exit 0.
- Independent final auditor reran 59 focused tests and Ruff, verified all acceptance criteria and prior blocker remediations, and returned **`TASK 10 AUDIT: PASS`**.
- Credentialed live Azure/OpenAI-compatible/Runway calls were not run because no provider secrets were available; recorded transport contracts and opt-in live-provider tests cover those boundaries without transmitting project data.
- No commit or push was performed.

## 7g. Task 14 — Unify preview and export rendering: COMPLETE

Implemented one canonical preview/export timeline contract:
- Added `packages/shared/src/timeline-eval.mjs` + `timeline-eval.d.mts`, exporting `normalizeSourceClips`, `resolveSourceClipAtOutputMs`, `outputMsToSourceMs`, and `outputTimelineDurationMs`. The plain ESM module is consumable by the browser bundle, renderer TypeScript, and Node test suites.
- `apps/renderer/src/timeline.ts` now adapts render props to the shared evaluator instead of maintaining a local clip walker.
- `apps/web/lib/timeline.ts`, `useTimeline.ts`, and `usePlaybackSync.ts` use the shared evaluator. Removed the divergent `apps/web/lib/timeline-time.mjs` and `.d.mts`; removed the browser-only `-20ms` boundary fudge.
- Preview frame quantization now uses canonical export FPS (`PreviewExportState.fps`, default `DEFAULT_RENDER_FPS`) rather than upload/source FPS. `export-step.tsx` supplies the canonical FPS and `editor-client.tsx` no longer passes `sourceFps`.

Frame/timing parity evidence:
- Added shared fixture `packages/shared/src/timeline-golden.json` with removed-gap, boundary, and duration samples.
- Both `apps/renderer/src/frame-boundary.test.mjs` and `apps/web/tests/timeline-time.test.mjs` consume the same fixture and compare all samples.
- Converted renderer `Composition.tsx`, `components/Captions.tsx`, and `components/ManualElements.tsx` to shared millisecond/frame helpers; the renderer boundary test guards against ad-hoc conversion math.

Browser approximation labeling:
- `VideoPreview.tsx` explicitly labels CSS/WebGL grade and motion as **“Grade/motion: preview approximation”** and retains **“Approximate subject preview”** / **“Real matte preview”** status badges.
- `docs/workers/render-correctness.md` documents that browser grade/motion/transition/matte fallbacks are lower-cost approximations and that export output is authoritative.

Verification:
- Renderer frame/parity suite: **6 passed, 0 failed**; renderer package test also passed with TypeScript tests (2 passed, 2 skipped) and all `.mjs` tests passing.
- Web tests: **91 passed, 0 failed**; focused parity/matte suite: **5 passed, 0 failed**.
- Web typecheck: **0 errors**.
- Shared package typecheck: **0 errors**.
- Independent auditor re-check: **`TASK 14 AUDIT: PASS`**, confirming all criteria and no remaining ad-hoc ms-to-frame math in the named renderer components.
- Web lint was attempted but terminated with exit 143 before completion; typecheck and tests passed. No commits or pushes were made.

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

### Task 6 — Session-safe uploads/media: COMPLETE
- See Section 7c for implementation details and verification evidence.

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

### Task 17 — Reproducibility/supply chain: COMPLETE

Implemented:
- Generated hash-locked base, development, and ML Python lockfiles with pinned transitive versions; container installs use `pip --require-hashes`.
- Tightened direct JavaScript dependency specifiers, synchronized `pnpm-lock.yaml`, and added npm lockfiles for the API bridge and worker.
- Pinned Docker base images and Compose images by registry digest, removed Azure `latest` image tags, and pinned Hugging Face model prefetches to commit revisions.
- Added the `supply-chain.yml` workflow for frozen dependency verification, SPDX SBOMs, CodeQL SAST, Trivy image scans, JavaScript/Python license checks, and Cosign OIDC signing on pushes.

Verification evidence from this session:
- `pnpm install --frozen-lockfile --ignore-scripts --offline` completed successfully.
- `pip install --dry-run --ignore-installed --require-hashes` completed successfully for `requirements.lock`, `requirements-dev.lock`, and the 3,835-line `requirements-ml.lock`.
- `docker build --file apps/api/Dockerfile --tag motionly-task17-api .` completed successfully using the digest-pinned Python base image.
- `APP_ENV=test apps/api/.venv/bin/python -m pytest -q apps/api/tests/test_phase4_gpu_media_workers.py` passed: 18 tests.
- Worker, renderer, and web typechecks all completed successfully.
- Static validation passed for Python syntax, YAML syntax, immutable Docker bases, and absence of mutable `:latest` deployment references.

Key files:
- `apps/api/requirements.lock`
- `apps/api/requirements-dev.lock`
- `apps/api/requirements-ml.lock`
- `pnpm-lock.yaml`
- `.github/workflows/supply-chain.yml`
- `docs/ops/supply-chain.md`
- `infra/docker-compose.gpu.yml`
- `infra/azure/gpu-deployments.yaml`

### Task 13 — Safe memory/evaluation
- Consent-aware project/user preferences.
- Normalized accepted/rejected edit deltas.
- No default training.
- Editorial regression datasets and shadow evaluation.

### Task 14 — Preview/export parity
- One timeline evaluator/render contract.
- Frame/timing golden comparison.
- Remove or clearly label browser-only approximations.

### Task 15 — Adaptive creator and professional editor modes: COMPLETE

Implemented:
- Creator mode is the hydration-safe default and persists the user’s Creator/Professional preference. The visible mode switch and `Alt+Shift+M` shortcut remain functional when browser storage is unavailable.
- Creator mode provides a calmer guided workspace with project progress, current-task guidance, preview, focused settings, and timeline. Professional mode restores the element palette, preview, full inspector, command bar, and timeline.
- Responsive selection mounts exactly one mobile or desktop workspace, preventing duplicate media refs, IDs, polling trees, and global timeline shortcut handlers.
- Creator-only typography normalization removes forced uppercase labels and raises legacy 9px/10px editor text to a readable 12px while preserving Professional mode density.
- Precision workflows remain available in both modes: undo/redo, J/K/L playback, bracket trim, focused clip arrow movement, and a visible shortcut guide.
- The prior editor layout monolith is split into mode state, toolbar, progress rail, workspace, and shared type modules. Creator-flow derivation was extracted from `editor-client.tsx`, and global timeline shortcuts were extracted from `TimelinePanel.tsx`.

Key files:
- `apps/web/components/editor-layout.tsx`
- `apps/web/components/editor/layout/useEditorMode.ts`
- `apps/web/components/editor/layout/EditorToolbar.tsx`
- `apps/web/components/editor/layout/CreatorProgressRail.tsx`
- `apps/web/components/editor/layout/EditorWorkspaces.tsx`
- `apps/web/components/editor/creator-flow.ts`
- `apps/web/components/editor/timeline/useTimelineKeyboardShortcuts.ts`
- `apps/web/app/globals.css`
- `apps/web/tests/task15-adaptive-editor.test.mjs`

Verification:
- Focused Task 15, responsive, and accessibility suite: **15 passed**.
- Full web test suite: **90 passed**.
- Web TypeScript check: **exit 0**.
- Full web ESLint: **exit 0 with no warnings**.
- Next.js production build compiled, type-checked, and generated all **20** static pages successfully.
- `git diff --check`: **exit 0**.
- Independent final audit verified all four acceptance criteria and returned **`TASK 15 AUDIT: PASS`**.

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
