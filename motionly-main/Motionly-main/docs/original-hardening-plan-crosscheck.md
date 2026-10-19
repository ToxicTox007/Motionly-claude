# Original 21-Task Hardening Plan — Evidence-Based Cross-Check

**Status:** Source cross-check completed; hardening release implementation remains in progress.  
**Repository assessed:** `d:\Motionly-claude\motionly-main\Motionly-main`  
**Assessment date:** 2026-08-29  
**Related working document:** [`hardening-release-progress.md`](./hardening-release-progress.md)

## Purpose

This document cross-checks the **original 21-task Production-Safe Full Hardening Release plan** against the current source tree, existing migrations/tests, and validation evidence recorded during this working session.

It deliberately distinguishes the following facts:

- **Source-complete:** the intended source implementation and focused regression coverage exist. This is not a claim that a deployed environment has exercised it.
- **Partially implemented:** meaningful controls exist, but a material source, test, or contract requirement remains.
- **Runtime/container proof required:** source appears repaired, but the plan specifically requires built-image, Compose, database, or production-like evidence that has not been obtained.
- **Not implemented:** the planned behavior is absent or current behavior remains fail-open.
- **Operationally blocked:** validation cannot safely run in this workspace without an approved non-destructive environment action.

No deployment, Docker start/reset, Supabase reset, real-secret rotation, destructive reconciliation, or production repair was performed during this work. A source-level implementation or a passing unit test must **not** be treated as proof that production configuration, data, network policy, or rollback behavior has been verified.

## Executive summary

### Status by original task

| Original task                                   | Status                                   | Short conclusion                                                                                                                                                  |
| ----------------------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Static build and lint baseline               | **Partially implemented**                | CI/scripts and prior focused checks exist, but current full static validation is incomplete and the worker script requires a missing local `.env`.                |
| 2. Stable same-origin routing                   | **Partially implemented**                | Browser callers use relative `/api`, but production topology still relies on Next rewrites to a distinct API host and Caddy lacks explicit app-host path routing. |
| 3. Controlled egress                            | **Partially implemented**                | Private Docker networks and proxy clearing exist; an explicit provider allowlist/egress boundary and positive/negative runtime tests do not.                      |
| 4. File-secret contract and rotation readiness  | **Partially implemented**                | Substantial `_FILE` and SSM Parameter Store/release-agent support exists; some upstream environment interpolation and no real rotation/reload rehearsal remain.   |
| 5. Python worker import startup                 | **Runtime/container proof required**     | The `/app` CWD source repair and static tests exist; final built-image startup/import proof has not run.                                                          |
| 6. BullMQ failure normalization                 | **Source-complete**                      | Returned `{ failed: true }` is converted to an exception before a job can complete successfully.                                                                  |
| 7. Remotion completion/cleanup idempotency      | **Source-complete**                      | State-aware cleanup protects a canonical artifact after an ambiguous completion result.                                                                           |
| 8. FFmpeg bounded cancellation                  | **Source-complete**                      | Cancellation/timeout cleanup uses bounded terminate → kill escalation and avoids unread-pipe deadlock.                                                            |
| 9. Queue cancellation and bounded snapshots     | **Source-complete (scoped)**             | Cancellation markers and failed-job snapshot scans are bounded; general job listing pagination remains a separate concern.                                        |
| 10. Atomic generic-media creation               | **Source-complete**                      | Persistent creation is a single service-role RPC with all-or-nothing session/reservation/parts persistence.                                                       |
| 11. Strict media finalization/deletion          | **Source-complete**                      | Durable deletion intent and fenced physical-deletion acknowledgement prevent early quota release.                                                                 |
| 12. Migration/reconciliation/repair tooling     | **Partially implemented**                | Safe forward migrations, expiry cleanup, and proof-only backfill primitives exist; a complete operator-run reconciliation/repair tool does not.                   |
| 13. Local storage/metrics exposure              | **Partially implemented**                | Production guards exist, but routes are still registered universally and ingress/runtime evidence is required.                                                    |
| 14. Workspace selection/auth callback           | **Source-complete**                      | Membership-checked selection uses 303 redirect; callback errors return safely to login.                                                                           |
| 15. Explicit matte capability                   | **Not implemented**                      | Background removal can still persist/queue without a guaranteed exact matte, and renders can still proceed without one.                                           |
| 16. Safe billing disable/shell fallback         | **Partially implemented**                | API purchase paths are disabled and unmetered entitlements exist; web fallback/actions can still present or request misleading billing state.                     |
| 17. Authoritative dashboard/analytics           | **Partially implemented**                | Dashboard storage is fabricated; analytics summary filter, plan ownership, and atomic idempotency are incomplete.                                                 |
| 18. Full `no_vocals` selection                  | **Partially implemented**                | Persisted authenticated API/render propagation exists; UI/contract wiring and several no-fallback regressions remain.                                             |
| 19. Observability/health/reconciliation signals | **Partially implemented**                | Metrics, heartbeats, and some reconciliation exist; rollout-grade dependency readiness, alerts, and reconciler operational signals are not proven.                |
| 20. Release-grade E2E validation matrix         | **Partially implemented**                | Focused UI/unit/contract tests exist; no CI-gated production-like matrix exercises the complete browser → proxy → API → queue → worker → storage path.            |
| 21. Coordinated release and rollback rehearsal  | **Runtime/deployment evidence required** | Release/canary/rollback scripts exist, but no approved deployment or real rehearsal occurred.                                                                     |

