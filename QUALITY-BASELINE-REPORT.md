# Quality Baseline Report
**Generated**: 2026-08-30  
**Branch**: main  
**Commit**: HEAD  
**Objective**: Establish reproducible defect ledger before fixes

---

## 1. Repository Status

### Modified Files (13)
- `.gitignore` (+2)
- `apps/api/app/api/internal_jobs.py` (+3)
- `apps/api/app/api/publishing.py` (+29)
- `apps/api/app/core/config.py` (+62)
- `apps/api/app/main.py` (+19)
- `apps/api/app/services/model_domain_adapters.py` (+4)
- `apps/api/app/services/model_gateway.py` (+29)
- `apps/api/app/services/model_gateway_factory.py` (+35)
- `apps/api/app/services/publishing.py` (+22)
- `infra/secrets/README.md` (+6)
- `package.json` (4 +/- 1)
- `packages/shared/openapi.json` (+313 lines, -1 line)
- `packages/shared/src/generated/api.ts` (+10, -4)

**Total**: 524 insertions, 14 deletions

### Untracked Files (9)
**New local-audit infrastructure**:
- `apps/api/app/core/local_audit.py` (new feature)
- `apps/api/scripts/audit_local_capabilities.py` (preflight)
- `apps/api/scripts/audit_local_topology.py` (topology validation)
- `apps/api/tests/test_local_audit_capabilities.py` (17 tests)
- `apps/api/tests/test_local_topology_audit.py` (isolated checks)
- `infra/Caddyfile.local-audit` (loopback ingress)
- `infra/docker-compose.local-audit.yml` (isolated overlay)
- `infra/local-audit/` (directory with env template, matrix)
- `semantic-review/` (directory outside main tree)

**Analysis**: All untracked files are deliberate additions for Task 2 local-audit implementation. Not "dirt" to be removed.

---

## 2. Static Check Results

### ✅ PASS: Python Dependency Health
```
Command: cd apps/api && python -m pip check
Result: No broken requirements found.
```

### ✅ PASS: Contract Validation
```
Command: pnpm contracts:check
Result: Exit code 0
Note: Generated OpenAPI matches runtime schema
```

### ✅ PASS: Ruff Lint (Python)
```
Command: cd apps/api && python -m ruff check .
Result: All checks passed!
```

### ❌ FAIL: Ruff Format (Python Code Style)
```
Command: cd apps/api && python -m ruff format --check .
Result: 49 files would be reformatted
Severity: P3 (style/consistency)
```

**Files requiring format** (partial list):
- `app/api/analytics.py`
- `app/api/billing.py`
- `app/api/internal_jobs.py`
- `app/api/publishing.py`
- `app/core/config.py`
- `app/core/local_audit.py`
- `app/main.py`
- `app/services/model_domain_adapters.py`
- `app/services/model_gateway.py`
- `app/services/model_gateway_factory.py`
- `app/services/publishing.py`
- `scripts/audit_local_capabilities.py`
- `scripts/audit_local_topology.py`
- *(and 36 more)*

**Impact**: Format inconsistency only, no functional defect

### ❌ FAIL: Pytest (Unit/Integration Tests)
```
Command: cd apps/api && python -m pytest tests/ -q --ignore=tests/test_task5*.py --ignore=tests/test_temporal_orchestration.py
Result: 11 failed, 855 passed, 23 skipped
Severity: P1-P2 (reproducible test failures)
```

**Collection Errors** (environment blockers):
- `test_task5_draft_render.py`: Missing `temporalio` module
- `test_task5_proposal_gate.py`: Missing `temporalio` module
- `test_temporal_orchestration.py`: Missing `temporalio` module

