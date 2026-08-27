"""Contracts for the immutable release-artifact GitHub Actions workflow."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "supply-chain.yml"

COMPONENTS = (
    "api",
    "web",
    "cpu-worker",
    "gpu-worker",
    "demucs-worker",
    "remotion-worker",
)
COMPOSE_IMAGE_VARIABLES = (
    "MOTIONLY_API_IMAGE",
    "MOTIONLY_WEB_IMAGE",
    "MOTIONLY_CPU_FFMPEG_WORKER_IMAGE",
    "MOTIONLY_GPU_WORKER_IMAGE",
    "MOTIONLY_GPU_FFMPEG_WORKER_IMAGE",
    "MOTIONLY_DEMUCS_WORKER_IMAGE",
    "MOTIONLY_PROVIDER_WORKER_IMAGE",
    "MOTIONLY_PLANNING_WORKER_IMAGE",
    "MOTIONLY_REMOTION_WORKER_IMAGE",
    "MOTIONLY_PUBLISHING_WORKER_IMAGE",
)


def test_supply_chain_builds_scans_signs_and_manifests_every_runtime_component() -> None:
    workflow = WORKFLOW_PATH.read_text()

    assert "docker/build-push-action@v6" in workflow
    assert "platforms: linux/amd64" in workflow
    assert "provenance: mode=max" in workflow
    assert "sbom: true" in workflow
    assert "steps.build.outputs.digest" in workflow
    assert "cosign sign --yes" in workflow
    assert "cosign sign-blob --yes" in workflow
    assert "release-manifest-${{ github.sha }}" in workflow
    assert ":latest" not in workflow

    assert "name: digest-${{ matrix.component }}" in workflow
    assert "name: sbom-${{ matrix.component }}" in workflow
    for component in COMPONENTS:
        assert f"component: {component}" in workflow

    for variable in COMPOSE_IMAGE_VARIABLES:
        assert f'--image "{variable}=' in workflow


def test_supply_chain_uses_hash_locked_python_dependencies() -> None:
    workflow = WORKFLOW_PATH.read_text()

    assert "--require-hashes -r apps/api/requirements-dev.lock" in workflow
    assert "--require-hashes -r apps/api/requirements-ml.lock --dry-run" in workflow