### What is genuinely complete in source today

The strongest completed source work maps to original Tasks **6–11** (except the reconciliation/tooling portion of Task 12) and **14**:

- worker-domain failures cannot silently become completed BullMQ jobs;
- Remotion completion recovery protects a canonical artifact;
- FFmpeg cancellation is bounded and cleanup-aware;
- queue cancellation state and failed-job snapshot work are bounded;
- generic-media creation is atomic;
- strict deletion retains quota until durable physical-deletion acknowledgement; and
- workspace selection/auth callback behavior is deterministic and fail-closed.

These are source-complete controls, not deployment completion claims. They still need the final broad validation stage and production-like operational proof before release approval.

### Highest-priority remaining gaps

1. **Task 15 / matte lifecycle** — enabled background removal remains fail-open and can create an export without the requested exact matte.
2. **Tasks 2–4 / deployment topology** — same-origin routing, controlled provider egress, and end-to-end secret consumption need an explicit tested operational topology.
3. **Tasks 16–18 / product truthfulness** — billing fallback, fake dashboard storage, analytics integrity, and remaining no-vocals UI/regressions need completion.
4. **Tasks 12, 19–21 / operations** — an operator-run reconciliation program, evidence-producing health/alert signals, release matrix, and rehearsal are required before any real release.

## Evidence and validation already available

The following checks were run during this session or were recorded as completed focused checks. They are useful evidence but do not replace the broad release matrix.

| Scope                              | Evidence                                                                                                                                                                                          |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Security/billing API batch         | `python -m pytest tests/test_config.py tests/test_auth_projects_storage.py tests/test_billing.py tests/test_service_hardening.py tests/test_renders.py -q` → `159 passed, 1 skipped, 2 warnings`. |
| Persisted stem/no-vocals API paths | Targeted Ruff check passed; `tests/test_scale.py tests/test_ffmpeg_render.py tests/test_renders.py -q` exited `0`.                                                                                |
| Worker selected-media behavior     | `node --check src/remotion.mjs` plus focused worker media tests passed: 6 tests.                                                                                                                  |
| Renderer                           | `pnpm run typecheck` in `apps/renderer` passed.                                                                                                                                                   |
| Generic-media lifecycle            | Recorded focused pytest result: 59 tests passed, with one external deprecation warning; Ruff/format/compilation/whitespace checks passed.                                                         |
| Diff whitespace                    | `git diff --check` exited `0` on the current dirty worktree.                                                                                                                                      |

Known validation limits:

- Local Supabase SQL lint/database integration cannot run because Docker/Postgres is unavailable. Starting/resetting Docker or Supabase is not authorized.
- `apps/worker` package `typecheck` uses `node --env-file=.env` and is blocked by the absent local `apps/worker/.env`. Direct syntax checks and focused Node tests passed; this is not a substitute for its container/runtime contract.
- No current full `pnpm` root build/lint/typecheck/test result, full Compose topology run, migration application, live storage deletion test, or rollback rehearsal exists.

## Original research and discovery gates

The original plan correctly required several facts that source inspection cannot establish. Their state is shown separately so code completion does not hide operational work.

| Discovery gate                                                                        | Current state                           | What remains                                                                                                             |
| ------------------------------------------------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Actual outbound provider callers/domains/IP behavior and available egress enforcement | **Pending operational discovery**       | Inventory all deployed API/worker roles, provider domains/DNS behavior, and choose an enforceable proxy/firewall policy. |
| Active Stripe subscriptions/checkout/portal contractual obligations                   | **Pending operational discovery**       | Run a non-mutating subscription/checkout/customer-portal inventory before disabling billing in a real environment.       |
| Existing generic-media/orphan/object-reference/B-roll/export counts                   | **Pending operational discovery**       | Run a read-only classification report against a target database/storage environment.                                     |
| Secret ownership/consumer map and rollback-safe rotation order                        | **Partially documented, not rehearsed** | Assign owners, issue new credentials, prove dual-compatible rollout/restart behavior, then approve revocation order.     |
| Storage provider idempotent-delete behavior for already-missing objects               | **Not proven against provider**         | Execute a controlled non-production storage contract test and document expected missing-object response semantics.       |