**Test Failures** (11):
1. `tests/security/test_phase0_tenancy.py::test_internal_worker_route_rejects_missing_worker_token`
2. `tests/security/test_phase0_tenancy.py::test_worker_route_rejects_wrong_worker_token`
3. `tests/test_analytics.py::test_ai_edit_analytics_summarizes_events_and_builds_fine_tuning_examples`
4. `tests/test_analytics.py::test_analytics_api_prefers_async_repository_methods`
5. `tests/test_health.py::test_temporal_health_uses_secure_client_rpc_and_redacts_transport_errors`
6. `tests/test_lightning_removed.py::test_no_lightning_references_remain_in_runtime_config_or_tests`
7. `tests/test_phase10_prelaunch.py::test_phase10_prelaunch_evidence_files_are_present`
8. `tests/test_phase10_prelaunch.py::test_renderer_feature_matrix_is_backed_by_golden_and_encoded_gates`
9. `tests/test_phase12_security.py::test_data_handling_documentation_exists`
10. `tests/test_phase7_failure_isolation.py::test_health_dependencies_endpoint_with_audio_ml`
11. `tests/test_release_compose_inputs.py::test_release_compose_input_writer_uses_manifest_digests_and_0600_files`

### ❌ BLOCKED: TypeScript Lint
```
Command: pnpm lint
Result: 'turbo' not recognized, node_modules missing
Severity: Environment Blocker
Issue: pnpm install not run, missing dependencies
```

### ⚠️ WARNING: pnpm Configuration Deprecation
```
Result: "pnpm.overrides" field in package.json is deprecated
Severity: P3 (configuration drift)
```

### ❌ FAIL: Docker Compose Configuration Validation
```
Command: docker compose -f infra/docker-compose.gpu.yml config -q
Result: 19 required variables missing
Severity: P2 (deployment blocker without .env file)
```

**Missing required variables**:
- `POSTGRES_PASSWORD`
- `SUPABASE_JWT_SECRET`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `REDIS_PASSWORD`
- `TEMPORAL_BOOTSTRAP_ADMIN_UNTIL`
- `MOTIONLY_TEMPORAL_SERVER_IMAGE`
- `MOTIONLY_API_IMAGE`
- `TEMPORAL_ADMIN_TOOLS_IMAGE`
- `MOTIONLY_WEB_IMAGE`
- `API_DOMAIN`
- `APP_DOMAIN`
- `SUPABASE_DOMAIN`
- *(and 6 more)*

**Analysis**: Expected behavior - Compose requires .env file for runtime. Not a defect unless secrets are leaked.

---

## 3. Generated Artifact Drift

### OpenAPI Schema Drift
**File**: `packages/shared/openapi.json`  
**Changes**: +313 lines, -1 line

**New schemas added**:
- `BillingMode` enum (managed, personal)
- `BillingStatusResponse` fields:
  - `billing_enabled: boolean`
  - `billing_mode: BillingMode`
  - `unmetered: boolean`

**Analysis**: Generated from FastAPI runtime. Schema drift indicates billing API changes not yet committed or regenerated inconsistently.

### TypeScript API Client Drift
**File**: `packages/shared/src/generated/api.ts`  
**Changes**: +10 lines, -4 lines

**Analysis**: TypeScript types updated to match OpenAPI schema. Should be regenerated after OpenAPI is stabilized.

---

## 4. Defect Ledger

### P0: Critical/Security/Data Integrity
*None identified in static checks*

### P1: Test Failures (Reproducible)

#### DEF-001: Worker Authentication Tests Failing
**Severity**: P1  
**Scope**: Security tenancy  
**Tests**:
- `test_internal_worker_route_rejects_missing_worker_token`
- `test_worker_route_rejects_wrong_worker_token`

**Reproduction**: 
```bash
cd apps/api
python -m pytest tests/security/test_phase0_tenancy.py::test_internal_worker_route_rejects_missing_worker_token -v
```

**Owner**: Unassigned  
**Proposed Test**: Already exists (failing)  
**Fix**: TBD after reproduction analysis  
**Validation**: Test passes

---

#### DEF-002: Analytics Tests Failing
**Severity**: P1  
**Scope**: Analytics repository  
**Tests**:
- `test_ai_edit_analytics_summarizes_events_and_builds_fine_tuning_examples`
- `test_analytics_api_prefers_async_repository_methods`

**Reproduction**:
```bash
cd apps/api
python -m pytest tests/test_analytics.py::test_ai_edit_analytics_summarizes_events_and_builds_fine_tuning_examples -v
```

**Owner**: Unassigned  
**Proposed Test**: Already exists (failing)  
**Fix**: TBD  
**Validation**: Tests pass

---

#### DEF-003: Temporal Health Check Test Failing
**Severity**: P1  
**Scope**: Health endpoint  
**Test**: `test_temporal_health_uses_secure_client_rpc_and_redacts_transport_errors`

