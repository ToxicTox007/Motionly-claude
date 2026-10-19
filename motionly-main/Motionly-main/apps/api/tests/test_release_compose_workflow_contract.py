"""GPU-stage full Compose integration workflow contract.

The full release Compose topology includes the GPU services, so its integration
job is deferred to the GPU stage: it lives only in the manually dispatched,
approval-protected ``gpu-stage.yml`` workflow and always cleans up.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "gpu-stage.yml"
SUPPLY_CHAIN_PATH = ROOT / ".github" / "workflows" / "supply-chain.yml"


def test_full_compose_integration_is_gpu_stage_only_and_always_cleans_up() -> None:
    workflow = WORKFLOW_PATH.read_text()

    assert "release-compose-integration:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "environment: gpu-stage" in workflow
    assert "runs-on: [self-hosted, linux, x64, gpu]" in workflow
    assert "run-id: ${{ inputs.release_run_id }}" in workflow
    assert "name: release-manifest-${{ inputs.release_sha }}" in workflow
    assert "write_compose_test_env.py" in workflow
    assert "docker compose --profile gpu --env-file" in workflow
    assert "up -d --wait --wait-timeout 240 --remove-orphans" in workflow
    assert "release-compose-logs-${{ inputs.release_sha }}" in workflow
    assert "if: always()" in workflow
    assert "down -v --remove-orphans || true" in workflow
    assert 'rm -rf "$RELEASE_INPUT_ROOT"' in workflow


def test_supply_chain_no_longer_starts_the_gpu_topology() -> None:
    supply_chain = SUPPLY_CHAIN_PATH.read_text()

    assert "release-compose-integration:" not in supply_chain
    assert "[self-hosted, linux, x64, gpu]" not in supply_chain