## Target architecture cross-check

| Architecture element                                                   | Current state                    | Remaining work                                                                                                                                                                                                    |
| ---------------------------------------------------------------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Browser uses same-origin `/` and `/api`                                | **Partial**                      | Client API base is relative, but current production Next rewrite/Caddy routing still depends on a separate API host. Make one explicit app-host path-routing contract and test it in Compose.                     |
| Caddy routes app/API/Supabase deterministically                        | **Partial**                      | Caddy currently has separate app/API/Supabase host blocks. Add/verify explicit app-host `/api/*` and any required `/supabase/*` behavior or use private service upstreams without public cross-origin dependency. |
| Redis, databases, Supabase control plane, Temporal persistence private | **Partially source-implemented** | Internal Docker networks exist; prove the real deployment attaches only intended ingress/egress paths.                                                                                                            |
| Provider egress only through a controlled boundary                     | **Not complete**                 | Introduce an allowlisted egress proxy/firewall/network policy and validate allowed/denied requests.                                                                                                               |
| File-backed secrets                                                    | **Partial**                      | `_FILE` support/release-agent secret files exist, but all consumers/upstream images and rotation procedure need runtime proof.                                                                                    |
| Generic media atomic lifecycle                                         | **Source-complete**              | Apply migrations and prove against a target database/storage system.                                                                                                                                              |

# Detailed task-by-task cross-check

## Task 1 — Restore static build and lint correctness

**Status: Partially implemented.**

### Done / evidence

- Root scripts define `build`, `lint`, `typecheck`, `test`, `format`, and shared-contract commands in `package.json`.
- Web scripts define Next build, ESLint, TypeScript, and test commands in `apps/web/package.json`.
- API Ruff/pytest configuration exists in `apps/api/pyproject.toml`.
- `.github/workflows/phase-0-safety.yml` includes contract checks, root lint/typecheck, and API Ruff checks.
- The worktree contains the dashboard workspace-selection changes and targeted API/renderer validation noted above; earlier duplicate-import/static defects are no longer reported by the focused checks that ran.

### Still left

1. Run and record fresh release-candidate static gates: root lint/typecheck/build where practical, web typecheck/build, API Ruff, formatter, worker checks, and contract checks.
2. Make the worker static scripts CI-runnable without a developer’s untracked `.env`, using a deliberately non-secret fixture environment or a check mode that does not load `.env`.
3. Keep the current workspace selection additions intact while fixing only static regressions.
4. Ensure CI reports duplicate identifiers/import ordering failures deterministically rather than relying only on local execution.

### Completion proof required

A clean release-candidate run of web typecheck, API Ruff, worker checks, root contracts, and relevant format/diff checks.

## Task 2 — Stable same-origin Next routing

**Status: Partially implemented.**

### Done / evidence

- Browser-side API callers use a relative `/api` base through `apps/web/lib/env.ts`.
- `apps/web/next.config.ts` has a rewrite for `/api/:path*`.
- `infra/Caddyfile` and the production Compose topology define web/API/Supabase routing surfaces.

### Still left

1. The production web service still supplies `NEXT_PUBLIC_API_BASE_URL=https://${API_DOMAIN}`. The Next rewrite therefore targets a distinct host instead of a private API service or explicit Caddy app-host path route.
2. Caddy currently uses separate host blocks for app, API, and Supabase; it does not explicitly route `APP_DOMAIN/api/*` and `APP_DOMAIN/supabase/*` itself.
3. Choose one immutable image contract:
   - Caddy owns `APP_DOMAIN/api/*` (and, if required, `/supabase/*`) routing; or
   - Next proxies to private service names while browser callers stay relative.
4. Remove ambiguous/duplicate public proxy paths and retain only needed non-browser host routes.
5. Add a manifest/source test that proves no localhost API/Supabase target is baked into the web image.

### Completion proof required

Build a web image without public endpoint build arguments; start a controlled Compose topology; prove `/login`, `/api/health/live`, and an appropriate Supabase gateway path work from the web/browser origin.

## Task 3 — Controlled outbound egress

**Status: Partially implemented.**

### Done / evidence

