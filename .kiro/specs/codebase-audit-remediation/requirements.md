# Requirements Document

## Introduction

This feature covers a depth audit of the Motionly monorepo at `motionly-main/Motionly-main` and the prioritized remediation of every finding it produces. The audit was performed directly against the working tree before these requirements were written; every assertion below cites a real file, symbol, or test name that was observed.

### Verified baseline at time of writing

| Surface | Command | Result |
| --- | --- | --- |
| `apps/api` | `python -m pytest tests/ -q` | 950 collected, **914 pass, 10 fail, 26 skip** (202s) |
| `apps/web` | `node --test tests/*.test.mjs` | 136 tests, **132 pass, 4 fail** (3 distinct root causes) |
| `apps/worker` | `node --env-file=.env --test src/*.test.mjs` | 73 tests, **71 pass, 1 fail, 1 skip** |
| `apps/renderer` | `pnpm --filter @motionly/renderer test` | **Cannot execute** — POSIX-only inline env assignment |
| `packages/shared` | `pnpm --filter @motionly/shared test` | Passes, but the script is only `tsc -p tsconfig.json --noEmit` — no tests exist |
| all TS projects | `pnpm -r typecheck` | **Passes** (4 of 5 projects; `apps/api` has no TS) |
| `apps/api` | `python -m ruff check apps/api` | **3 errors** |
| `apps/api` | `python -m ruff format --check apps/api` | 79 files would be reformatted, 207 already formatted |
| contracts | `python apps/api/scripts/generate_openapi_contract.py --check` | **Exit 0** — working tree matches runtime |

Total confirmed failing tests: **15** (10 Python, 4 web, 1 worker), plus one workspace whose suite cannot execute at all.

### Prior ledger is stale and partly incorrect

The existing artifacts `BASELINE-SUMMARY-AND-NEXT-STEPS.md` and `DEFECT-ANALYSIS-P1.md` were treated as a starting point, not as truth. Verification found:

- The ledger records **11** Python failures and **855** passing; the actual counts are **10** and **914**.
- The ledger lists `temporalio Missing` as an environment blocker and calls it an "optional dependency". `temporalio==1.31.0` is a **required** entry in `apps/api/pyproject.toml:18` and in `apps/api/requirements.lock:548`, it is installed, and all three previously-uncollectable test files now collect and run. No `--ignore` flags are needed.
- The ledger lists `DEF-003 test_temporal_health_uses_secure_client_rpc_and_redacts_transport_errors` as failing. It **passes**.
- The ledger states for DEF-006 that "DATA_HANDLING.md does not exist". The file **does exist** and is tracked at `motionly-main/Motionly-main/DATA_HANDLING.md`. DEF-006 is purely a test-path defect.
- The ledger lists `TypeScript BLOCKED — Missing node_modules`. `node_modules` is present at the workspace root and in `apps/web`, `apps/worker`, `apps/renderer`, `packages/shared`, and `pnpm -r typecheck` passes.
- The ledger states `Python Lint (Ruff) PASS — Zero linting errors` and `49 files` needing format. Actual: **3** lint errors and **79** files needing format.
- The ledger contains **zero** findings from `apps/web`, `apps/worker`, `apps/renderer`, or `packages/shared`. Five defects in those workspaces were found during this audit.

### Categories with no findings

Two categories were swept and came back clean. This is recorded so that remediation does not invent work:

- **Marker-based unfinished work:** zero occurrences of `TODO`, `FIXME`, `XXX`, or `HACK` across `apps/api/app`, `apps/web`, `apps/worker/src`, `apps/renderer/src`, and `packages`. Zero occurrences of `NotImplementedError` in `apps/api/app`.
- **Dead Python modules:** an AST-based import graph over all 191 modules under `apps/api/app` found only `app.workers.matte_job` and `app.workers.visual_job` unimported by Python, and both are live subprocess entry points invoked by `apps/worker/src/worker.mjs:476` as `python -m app.workers.<module>`. All 35 modules that define `router = APIRouter(...)` are registered in `apps/api/app/main.py`.

## Glossary

