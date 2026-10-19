# Motionly Hardening Release — Progress, Plan, and Remaining Work

**Status:** In progress — foundational reliability and generic-media safety are complete; security/billing/no-vocals are partially complete; matte lifecycle, analytics/frontend integration, deployment-source hardening, and final validation remain.

**Workspace:** `d:\Motionly-claude\motionly-main\Motionly-main`  
**Last updated:** 2026-08-29  
**Release posture:** Source-only hardening. No deployment, production secret rotation, Supabase reset, Docker start/reset, destructive reconciliation, or deletion of existing production data is authorized.

## 1. Release objective

This release addresses the hardening audit as one coherent, production-safe release. The intended result is a system that:

- fails closed when a required worker, media artifact, matte, or security dependency is unavailable;
- persists media lifecycle state atomically and does not release quota before verified physical deletion;
- makes billing-disabled behavior explicit rather than accidentally presenting a misleading Free plan;
- keeps authenticated workspace boundaries, queue execution, and object references durable;
- prevents a selected `no_vocals` stem from silently becoming vocals during rendering;
- prevents background-removal exports from continuing without their required matte;
- exposes trustworthy analytics/dashboard state rather than stale or fabricated values; and
- keeps deployment configuration safe without performing a deployment from this working session.

The release must preserve the pre-existing dirty worktree. Changes outside the audited hardening scope are not to be reformatted, discarded, or overwritten.

## 2. Agreed product and safety decisions

| Area                  | Decision in force                                                                             | Explicitly rejected                                                       |
| --------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Release scope         | Complete the full hardening release (`1b`)                                                    | Blockers-only or incremental release                                      |
| Operating model       | Production-safe source completion (`3b`)                                                      | Deploy-now or destructive production actions                              |
| Billing               | Billing remains disabled for this release (`1c`); show disabled/unmetered behavior explicitly | Live/test Stripe purchase UX or misleading Free-plan state                |
| Media deletion/quota  | Release quota only after physical deletion is durably acknowledged (`2a`)                     | Soft deletion or logical quota release before cleanup                     |
| Stem selection        | Fully ship typed, authenticated, persisted `no_vocals` selection (`3a`)                       | Hiding it, vocals-only behavior, or silent fallback                       |
| Local auth            | Keep the exact local/development bypass, centralized and fail-closed elsewhere                | Raw per-request `SKIP_API_AUTH` decisions or removal of local dev support |
| Background removal    | Block/defer rendering until the requested matte is available                                  | Rendering an export without the requested matte                           |
| Analytics idempotency | Add atomic, forward-only idempotency persistence                                              | Destructive cleanup of existing production duplicates                     |

## 3. Current release board

| Task | Status                                      | What it covers                                                                                                   | Current state                                                                                                                                                            |
| ---- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1    | **Complete**                                | Worker/render/queue reliability                                                                                  | Implemented and focused checks passed.                                                                                                                                   |
| 2    | **Complete**                                | Generic-media lifecycle, deletion, quota, reconciliation                                                         | Implemented and reviewed; focused checks passed.                                                                                                                         |
| 3    | **In progress**                             | Conditional security, billing-disabled behavior, persisted stems, matte lifecycle, analytics, dashboard/frontend | Security and billing changes are complete; persisted no-vocals implementation is in place and focused checks pass; matte, analytics, and frontend/dashboard work remain. |
| 4    | **Not started**                             | Compose/web routing, controlled egress, file-secret source configuration                                         | Source-only configuration hardening remains.                                                                                                                             |
| 5    | **Not started**                             | Broad validation, final diff review, release summary                                                             | Must happen after remaining implementation; database-backed checks are currently environment-blocked.                                                                    |
| 6    | **Complete after this document is written** | Durable in-repository progress/status plan                                                                       | This document is the artifact.                                                                                                                                           |

## 4. Completed work

### 4.1 Task 1 — Worker, render, and queue reliability

The worker/render/queue audit findings have been addressed in the following ways:

| Audit concern                                                                | Implemented hardening                                                                           | Main areas changed                                                            |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Python workers run from ambiguous media paths                                | Added an explicit Python media working directory to the Python worker images.                   | `apps/worker/Dockerfile.python`, `Dockerfile.python.demucs`, `Dockerfile.gpu` |
| Worker jobs could return `{ failed: true }` yet be treated as success        | Normalized returned failure payloads so all worker job types propagate failure correctly.       | API/worker queue bridge and worker execution paths                            |
| FFmpeg cancellation could hang or leave processes/resources behind           | Added non-blocking cancellation, termination escalation, and timeout cleanup.                   | `apps/api/app/services/ffmpeg_render.py`                                      |
| Canceled exports could leave uploaded artifacts                              | Added canceled post-upload render artifact cleanup.                                             | Render/retention service paths                                                |
| Ambiguous Remotion completion could delete canonical artifacts               | Protected canonical Remotion artifacts when completion outcome is uncertain.                    | `apps/worker/src/remotion.mjs` and tests                                      |
| Queue failure scans/cancellation state could become unbounded or non-durable | Bounded failed-job scans with truncation state and added atomic, expiring cancellation markers. | Queue services and bridge implementations                                     |
| BullMQ had unreachable lifecycle no-ops                                      | Removed unreachable `start` / `complete` / `fail` no-op paths.                                  | Queue bridge implementations                                                  |

**Focused validation already completed:** relevant Python and Node reliability tests passed.

### 4.2 Task 2 — Generic-media lifecycle, physical deletion, and quota safety

The generic-media lifecycle now follows a strict durable sequence rather than best-effort cleanup:

1. Media creation is persisted atomically through a session-aware creation path.
2. Deletion creates durable deletion intent rather than making direct object deletion the authority.
3. Object deletion acknowledgement is fenced, so stale/repeated workers cannot incorrectly finalize a deletion.
4. Quota is released only after physical deletion has been acknowledged.
5. B-roll and style-reference owners validate session ownership and recover safely from response loss.
6. Style changes use revision/CAS behavior where concurrent writes matter.
7. Object-reference/session locking and null direct-quota identifiers preserve safe rollout behavior alongside old workers.
8. Deleted style references are handled safely by replacement/training flows.

**Key files:**

- `apps/api/app/services/generic_media_upload.py`
- `apps/api/app/api/admin.py`
- `apps/api/app/api/broll.py`
- `apps/api/app/api/scale.py`
- `apps/api/app/services/retention.py`
- `supabase/migrations/202608120001_generic_media_strict_deletion.sql`
- `supabase/tests/task_25_generic_media_strict_deletion_test.sql`

**Completed validation:** Ruff checks/formatting, Python compilation, focused pytest coverage (59 tests, with one external deprecation warning), and tracked/untracked whitespace checks passed. The migration is forward-only; no applied migration was modified.

### 4.3 Task 3 — Completed security and billing work

#### Conditional API authentication

The local API-auth bypass remains available only under the exact local/development policy, while production is fail-closed:

- `Settings` now owns parsed `skip_api_auth` / `api_auth_bypass_enabled` behavior.
- Validation uses the parsed settings field rather than an ad hoc raw `os.getenv("SKIP_API_AUTH")` lookup.
- `get_actor()` uses the centralized `settings.api_auth_bypass_enabled` decision.
- Local workspace-membership fallback remains constrained to `LOCAL_AUTH_BYPASS_ENVS`.

This preserves local developer ergonomics without allowing an accidental production environment variable interpretation to bypass authentication.

#### Internal worker endpoint protection

`apps/api/app/api/internal_jobs.py` now applies `Depends(require_worker_token)` at the router level. The duplicated endpoint-level token validation was removed while forwarding of `x_motionly_worker_token` was retained for downstream job execution.

#### Billing-disabled contract

When billing is disabled, the API returns an explicit disabled/unmetered billing mode instead of pretending the user is on a Free plan:

- Added `BillingMode`, `billing_enabled`, `billing_mode`, `unmetered`, and nullable limits as appropriate.
- Disabled mode grants Agency-equivalent personal entitlements for status, render, effects, and B-roll behavior.
- API/UI consumers can distinguish an intentional billing-off release state from a subscription state.

**Completed validation:**

```text
python -m pytest tests/test_config.py tests/test_auth_projects_storage.py tests/test_billing.py tests/test_service_hardening.py tests/test_renders.py -q
159 passed, 1 skipped, 2 warnings
```

A correct-working-directory Ruff check and `git diff --check` also passed for this batch.

### 4.4 Task 3 — Persisted and authenticated `no_vocals` selection

The selected stem is now a persisted resource choice rather than arbitrary client JSON or an implicit rendering preference:

1. `StemSelectionUpdateRequest` constrains selection to a typed active stem with an expected revision.
2. `PATCH /projects/{project_id}/phase5/stems/{resource_id}` authenticates access, validates workspace/project/resource ownership, validates the resource kind and READY state, and uses compare-and-swap revision control.
3. The selected artifact must match the exact canonical path:
   `workspaces/{workspace}/projects/{project}/stems/{resource}/{active}.wav`.
4. The selected durable object is inspected before selection is persisted.
5. Render creation revalidates the selected artifact. An explicit selected `no_vocals` artifact that is missing or invalid raises an error rather than falling back to vocals.
6. FFmpeg, premium Remotion worker prop assembly, and renderer components all select the explicitly active stem and no longer use a `no_vocals → vocals` fallback.

**Expected API failure behavior:**

| Condition                                                               | Result                                         |
| ----------------------------------------------------------------------- | ---------------------------------------------- |
| Resource is absent                                                      | `404 stem_separation_not_found`                |
| Resource kind is wrong                                                  | `409 stem_resource_kind_invalid`               |
| Stem separation is not ready                                            | `409 stem_separation_not_ready`                |
| Selected artifact is missing/malformed/outside its exact canonical path | `409 stem_artifact_unavailable`                |
| Storage object inspection fails unexpectedly                            | `502 stem_artifact_verification_failed`        |
| Revision is stale                                                       | Existing `409 collaboration_revision_conflict` |

**Focused validation already completed:**

```text
python -m ruff check app/api/scale.py app/api/renders.py app/schemas/scale.py app/services/render_jobs.py tests/test_scale.py tests/test_ffmpeg_render.py
# passed

python -m pytest tests/test_scale.py tests/test_ffmpeg_render.py tests/test_renders.py -q
# exit 0

node --check src/remotion.mjs
node --test src/task6-worker-signing.test.mjs src/tenant-media-scope.test.mjs
# 6 passed

pnpm run typecheck  # apps/renderer
# passed
```

The `apps/worker` package `pnpm run typecheck` did not run because that script requires a local `apps/worker/.env`, which is not present. Its direct Remotion syntax check and focused Node tests passed, so this is a local configuration blocker rather than a reported code failure.

## 5. Work still required

### 5.1 Finish persisted no-vocals regression coverage

The implementation is in place, but the remaining focused tests should explicitly prove all relevant non-fallback boundaries:

1. **Selection alias rejection:** a `no_vocals` entry pointing to the existing valid vocals object must be rejected before persistence because it does not match the canonical no-vocals path.
2. **Render preflight after selection:** when selected no-vocals is missing while vocals is valid, render construction must inspect only the selected no-vocals path and return `stem_artifact_unavailable`.
3. **Malformed selected entry:** omitted/empty/partial `no_vocals` entries must result in `render_selected_stem_unavailable`, never a vocals lookup.
4. **Premium worker prop assembly:** missing/malformed selected no-vocals must reject before worker signing can substitute an alternate stem.
5. **Renderer runtime helper:** extract or test a shared selected-stem resolver to prove both `Composition.tsx` and `VideoScene.tsx` throw on missing selected no-vocals rather than returning vocals.