- `infra/docker-compose.gpu.yml` defines private/internal networks for application, Temporal control, and Temporal persistence traffic.
- The ingress service is the intended public-network attachment.
- The volume-permission helper uses `network_mode: none`.
- Remotion clears `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY`, limiting `NO_PROXY` to loopback; this reduces unintended Chromium proxy use.
- Static Compose contracts cover explicit network membership and isolation from the Temporal persistence network.

### Still left

1. There is no explicit egress proxy, provider-dedicated network, host allowlist, or firewall policy for Groq, OpenRouter, Runway, Stripe, Sentry, model downloads, publishing APIs, and other provider callers.
2. Document every external caller by service role and its required domains/IP/DNS behavior.
3. Attach only provider-dependent API/worker roles to the controlled egress boundary.
4. Keep Redis, Postgres, Supabase internal services, Temporal persistence, and storage control planes private-only.
5. Distinguish provider capability failures from egress-denied failures in readiness/telemetry.

### Completion proof required

A Compose/infrastructure integration test showing an allowed service reaches an approved test endpoint while database/Redis/Temporal-only services cannot; provider readiness must report a distinct egress-denied diagnosis.

## Task 4 — File-secret contract and credential rotation readiness

**Status: Partially implemented.**

### Done / evidence

- Compose mounts Docker secrets and exposes `_FILE` variables for several application-managed secrets.
- `apps/api/app/core/config.py` loads an allowlisted set of secret-file values when a direct environment value is absent.
- `apps/worker/src/file-secret-startup.test.mjs` demonstrates worker/Remotion loading a token from a secret file.
- `scripts/deploy/apply_release.sh` retrieves SSM Parameter Store secrets into release-specific permission-restricted files; [`docs/security/secrets.md`](security/secrets.md) documents the model.
- Deployment-contract tests inspect portions of the file-secret/release-agent behavior.

### Still left

1. Verify `REDIS_URL_FILE` and every API/worker consumer follows the same file-precedence/missing-file contract, including reserved-character password encoding.
2. Several upstream images still require ordinary environment variables; the release script creates a protected Compose environment file to support them. Add minimal audited wrappers where viable and document exceptions that cannot be file-only.
3. Define secret rotation lifecycle: issuance, dual-compatible rollout, process restart/reload, verification, old-secret revocation, and rollback.
4. Prove file content never leaks into command lines, logs, committed files, rendered config, or inspectable durable intermediates.
5. Remove populated shared-workspace `.env`/`.env.local` files after an approved rotation, retaining only sanitized examples.

### Completion proof required

A non-production rotation drill using reserved-character test passwords, `_FILE` precedence/missing-secret assertions, application/worker/upstream startup verification, and secret-redaction inspection.

## Task 5 — Python worker import startup in built images

**Status: Runtime/container proof required.**

### Done / evidence

- `apps/worker/Dockerfile.python`, `Dockerfile.python.demucs`, and `Dockerfile.gpu` establish `PYTHON_MEDIA_CWD=/app` and place the Python application under `/app/app`.
- `apps/worker/src/worker.mjs` launches Python executors with the configured `/app` CWD before returning to the Node-worker location.
- `apps/worker/src/worker-supervision.test.mjs` asserts expected subprocess CWD and Dockerfile contracts.

### Still left

1. Build each CPU/GPU/Python worker image from the current worktree.
2. Run imports/pings as the final non-root container user with mounted writable media/model paths.
3. Exercise domain, visual, and matte JSON-line executors from their actual Node worker CWD.
4. Run a fixture-backed BullMQ job for each relevant executor.

### Completion proof required

Fresh built-image smoke results that show `import app.workers.domain_job`, `visual_job`, and `matte_job` work and that executors process a known fixture job without `ModuleNotFoundError`.

## Task 6 — Normalize BullMQ domain failure semantics

**Status: Source-complete.**

### Done / evidence

- `apps/worker/src/worker.mjs` centralizes failure-shape normalization through `throwIfJobResultFailed`.
- A returned `{ failed: true }` becomes a structured thrown error before success telemetry, workflow success completion, or BullMQ acknowledgement.
- Workflow and ordinary jobs share the normalization path while retaining workflow-specific failure behavior.
- `apps/worker/src/workflow-fencing.test.mjs` covers ordinary returned failure rejection and workflow failure rather than completion.
- The old unsupported BullMQ `start`/`complete`/`fail` no-op expectations were removed/redesigned in the reliability work.

### Still left

Run focused worker tests again as part of final validation and, in an approved test Redis environment, observe the actual BullMQ retry/failure terminal state and SLO metrics.

## Task 7 — Remotion completion and cleanup idempotency

**Status: Source-complete.**

### Done / evidence