**Reproduction**:
```bash
cd apps/api
python -m pytest tests/test_health.py::test_temporal_health_uses_secure_client_rpc_and_redacts_transport_errors -v
```

**Owner**: Unassigned  
**Proposed Test**: Already exists (failing)  
**Fix**: TBD  
**Validation**: Test passes

---

#### DEF-004: Lightning Reference Removal Test Failing
**Severity**: P1  
**Scope**: Cleanup verification  
**Test**: `test_no_lightning_references_remain_in_runtime_config_or_tests`

**Reproduction**:
```bash
cd apps/api
python -m pytest tests/test_lightning_removed.py::test_no_lightning_references_remain_in_runtime_config_or_tests -v
```

**Owner**: Unassigned  
**Proposed Test**: Already exists (failing)  
**Fix**: Remove remaining lightning references  
**Validation**: Test passes

---

#### DEF-005: Phase 10 Prelaunch Evidence Files Missing
**Severity**: P1  
**Scope**: Release evidence  
**Tests**:
- `test_phase10_prelaunch_evidence_files_are_present`
- `test_renderer_feature_matrix_is_backed_by_golden_and_encoded_gates`

**Reproduction**:
```bash
cd apps/api
python -m pytest tests/test_phase10_prelaunch.py::test_phase10_prelaunch_evidence_files_are_present -v
```

**Owner**: Unassigned  
**Proposed Test**: Already exists (failing)  
**Fix**: Generate or locate missing evidence files  
**Validation**: Tests pass

---

#### DEF-006: Security Data Handling Documentation Missing
**Severity**: P1  
**Scope**: Documentation  
**Test**: `test_data_handling_documentation_exists`

**Reproduction**:
```bash
cd apps/api
python -m pytest tests/test_phase12_security.py::test_data_handling_documentation_exists -v
```

**Owner**: Unassigned  
**Proposed Test**: Already exists (failing)  
**Fix**: Create data handling documentation  
**Validation**: Test passes

---

#### DEF-007: Health Dependencies Endpoint Audio ML Test Failing
**Severity**: P1  
**Scope**: Health endpoint dependencies  
**Test**: `test_health_dependencies_endpoint_with_audio_ml`

**Reproduction**:
```bash
cd apps/api
python -m pytest tests/test_phase7_failure_isolation.py::test_health_dependencies_endpoint_with_audio_ml -v
```

**Owner**: Unassigned  
**Proposed Test**: Already exists (failing)  
**Fix**: TBD  
**Validation**: Test passes

---

#### DEF-008: Release Compose Input Writer Test Failing
**Severity**: P1  
**Scope**: Release composition  
**Test**: `test_release_compose_input_writer_uses_manifest_digests_and_0600_files`

**Reproduction**:
```bash
cd apps/api
python -m pytest tests/test_release_compose_inputs.py::test_release_compose_input_writer_uses_manifest_digests_and_0600_files -v
```

**Owner**: Unassigned  
**Proposed Test**: Already exists (failing)  
**Fix**: TBD  
**Validation**: Test passes

---

### P2: Deployment/Configuration Issues

#### DEF-009: Docker Compose Requires .env File
**Severity**: P2 (expected, but blocks ad-hoc validation)  
**Scope**: Compose configuration  
**Issue**: 19 required environment variables not set

**Reproduction**:
```bash
docker compose -f infra/docker-compose.gpu.yml config -q
# Fails with missing variable errors
```

**Owner**: Unassigned  
**Proposed Test**: Create test that validates .env.example completeness  
**Fix**: Not a defect - by design. Document .env.example usage.  
**Validation**: Compose config succeeds with valid .env

---

#### DEF-010: Generated OpenAPI Schema Drift
**Severity**: P2  
**Scope**: API contract  
**File**: `packages/shared/openapi.json`

**Reproduction**:
```bash
cd packages/shared
pnpm contracts:check  # Currently passes but schema is uncommitted
git diff packages/shared/openapi.json  # Shows +313 lines
```

**Owner**: Unassigned  
**Proposed Test**: contracts:check should fail if generated differs from committed  
**Fix**: Commit regenerated schema or revert billing changes  
**Validation**: git status shows no uncommitted schema changes