The stronger content-identity case (a maliciously overwritten no-vocals object with vocal bytes) requires a future durable per-stem object reference/hash contract; current guards ensure correct path and existence for normal API-created manifests.

### 5.2 Matte lifecycle: mandatory dependency, exact identity, and render blocking

This is the next implementation priority. Today, enabled background removal can be persisted while a matte is skipped due to optional dependencies, and render construction chooses `latest_matte_for_upload`, which is ambiguous and can proceed without an available matte. The release must replace that behavior.

#### Required invariant

If an effects configuration enables background removal, it must have a corresponding exact matte for the active upload and normalized segmentation settings. A render must not be queued until that exact matte is completed and usable.

#### Required changes

1. **Fail closed before effects queueing.**
   - In `apps/api/app/api/effects.py::update_effects`, determine effective background-removal settings after default/reset normalization.
   - Before effects work is queued, require a configured visual repository, matte queue, queue availability, and an active READY source upload.
   - If that capability is unavailable, return `503 background_removal_unavailable` and do not queue the effects job.
   - Keep plan-entitlement denial (`402 premium_effects_plan_required`) ahead of operational capability checks.

2. **Remove worker-side silent skipping.**
   - In `apps/api/app/services/layer_jobs.py::execute_effects_update`, do not use optional `getattr(..., None)` behavior for an enabled background-removal request.
   - Re-check capability in the worker because dependencies can change after the API queues work.
   - Queue/reuse the matte before persisting an enabled effects configuration as READY.
   - A queue/enqueue failure must fail the effects job and leave a recoverable, explicit matte state rather than a successful config with `matte_job_id=None`.

3. **Persist an explicit forward matte relation.**
   - Add a forward-only migration adding nullable `visual_presets.matte_id`.
   - Preserve nullability for disabled background removal and historical configurations that cannot be linked safely.
   - Add safe index/FK constraints where compatible, while retaining runtime tenant/project checks.
   - Extend `EffectsConfigRecord`, projections, row mapping, create methods, and API response with `matte_id` and an exact status URL.
   - Do **not** backfill historical rows by choosing the latest matte: historical effects configurations do not record enough source-upload identity to make that safe.

4. **Use exact record retrieval.**
   - Add repository `get_matte_by_id` / async equivalent, scoped by workspace/project.
   - Fix `run_matte_for_upload` so a supplied `matte_id` fetches that durable record rather than relying on the inherited in-memory map or a latest-record fallback.
   - Add a settings-aware legacy lookup only for historical null-linked configurations; it must match upload, normalized segmentation settings, and model version deterministically.

5. **Expose dedicated exact matte status.**
   - Add `GET /projects/{project_id}/visual/mattes/{matte_id}`.
   - Return raw lifecycle states (`pending`, `processing`, `completed`, `failed`, `skipped`) plus safe metadata, normalized settings, output metadata, retry/error details, and a signed preview URL for a completed matte.
   - Return tenant-scoped `404 matte_not_found` for unknown/cross-tenant IDs.
   - Keep `GET /visual/status` as its existing preview-oriented compatibility endpoint; it is not exact enough for render gating.

6. **Block rendering based on the exact matte.**
   - Replace `latest_matte_for_upload` in `apps/api/app/api/renders.py::_create_render_target`.
   - Validate linked matte ownership, active upload ID, normalized segmentation settings, model version, and a completed matte artifact.
   - Return recoverable errors instead of creating an export without a requested matte:
     - `503 background_removal_unavailable`
     - `409 background_matte_pending`
     - `409 background_matte_failed`
     - `409 background_matte_unavailable`
   - Continue normal render behavior when background removal is disabled.