- `apps/worker/src/remotion.mjs` uses attempt-scoped final artifact paths and the authoritative completion RPC.
- On ambiguous completion failure, it reads authoritative render state before cleanup.
- It retains an artifact if the attempt path has become canonical and deletes only paths proven not to be the canonical winner.
- `apps/worker/src/remotion-cas.test.mjs` covers lost-response canonical retention, losing-attempt cleanup, failed/canceled artifact cleanup, and idempotent completion behavior.

### Still left

Run a real Supabase/storage response-loss race in non-production to prove RPC, RLS, object storage, and retry behavior together.

## Task 8 — FFmpeg cancellation boundedness

**Status: Source-complete.**

### Done / evidence

- `apps/api/app/services/ffmpeg_render.py` avoids unread stdout/stderr pipe polling in cancellable execution.
- It checks cancellation/timeout with bounded polling, removes partial output, terminates, waits a grace period, and escalates to kill only when needed.
- Render cancellation remains distinct from ordinary render failure.
- `apps/api/tests/test_ffmpeg_render.py` covers termination, escalation, closed diagnostic pipes, and partial-output cleanup.

### Still left

Exercise a verbose real FFmpeg process and a real GPU/CPU cancellation path in a target image before calling the container behavior release-ready.

## Task 9 — Queue cancellation accounting and snapshot bounds

**Status: Source-complete for cancellation markers and failed-job snapshots.**

### Done / evidence

- `apps/worker/src/queue-bridge.mjs` stores cancellations in per-queue Redis sorted sets using one Lua operation that removes expired entries and avoids duplicate increments.
- Failed-job snapshot scanning has a configurable bound and reports `failed_scan_truncated` rather than walking all retained failures.
- Snapshot mapping preserves the bounded/truncated state for API consumers.
- Focused queue bridge/control-plane tests cover duplicate cancellation, cancellation exclusion, truncation, malformed snapshots, and related state.

### Scope caveat / remaining work

- The bound applies to ordinary queue **snapshots**. If administrative `jobs()` listing is expected to have a global cardinality limit, it remains fully paged and should be separately bounded/paginated by policy.
- Run real Redis concurrent-cancellation and expiry/purge contract tests with `RUN_REDIS_CONTRACTS` before operational sign-off.

## Task 10 — Atomic generic-media session creation

**Status: Source-complete.**

### Done / evidence

- `GenericMediaUploadSessionService.initiate()` delegates persistent creation to a single service-role RPC rather than independent quota/session/part inserts.
- `motionly_create_generic_media_upload_session` in `supabase/migrations/202608120001_generic_media_strict_deletion.sql` validates identity, tenant/project, allowed owner/type pair, canonical path, part plan, and idempotent session reuse.
- The RPC reserves quota and creates the session plus all part rows in one database invocation.
- The service handles possible response loss by reloading the exact session rather than blindly releasing quota.
- API/SQL tests cover the one-RPC creation path and its service-role boundary.

### Still left

Apply the migration and run the API/SQL tests against a non-production database, including injected part-creation failure and response-after-commit loss.

## Task 11 — Atomic ownership finalization and strict deletion

**Status: Source-complete.**

### Done / evidence

- B-roll and style-reference flows validate session/owner type/object type/tenant binding before completion.
- Finalization locks object references and sessions, verifies bucket/path/hash/size/ownership, commits quota, and links the durable reference atomically.
- User deletion creates durable generic-media deletion intent rather than directly releasing quota.
- Generic deletion acknowledgement waits for sibling deletion rows and only then finalizes/release quota; normal acknowledgement cannot incorrectly consume generic deletion rows.
- Generic rows clear direct quota identifiers for rolling-worker safety.
- Retention dispatches generic deletion rows through fenced generic acknowledgement.
- `supabase/migrations/202608120001_generic_media_strict_deletion.sql`, `supabase/tests/task_25_generic_media_strict_deletion_test.sql`, and focused API tests cover the database/service contract.

### Still left

Apply and observe the flow in staging: deletion failure remains visibly retryable with quota retained; a successful retry releases quota exactly once; deleting/deleted objects are never handed out through signed URL flows.

## Task 12 — Migration, reconciliation, and repair tooling

**Status: Partially implemented.**

### Done / evidence

- Forward-only migrations add durable generic-media session/part/owner fields and strict deletion primitives.
- `202608110001_asset_library_durable_references.sql` supplies a service-role-only, rerunnable **proof-only** asset-library reference backfill RPC.
- Legacy media without proof is quarantined/failed rather than guessed into a reference.
- Retention expires a class of stranded generic-media sessions.
- SQL tests cover proof-only backfill and lifecycle/finalization function properties.

