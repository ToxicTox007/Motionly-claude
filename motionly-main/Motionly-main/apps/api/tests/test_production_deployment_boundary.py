"""Guard the approved single-VM Compose production deployment boundary."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_only_single_vm_compose_is_an_approved_production_deployment_path() -> None:
    assert not (ROOT / ".railway").exists()
    assert not (ROOT / "infra" / "azure").exists()

    package = (ROOT / "package.json").read_text()
    lock = (ROOT / "pnpm-lock.yaml").read_text()
    assert '"railway"' not in package
    assert "railway@" not in lock

    deploy_workflow = (ROOT / ".github" / "workflows" / "deploy-compose-vm.yml").read_text()
    assert "az vm run-command invoke" in deploy_workflow
    assert "vmss" not in deploy_workflow.lower()

    host_runbook = (ROOT / "docs" / "ops" / "compose-vm-deployment.md").read_text()
    assert "exactly one supported runtime" in host_runbook
    assert "VMSS is explicitly unsupported" in host_runbook

    legacy_plan = (ROOT / "docs" / "azure_cost_optimized_plan.md").read_text()
    assert "retired" in legacy_plan.lower()
    assert "hybrid deployment plan" in legacy_plan.lower()
