"""Contracts for the immutable, fail-closed production Compose configuration."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = ROOT / "infra" / "docker-compose.gpu.yml"
PRODUCTION_TEMPLATE_PATH = ROOT / "infra" / "production-compose.env.example"

APPLICATION_IMAGE_VARIABLES = (
    "MOTIONLY_API_IMAGE",
    "MOTIONLY_WEB_IMAGE",
    "MOTIONLY_CPU_FFMPEG_WORKER_IMAGE",
    "MOTIONLY_GPU_WORKER_IMAGE",
    "MOTIONLY_GPU_FFMPEG_WORKER_IMAGE",
    "MOTIONLY_DEMUCS_WORKER_IMAGE",
    "MOTIONLY_PROVIDER_WORKER_IMAGE",
    "MOTIONLY_PLANNING_WORKER_IMAGE",
    "MOTIONLY_REMOTION_WORKER_IMAGE",
    "MOTIONLY_TEMPORAL_SERVER_IMAGE",
)

TEMPORAL_IMAGE_VARIABLES = ("TEMPORAL_ADMIN_TOOLS_IMAGE",)

REQUIRED_VARIABLES = (
    *APPLICATION_IMAGE_VARIABLES,
    *TEMPORAL_IMAGE_VARIABLES,
    "SECRETS_DIR",
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "SUPABASE_JWT_SECRET",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "APP_DOMAIN",
    "PUBLIC_ORIGIN",
    "TEMPORAL_BOOTSTRAP_ADMIN_UNTIL",
)

SECRET_FILE_NAMES = (
    "postgres_password",
    "redis_password",
    "temporal_db_password",
    "supabase_service_role_key",
    "supabase_jwt_secret",
    "admin_api_token",
    "worker_api_token",
    "groq_api_key",
    "openrouter_api_key",
    "runway_api_key",
    "azure_openai_api_key",
    "metrics_bearer_token",
    "edge_origin_verify",
    "edge_origin_verify_previous",
    "temporal_ca_cert",
    "temporal_server_cert",
    "temporal_server_key",
    "temporal_internode_cert",
    "temporal_internode_key",
    "temporal_api_cert",
    "temporal_api_key",
    "temporal_dispatcher_cert",
    "temporal_dispatcher_key",
    "temporal_worker_cert",
    "temporal_worker_key",
    "temporal_reconciler_cert",
    "temporal_reconciler_key",
    "temporal_namespace_bootstrap_cert",
    "temporal_namespace_bootstrap_key",
    "temporal_diagnostics_cert",
    "temporal_diagnostics_key",
)

POSTGRES_17_IMAGE = (
    "supabase/postgres:17.4.1.069@"
    "sha256:6900ef3bde159491c8774249dfd213926416f716ef0c897897ae4f88e6819c72"
)
DIGEST_IMAGE_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$")


def _production_values(secret_directory: Path) -> dict[str, str]:
    values = {
        "SECRETS_DIR": str(secret_directory),
        "POSTGRES_PASSWORD": "postgres-test-password",
        "REDIS_PASSWORD": "redis-test-password",
        "SUPABASE_JWT_SECRET": "test-jwt-secret",
        "SUPABASE_ANON_KEY": "test-anon-key",
        "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
        "APP_DOMAIN": "app.example.test",
        "PUBLIC_ORIGIN": "https://app.example.test",
        "TEMPORAL_BOOTSTRAP_ADMIN_UNTIL": "2026-08-12T00:00:00Z",
    }
    for variable in APPLICATION_IMAGE_VARIABLES:
        image_name = variable.lower().removesuffix("_image").replace("motionly_", "")
        values[variable] = f"ghcr.io/motionly/{image_name}@sha256:{'a' * 64}"
    values.update(
        {
            "TEMPORAL_ADMIN_TOOLS_IMAGE": f"temporalio/admin-tools@sha256:{'c' * 64}",
        }
    )
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))


def _compose_environment() -> dict[str, str]:
    # Do not let a developer's shell mask a missing-variable failure.
    environment = {key: value for key, value in os.environ.items() if key not in REQUIRED_VARIABLES}
    return environment


def _compose_config(environment_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(environment_file),
            "-f",
            str(COMPOSE_PATH),
            "config",
            "-q",
        ],
        cwd=ROOT,
        env=_compose_environment(),
        check=False,
        capture_output=True,
        text=True,
    )


def test_production_compose_requires_digest_images_and_postgres_17() -> None:
    source = COMPOSE_PATH.read_text()

    assert "build:" not in source
    assert POSTGRES_17_IMAGE in source
    assert "supabase/postgres:15" not in source

    image_references = re.findall(r"^\s+image:\s+([^\n]+)$", source, flags=re.MULTILINE)
    assert image_references
    for image_reference in image_references:
        if "MOTIONLY_" in image_reference:
            assert ":?" in image_reference
            assert "digest-pinned GHCR" in image_reference
        elif "TEMPORAL_" in image_reference:
            assert ":?" in image_reference
            assert "digest-pinned Temporal image" in image_reference
        else:
            assert "@sha256:" in image_reference, image_reference

    for variable in REQUIRED_VARIABLES:
        assert f"${{{variable}:?" in source

    template_values = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in PRODUCTION_TEMPLATE_PATH.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    for variable in REQUIRED_VARIABLES:
        assert variable in template_values
        assert not template_values[variable]


def test_production_compose_supplies_only_digest_pinned_ghcr_application_images(
    tmp_path: Path,
) -> None:
    values = _production_values(tmp_path / "secrets")

    for variable in APPLICATION_IMAGE_VARIABLES:
        assert DIGEST_IMAGE_PATTERN.fullmatch(values[variable]), values[variable]


@pytest.mark.parametrize("missing_variable", REQUIRED_VARIABLES)
def test_production_compose_fails_closed_for_every_required_variable(
    tmp_path: Path, missing_variable: str
) -> None:
    if shutil.which("docker") is None:
        pytest.fail("docker compose is required to validate the production Compose contract")

    secret_directory = tmp_path / "secrets"
    secret_directory.mkdir(mode=0o700)
    for secret_name in SECRET_FILE_NAMES:
        secret_path = secret_directory / secret_name
        secret_path.write_text(f"test-{secret_name}\n")
        secret_path.chmod(0o600)

    values = _production_values(secret_directory)
    environment_file = tmp_path / ".env"
    _write_env_file(environment_file, values)

    complete = _compose_config(environment_file)
    assert complete.returncode == 0, complete.stderr or complete.stdout

    values.pop(missing_variable)
    _write_env_file(environment_file, values)
    missing = _compose_config(environment_file)
    output = missing.stderr + missing.stdout
    assert missing.returncode != 0, output
    assert missing_variable in output


def test_services_derive_every_public_url_from_the_one_public_origin(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.fail("docker compose is required to validate the production Compose contract")
    secret_directory = tmp_path / "secrets"
    secret_directory.mkdir(mode=0o700)
    for secret_name in SECRET_FILE_NAMES:
        (secret_directory / secret_name).write_text(f"test-{secret_name}\n")
    values = _production_values(secret_directory)
    values["PUBLIC_ORIGIN"] = "https://d111abcdef8.cloudfront.net"
    environment_file = tmp_path / ".env"
    _write_env_file(environment_file, values)

    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(environment_file),
            "-f",
            str(COMPOSE_PATH),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=_compose_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    origin = "https://d111abcdef8.cloudfront.net"

    # The web image is configured at runtime: no NEXT_PUBLIC_* build values.
    assert services["web"]["environment"] == {
        "APP_ENV": "production",
        "PUBLIC_ORIGIN": origin,
        "API_INTERNAL_URL": "http://api:8000",
        "SUPABASE_URL": "http://supabase-gateway:8000",
        "SUPABASE_ANON_KEY": "test-anon-key",
    }
    api = services["api"]["environment"]
    assert api["API_PUBLIC_BASE_URL"] == f"{origin}/api"
    assert api["SUPABASE_PUBLIC_URL"] == f"{origin}/supabase"
    assert api["API_CORS_ORIGINS"] == origin
    assert api["SUPABASE_URL"] == "http://supabase-gateway:8000"
    auth = services["supabase-auth"]["environment"]
    assert auth["GOTRUE_SITE_URL"] == origin
    assert auth["API_EXTERNAL_URL"] == f"{origin}/supabase"
    assert services["ingress"]["environment"] == {"APP_DOMAIN": "app.example.test"}


def _service_block(source: str, service: str) -> str:
    marker = f"  {service}:\n"
    start = source.index(marker)
    match = re.search(r"^  [a-z0-9][a-z0-9-]*:\n|^volumes:", source[start + len(marker) :], re.M)
    assert match, service
    return source[start : start + len(marker) + match.start()]


def test_temporal_compose_is_private_bootstrapped_and_runs_every_role() -> None:
    source = COMPOSE_PATH.read_text()

    for service in (
        "temporal-postgres",
        "temporal-db-init",
        "temporal-schema",
        "temporal",
        "temporal-namespace",
        "temporal-dispatcher",
        "temporal-worker",
        "workflow-reconciler",
    ):
        assert f"  {service}:" in source
    assert "MOTIONLY_TEMPORAL_SERVER_IMAGE:?MOTIONLY_TEMPORAL_SERVER_IMAGE" in source
    assert "digest-pinned GHCR custom Temporal image" in source
    assert "TEMPORAL_ADMIN_TOOLS_IMAGE:?TEMPORAL_ADMIN_TOOLS_IMAGE" in source
    assert 'TEMPORAL_TLS_ENABLED: "true"' in source
    assert "TEMPORAL_SERVER_ROOT_CA_PATH: /run/secrets/temporal_ca_cert" in source
    assert "TEMPORAL_SERVER_NAME: temporal" in source
    assert 'ports: ["7233:7233"]' not in source
    assert "7233:" not in _service_block(source, "temporal")
    assert "temporal:7233" not in (ROOT / "infra" / "Caddyfile").read_text()
    for secret in (
        "temporal_ca_cert",
        "temporal_api_cert",
        "temporal_dispatcher_cert",
        "temporal_worker_cert",
        "temporal_reconciler_cert",
        "temporal_namespace_bootstrap_cert",
        "temporal_diagnostics_cert",
    ):
        assert secret in source


def test_compose_network_contract_has_no_default_or_temporal_persistence_escape() -> None:
    source = COMPOSE_PATH.read_text()
    # Only the ingress network publishes a port (CloudFront's origin); every
    # other network is private and all services declare an explicit membership.
    assert "  ingress: {}" in source
    for network in ("app", "temporal-control", "temporal-persistence"):
        assert f"  {network}:\n    internal: true" in source
    services = (
        "postgres",
        "temporal-postgres",
        "temporal-db-init",
        "temporal-schema",
        "temporal",
        "temporal-namespace",
        "redis",
        "supabase-auth",
        "supabase-rest",
        "supabase-storage",
        "supabase-gateway",
        "api",
        "temporal-dispatcher",
        "temporal-worker",
        "workflow-reconciler",
        "web",
        "ingress",
        "cpu-ffmpeg-worker",
        "gpu-inference-worker",
        "matte-worker",
        "gpu-ffmpeg-worker",
        "demucs-worker",
        "provider-worker",
        "planning-worker",
        "remotion-worker",
    )
    for service in services:
        assert "networks:" in _service_block(source, service), service
    assert "networks: [temporal-persistence]" in _service_block(source, "temporal-postgres")
    assert "networks: [temporal-persistence]" in _service_block(source, "temporal-db-init")
    assert "networks: [temporal-persistence]" in _service_block(source, "temporal-schema")
    assert "networks: [temporal-control, temporal-persistence]" in _service_block(
        source, "temporal"
    )
    assert "temporal-persistence" not in _service_block(source, "api")
    assert "temporal-persistence" not in _service_block(source, "temporal-dispatcher")
    assert "temporal-persistence" not in _service_block(source, "temporal-worker")
    assert "temporal-persistence" not in _service_block(source, "workflow-reconciler")
    assert "temporal-control" not in _service_block(source, "web")
    assert "temporal-control" not in _service_block(source, "ingress")


def test_temporal_bootstrap_scripts_use_secrets_and_matching_schema_tools() -> None:
    temporal_root = ROOT / "infra" / "temporal"
    database_bootstrap = (temporal_root / "init-postgres.sh").read_text()
    schema_bootstrap = (temporal_root / "apply-schema.sh").read_text()
    namespace_bootstrap = (temporal_root / "bootstrap-namespace.sh").read_text()
    server_start = (temporal_root / "start-server.sh").read_text()

    assert "cat /run/secrets/postgres_password" in database_bootstrap
    assert "cat /run/secrets/temporal_db_password" in database_bootstrap
    assert "create database" in database_bootstrap
    assert "temporal_visibility" in database_bootstrap
    assert "temporal-sql-tool" in schema_bootstrap
    assert "setup-schema -v 0.0" in schema_bootstrap
    assert "update-schema" in schema_bootstrap
    assert "postgresql/v12/temporal/versioned" in schema_bootstrap
    assert "postgresql/v12/visibility/versioned" in schema_bootstrap
    assert "operator cluster health" in namespace_bootstrap
    assert "--tls-ca-path /run/secrets/temporal_ca_cert" in namespace_bootstrap
    assert "temporal_namespace_bootstrap_cert" in namespace_bootstrap
    assert "operator namespace create" in namespace_bootstrap
    assert "TEMPORAL_NAMESPACE_RETENTION" in namespace_bootstrap
    assert "motionly-temporal-server start" in server_start
    assert "MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL" in server_start
    template = (temporal_root / "config" / "production.yaml.tmpl").read_text()
    assert "requireClientAuth: true" in template
    assert "clientCaFiles:" in template
    assert "serverName: temporal" in template
    assert "temporal_server_cert" in template and "temporal_internode_cert" in template