---

### P3: Style/Documentation

#### DEF-011: Python Code Formatting Drift
**Severity**: P3  
**Scope**: Code style  
**Impact**: 49 files need reformatting

**Reproduction**:
```bash
cd apps/api
python -m ruff format --check .
# Exit code 1, lists 49 files
```

**Owner**: Unassigned  
**Proposed Test**: CI check for ruff format compliance  
**Fix**: Run `ruff format .`  
**Validation**: `ruff format --check .` exits 0

---

#### DEF-012: pnpm Configuration Deprecation Warning
**Severity**: P3  
**Scope**: package.json configuration  
**Warning**: "pnpm.overrides" field deprecated

**Reproduction**:
```bash
pnpm <any-command>
# Emits deprecation warning
```

**Owner**: Unassigned  
**Proposed Test**: Parse package.json and check for deprecated fields  
**Fix**: Migrate to new pnpm settings format  
**Validation**: No deprecation warnings

---

### Environment Blockers (Not Defects)

#### BLOCK-001: Missing temporalio Dependency
**Scope**: Test collection  
**Files**: `test_task5_draft_render.py`, `test_task5_proposal_gate.py`, `test_temporal_orchestration.py`

**Issue**: `ModuleNotFoundError: No module named 'temporalio'`

**Resolution**: Install temporalio or mark tests as requiring optional dependency

---

#### BLOCK-002: Missing node_modules
**Scope**: TypeScript tooling  
**Issue**: pnpm install not run

**Resolution**: Run `pnpm install` to install dependencies

---

## 5. Immediate Actions Required

### P0 (None)
No critical defects identified.

### P1 (8 Test Failures)
**Approach**: Reproduce each test individually with verbose output, analyze root cause, write focused regression test if missing, then fix.

**Order**:
1. DEF-001: Worker auth (security)
2. DEF-006: Data handling docs (compliance)
3. DEF-002: Analytics tests
4. DEF-003: Temporal health
5. DEF-004: Lightning cleanup
6. DEF-005: Prelaunch evidence
7. DEF-007: Health deps
8. DEF-008: Release compose

### P2 (2 Items)
1. DEF-010: Commit or revert OpenAPI schema changes
2. DEF-009: Document .env.example usage (may be complete)

### P3 (2 Items - Defer)
1. DEF-011: Format Python code (batch operation after P1 fixes)
2. DEF-012: Update pnpm config (low risk)

---

## 6. Test-Before-Fix Principle

**Every P1 defect requires**:
1. ✅ Deterministic reproduction command
2. ✅ Existing focused test (or new test if gap exists)
3. ⏳ Root cause analysis
4. ⏳ Minimal fix that makes test pass
5. ⏳ Validation that fix doesn't break other tests

**Status Legend**:
- ✅ Complete
- ⏳ Pending
- ❌ Not started

---

## 7. Baseline Evidence Summary

| Category | Status | Count | Notes |
|----------|--------|-------|-------|
| Modified Files | ⚠️ Uncommitted | 13 | Local-audit feature work |
| Untracked Files | ✅ Intentional | 9 | New feature artifacts |
| Dependency Health | ✅ Pass | - | No broken requirements |
| Contract Validation | ✅ Pass | - | OpenAPI matches runtime |
| Ruff Lint | ✅ Pass | - | No linting errors |
| Ruff Format | ❌ Fail | 49 files | Style only, P3 |
| Unit Tests | ⚠️ Partial | 855 pass, 11 fail | Requires investigation |
| TypeScript Lint | 🚫 Blocked | - | Missing node_modules |
| Compose Config | ⚠️ Expected | - | Requires .env file |
| Generated Drift | ⚠️ Present | 2 files | OpenAPI + TS client |

**Overall Health**: Good foundation, 11 reproducible test failures require investigation.

---

## 8. Next Steps

1. Run detailed analysis on each P1 test failure (with full output)
2. Document root cause for each
3. Apply test-driven fixes (fix only what makes test pass)
4. Rerun full test suite after each fix
5. Address P2 issues (schema drift)
6. Batch format P3 issues
7. Update this report with resolution status

---

**Report Status**: Initial Baseline Established  
**Next Update**: After P1 defect analysis phase