- **Monorepo**: the pnpm/turbo workspace rooted at `motionly-main/Motionly-main`, containing `apps/api`, `apps/web`, `apps/worker`, `apps/renderer`, and `packages/shared`.
- **API_Service**: the FastAPI application in `apps/api`, assembled by `create_app()` in `apps/api/app/main.py`.
- **Web_App**: the Next.js application in `apps/web`.
- **Worker_Service**: the BullMQ consumer process in `apps/worker`.
- **Renderer_Package**: the Remotion renderer in `apps/renderer`.
- **Shared_Package**: the generated-contract package `packages/shared`.
- **Audit_Report**: the single evidence document this feature produces, replacing `BASELINE-SUMMARY-AND-NEXT-STEPS.md`, `DEFECT-ANALYSIS-P1.md`, and `QUALITY-BASELINE-REPORT.md`.
- **Baseline_Suite**: the union of `apps/api` pytest, `apps/web` node tests, `apps/worker` node tests, `apps/renderer` node tests, and `pnpm -r typecheck`.
- **Contract_Generator**: `apps/api/scripts/generate_openapi_contract.py`, which emits `packages/shared/openapi.json` and `packages/shared/src/generated/api.ts`.
- **Internal_Worker_Router**: the `APIRouter` declared at `apps/api/app/api/internal_jobs.py:44` with prefix `/internal/jobs`.
- **Local_Storage_Router**: the `APIRouter` declared at `apps/api/app/api/local_storage.py:11` with prefix `/_local/storage`.
- **Feedback_Endpoint**: the `POST /feedback` handler `create_launch_feedback` at `apps/api/app/api/feedback.py:11`.
- **Verification_Gate**: any automated test or script whose stated purpose is to block a class of regression.
- **Vacuous_Gate**: a Verification_Gate that cannot fail, because it contains no assertion, because its assertion target is unreachable, or because its skip condition is always true.
- **Severity_Blocker**: a finding that prevents the Baseline_Suite from running to completion on a supported developer platform.
- **Severity_Correctness**: a finding that causes a Verification_Gate to report a false result or causes wrong runtime behavior.
- **Severity_Security**: a finding that weakens authentication, authorization, tenancy isolation, secret handling, or supply-chain pinning.
- **Severity_Cosmetic**: a finding that changes neither runtime behavior nor Verification_Gate outcomes.

## Requirements

### Requirement 1: Establish an accurate, reproducible audit ledger

**User Story:** As the engineer owning this remediation, I want one ledger whose every entry reproduces on demand, so that I fix real defects instead of stale ones.

#### Acceptance Criteria

1. THE Audit_Report SHALL record, for each finding, a finding identifier, a severity drawn from the set {Severity_Blocker, Severity_Correctness, Severity_Security, Severity_Cosmetic}, a file path with line number, a single reproduction command, the observed output, and the root cause.
2. THE Audit_Report SHALL record the measured counts 914 passing, 10 failing, and 26 skipped for the `apps/api` pytest suite as the pre-remediation baseline.
3. THE Audit_Report SHALL record the measured counts 132 passing and 4 failing for the `apps/web` node test suite and 71 passing, 1 failing, and 1 skipped for the `apps/worker` node test suite as the pre-remediation baseline.
4. THE Audit_Report SHALL mark the ledger entries `temporalio Missing`, `node_modules Missing`, `DEF-003`, and the claim that `DATA_HANDLING.md` is absent as superseded, and SHALL state the verified observation that contradicts each entry.
5. WHERE a finding could not be verified in the audit environment, THE Audit_Report SHALL label that finding `unverified` and SHALL state the specific capability that was absent.
6. WHEN a reviewer runs the reproduction command recorded for a finding, THE Baseline_Suite SHALL produce output that matches the observed output recorded for that finding.
7. THE Audit_Report SHALL carry every verified finding currently held in `BASELINE-SUMMARY-AND-NEXT-STEPS.md`, `DEFECT-ANALYSIS-P1.md`, and `QUALITY-BASELINE-REPORT.md`.
8. WHEN the Audit_Report carries the verified content of those three predecessor documents, THE Monorepo SHALL delete `BASELINE-SUMMARY-AND-NEXT-STEPS.md`, `DEFECT-ANALYSIS-P1.md`, and `QUALITY-BASELINE-REPORT.md`.

### Requirement 2: Repair the ten failing API tests