### Still left

1. Add a checked-in dry-run reconciliation command/report that classifies all original-plan cases: missing parts, completed sessions lacking references, verified assets lacking references, missing active storage, quota not released after delete, and canonical export artifact mismatch.
2. Add bounded, idempotent repair actions with audit logging and explicit human approval gates.
3. Provide an operator-facing way/runbook to invoke and report the existing proof-only backfill safely.
4. Add legacy-upgrade fixtures and prove running repair twice reaches the same state without tenant crossing.
5. Do not delete/reconcile production data until a human approves a dry-run report.

## Task 13 — Conditional local-storage and metrics exposure

**Status: Partially implemented.**

### Done / evidence

- `apps/api/app/api/local_storage.py` returns unavailable in production or when Supabase storage is configured without forced local media.
- Production configuration validation rejects forced local media and missing metrics protection.
- `apps/api/app/api/operations.py` supports bearer-token protection for `/metrics`.
- Local storage performs path validation, with traversal/prod-unavailable tests.

### Still left

1. `local_storage_router` and operations router are registered unconditionally. In non-production configurations using local storage, the local route remains broad rather than absent/loopback-only.
2. Confirm and enforce ingress behavior: staging-like environments must not expose local storage, and `/metrics` must be accessible only to the intended collector/internal listener.
3. Add HTTP authorization/ingress contract tests for metrics and local storage, not only source/config checks.
4. Preserve developer fixture storage without exposing an unauthenticated public storage route.

### Completion proof required

A staging-like startup configuration must reject forced local storage/missing metrics token and demonstrate that neither `/_local/storage` nor unauthenticated `/metrics` is publicly accessible.

## Task 14 — Workspace selection and authentication callback

**Status: Source-complete.**

### Done / evidence

- `apps/web/app/api/workspaces/select/route.ts` verifies active membership before storing the selected workspace ID in an HTTP-only, SameSite=Lax cookie.
- A selector POST returns a 303 redirect, preserving correct POST → GET browser behavior.
- `apps/web/lib/api-auth.ts` rechecks membership and only auto-selects a workspace when exactly one valid membership exists.
- `apps/web/app/auth/callback/route.ts` requires/exchanges an OAuth code and routes missing/invalid/exchange errors safely to login without exposing provider internals.
- Workspace/auth route tests cover redirect/exchange structure; dashboard/picker sources handle missing/invalid selection state.

### Still left

Run a live browser/Supabase integration test for two workspaces, membership revocation between selection/dashboard, and invalid/expired/replayed OAuth code behavior.

## Task 15 — Explicit matte execution capability

**Status: Not implemented; highest functional gap.**

### Current unsafe behavior

- `apps/api/app/api/effects.py::update_effects` normalizes background-removal settings and queues effects without proving that the visual repository, matte queue, worker capability, or active READY upload exists.
- `apps/api/app/services/layer_jobs.py::execute_effects_update` creates a READY effects config before optional `getattr(... visual_repository/matte_queue ...)` checks; absent dependencies can result in a successful configuration with no matte job.
- `apps/api/app/api/renders.py::_create_render_target` selects `latest_matte_for_upload`, writes a status into render props, and still queues an export when the requested matte is pending, failed, absent, mismatched, or not exact.
- Visual repositories lack an exact tenant/project-scoped matte lookup by ID; persistent matte workers can fall back to latest records.
- There is no durable `visual_presets.matte_id` relationship and no exact matte lifecycle endpoint.

### Required implementation

1. Before queueing enabled background removal, return `503 background_removal_unavailable` if capabilities/dependencies are unavailable.
2. Recheck the same capability in the worker and never silently skip matte enqueueing.
3. Add a forward-only nullable `visual_presets.matte_id` relationship. Do not unsafe-backfill historical configurations with a latest matte.
4. Add exact sync/async repository lookup and deterministic constrained legacy fallback by upload + normalized segmentation settings + model version.
5. Add `GET /projects/{project_id}/visual/mattes/{matte_id}` with raw lifecycle state, safe error/retry data, and completed preview URL.
6. At render creation, require the exact linked/matching completed matte. Return:
   - `503 background_removal_unavailable`
   - `409 background_matte_pending`
   - `409 background_matte_failed`
   - `409 background_matte_unavailable`
7. Add effects, matte worker, visual endpoint, render, and editor polling regressions.

## Task 16 — Safely disable billing and make shell data noncritical

**Status: Partially implemented.**

### Done / evidence

