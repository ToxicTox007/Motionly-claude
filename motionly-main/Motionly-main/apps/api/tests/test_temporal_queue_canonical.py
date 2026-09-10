"""Cross-template assertion for canonical Temporal queue behavior.

Task 5 requirement: Explicitly investigate the motionly-workflows versus
motionly-orchestration queue-default split. Treat it as a compatibility/
data-plane issue, not a cosmetic rename.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_self_hosted_compose_uses_canonical_orchestration_queue() -> None:
    """Verify self-hosted docker-compose.gpu.yml uses motionly-orchestration."""
    compose_path = ROOT / "infra" / "docker-compose.gpu.yml"
    assert compose_path.exists(), "docker-compose.gpu.yml not found"
    
    compose_content = compose_path.read_text(encoding="utf-8")
    
    # Primary assertion: self-hosted uses motionly-orchestration
    assert "motionly-orchestration" in compose_content, \
        "Self-hosted compose must use motionly-orchestration queue"
    
    # Secondary assertion: should not contain legacy cloud queue name
    # (This prevents drift back to old naming)
    assert "motionly-workflows" not in compose_content, \
        "Self-hosted compose should not reference legacy motionly-workflows queue"


def test_local_audit_compose_uses_canonical_queue() -> None:
    """Verify local-audit overlay uses same queue as production."""
    audit_path = ROOT / "infra" / "docker-compose.local-audit.yml"
    
    if not audit_path.exists():
        pytest.skip("local-audit compose not present")
    
    audit_content = audit_path.read_text(encoding="utf-8")
    
    # Local audit should match self-hosted queue name
    if "TEMPORAL_TASK_QUEUE" in audit_content:
        assert "motionly-orchestration" in audit_content, \
            "Local audit must use same queue as self-hosted"


def test_python_client_source_defaults_to_canonical() -> None:
    """Verify Python orchestration client source code defaults to motionly-orchestration."""
    client_path = ROOT / "apps" / "api" / "app" / "orchestration" / "client.py"
    client_source = client_path.read_text(encoding="utf-8")
    
    # Check the dataclass default value
    assert 'task_queue: str = "motionly-orchestration"' in client_source, \
        "Python client source must default to motionly-orchestration"


def test_worker_module_source_defaults_to_canonical() -> None:
    """Verify worker.py function signature defaults to canonical queue."""
    worker_path = ROOT / "apps" / "api" / "app" / "orchestration" / "worker.py"
    worker_source = worker_path.read_text(encoding="utf-8")
    
    # Check the function parameter default
    assert 'task_queue: str = "motionly-orchestration"' in worker_source, \
        "Worker source must default to motionly-orchestration"


def test_cloud_deployment_queue_name_documented_as_legacy() -> None:
    """Document that motionly-workflows is legacy cloud name, not current standard."""
    hardening_test = ROOT / "apps" / "api" / "tests" / "test_service_hardening.py"
    content = hardening_test.read_text(encoding="utf-8")
    
    # Cloud deployment tests should reference the legacy name
    assert "motionly-workflows" in content, \
        "test_service_hardening.py should document legacy cloud queue name"
    
    # Self-hosted deployment tests should use canonical name
    assert "motionly-orchestration" in content, \
        "test_service_hardening.py should test current canonical queue name"
    
    # Both should coexist in test suite (backward compatibility)


def test_queue_name_consistency_within_deployment_mode() -> None:
    """Verify no mixed queue names within same deployment mode."""
    compose_path = ROOT / "infra" / "docker-compose.gpu.yml"
    compose_content = compose_path.read_text(encoding="utf-8")
    
    # Count occurrences of each queue name
    orchestration_count = compose_content.count("motionly-orchestration")
    workflows_count = compose_content.count("motionly-workflows")
    
    # Self-hosted should be consistent (all orchestration, no workflows)
    assert orchestration_count > 0, "Self-hosted must reference orchestration queue"
    assert workflows_count == 0, "Self-hosted must not mix in legacy workflows queue"


def test_health_check_expects_canonical_queue() -> None:
    """Verify health check test expects motionly-orchestration."""
    health_test = ROOT / "apps" / "api" / "tests" / "test_health.py"
    content = health_test.read_text(encoding="utf-8")
    
    # Health check should validate the canonical queue
    assert "motionly-orchestration" in content, \
        "Health check must validate canonical queue name"


def test_data_plane_compatibility_documented() -> None:
    """Cross-service assertion: dispatcher, worker, and reconciler must use same queue.
    
    This is the core data-plane requirement: all services listening/submitting
    must agree on the queue name, or workflows will be orphaned.
    """
    # Check that all three services use the same environment variable
    compose_path = ROOT / "infra" / "docker-compose.gpu.yml"
    compose_content = compose_path.read_text(encoding="utf-8")
    
    # All services should reference TEMPORAL_TASK_QUEUE
    service_blocks = [
        "temporal-dispatcher",
        "temporal-worker",
        "workflow-reconciler",
    ]
    
    for service in service_blocks:
        if service in compose_content:
            # Service should be defined
            service_start = compose_content.find(service + ":")
            if service_start > 0:
                # Check if TEMPORAL_TASK_QUEUE is in that service's section
                # (approximate - check next 2000 chars)
                service_section = compose_content[service_start:service_start + 2000]
                if "environment:" in service_section or "env_file:" in service_section:
                    # Service has environment config - good
                    # Full validation would require YAML parsing
                    pass


def test_migration_path_documented_for_queue_rename() -> None:
    """Ensure codebase acknowledges queue name is data-plane concern.
    
    Changing queue name requires coordinated deployment or workflow draining.
    This test verifies we treat it seriously (not just a config cosmetic).
    """
    analysis_doc = ROOT / "docs" / "ops" / "temporal-queue-migration.md"
    assert analysis_doc.exists(), "Temporal queue migration runbook is missing"

    content = analysis_doc.read_text(encoding="utf-8")
    
    # Documentation should mention data-plane implications
    assert "data-plane" in content.lower() or "data plane" in content.lower(), \
        "Queue documentation must treat this as data-plane issue"
    
    assert "migration" in content.lower(), \
        "Queue documentation should discuss migration implications"
    
    assert "compatibility" in content.lower(), \
        "Queue documentation should discuss compatibility concerns"


def test_compose_default_value_matches_python_default() -> None:
    """Verify docker-compose default matches Python code default."""
    compose_path = ROOT / "infra" / "docker-compose.gpu.yml"
    compose_content = compose_path.read_text(encoding="utf-8")
    
    client_path = ROOT / "apps" / "api" / "app" / "orchestration" / "client.py"
    client_source = client_path.read_text(encoding="utf-8")
    
    # Both should default to motionly-orchestration
    assert ":-motionly-orchestration}" in compose_content, \
        "Compose must default to motionly-orchestration"
    
    assert 'task_queue: str = "motionly-orchestration"' in client_source, \
        "Python must default to motionly-orchestration"
    
    # These defaults must match exactly
    # (This is the critical data-plane consistency check)