**User Story:** As a developer, I want the `apps/api` suite to pass without exclusions, so that a failing test is a real signal.

#### Acceptance Criteria

1. WHEN `python -m pytest tests/ -q` runs in `apps/api` with no `--ignore` flags, THE API_Service test suite SHALL report zero failures.
2. THE Internal_Worker_Router handler `run_queue_job` at `apps/api/app/api/internal_jobs.py:56` SHALL validate `x_motionly_worker_token` against `settings.worker_api_token` before reading `registry.jobs[queue_job_id]`, so that `tests/security/test_phase0_tenancy.py::test_internal_worker_route_rejects_missing_worker_token` and `::test_worker_route_rejects_wrong_worker_token` raise `ApiError(403, "worker_access_denied", ...)` instead of `ApiError(404, "queue_job_not_found", ...)`.
3. THE test `tests/test_analytics.py::test_ai_edit_analytics_summarizes_events_and_builds_fine_tuning_examples` SHALL pass the `request` argument that `get_fine_tuning_dataset` at `apps/api/app/api/analytics.py:107` requires, and THE test `tests/test_analytics.py::test_analytics_api_prefers_async_repository_methods` SHALL pass the same argument.
4. THE test `tests/test_phase7_failure_isolation.py::test_health_dependencies_endpoint_with_audio_ml` SHALL pass the `actor` argument that `dependencies` at `apps/api/app/api/health.py:119` requires.
5. THE test `tests/test_phase12_security.py::test_data_handling_documentation_exists` SHALL locate `DATA_HANDLING.md` through a path derived from `Path(__file__)` rather than the literal `/workspaces/Motionly-claude/motionly-main/Motionly-main/DATA_HANDLING.md` recorded at `apps/api/tests/test_phase12_security.py:169`.
6. THE test `tests/test_phase10_prelaunch.py::test_phase10_prelaunch_evidence_files_are_present` SHALL assert against a phrase that `infra/secrets/README.md` contains, given that neither the committed revision nor the working tree contains the literal `never commit` and both contain `Do not commit a local secret directory`.
7. THE test `tests/test_phase10_prelaunch.py::test_renderer_feature_matrix_is_backed_by_golden_and_encoded_gates` SHALL read `docs/renderer-feature-matrix.md` with an explicit `encoding="utf-8"` argument, so that the byte `0x9d` at position 116 no longer raises `UnicodeDecodeError` under the `cp1252` default codec.
8. THE test `tests/test_lightning_removed.py::test_no_lightning_references_remain_in_runtime_config_or_tests` SHALL treat `scripts/handoff-acceptance.json` as in-scope evidence rather than as an offender, given that line 71 of that file is the acceptance criterion `Custom GPU workers replace Lightning AI through a provider-neutral compute interface.` and line 73 names the very test that flags it.
9. WHERE the `openssl` executable is absent from the environment, THE test `tests/test_release_compose_inputs.py::test_release_compose_input_writer_uses_manifest_digests_and_0600_files` SHALL report a skip that names the missing executable rather than a failed assertion on `result.returncode`.

### Requirement 3: Repair the five failing checks outside `apps/api`

**User Story:** As a developer, I want the web, worker, and renderer suites to run and pass, so that the audit covers the whole monorepo rather than only the Python backend.

#### Acceptance Criteria

