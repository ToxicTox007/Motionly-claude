# P1 Defect Analysis - Detailed Reproduction Evidence

**Purpose**: Document root cause and reproduction for each P1 test failure before applying fixes  
**Date**: 2026-08-30  
**Status**: Analysis Phase

---

## DEF-001: Worker Auth Token Validation Order

### Test
```
tests/security/test_phase0_tenancy.py::test_internal_worker_route_rejects_missing_worker_token
```

### Reproduction
```bash
cd apps/api
python -m pytest tests/security/test_phase0_tenancy.py::test_internal_worker_route_rejects_missing_worker_token -vv
```

### Expected Behavior
- Missing worker token should return 403 Forbidden before job lookup

### Actual Behavior
- Returns 404 Not Found (job lookup happens before auth check)
- Error: `ApiError(404, 'queue_job_not_found', 'Queue job was not found.')`
- Expected: `ApiError(403, ...)` for missing authentication

### Root Cause
Authentication check happens AFTER database lookup. Security checks should be first in the request pipeline.

### Code Location
`apps/api/app/api/internal_jobs.py::run_queue_job()`

### Fix Required
Move worker token validation before job retrieval from database.

### Test Validation
```bash
# After fix, this should pass:
cd apps/api
python -m pytest tests/security/test_phase0_tenancy.py::test_internal_worker_route_rejects_missing_worker_token
```

**Status**: 🔍 Root cause identified, ready for fix

---

## DEF-002: Similar Worker Auth Issue

### Test
```
tests/security/test_phase0_tenancy.py::test_worker_route_rejects_wrong_worker_token
```

### Reproduction
```bash
cd apps/api
python -m pytest tests/security/test_phase0_tenancy.py::test_worker_route_rejects_wrong_worker_token -vv
```

### Expected Behavior
- Wrong worker token should return 403 Forbidden before job lookup

### Actual Behavior  
- Likely same issue as DEF-001 (404 before 403)

### Root Cause
Same as DEF-001 - authentication order issue

### Fix Required
Same fix as DEF-001 will likely resolve this

**Status**: 🔍 Grouped with DEF-001

---

## DEF-004: Lightning References in handoff-acceptance.json

### Test
```
tests/test_lightning_removed.py::test_no_lightning_references_remain_in_runtime_config_or_tests
```

### Reproduction
```bash
cd apps/api
python -m pytest tests/test_lightning_removed.py::test_no_lightning_references_remain_in_runtime_config_or_tests -vv
```

### Expected Behavior
- No "lightning" references (case-insensitive, excluding "lightningcss") should exist

### Actual Behavior
```
AssertionError: Lightning references still present in: ['scripts\\handoff-acceptance.json']
```

### Root Cause
File `scripts/handoff-acceptance.json` contains "lightning" reference

### Investigation Needed
Check what the reference is:
```bash
cd apps/api
grep -i lightning scripts/handoff-acceptance.json
```

### Fix Required
- If legitimate reference to Lightning AI framework or similar: update test exclusion list
- If stale reference: remove from JSON file
- Requires manual inspection to determine

**Status**: 📋 Needs file inspection

---

## DEF-006: Missing DATA_HANDLING.md Documentation

### Test
```
tests/test_phase12_security.py::test_data_handling_documentation_exists
```

### Reproduction
```bash
cd apps/api
python -m pytest tests/test_phase12_security.py::test_data_handling_documentation_exists -vv
```

### Expected Behavior
- File exists at `/workspaces/Motionly-claude/motionly-main/Motionly-main/DATA_HANDLING.md`

### Actual Behavior
```
AssertionError: assert False
  where False = WindowsPath('/workspaces/Motionly-claude/motionly-main/Motionly-main/DATA_HANDLING.md').exists()
```

### Root Cause
- **Path Issue**: Test uses hardcoded `/workspaces/` path (Linux container path)
- On Windows: Path is `D:\Motionly-claude\motionly-main\Motionly-main\`
- **Missing File**: DATA_HANDLING.md does not exist

### Fix Required
1. **Immediate**: Create DATA_HANDLING.md documentation
2. **Test Fix**: Update test to use dynamic path resolution (ROOT variable)

### File Location Fix
```python
# Current (wrong):
doc_path = Path("/workspaces/Motionly-claude/motionly-main/Motionly-main/DATA_HANDLING.md")

# Should be:
doc_path = ROOT / "DATA_HANDLING.md"  # where ROOT is defined relative to test file
```

### Documentation Template Required
DATA_HANDLING.md should document:
- Data retention policies
- PII handling
- User data deletion procedures
- Backup and recovery
- Third-party data sharing
- Compliance (GDPR, CCPA)

**Status**: 🔍 Two-part fix required (test path + create doc)

---

## DEF-002, DEF-003, DEF-005, DEF-007, DEF-008: Pending Analysis

### Remaining Tests to Analyze
- `test_ai_edit_analytics_summarizes_events_and_builds_fine_tuning_examples`
- `test_analytics_api_prefers_async_repository_methods`
- `test_temporal_health_uses_secure_client_rpc_and_redacts_transport_errors`
- `test_phase10_prelaunch_evidence_files_are_present`
- `test_renderer_feature_matrix_is_backed_by_golden_and_encoded_gates`
- `test_health_dependencies_endpoint_with_audio_ml`
- `test_release_compose_input_writer_uses_manifest_digests_and_0600_files`

### Next Steps
Run each with `-vv` to capture full failure details.

---

## Priority Order for Fixes

### Immediate (Security)
1. ✅ **DEF-001/002**: Worker auth validation order (security issue)

### High Priority (Compliance)
2. ✅ **DEF-006**: DATA_HANDLING.md documentation (compliance requirement)

### Medium Priority (Cleanup)
3. 📋 **DEF-004**: Lightning reference cleanup (technical debt)

### Analyze Next
4. ⏳ DEF-002: Analytics tests
5. ⏳ DEF-003: Temporal health
6. ⏳ DEF-005: Prelaunch evidence  
7. ⏳ DEF-007: Health dependencies
8. ⏳ DEF-008: Release compose

---

## Fix Workflow Template

For each defect:
1. ✅ Reproduce with command
2. ✅ Capture full error output
3. ✅ Identify root cause
4. ⏳ Write focused regression test (if missing)
5. ⏳ Apply minimal fix
6. ⏳ Verify test passes
7. ⏳ Run full test suite (no regressions)
8. ⏳ Update this document with resolution

---

**Status Summary**:
- **Analyzed**: 3 defects (DEF-001, DEF-004, DEF-006)
- **Ready to Fix**: 2 defects (DEF-001, DEF-006)
- **Needs Inspection**: 1 defect (DEF-004)
- **Pending Analysis**: 5 defects
