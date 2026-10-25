"""The GPU-free remote-AI release artifacts (CPU-only production).

The signed release manifest may carry one *optional* pair: the
``MOTIONLY_CPU_VISION_WORKER_IMAGE`` image and the ``cpu-vision-worker`` SBOM.
The pair travels together or not at all; a manifest without it still validates
so every release published before this change is unaffected. The release bundle
carries ``infra/docker-compose.remote-ai.yml`` so the signed release path can
deploy the GPU-free overlay, and the supply-chain workflow passes both optional
flags to the manifest generator.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = ROOT / "scripts" / "release" / "generate_release_manifest.py"
PACKAGER_PATH = ROOT / "scripts" / "release" / "package_release_bundle.py"
SCHEMA_PATH = ROOT / "infra" / "release-manifest.schema.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "supply-chain.yml"

spec = importlib.util.spec_from_file_location("gpu_free_release_manifest", GENERATOR_PATH)
assert spec and spec.loader
release_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_manifest)

_DIGEST = "a" * 64


def _required_images() -> dict[str, str]:
    return {
        variable: f"ghcr.io/motionly/{component}@sha256:{_DIGEST}"
        for variable, component in release_manifest.IMAGE_COMPONENT_BY_VARIABLE.items()
    }


def _required_sboms() -> dict[str, str]:
    return {
        component: f"sbom-{component}.spdx.json" for component in release_manifest.BUILD_COMPONENTS
    }


def _optional_image() -> dict[str, str]:
    return {
        variable: f"ghcr.io/motionly/{component}@sha256:{_DIGEST}"
        for variable, component in release_manifest.OPTIONAL_IMAGE_COMPONENT_BY_VARIABLE.items()
    }


def _optional_sbom() -> dict[str, str]:
    return {
        component: f"sbom-{component}.spdx.json"
        for component in release_manifest.OPTIONAL_BUILD_COMPONENTS
    }


def _common_manifest(images: dict[str, str], sboms: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision": "1234567890abcdef",
        "created_at": "2026-10-25T00:00:00Z",
        "compose_file": "infra/docker-compose.gpu.yml",
        "images": images,
        "sboms": sboms,
    }


def test_the_optional_cpu_vision_pair_validates() -> None:
    manifest = _common_manifest(
        {**_required_images(), **_optional_image()},
        {**_required_sboms(), **_optional_sbom()},
    )
    validated = release_manifest.validate_manifest(manifest)
    assert "MOTIONLY_CPU_VISION_WORKER_IMAGE" in validated["images"]
    assert "cpu-vision-worker" in validated["sboms"]


def test_a_manifest_without_the_optional_pair_still_validates() -> None:
    manifest = _common_manifest(_required_images(), _required_sboms())
    validated = release_manifest.validate_manifest(manifest)
    assert "MOTIONLY_CPU_VISION_WORKER_IMAGE" not in validated["images"]
    assert "cpu-vision-worker" not in validated["sboms"]


def test_the_optional_image_without_its_sbom_is_rejected() -> None:
    manifest = _common_manifest(
        {**_required_images(), **_optional_image()},
        _required_sboms(),
    )
    with pytest.raises(ValueError, match="together"):
        release_manifest.validate_manifest(manifest)


def test_the_optional_sbom_without_its_image_is_rejected() -> None:
    manifest = _common_manifest(
        _required_images(),
        {**_required_sboms(), **_optional_sbom()},
    )
    with pytest.raises(ValueError, match="together"):
        release_manifest.validate_manifest(manifest)


def test_an_unknown_image_variable_is_rejected() -> None:
    manifest = _common_manifest(
        {**_required_images(), "MOTIONLY_MYSTERY_IMAGE": f"ghcr.io/motionly/x@sha256:{_DIGEST}"},
        _required_sboms(),
    )
    with pytest.raises(ValueError, match="every Compose image variable"):
        release_manifest.validate_manifest(manifest)


def test_an_unknown_sbom_component_is_rejected() -> None:
    manifest = _common_manifest(
        _required_images(),
        {**_required_sboms(), "mystery-worker": "sbom-mystery-worker.spdx.json"},
    )
    with pytest.raises(ValueError, match="every built component"):
        release_manifest.validate_manifest(manifest)


def test_the_optional_pair_round_trips_through_the_cli(tmp_path: Path) -> None:
    bundle = tmp_path / "release-bundle.tar.gz"
    layout = tmp_path / "release-bundle.layout.json"
    package = subprocess.run(
        [
            sys.executable,
            str(PACKAGER_PATH),
            "--output",
            str(bundle),
            "--layout-output",
            str(layout),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert package.returncode == 0, package.stderr

    output = tmp_path / "release-manifest.json"
    arguments = [
        sys.executable,
        str(GENERATOR_PATH),
        "--revision",
        "1234567890abcdef",
        "--created-at",
        "2026-10-25T00:00:00Z",
        "--bundle",
        str(bundle),
        "--bundle-layout",
        str(layout),
        "--output",
        str(output),
    ]
    for variable, image in {**_required_images(), **_optional_image()}.items():
        arguments.extend(["--image", f"{variable}={image}"])
    for component in (
        *release_manifest.BUILD_COMPONENTS,
        *release_manifest.OPTIONAL_BUILD_COMPONENTS,
    ):
        arguments.extend(["--sbom", f"{component}=sbom-{component}.spdx.json"])

    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text())
    assert manifest["images"]["MOTIONLY_CPU_VISION_WORKER_IMAGE"].endswith(f"@sha256:{_DIGEST}")
    assert manifest["sboms"]["cpu-vision-worker"] == "sbom-cpu-vision-worker.spdx.json"


def test_the_schema_keeps_the_required_lists_and_adds_only_the_optional_properties() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    images = schema["properties"]["images"]
    sboms = schema["properties"]["sboms"]

    # The required lists are exactly the mandatory generator contract: the
    # optional variable/component is a property but never required.
    assert images["required"] == list(release_manifest.IMAGE_COMPONENT_BY_VARIABLE)
    assert sboms["required"] == list(release_manifest.BUILD_COMPONENTS)
    assert "MOTIONLY_CPU_VISION_WORKER_IMAGE" not in images["required"]
    assert "cpu-vision-worker" not in sboms["required"]

    assert "MOTIONLY_CPU_VISION_WORKER_IMAGE" in images["properties"]
    assert "cpu-vision-worker" in sboms["properties"]
    assert images["additionalProperties"] is False
    assert sboms["additionalProperties"] is False


def test_the_bundle_layout_carries_the_gpu_free_overlay_and_its_registry(tmp_path: Path) -> None:
    bundle = tmp_path / "release-bundle.tar.gz"
    layout = tmp_path / "release-bundle.layout.json"
    result = subprocess.run(
        [
            sys.executable,
            str(PACKAGER_PATH),
            "--output",
            str(bundle),
            "--layout-output",
            str(layout),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    paths = {entry["path"] for entry in json.loads(layout.read_text())}
    assert "infra/docker-compose.remote-ai.yml" in paths
    # The GPU-free overlay's cpu-vision-worker depends on the inference registry,
    # which the base topology mounts via ``${AI_REGISTRY_DIR:-./ai-registry}``.
    assert "infra/ai-registry/registry.json" in paths
    # The production topology is still carried unchanged.
    assert "infra/docker-compose.gpu.yml" in paths


def test_the_supply_chain_manifest_step_passes_both_optional_flags() -> None:
    workflow = WORKFLOW_PATH.read_text()
    assert '--image "MOTIONLY_CPU_VISION_WORKER_IMAGE=' in workflow
    assert "/cpu-vision-worker@$(digest cpu-vision-worker)" in workflow
    assert "--sbom cpu-vision-worker=sbom-cpu-vision-worker.spdx.json" in workflow