1. WHEN `node --test tests/*.test.mjs` runs in `apps/web`, THE Web_App test suite SHALL report zero failures.
2. WHEN `node --env-file=.env --test src/*.test.mjs` runs in `apps/worker`, THE Worker_Service test suite SHALL report zero failures.
3. THE Shared_Package SHALL contain `packages/shared/src/timeline-golden.json`, given that `apps/web/tests/timeline-time.test.mjs:32` reads that path, `apps/renderer/src/frame-boundary.test.mjs:74` reads that path, `docs/workers/render-correctness.md:18` documents that path as the shared timing golden, and the file is absent from the repository.
4. THE test `apps/worker/src/storage-streaming.test.mjs` SHALL locate the `uploadToStorage` function that exists at `apps/worker/src/remotion.mjs:586`, given that the regular expression `/async\s+function\s+uploadToStorage\([\s\S]*?\}\n/` at line 16 of that test returns `null` against the 1504 CRLF-terminated lines of `remotion.mjs`.
5. THE test `apps/web/tests/task11-proposal-workflow.test.mjs` SHALL match the source at `apps/web/components/editor/ai-step.tsx:758` where `planAppliedLocally = true;` is followed by `if (workflowOwned) {`, given that the expected literal at line 96 of that test embeds `\n` and the checked-out file uses `\r\n`.
6. THE test `apps/web/tests/service-hardening.test.mjs:30` SHALL assert a Content-Security-Policy `connect-src` expectation that matches the observability provider the Web_App actually uses, given that the assertion `assert.match(config, /sentry\.io/)` no longer matches `apps/web/next.config.ts`.
7. WHEN `pnpm --filter @motionly/renderer test` runs on Windows, THE Renderer_Package test suite SHALL execute its test files, given that the current script `TS_NODE_TRANSPILE_ONLY=true node --loader ts-node/esm --test src/*.test.ts` fails with `'TS_NODE_TRANSPILE_ONLY' is not recognized as an internal or external command`.
8. THE Renderer_Package scripts `test:encoded` and `test:golden` SHALL set `RUN_RENDERER_ENCODED_TESTS`, `RUN_RENDERER_GOLDEN_TESTS`, and `TS_NODE_TRANSPILE_ONLY` through a mechanism that executes on Windows and on POSIX shells.

### Requirement 4: Close the security findings

**User Story:** As a security reviewer, I want authorization to precede data access and unauthenticated write paths to be justified or removed, so that a non-production deployment is not a tenancy hole.

#### Acceptance Criteria

1. THE Internal_Worker_Router SHALL enforce worker-token validation inside every route handler in `apps/api/app/api/internal_jobs.py` in addition to the router-level `dependencies=[Depends(require_worker_token)]` declared at line 47, so that a direct in-process call to a handler cannot reach repository state without a token check.
2. THE Audit_Report SHALL record the verified distinction that HTTP requests to `/internal/jobs/{queue_job_id}/run` are guarded by the router-level dependency, and that the two failing tenancy tests invoke `run_queue_job` as a plain Python function and therefore bypass FastAPI dependency resolution.
3. THE Feedback_Endpoint SHALL require either an authenticated actor or a named non-user credential before writing to the `launch_feedback` table with `settings.supabase_service_role_key`, given that `apps/api/app/api/feedback.py:11` accepts only `payload` and `request`.
4. THE Feedback_Endpoint SHALL perform its outbound Supabase write through a non-blocking mechanism, given that `apps/api/app/api/feedback.py:20` is the only synchronous `httpx` call lexically inside an `async def` in the whole of `apps/api/app` and therefore blocks the event loop for up to its 10-second timeout.
5. WHERE the Local_Storage_Router is reachable, THE API_Service SHALL require a caller credential or a verifiable signature before serving `GET /_local/storage/{bucket}/{object_path}` or accepting `PUT /_local/storage/{bucket}/{object_path}`, given that `apps/api/app/api/local_storage.py` performs no actor lookup, no workspace check, and no signature check.
6. THE Audit_Report SHALL record that `_local_storage` at `apps/api/app/api/local_storage.py:13` raises `ApiError(404, ...)` when `settings.app_env == "production"`, that `app.include_router(local_storage_router)` at `apps/api/app/main.py:763` is unconditional, and that `LocalObjectStorage.object_path` together with `_safe_part` in `apps/api/app/services/local_storage.py` rejects directory traversal.
7. THE Audit_Report SHALL classify `handle_stripe_webhook`, `live`, `ready`, `metrics`, `complete_publish_oauth`, and the three `public_reviews` handlers as intentionally unauthenticated, and SHALL name the compensating control for each.
8. THE Monorepo SHALL declare the dependency overrides `@opentelemetry/core@<2.8.0 -> 2.8.0`, `postcss@<8.5.10 -> 8.5.10`, and `ws@>=8.0.0 <8.21.0 -> 8.21.0` in a location that the pinned package manager reads, given that every `pnpm` invocation in this workspace emits `The "pnpm" field in package.json is no longer read by pnpm. The following keys were ignored: "pnpm.overrides"`.
9. WHEN a dependency install runs after the override relocation, THE Monorepo SHALL resolve `@opentelemetry/core`, `postcss`, and `ws` to versions at or above the three pinned floors.
10. THE broad exception handlers whose entire body is `pass` at `apps/api/app/api/broll.py:272`, `apps/api/app/api/broll.py:367`, `apps/api/app/api/scale.py:530`, and `apps/api/app/repositories/ai_proposals.py:814` SHALL either record the suppressed error through the logging path in `apps/api/app/core/logging.py` or carry a comment stating why suppression is correct.

