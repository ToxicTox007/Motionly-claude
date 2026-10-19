from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = ROOT / "scripts" / "release" / "generate_release_manifest.py"
PACKAGER_PATH = ROOT / "scripts" / "release" / "package_release_bundle.py"
WRITER_PATH = ROOT / "scripts" / "release" / "write_compose_test_env.py"

spec = importlib.util.spec_from_file_location("release_manifest", GENERATOR_PATH)
assert spec and spec.loader
release_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_manifest)


def _manifest(tmp_path: Path) -> dict[str, object]:
    bundle = tmp_path / "release-bundle.tar.gz"
    layout = tmp_path / "release-bundle.layout.json"
    bundled = subprocess.run(
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
    assert bundled.returncode == 0, bundled.stderr
    images = {
        variable: f"ghcr.io/motionly/{component}@sha256:{'a' * 64}"
        for variable, component in release_manifest.IMAGE_COMPONENT_BY_VARIABLE.items()
    }
    sboms = {
        component: f"sbom-{component}.spdx.json" for component in release_manifest.BUILD_COMPONENTS
    }
    return release_manifest.build_manifest(
        revision="1234567890abcdef",
        created_at="2026-08-09T00:00:00Z",
        images=images,
        sboms=sboms,
        bundle_path=bundle,
        bundle_layout=json.loads(layout.read_text()),
    )


def test_release_compose_input_writer_uses_manifest_digests_and_0600_files(tmp_path: Path) -> None:
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(_manifest(tmp_path)))
    environment_path = tmp_path / "compose.env"
    secret_directory = tmp_path / "secrets"

    result = subprocess.run(
        [
            sys.executable,
            str(WRITER_PATH),
            "--manifest",
            str(manifest_path),
            "--env-file",
            str(environment_path),
            "--secrets-dir",
            str(secret_directory),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    environment = dict(line.split("=", 1) for line in environment_path.read_text().splitlines())
    assert environment["MOTIONLY_API_IMAGE"].startswith("ghcr.io/motionly/api@sha256:")
    assert environment["MOTIONLY_TEMPORAL_SERVER_IMAGE"].startswith(
        "ghcr.io/motionly/temporal-server@sha256:"
    )
    assert environment["TEMPORAL_BOOTSTRAP_ADMIN_UNTIL"].endswith("Z")
    assert environment["TEMPORAL_ADMIN_TOOLS_IMAGE"].startswith("temporalio/admin-tools@sha256:")
    assert environment["SECRETS_DIR"] == str(secret_directory)
    # One public origin; the ingress serves the app domain.
    assert environment["APP_DOMAIN"] == "app.release.test"
    assert environment["PUBLIC_ORIGIN"] == "https://app.release.test"
    assert "API_DOMAIN" not in environment and "SUPABASE_DOMAIN" not in environment
    assert stat.S_IMODE(environment_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(secret_directory.stat().st_mode) == 0o700
    for secret_name in (
        "postgres_password",
        "temporal_db_password",
        "worker_api_token",
        "azure_openai_api_key",
        "temporal_ca_cert",
        "temporal_server_cert",
        "temporal_server_key",
        "temporal_api_cert",
        "temporal_dispatcher_cert",
        "temporal_worker_cert",
        "temporal_reconciler_cert",
        "temporal_namespace_bootstrap_cert",
        "temporal_diagnostics_cert",
    ):
        assert stat.S_IMODE((secret_directory / secret_name).stat().st_mode) == 0o444
    certificate = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(secret_directory / "temporal_api_cert"),
            "-noout",
            "-subject",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert certificate.returncode == 0, certificate.stderr
    assert "CN=motionly-api" in certificate.stdout


def test_release_compose_input_writer_refuses_existing_secret_paths(tmp_path: Path) -> None:
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(json.dumps(_manifest(tmp_path)))
    environment_path = tmp_path / "compose.env"
    secret_directory = tmp_path / "secrets"
    secret_directory.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(WRITER_PATH),
            "--manifest",
            str(manifest_path),
            "--env-file",
            str(environment_path),
            "--secrets-dir",
            str(secret_directory),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "refusing to overwrite" in result.stderr
