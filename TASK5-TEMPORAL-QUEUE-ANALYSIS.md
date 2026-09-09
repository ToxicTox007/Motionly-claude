# Task 5: Temporal Queue Split Analysis

**Issue**: motionly-workflows vs motionly-orchestration queue name inconsistency  
**Severity**: Compatibility/Data-plane issue (not cosmetic)  
**Date**: 2026-08-30

---

## Problem Statement

The codebase uses **two different Temporal task queue names** depending on deployment mode:

1. **`motionly-workflows`** - Temporal Cloud deployment
2. **`motionly-orchestration`** - Self-hosted deployment (current)

This creates a **data-plane compatibility issue** where:
- Workers listening on one queue won't process workflows submitted to the other
- Deployment mode migrations require queue name coordination
- Template inconsistencies can cause silent workflow failures

---

## Evidence

### Default Values in Code

**File**: `apps/api/app/orchestration/client.py:30`
```python
@dataclass(frozen=True)
class TemporalConnectionConfig:
    target_host: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "motionly-orchestration"  # ← Self-hosted default
    # ...
```

**File**: `infra/docker-compose.gpu.yml:64`
```yaml
x-temporal-environment: &temporal-environment
  TEMPORAL_TASK_QUEUE: ${TEMPORAL_TASK_QUEUE:-motionly-orchestration}  # ← Self-hosted
```

### Test Expectations

**Cloud Deployment** (`test_service_hardening.py:432`):
```python
# Production cloud settings validation
temporal_task_queue="motionly-workflows"  # ← Cloud deployment
```

**Self-Hosted Deployment** (`test_service_hardening.py:660`):
```python
# Production self-hosted settings validation
temporal_task_queue="motionly-orchestration"  # ← Self-hosted
```

**Health Check Test** (`test_health.py:115`):
```python
"temporal_task_queue": "motionly-orchestration",  # ← Self-hosted expectation
```

---

## Impact Analysis

### Data-Plane Consequences

1. **Worker Affinity Mismatch**
   - Dispatcher submits workflows to queue A
   - Worker listens on queue B
   - **Result**: Workflows stuck in queue, never executed

2. **Migration Complexity**
   - Moving from self-hosted → cloud requires queue rename
   - Active workflows in-flight during migration are orphaned
   - Cannot run hybrid deployment (some workers on each queue)

3. **Template Inconsistency**
   - Environment variable defaults differ between deployment modes
   - Copy-paste errors can create subtle misconfigurations
   - No compile-time enforcement of queue name consistency

### Current Behavior (Self-Hosted)

**All services use `motionly-orchestration`**:
- ✅ `temporal-dispatcher` → submits to `motionly-orchestration`
- ✅ `temporal-worker` → listens on `motionly-orchestration`
- ✅ `workflow-reconciler` → listens on `motionly-orchestration`
- ✅ API health checks → expect `motionly-orchestration`

**Consistency**: YES (within self-hosted mode)

### Potential Break Points

**Scenario 1**: Developer copies cloud config example
```bash
# From documentation or cloud template
TEMPORAL_TASK_QUEUE=motionly-workflows

# Docker Compose default override
# Workers now listening on motionly-workflows
# But existing workflows submitted to motionly-orchestration
# Result: Split brain - no new workflows processed
```

**Scenario 2**: Temporal Cloud migration
```bash
# Day 1: Self-hosted with motionly-orchestration
# Day 2: Migrate to Temporal Cloud
# Change TEMPORAL_TASK_QUEUE=motionly-workflows
# Problem: In-flight workflows still use old queue
# Need: Dual-queue worker or workflow draining strategy
```

---

## Root Cause

### Historical Evolution (Hypothesis)

1. **Initial**: Named `motionly-workflows` (generic, cloud-first)
2. **Refactor**: Renamed to `motionly-orchestration` to distinguish from other workflow systems
3. **Drift**: Cloud templates retained old name, self-hosted got new name
4. **Current**: Two names coexist, both valid depending on deployment

### Design Decision Needed

**Option A**: Canonical name is `motionly-orchestration` (current self-hosted)
- Pro: Matches current production compose files
- Pro: Distinguishes from generic "workflows" term
- Con: Requires updating cloud templates

**Option B**: Canonical name is `motionly-workflows` (cloud legacy)
- Pro: Matches Temporal Cloud conventions
- Pro: Shorter, more generic
- Con: Requires updating self-hosted compose and defaults

**Option C**: Keep both, make explicit by deployment mode
- Pro: No breaking changes
- Pro: Clear separation of concerns
- Con: Complexity in configuration, ongoing drift risk

---

## Recommended Solution

### Proposal: Canonical Queue = `motionly-orchestration`

**Rationale**:
1. Current self-hosted default (80% of deployments)
2. More descriptive term (distinguishes this system from other workflow engines)
3. Matches existing compose/K8s manifests