### Requirement 5: Restore a clean cross-platform verify loop

**User Story:** As a developer on Windows, I want the whole Baseline_Suite to run from a fresh clone, so that platform accidents are not mistaken for product defects.

#### Acceptance Criteria

1. WHEN a developer runs the Baseline_Suite on Windows and on Linux from a fresh clone, THE Monorepo SHALL execute every workspace test script on both platforms.
2. THE Monorepo SHALL contain a `.gitattributes` file that declares end-of-line normalization for source files, given that no `.gitattributes` exists and that two Verification_Gates fail solely because checked-out files use CRLF.
3. THE 75 `read_text()` calls across the 15 test files under `apps/api/tests` that omit an encoding argument SHALL pass an explicit `encoding="utf-8"`, so that the `cp1252` default codec cannot decide a test outcome.
4. THE API_Service test suite SHALL derive every filesystem path from `Path(__file__)` or an equivalent relative anchor, replacing the literal prefix `/workspaces/Motionly-claude` wherever that prefix appears.
5. THE script `scripts/assemble_docs.py` SHALL derive `workspace_dir` from its own location rather than from the literal `/workspaces/Motionly-claude/motionly-main/Motionly-main` at line 5, or THE Monorepo SHALL delete `scripts/assemble_docs.py` as unreferenced.
6. WHERE a `.env` file is absent, THE Worker_Service SHALL run its `test` and `typecheck` scripts to completion, given that all six `apps/worker` scripts currently pass `node --env-file=.env` and that Node fails when the named file is missing.
7. THE Monorepo SHALL document `apps/worker/.env` as a Baseline_Suite prerequisite in the contributor-facing setup instructions.
8. THE Audit_Report SHALL record each of the 26 skipped `apps/api` tests together with the capability that would let it run, covering the 10 skips reading `Live provider contracts are disabled`, the 7 requiring `RUN_REDIS_CONTRACTS`, the 3 requiring FFmpeg, the 2 requiring `RUN_SUPABASE_CONTRACTS`, the 1 requiring `RUN_TEMPORAL_TIME_SKIPPING_TESTS`, the 1 requiring `cv2`, the 1 reading `Queue analysis documentation not generated yet`, and the 1 reading `Not a git repository - cannot verify immutability`.
9. WHEN `python -m ruff check apps/api` runs, THE API_Service SHALL report zero errors, resolving `E501` at `apps/api/tests/test_database_schema_validation.py:52` and `F401` for `inspect` and `os` at `apps/api/tests/test_temporal_queue_canonical.py:10` and `:11`.
10. WHEN `python -m ruff format --check apps/api` runs, THE API_Service SHALL report zero files needing reformatting, reducing the current count of 79 to zero.

### Requirement 6: Finish or reverse the half-migrated features

**User Story:** As a maintainer, I want every partial migration either completed or reverted, so that no subsystem has live references on both sides of a rename or removal.

#### Acceptance Criteria

