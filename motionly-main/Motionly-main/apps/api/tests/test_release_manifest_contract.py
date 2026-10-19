from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = ROOT / "scripts" / "release" / "generate_release_manifest.py"
PACKAGER_PATH = ROOT / "scripts" / "release" / "package_release_bundle.py"
SCHEMA_PATH = ROOT / "infra" / "release-manifest.schema.json"

spec = importlib.util.spec_from_file_location("release_manifest", GENERATOR_PATH)
assert spec and spec.loader
release_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_manifest)


def _bundle_arguments(tmp_path: Path) -> tuple[Path, Path]:
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
    return bundle, layout


def _arguments_for_manifest(
    output: Path, tmp_path: Path, image_suffix: str = "a" * 64
) -> list[str]:
    bundle, layout = _bundle_arguments(tmp_path)
    arguments = [
        sys.executable,
        str(GENERATOR_PATH),
        "--revision",
        "1234567890abcdef",
        "--created-at",
        "2026-08-09T00:00:00Z",
        "--bundle",
        str(bundle),
        "--bundle-layout",
        str(layout),
        "--output",
        str(output),
    ]
    for variable, component in release_manifest.IMAGE_COMPONENT_BY_VARIABLE.items():
        arguments.extend(
            [
                "--image",
                f"{variable}=ghcr.io/motionly/{component}@sha256:{image_suffix}",
            ]
        )
    for component in release_manifest.BUILD_COMPONENTS:
        arguments.extend(["--sbom", f"{component}=sbom-{component}.spdx.json"])
    return arguments


def test_manifest_generator_binds_every_production_compose_variable_and_bundle(
    tmp_path: Path,
) -> None:
    output = tmp_path / "release-manifest.json"
    result = subprocess.run(
        _arguments_for_manifest(output, tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text())
    assert manifest["schema_version"] == 2
    assert manifest["release_id"] == "1234567890abcdef"
    assert manifest["revision"] == "1234567890abcdef"
    assert set(manifest["images"]) == set(release_manifest.IMAGE_COMPONENT_BY_VARIABLE)
    assert set(manifest["sboms"]) == set(release_manifest.BUILD_COMPONENTS)
    assert manifest["bundle"]["name"] == "release-bundle.tar.gz"
    assert manifest["bundle"]["sha256"]
    assert any(
        item["path"] == "infra/temporal/start-server.sh" for item in manifest["bundle"]["files"]
    )
    assert any(
        item["path"] == "infra/temporal/config/production.yaml.tmpl"
        for item in manifest["bundle"]["files"]
    )
    assert any(
        item["path"] == "infra/temporal/server/cmd/motionly-temporal-server/main.go"
        for item in manifest["bundle"]["files"]
    )
    assert all("@sha256:" in image for image in manifest["images"].values())


def test_manifest_generator_rejects_mutable_or_non_ghcr_images(tmp_path: Path) -> None:
    output = tmp_path / "release-manifest.json"
    result = subprocess.run(
        _arguments_for_manifest(output, tmp_path, image_suffix="not-a-digest"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "digest-pinned GHCR image" in result.stderr
    assert not output.exists()


def test_bundle_validation_rejects_modified_archive_before_extraction(tmp_path: Path) -> None:
    output = tmp_path / "release-manifest.json"
    result = subprocess.run(
        _arguments_for_manifest(output, tmp_path), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    bundle = tmp_path / "release-bundle.tar.gz"
    bundle.write_bytes(bundle.read_bytes() + b"tampered")

    validation = subprocess.run(
        [
            sys.executable,
            str(GENERATOR_PATH),
            "--validate-bundle",
            "--manifest",
            str(output),
            "--bundle",
            str(bundle),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert validation.returncode == 2
    assert "does not match signed manifest" in validation.stderr


def test_release_schema_lists_exactly_the_v2_generator_contract() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema["properties"]["schema_version"]["const"] == 2
    assert schema["properties"]["images"]["required"] == list(
        release_manifest.IMAGE_COMPONENT_BY_VARIABLE
    )
    assert schema["properties"]["sboms"]["required"] == list(release_manifest.BUILD_COMPONENTS)
    assert set(schema["properties"]["bundle"]["required"]) == {
        "name",
        "sha256",
        "size_bytes",
        "files",
    }


def test_only_ghcr_or_a_loopback_rehearsal_registry_is_a_valid_image_source() -> None:
    digest = "a" * 64
    valid = release_manifest.DIGEST_IMAGE_RE.fullmatch
    assert valid(f"ghcr.io/motionly/api@sha256:{digest}")
    # The local release rehearsal's registry; the release agent additionally
    # requires every image to come from its configured registry, and only a
    # rehearsal host accepts a loopback one (scripts/deploy/apply_release.sh).
    assert valid(f"127.0.0.1:15001/motionly/api@sha256:{digest}")
    assert valid(f"localhost:15001/motionly/api@sha256:{digest}")
    for invalid in (
        f"docker.io/motionly/api@sha256:{digest}",
        f"10.0.0.5:5000/motionly/api@sha256:{digest}",
        f"127.0.0.1/motionly/api@sha256:{digest}",
        "127.0.0.1:15001/motionly/api:latest",
    ):
        assert not valid(invalid), invalid


def test_the_bundle_carries_every_bind_mount_source_of_the_production_topology() -> None:
    """Found by the release rehearsal: a flow-style mount
    (`volumes: [./supabase-kong.yml:/kong/kong.yml:ro]`) was left out, so the
    gateway started without its configuration and never became healthy."""
    import yaml

    bundle_spec = importlib.util.spec_from_file_location(
        "release_bundle", ROOT / "scripts" / "release" / "package_release_bundle.py"
    )
    assert bundle_spec and bundle_spec.loader
    bundle = importlib.util.module_from_spec(bundle_spec)
    sys.modules["release_bundle"] = bundle
    bundle_spec.loader.exec_module(bundle)

    class Loader(yaml.SafeLoader):
        pass

    def construct(loader, _suffix, node):
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        return loader.construct_scalar(node)

    Loader.add_multi_constructor("!", construct)
    compose_file = ROOT / "infra" / "docker-compose.gpu.yml"
    compose = yaml.load(compose_file.read_text(encoding="utf-8"), Loader=Loader)
    expected = set()
    for service in compose["services"].values():
        for volume in service.get("volumes") or []:
            source = volume.get("source") if isinstance(volume, dict) else volume.split(":")[0]
            if isinstance(source, str) and source.startswith("."):
                expected.add((compose_file.parent / source).resolve().relative_to(ROOT).as_posix())

    carried = {path.relative_to(ROOT).as_posix() for path in bundle.compose_bind_mount_sources()}

    assert "infra/supabase-kong.yml" in expected
    assert expected <= carried, sorted(expected - carried)