- `BILLING_ENABLED` defaults to false in API settings.
- API checkout, portal, and webhook paths fail closed with billing-disabled behavior.
- Billing status reports an explicit disabled/unmetered contract with Agency-equivalent personal entitlements, rather than a fake Free plan.
- The billing page disables visible management/checkout controls when billing is disabled.
- Focused API billing/security validation passed in this session.

### Still left

1. `apps/web/lib/billing.ts` still has a fallback modeled as a metered/restricted Free subscription on no context, non-OK response, or exception. Replace it with explicit unavailable/disabled state.
2. `apps/web/app/(app)/billing/actions.ts` needs independent `env.billingEnabled` guards and a server-side allowlist for payable plan values.
3. Remove/adjust mock price-plan and “Free workspace” language so UI never contradicts the disabled API contract.
4. Make `AppShell`/all authenticated pages resilient to billing fetch failure without showing fictional entitlement data.
5. Add web tests for disabled billing, API failure, missing workspace, checkout action guard, and no portal/checkout network request.
6. Before an actual billing disable, complete the non-mutating active-subscription/checkout/portal inventory from the research gate.

## Task 17 — Authoritative dashboard and analytics data

**Status: Partially implemented.**

### Done / evidence

- Dashboard project data comes from authenticated API calls in the happy path.
- Analytics event listing is workspace/project scoped and accepts a layer parameter for the event list.

### Still left

1. `DashboardClient` computes “Storage used” as `Math.min(localProjects.length * 10, 100)`. Remove it or replace it with an authoritative quota/object-reference usage endpoint and truthful loading/error state.
2. Ensure ready/draft counts and upload state are not silently distorted by a limited project list or per-upload fetch failures.
3. Pass the optional `layer` filter to analytics summaries, not only event listing.
4. Validate `edit_plan_id` belongs to the actor workspace and requested project before recording AI-edit feedback.
5. Replace Supabase check-then-insert analytics idempotency with an atomic, forward-only RPC/persistence operation and add concurrent SQL coverage.
6. Derive training-example count from the same policy-filtered frozen dataset as fine-tuning export, or remove the field until authoritative.

## Task 18 — Ship complete authenticated `no_vocals` selection

**Status: Partially implemented.**

### Done / evidence

- `StemSelectionUpdateRequest` defines a typed selected active stem with expected revision.
- `PATCH /projects/{project_id}/phase5/stems/{resource_id}` validates actor workspace/project/resource ownership, resource kind, READY state, exact canonical artifact path, durable object existence, and CAS revision.
- Render preflight includes selected stem state and rejects an unavailable explicit selected stem.
- FFmpeg, Remotion prop assembly, `Composition.tsx`, and `VideoScene.tsx` select the active stem rather than using a direct no-vocals-to-vocals fallback.
- Focused API/FFmpeg and worker media tests have passed.

### Still left

1. Add an API selection test rejecting `no_vocals` that aliases the valid vocals object.
2. Add render-preflight tests proving it inspects only the selected no-vocals path while a valid vocals path exists.
3. Add malformed/partial no-vocals manifest tests that prove no vocals lookup occurs.
4. Add premium-worker signing/prop-assembly regression coverage for missing/malformed selected no-vocals.
5. Extract/test a shared renderer selected-stem resolver or add runtime coverage for both `Composition.tsx` and `VideoScene.tsx`.
6. Wire/test the editor selector in `ScalePanel.tsx`, including unavailable/processing/revision-conflict states.
7. Regenerate shared API/OpenAPI contracts after route/schema completion.
8. Consider a future durable per-stem object-reference/hash identity if the release must protect against bytes overwritten at a canonical no-vocals path. Current code validates normal API-created path/existence, not audio content identity.

## Task 19 — Operational observability, health, and reconciliation signals

**Status: Partially implemented.**

### Done / evidence

- API live/ready endpoints exist.
- Prometheus-style queue/backpressure/worker-heartbeat/cost/provider metrics exist in operations paths.
- Worker-token-protected heartbeat/provider SLO writes exist.
- Bounded workflow reconciliation SQL and application reconciler components exist.
- Worker failure, canonical render cleanup, queue state, and media lifecycle implementation include structured telemetry-oriented behavior.

### Still left

1. Make release readiness validate required worker roles, secret files, route connectivity, and egress policy without exposing secrets.
2. Connect deletion backlog/failure, reconciliation backlog/result, provider egress denial, and media/quota mismatch to durable metrics and actionable alerts.
3. Prove reconciler scheduling, identity, last-run success/failure, and bounded backlog behavior in a production-like runtime.
4. Restrict sensitive identifiers/URLs in logs and add explicit redaction event tests.
5. Add alert threshold configuration tests rather than hardcoded/undemonstrated operations assumptions.
6. Avoid treating `/health/live` as a full readiness/business-workflow proof; it is intentionally dependency-free.