1. THE Web_App SHALL either initialize its error-reporting client or remove every remaining consumer of that client, given that `apps/web/instrumentation.ts:1` and `apps/web/instrumentation-client.ts:1` both read `Sentry removed` and export no-ops while `apps/web/app/global-error.tsx:3` still imports `@sentry/nextjs` and line 15 still calls `Sentry.captureException(error)`.
2. WHILE the Web_App error-reporting client stays uninitialized, THE Web_App SHALL render error-page copy that describes only the recovery actions available to the user, replacing the sentence `The error has been reported.` currently emitted by `apps/web/app/global-error.tsx`.
3. WHERE the observability removal is completed, THE Monorepo SHALL remove the `@sentry/nextjs` entry at `apps/web/package.json:19`, the tunnel route at `apps/web/app/api/glitchtip-tunnel/route.ts`, the `sentry_dsn` setting at `apps/api/app/core/config.py:369`, and the `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN`, `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`, and `SENTRY_URL` entries in `.env.example`, `apps/web/.env.example`, and `prod.env.example`.
4. THE Monorepo SHALL use one Temporal task-queue name across configuration and code, given that `.env.example:63`, `apps/api/.env.example:56`, and `prod.env.example:81` set `TEMPORAL_TASK_QUEUE=motionly-workflows` while `apps/api/app/orchestration/client.py:30`, `:45`, `:62`, `apps/api/app/orchestration/worker.py:24`, and `infra/docker-compose.gpu.yml:63` all default to `motionly-orchestration`.
5. THE Audit_Report SHALL record the Temporal task-queue divergence as a data-plane concern, and SHALL state the coordinated-deployment or queue-drain step required before the name changes in a running environment.
6. THE test `apps/api/tests/test_temporal_queue_canonical.py::test_migration_path_documented_for_queue_rename` SHALL locate its analysis document, given that it reads `ROOT / "TASK5-TEMPORAL-QUEUE-ANALYSIS.md"` where `ROOT` resolves to `motionly-main/Motionly-main` while the document sits at the outer workspace root and the test therefore always skips.
7. THE Audit_Report SHALL record that the Lightning AI removal is otherwise complete, citing that `apps/api/app/services/lightning_scaling.py` is absent so `tests/test_lightning_removed.py::test_lightning_service_module_is_deleted` passes, and that the surviving `pytorch-lightning` entries in `apps/api/requirements-ml.lock` are an unrelated machine-learning dependency excluded from the scan by the `.lock` suffix rule at `apps/api/tests/test_lightning_removed.py:63`.
8. THE Audit_Report SHALL correct the two `Needs verification` entries in `docs/architecture/motionly-tech-stack-and-data-flow.md` that this audit settled, recording that `stock_asset_enrichment` is present in `allQueueNames` at `apps/worker/src/queue-common.mjs:12` and that `temporalio` is a declared and installed dependency.

### Requirement 7: Remove dead code and unused declarations

**User Story:** As a maintainer, I want unreferenced declarations gone, so that the dependency and schema surface reflects what the product runs.

#### Acceptance Criteria

1. THE API_Service SHALL remove the `scipy` and `scikit-learn` entries from the `visual-ml` optional-dependency group in `apps/api/pyproject.toml`, or THE Audit_Report SHALL state the transitive requirement that keeps each entry, given that neither name appears anywhere under `apps/api`.
2. THE Audit_Report SHALL record that `uvicorn`, `ruff`, and `pip-licenses` are command-line entry points rather than dead dependencies, and that `pyannote.audio` and `Pillow` are live through the function-local imports at `apps/api/app/services/audio_ml_providers.py:84` and `apps/api/app/services/stock_video.py:398`.
3. THE Monorepo SHALL either add an application or SQL consumer for the tables `editorial_learning_evaluations` and `tenant_integrity_audit_runs`, or SHALL drop those two tables through a forward migration, given that each is created by exactly one migration and referenced by no other SQL file, no application module, and no document.
4. THE Audit_Report SHALL list the ten further tables that exist only as SQL-test subjects with no application consumer, namely `ai_edit_proposal_decisions`, `editorial_learning_policy_events`, `generic_media_deletion_jobs`, `performance_feedback_policy_events`, `tenant_integrity_constraint_manifest`, `tenant_integrity_findings`, `tenant_integrity_remediation_events`, `transactional_outbox`, `workflow_run_commands`, and `workflow_step_attempts`, and SHALL state for each whether the absent consumer is planned or abandoned.
5. THE Monorepo SHALL wire `scripts/validate-data-foundations.sh` and `scripts/validate-web-ingress.sh` into a workflow under `.github/workflows` or into a `package.json` script, or SHALL delete both files, given that neither is referenced from CI, `package.json`, `turbo.json`, documentation, or code.
6. THE Renderer_Package and Shared_Package SHALL declare distinct commands for `build`, `lint`, `typecheck`, and `test`, or SHALL remove the redundant aliases, given that all four resolve to `tsc -p tsconfig.json --noEmit` in `packages/shared/package.json` and that `build`, `lint`, and `typecheck` resolve to that same command in `apps/renderer/package.json`.
7. THE `turbo.json` task `build` SHALL declare outputs that the workspaces produce, given that it lists `dist/**` while the `build` scripts of `apps/renderer` and `packages/shared` pass `--noEmit` and emit nothing.
8. THE Audit_Report SHALL record that `apps/web/tests/dead-ui-exports.test.mjs` already guards six retired exports and passes, and SHALL state which additional workspaces adopt the same guard pattern.