7. **Add focused regression coverage.**
   - `apps/api/tests/test_effects.py`: missing capability returns 503 and does not create a success config; configured capability produces a matte relation/job.
   - `apps/api/tests/test_phase4_gpu_media_workers.py`: exact ID retrieval, non-default settings preservation, and stale/latest-matte avoidance.
   - `apps/api/tests/test_visual.py`: exact status endpoint for all lifecycle states, scoped 404, async-repository preference.
   - `apps/api/tests/test_renders.py`: linked matching matte wins over a newer unrelated matte; pending/failed/unavailable paths block export; disabled background removal does not require a matte.

### 5.3 Analytics integrity

The analytics audit work remains:

1. Apply optional `layer` filtering to `get_ai_edit_analytics` summaries, not just raw event lists.
2. Validate that `edit_plan_id` submitted to `record_ai_edit_feedback` belongs to the authenticated workspace and project.
3. Replace Supabase check-then-insert idempotency with an atomic, forward-only persistence/RPC approach.
4. Add SQL contract coverage for concurrent/idempotent insertion.
5. Do not delete or reconcile existing duplicate production analytics data; the release must only prevent new races.

### 5.4 Frontend, dashboard, and UI contracts

The remaining frontend work must use the hardened API states rather than conceal them:

| Area                                                  | Required completion                                                                                                                                                                   |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/web/components/editor/ScalePanel.tsx`           | Typed analytics response with an error/failure fallback; persisted stem selector wired to the new authenticated CAS route.                                                            |
| `apps/web/components/editor/preview/VideoPreview.tsx` | Use one shared `available && Boolean(previewUrl)` condition for matte preview behavior.                                                                                               |
| `apps/web/components/editor-client.tsx`               | Targeted matte status revalidation/polling using the exact matte status URL, especially after enabling background removal.                                                            |
| `apps/web/app/(app)/billing/actions.ts`               | Independent `env.billingEnabled` guards and an allow-list for plan values.                                                                                                            |
| `apps/web/lib/billing.ts`                             | Represent billing unavailability/disabled mode explicitly.                                                                                                                            |
| Dashboard page and `DashboardClient.tsx`              | Remove or accurately relabel the fake storage meter; no fabricated quota display.                                                                                                     |
| Shared contracts                                      | Regenerate/update `packages/shared/src/index.ts`, `packages/shared/src/generated/api.ts`, and `packages/shared/openapi.json` using the existing project convention after API changes. |

### 5.5 Task 4 — Source-only Compose/web/deployment configuration hardening

This task has not yet been implemented. It must be completed without starting services or deploying:

1. Inspect Compose and web routing configuration for exposed/misrouted paths and make routes explicit.
2. Restrict outbound network/egress behavior to the intended provider and infrastructure paths.
3. Configure file-backed secrets/sources safely so runtime configuration is not embedded in images or accidentally committed.
4. Use pinned/existing dependencies only; do not add unreviewed packages.
5. Validate configuration statically (`docker compose config` or equivalent only if it does not start/recreate containers) and do not run Docker/Supabase lifecycle commands without explicit approval.

### 5.6 Task 5 — Validation, review, and release readiness

After implementation, run the narrowest relevant checks first and then broader checks:

1. Targeted Python Ruff checks and focused pytest suites for changed API/services/repositories.
2. Node/worker focused tests and syntax checks; use a temporary approved non-secret test environment only if the worker package script requires `.env`.
3. Renderer/web type checks and test suites.
4. OpenAPI/shared-contract generation/checks after all schema/route changes.
5. Migration SQL tests and linting if a non-destructive database environment is made available.
6. `git diff --check`, targeted diff inspection, and a behavior-oriented final review.
7. Re-read this release scope and verify every stated success criterion before declaring release readiness.

## 6. Planned execution order

The remaining implementation should proceed in this order to minimize incompatible contracts and avoid partial fail-open behavior:

1. Add remaining no-vocals regression coverage and retain current passing behavior.
2. Create the forward-only matte relation migration and tolerant repository/schema readers.
3. Implement matte capability gating, mandatory worker enqueueing, exact record lookup, and the dedicated status endpoint.
4. Make render creation fail closed on exact matte state; add tests for pending, failed, unavailable, cross-upload, and stale/latest cases.
5. Wire the editor polling/preview behavior to the exact matte contract.
6. Finish analytics ownership/filtering/atomic idempotency work and SQL test coverage.
7. Finish billing-disabled frontend guards and dashboard storage-meter correction.
8. Regenerate shared OpenAPI/client contracts.
9. Complete Compose/web routing, controlled egress, and file-secret source hardening.
10. Run final targeted/broad validation, inspect the diff, and write the final completion summary.

## 7. Validation evidence and current blockers

### Completed validation evidence

| Scope                               | Result                                                                                             |
| ----------------------------------- | -------------------------------------------------------------------------------------------------- |
| Worker/render/queue hardening       | Focused Python and Node checks passed.                                                             |
| Generic-media lifecycle             | Ruff checks/formatting, Python compilation, focused pytest (59 passing), whitespace checks passed. |
| Security/billing API behavior       | `159 passed, 1 skipped, 2 warnings` in targeted pytest suite.                                      |
| Persisted stem/no-vocals API checks | Targeted Ruff check passed; selected test suite exited 0.                                          |
| Worker selected-media behavior      | `node --check` passed; six focused tests passed.                                                   |
| Renderer                            | `pnpm run typecheck` passed.                                                                       |

### Blocked validation

Local Supabase SQL lint/database integration cannot run because Docker/Postgres is unavailable. Observed failures:

```text
failed to inspect container health: error during connect: ... open //./pipe/docker_engine: The system cannot find the file specified.
failed to connect to postgres: ... host=127.0.0.1 user=postgres database=postgres ... connectex: No connection could be made because the target machine actively refused it.
```

Do not start/reset Docker or Supabase to resolve this blocker without explicit approval. The source and focused non-database tests can continue safely.

The worker package typecheck is also currently blocked by the absence of `apps/worker/.env`; direct syntax and focused Node checks were used instead. No real secret should be created or copied into this workspace merely to make that script execute.

## 8. Dirty-tree protection and review rules

The worktree already contains unrelated/in-progress changes, including renderer/web improvements, workspace picker work, and prior reliability/media-lifecycle hardening. The remaining work must:

- avoid blanket formatting or generated-file rewrites until contract generation is intentionally scheduled;
- preserve unrelated changes in `apps/api/app/api/analytics.py`, `apps/api/app/api/renders.py`, `apps/api/app/repositories/analytics.py`, `apps/api/app/services/scale.py`, renderer/web paths, queue/worker paths, and `semantic-review/`;
- use forward-only migrations only;
- avoid destructive Git actions, commits, or pushes unless separately requested; and
- record any validation that cannot run rather than treating a command exit status alone as release proof.

## 9. Definition of done

The hardening release is ready for review only when all of the following are true:

- [x] Worker/render/queue reliability changes are implemented and focused-validated.
- [x] Generic-media deletion/quota lifecycle is atomic, fenced, physically strict, and focused-validated.
- [x] Conditional auth and internal worker endpoint hardening are implemented and validated.
- [x] Billing-disabled API behavior is explicit and validated.
- [ ] Persisted no-vocals selection has complete API/worker/renderer regression coverage.
- [ ] Background removal has capability gating, an exact durable matte relation/status endpoint, and render-time fail-closed behavior.
- [ ] Analytics filters, ownership validation, and atomic idempotency are implemented with SQL coverage.
- [ ] Dashboard/editor/billing UI accurately represent the new backend contracts.
- [ ] Shared OpenAPI/client contracts are regenerated and checked.
- [ ] Compose/web routing, egress, and file-secret source configuration are hardened without deployment.
- [ ] Relevant API, worker, renderer, web, contract, migration, and whitespace validation has passed or is explicitly recorded as environment-blocked.
- [ ] Final diff review confirms unrelated dirty work was preserved and no destructive/deployment action occurred.