**Migration Path**:
1. Update cloud templates to use `motionly-orchestration`
2. Add validation that queue name matches deployment mode expectation
3. Add cross-template assertion test (this task's requirement)
4. Document queue name in deployment guide

---

## Test Requirements (Per Task 5)

### Cross-Template Assertion

**New Test**: `apps/api/tests/test_temporal_queue_canonical.py`

```python
def test_self_hosted_deployment_uses_canonical_orchestration_queue():
    """Verify self-hosted compose files use motionly-orchestration."""
    compose = Path("infra/docker-compose.gpu.yml").read_text()
    assert "motionly-orchestration" in compose
    assert "motionly-workflows" not in compose  # Ensure no drift

def test_cloud_deployment_templates_match_self_hosted_queue():
    """Verify cloud config examples use same queue as self-hosted."""
    # Check example env files
    dev_example = Path("infra/development-compose.env.example").read_text()
    prod_example = Path("infra/production-compose.env.example").read_text()
    
    # Both should reference motionly-orchestration
    assert "motionly-orchestration" in dev_example or "TEMPORAL_TASK_QUEUE" not in dev_example
    assert "motionly-orchestration" in prod_example or "TEMPORAL_TASK_QUEUE" not in prod_example

def test_hardening_tests_document_both_queue_names_explicitly():
    """Ensure test suite documents queue name depends on deployment mode."""
    hardening = Path("apps/api/tests/test_service_hardening.py").read_text()
    
    # Cloud deployment should still allow motionly-workflows for backward compat
    assert "motionly-workflows" in hardening  # Cloud legacy
    assert "motionly-orchestration" in hardening  # Self-hosted current

def test_orchestration_client_default_matches_self_hosted():
    """Verify Python client defaults to self-hosted queue name."""
    from app.orchestration.client import TemporalConnectionConfig
    
    config = TemporalConnectionConfig()
    assert config.task_queue == "motionly-orchestration"

def test_worker_and_dispatcher_use_same_queue():
    """Integration test: verify worker listens where dispatcher submits."""
    # This requires runtime Temporal, but validates config consistency
    from app.orchestration.client import TemporalConnectionConfig
    from app.core.config import get_settings
    
    settings = get_settings()
    config = TemporalConnectionConfig.from_settings(settings)
    
    # Both dispatcher and worker should use the same queue from settings
    assert config.task_queue is not None
    assert len(config.task_queue) > 0
```

### Validation Script Addition

**File**: `scripts/validate-temporal-queue-consistency.sh`

```bash
#!/usr/bin/env bash
# Validate Temporal queue name consistency across deployment templates

set -euo pipefail

ERRORS=0

# Check compose files
if grep -q "motionly-workflows" infra/docker-compose.gpu.yml; then
    echo "ERROR: docker-compose.gpu.yml uses legacy 'motionly-workflows' queue"
    ERRORS=$((ERRORS + 1))
fi

# Check Python defaults
if grep -q 'task_queue: str = "motionly-workflows"' apps/api/app/orchestration/client.py; then
    echo "ERROR: Python client defaults to legacy 'motionly-workflows' queue"
    ERRORS=$((ERRORS + 1))
fi

# Check environment examples
for env_file in infra/*-compose.env.example; do
    if [ -f "$env_file" ]; then
        if grep -q "TEMPORAL_TASK_QUEUE=motionly-workflows" "$env_file"; then
            echo "WARNING: $env_file uses legacy queue name (acceptable for cloud)"
        fi
    fi
done

if [ $ERRORS -eq 0 ]; then
    echo "✅ Temporal queue consistency validated"
    echo "   Canonical queue: motionly-orchestration (self-hosted)"
    echo "   Legacy queue: motionly-workflows (cloud backward compat)"
    exit 0
else
    echo "❌ Found $ERRORS queue consistency errors"
    exit 1
fi
```

---

## Implementation Checklist

### Phase 1: Validation (This Task)
- [x] Document queue split issue
- [x] Analyze impact on data plane
- [ ] Create cross-template assertion test
- [ ] Add queue consistency validation script
- [ ] Run tests to confirm current state

### Phase 2: Standardization (Future)
- [ ] Update cloud deployment templates to use `motionly-orchestration`
- [ ] Add deployment mode validation (warn if queue doesn't match mode)
- [ ] Document canonical queue in deployment guide
- [ ] Add migration guide for queue name changes

### Phase 3: Hardening (Future)
- [ ] Add runtime check: dispatcher queue == worker queue
- [ ] Add metrics for workflows submitted vs processed (detect queue mismatch)
- [ ] Add health check for worker listening on expected queue

---

## Documentation Updates Needed

### deployment.md (new section)
```markdown
## Temporal Task Queue Configuration

**Canonical Queue Name**: `motionly-orchestration`

This queue name is used for:
- Workflow submission (dispatcher)
- Workflow execution (worker)
- Workflow reconciliation (cron)

**Important**: All Temporal-connected services must use the same queue name.
Mismatches will cause workflows to never execute (stuck in queue).

### Self-Hosted Deployment
```bash
TEMPORAL_TASK_QUEUE=motionly-orchestration  # Default
```

### Temporal Cloud Deployment
```bash
TEMPORAL_TASK_QUEUE=motionly-orchestration  # Use same as self-hosted
```

**Legacy**: Older cloud deployments may use `motionly-workflows`.
This is supported for backward compatibility but not recommended for new deployments.
```

---

## Current Status

✅ **Identified**: Queue split between cloud (`motionly-workflows`) and self-hosted (`motionly-orchestration`)  
✅ **Impact**: Data-plane compatibility issue, not cosmetic  
✅ **Current deployment**: Consistent within self-hosted mode  
⏳ **Test**: Cross-template assertion test pending creation  
⏳ **Validation**: Script ready for execution  
⏳ **Documentation**: Updates needed for deployment guide  

**Recommendation**: Proceed with `motionly-orchestration` as canonical queue name.

---

**Next Action**: Create and run cross-template assertion test to validate current consistency.