### Requirement 8: Make every verification gate capable of failing

**User Story:** As a reviewer, I want a passing suite to mean something, so that a gate that cannot fail is not counted as coverage.

#### Acceptance Criteria

1. THE Baseline_Suite SHALL consist only of Verification_Gates that are capable of failing.
2. THE test `apps/api/tests/test_database_schema_validation.py::test_migrations_are_forward_only_not_edited` SHALL assert on its git history result, given that its body contains only a `print` call and therefore cannot fail.
3. THE test `apps/api/tests/test_database_schema_validation.py::test_migrations_are_forward_only_not_edited` SHALL pass git a path that resolves from the repository root that git actually uses, given that it passes `migration.relative_to(ROOT)` while the `.git` directory sits two levels above `ROOT` and every `git log` invocation therefore returns empty output.
4. THE test `apps/api/tests/test_database_schema_validation.py::test_no_migration_reordering_detected` SHALL evaluate its skip condition against the repository root that git uses, given that `(ROOT / ".git").exists()` is false in this checkout layout and the gate therefore always skips with `Not a git repository - cannot verify immutability`.
5. THE test `apps/api/tests/test_temporal_queue_canonical.py::test_data_plane_compatibility_documented` SHALL assert that the dispatcher, worker, and reconciler services agree on the task-queue name, given that its body reaches a bare `pass` inside two nested conditionals and executes no assertion.
6. WHEN a Verification_Gate is skipped, THE Baseline_Suite SHALL emit a skip reason that names the absent capability.
7. THE Audit_Report SHALL record that 36 test files under `apps/web/tests`, `apps/worker/src`, and `apps/renderer/src` assert against raw source text read through `readFile`, and SHALL state the mitigation chosen for the brittleness that produced the CRLF failures in Requirement 3.

### Requirement 9: Clear repository cruft and settle generated-artifact state

**User Story:** As a maintainer, I want the tracked tree free of scratch output and the generated contracts consistent with runtime, so that a clean checkout builds and verifies.

#### Acceptance Criteria

1. THE Monorepo SHALL untrack the 26 files under `.motionly/test-storage/exports/workspaces/` totalling 77272 bytes, given that `.gitignore:20` already lists `.motionly/` and that a gitignore entry does not untrack files already in the index.
2. THE Monorepo SHALL commit the regenerated `packages/shared/openapi.json` and `packages/shared/src/generated/api.ts`, given that `python apps/api/scripts/generate_openapi_contract.py --check` exits 0 against the working tree and that the committed revision therefore differs from runtime by 313 lines.
3. THE Audit_Report SHALL record that the generated-contract divergence is between the committed revision and runtime rather than between the working tree and runtime, and SHALL state that `pnpm contracts:check` invoked by `.github/workflows/phase-0-safety.yml:54` would fail on a clean checkout of the committed revision.
4. THE Monorepo SHALL delete the tracked documents `frontend_audit.md` and `engineering_audit.md`, or THE Audit_Report SHALL record both as current references, given that commit `aa795b9` is titled `Delete clean-copy, frontend_audit.md, and motionly-production-readiness-work-plan.md` while both files remain tracked at the repository root.
5. THE Monorepo SHALL consolidate or remove the nine untracked report documents at the repository root, namely `BLOCKER-RESOLUTION-SUMMARY.md`, `DOCKER-COMPOSE-ENV-GUIDE.md`, `ENV-SETUP-QUICK-REFERENCE.md`, `TASK6-COMPLETION-SUMMARY.md`, `TASK6-WEB-INGRESS-VALIDATION.md`, and `VALIDATION-RESULTS-SUMMARY.md`, together with the outer-workspace reports `TASK4-COMPLETION-REPORT.md`, `TASK4-DATA-FOUNDATIONS-PLAN.md`, and `TASK5-COMPLETION-REPORT.md`.
6. THE Audit_Report SHALL record that `.ruff_cache/` and `.turbo/` are present on disk, are matched by `.gitignore:2` and `.gitignore:13`, and carry no tracked files.
7. THE Audit_Report SHALL record that `apps/web/.env`, `apps/worker/.env`, and `infra/.env.local` exist on disk and are untracked, and that only `.env.example` variants are tracked.
8. THE Audit_Report SHALL account for each of the 22 modified or deleted paths and each untracked path reported by `git status`, and SHALL classify each as intended feature work, an audit artifact, or cruft.