## Task 20 — Release-grade end-to-end validation matrix

**Status: Partially implemented.**

### Done / evidence

- Web package declares Playwright and a `test:browser` command; `pnpm verify --stage browser` runs the browser suite against the CPU stage in five setups.
- Focused Playwright workflow UI tests, Node source-contract tests, API unit/contract tests, worker tests, migrations, and deployment scripts exist.
- Existing CI workflows cover portions of static validation, supply chain, contracts, and deployment artifact flow.

### Still left

1. Commit a release matrix that identifies test layer, environment, fixtures, owners, exact pass/fail criteria, and retained evidence location.
2. Make the matrix CI-gated for a production-like topology; current Playwright harness stubs workflow requests and does not run the deployed app/API/queue/storage path.
3. Cover same-origin routing, secret-file startup, egress allowed/denied, worker image imports/pings, BullMQ retry, generic-media lifecycle, deletion/reconciliation, workspace/OAuth, billing-disabled shell, no-vocals, matte lifecycle, render/export, migrations/RLS, and rollback compatibility.
4. Use controlled provider stubs where external providers are inappropriate in CI, while still exercising real network policy boundaries where required.
5. Retain sanitized logs, screenshots, traces, reports, and migration evidence for failed release candidates.

## Task 21 — Coordinated release and rollback rehearsal

**Status: Runtime/deployment evidence required.**

### Done / evidence

- `scripts/canary-gate.mjs` has staged 5/25/100 canary, SLO comparison, HTTPS/authorization checks, failure rollback request, and stable-health verification logic.
- `scripts/canary-gate.test.mjs` has simulated success/failure/rollback coverage.
- `.github/workflows/deploy-aws.yml` publishes signed release artifacts to S3 and applies them through the instance's release agent over SSM Run Command.
- `scripts/deploy/apply_release.sh` records previous/current releases and attempts reactivation of the previously recorded release when activation/liveness fails.

### Still left

1. Wire the real deployment flow to the canary-gate/metrics/rollback evidence contract; source inspection did not show the deployment workflow invoking the standalone canary gate.
2. Produce a written executable runbook with named owners, approval gates, communications, backup/restore checks, migration order, feature/config rollback, secret rotation order, and non-destructive repair scope.
3. Run an approved staging/promotion rehearsal using the exact signed artifact and topology.
4. Collect real baseline/candidate metrics at every canary stage and deliberately trigger an application rollback to the recorded stable revision.
5. Verify forward-only migration compatibility, data integrity, business workflows, and restoration after rollback.
6. Obtain product/security/data/operations sign-off. Do not perform an actual release until all preceding source/runtime gates pass.

# Recommended remaining execution order

The original plan can now be condensed into the following safe implementation order without losing any required control:

1. Finish no-vocals regressions and editor/contract integration (Task 18).
2. Implement the complete exact-matte invariant, status endpoint, render blocking, and frontend polling (Task 15).
3. Finish billing shell/action guards and authoritative dashboard/analytics work (Tasks 16–17).
4. Build operator-safe reconciliation/reporting tools and related observability signals (Tasks 12, 19).
5. Complete same-origin routing, egress boundary, secret-file exception handling, and worker-image proof preparation (Tasks 2–5, 13).
6. Add the release-grade test matrix and run source/unit/contract/container/Compose checks in an approved non-production environment (Task 20).
7. Perform discovery inventories, migration application planning, dry-run classification, rotation planning, and staged rehearsal only with explicit operational approval (research gates and Task 21).

# Release completion criteria restated

The original release is **not complete** until all of these are true:

- [ ] Full static checks, API/worker/renderer/web tests, contracts, migrations, containers, browser flows, and Compose release tests pass with retained evidence.
- [ ] Same-origin routing, controlled egress, and file-secret behavior are proven in the deployed topology.
- [ ] No critical/high audit issue remains, especially the matte fail-open path.
- [ ] Existing inconsistent media data has a non-destructive dry-run classification, approved repair/quarantine plan, and monitoring.
- [ ] Billing is truthful and unavailable across both API and UI; no fabricated fallback state remains.
- [ ] Strict deletion and full no-vocals selection work end-to-end through real storage/queues/render engines.
- [ ] Observability shows actionable health, queue, provider, deletion, and reconciliation state without sensitive leakage.
- [ ] A signed release artifact passes a staging rehearsal, canary/rollback proof, and formal security/data/operations/product sign-off.

Until then, the correct status is: **significant source hardening completed; production-safe full release not yet ready for deployment.**