### Requirement 10: Uphold the invariants after remediation

**User Story:** As a reviewer, I want the remediation to leave behind checkable invariants, so that the same defect classes cannot silently return.

#### Acceptance Criteria

1. THE API_Service SHALL satisfy the invariant that for every route handler under `apps/api/app/api/internal_jobs.py`, `apps/api/app/api/internal_renders.py`, `apps/api/app/api/internal_batches.py`, `apps/api/app/api/internal_retention.py`, and `apps/api/app/api/internal_workflows.py`, credential validation precedes the first repository or registry access, whether the handler is entered through HTTP or through a direct in-process call.
2. THE Monorepo SHALL satisfy the invariant that every provider imported by a module loaded at runtime has a reachable initialization path.
3. WHEN `python apps/api/scripts/generate_openapi_contract.py --check` runs against a clean checkout of the default branch, THE Contract_Generator SHALL exit with status 0.
4. THE Monorepo SHALL satisfy the invariant that the set of documented filesystem artifacts and the set of present filesystem artifacts agree, so that no document names a path the repository lacks.
5. THE Monorepo SHALL satisfy the invariant that a test outcome is independent of line-ending style and of the platform default text codec.
6. THE Monorepo SHALL satisfy the invariant that every table created by a migration under `supabase/migrations` has at least one reader or writer in application code, in SQL, or in a documented operational procedure.
7. THE Monorepo SHALL satisfy the invariant that each queue name, task-queue name, and environment-variable default is identical across code defaults, compose definitions, and example environment files.
8. WHEN a Verification_Gate is introduced or edited, THE Baseline_Suite SHALL demonstrate that the gate fails against a deliberately broken input before the gate is accepted.

### Requirement 11: Sequence the remediation by severity

**User Story:** As the engineer scheduling this work, I want a fixed order that clears blockers before cosmetics, so that each stage leaves the tree more verifiable than it found it.

#### Acceptance Criteria

1. THE remediation SHALL address findings in the order Severity_Blocker, then Severity_Security, then Severity_Correctness, then Severity_Cosmetic.
2. THE remediation SHALL treat the Renderer_Package script incompatibility in Requirement 3.7, the absent `packages/shared/src/timeline-golden.json` in Requirement 3.3, and the absent `.gitattributes` in Requirement 5.2 as Severity_Blocker.
3. THE remediation SHALL treat the handler-level token guard in Requirement 4.1, the Feedback_Endpoint exposure in Requirement 4.3, the Local_Storage_Router exposure in Requirement 4.5, the dependency-override relocation in Requirement 4.8, and the silently dead error reporting in Requirement 6.1 as Severity_Security.
4. THE remediation SHALL treat the Vacuous_Gate findings in Requirement 8 and the Temporal task-queue divergence in Requirement 6.4 as Severity_Correctness.
5. THE remediation SHALL treat the 79-file formatting gap in Requirement 5.10, the redundant script aliases in Requirement 7.6, and the document consolidation in Requirement 9.5 as Severity_Cosmetic.
6. WHEN a remediation stage completes, THE Baseline_Suite SHALL report a failure count at or below the count reported when that stage began.
7. IF a fix for one finding causes a previously passing Verification_Gate to fail, THEN THE Audit_Report SHALL record the regression together with the fix that introduced it.
8. IF a fix for one finding causes a previously passing Verification_Gate to fail, THEN THE remediation SHALL restore that gate to passing before the stage is marked complete.
