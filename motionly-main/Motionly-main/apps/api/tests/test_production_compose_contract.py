"""Contracts for the immutable, fail-closed production Compose configuration."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.vendor_names import names_vendor

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
    "BILLING_MODE",
    "TEMPORAL_BOOTSTRAP_ADMIN_UNTIL",
)

SECRET_FILE_NAMES = (
    "postgres_password",
    "redis_password",
    "temporal_db_password",
    "supabase_service_role_key",
    "supabase_worker_key",
    "supabase_jwt_secret",
    "realtime_db_enc_key",
    "realtime_secret_key_base",
    "realtime_metrics_jwt_secret",
    "admin_api_token",
    "worker_api_token",
    # One generic inference secrets bundle (plan Task 26); no vendor-named key.
    "ai_secrets_bundle",
    # Bedrock static keys (docs/aws): mounted only into the API and the workers
    # that call a Bedrock route; empty when no Bedrock route is configured.
    "aws_inference_access_key_id",
    "aws_inference_secret_access_key",
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
        "BILLING_MODE": "disabled",
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
    # BILLING_MODE comes from the one env input so the web runtime agrees with
    # the API on the deployment's billing posture (B17).
    assert services["web"]["environment"] == {
        "APP_ENV": "production",
        "PUBLIC_ORIGIN": origin,
        "API_INTERNAL_URL": "http://api:8000",
        "SUPABASE_URL": "http://supabase-gateway:8000",
        "SUPABASE_ANON_KEY": "test-anon-key",
        "BILLING_MODE": "disabled",
    }
    api = services["api"]["environment"]
    assert api["API_PUBLIC_BASE_URL"] == f"{origin}/api"
    assert api["BILLING_MODE"] == "disabled"
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
        "supabase-realtime",
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


# Variables the pinned Realtime release reads with System.fetch_env! (its
# /app/releases/2.78.10/runtime.exs). GEN_RPC_CERTFILE/KEYFILE/CACERTFILE are
# required only when GEN_RPC_SSL_SERVER_PORT is set, which the stack does not.
REALTIME_REQUIRED_SECRETS = {
    "METRICS_JWT_SECRET": "realtime_metrics_jwt_secret",
    "SECRET_KEY_BASE": "realtime_secret_key_base",
}


def test_realtime_receives_every_variable_its_pinned_release_requires() -> None:
    source = COMPOSE_PATH.read_text(encoding="utf-8")
    block = _service_block(source, "supabase-realtime")
    assert "supabase/realtime:v2.78.10@sha256:" in block
    assert "GEN_RPC_SSL_SERVER_PORT" not in block
    writer = (ROOT / "scripts" / "release" / "write_compose_test_env.py").read_text("utf-8")
    agent = (ROOT / "scripts" / "deploy" / "apply_release.sh").read_text("utf-8")
    for variable, secret in REALTIME_REQUIRED_SECRETS.items():
        assert f'export {variable}="$(cat /run/secrets/{secret})"' in block, variable
        assert re.search(rf"^\s+{secret},?$", block, re.MULTILINE), secret
        assert re.search(rf"^  {secret}:\n    file: ", source, re.MULTILINE), secret
        # Generated for the CPU stage and the release rehearsal; fetched by the
        # release agent from Parameter Store in production.
        assert writer.count(f'"{secret}"') == 2, secret
        assert f'"$secrets_dir/{secret}"' in agent, secret



def test_a_fresh_database_creates_the_schema_realtime_migrates_into() -> None:
    """Realtime sets search_path to _realtime and creates its migrations table
    there; Postgres refuses ("no schema has been selected to create in") unless
    the schema exists first. Like Supabase's own self-hosting setup, a Postgres
    init script creates it when the data directory is initialised."""
    source = COMPOSE_PATH.read_text(encoding="utf-8")
    realtime = _service_block(source, "supabase-realtime")
    assert 'DB_AFTER_CONNECT_QUERY: "SET search_path TO _realtime"' in realtime
    postgres = _service_block(source, "postgres")
    mount = "./db/realtime.sql:/docker-entrypoint-initdb.d/init-scripts/98-realtime.sql:ro"
    assert mount in postgres
    script = (ROOT / "infra" / "db" / "realtime.sql").read_text(encoding="utf-8")
    assert "create schema if not exists _realtime;" in script
    assert "alter schema _realtime owner to supabase_admin;" in script
    assert "DB_USER: supabase_admin" in realtime


def test_realtime_healthcheck_uses_a_tool_its_image_ships() -> None:
    """The pinned Realtime image has curl and no wget; a wget healthcheck can
    never pass, so the service never reports healthy and db-migrate never runs."""
    block = _service_block(COMPOSE_PATH.read_text(encoding="utf-8"), "supabase-realtime")
    healthcheck = block[block.index("healthcheck:") :]
    assert '"wget"' not in healthcheck
    assert '"curl"' in healthcheck
    assert "http://127.0.0.1:4000/api/ping" in healthcheck
    # Realtime resolves the tenant from the Host header and answers 401
    # "Tenant not found" without it; Kong sends this host for the seeded tenant.
    assert '"Host: realtime-dev.supabase-realtime"' in healthcheck


def test_realtime_secret_shim_hands_off_to_the_images_own_startup() -> None:
    """The image's entrypoint (tini -> /app/run.sh) migrates, seeds the
    `realtime-dev` tenant Kong routes to when SEED_SELF_HOST=true, and applies
    RLIMIT_NOFILE. The secret-reading shim must exec it, not replace it: without
    the seed every /supabase/realtime request is refused with 'Tenant not found'."""
    block = _service_block(COMPOSE_PATH.read_text(encoding="utf-8"), "supabase-realtime")
    assert 'SEED_SELF_HOST: "true"' in block
    assert "exec /usr/bin/tini -s -g -- /app/run.sh /app/bin/server" in block
    assert "Realtime.Release.migrate" not in block


def _compose_services() -> dict[str, dict]:
    import yaml

    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]


def test_every_secret_path_a_service_reads_is_mounted_into_that_service() -> None:
    """A service that names /run/secrets/<x> in its environment or entrypoint
    without mounting <x> crashes at start (the API read the workers'
    supabase_worker_key through the shared media environment)."""
    problems = []
    for name, service in _compose_services().items():
        mounted = set(service.get("secrets") or [])
        text = json.dumps([service.get("environment") or {}, service.get("entrypoint") or []])
        for secret in sorted(set(re.findall(r"/run/secrets/([a-z0-9_]+)", text))):
            if secret not in mounted:
                problems.append(f"{name} reads /run/secrets/{secret} but does not mount it")
    assert problems == []


def test_the_api_uses_the_service_role_key_and_workers_use_the_restricted_key() -> None:
    services = _compose_services()
    assert services["api"]["environment"]["SUPABASE_SERVICE_ROLE_KEY_FILE"] == (
        "/run/secrets/supabase_service_role_key"
    )
    for worker in ("cpu-ffmpeg-worker", "remotion-worker"):
        assert services[worker]["environment"]["SUPABASE_SERVICE_ROLE_KEY_FILE"] == (
            "/run/secrets/supabase_worker_key"
        ), worker


# -- Task 26 acceptance ----------------------------------------------------------------


def test_a_fresh_production_compose_needs_no_vendor_variable_and_starts_no_gpu_service(
    tmp_path: Path,
) -> None:
    """Task 26 acceptance: a fresh production Compose configuration with only a
    registry file and a secrets bundle passes `compose config`, requires no
    vendor-specific environment variable, and starts no GPU service by default.
    """
    if shutil.which("docker") is None:
        pytest.fail("docker compose is required to validate the production Compose configuration")

    secret_directory = tmp_path / "secrets"
    secret_directory.mkdir(mode=0o700)
    for secret_name in SECRET_FILE_NAMES:
        (secret_directory / secret_name).write_text(f"test-{secret_name}\n")
    values = _production_values(secret_directory)

    # The only inference inputs are the generic registry file and the secrets
    # bundle; no vendor-specific variable is required to render or start the
    # stack. Checked with the neutrality scanner's matcher, plus the two
    # removed vendor-shaped settings families it does not cover.
    assert not [
        name
        for name in values
        if names_vendor(name) or name.startswith(("AZURE_OPENAI", "MODEL_GATEWAY_ROUTES"))
    ]

    environment_file = tmp_path / ".env"
    _write_env_file(environment_file, values)
    result = _compose_config(environment_file)
    assert result.returncode == 0, result.stderr or result.stdout

    rendered = subprocess.run(
        [
            "docker", "compose", "--env-file", str(environment_file),
            "-f", str(COMPOSE_PATH), "config", "--format", "json",
        ],
        cwd=ROOT,
        env=_compose_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    gpu_services = {"gpu-inference-worker", "matte-worker", "gpu-ffmpeg-worker"}
    assert not gpu_services & set(services)
    assert not [name for name, service in services.items() if service.get("gpus")]
    # The mounted inference inputs are exactly the registry volume and the one
    # generic secrets bundle; no vendor-named secret is mounted.
    api = services["api"]
    api_secrets = {item["source"] for item in api.get("secrets") or []}
    assert "ai_secrets_bundle" in api_secrets
    assert not [name for name in api_secrets if names_vendor(name)]
    assert api["environment"]["AI_REGISTRY_FILE"].endswith("/registry.json")
    assert api["environment"]["AI_SECRETS_BUNDLE_FILE"].endswith("/ai_secrets_bundle")


def test_create_app_in_production_serves_planning_and_transcription_from_a_registry() -> None:
    """Task 26 acceptance (application level): with only a registry file and a
    secrets bundle, a production app builds its inference runtime and serves the
    two required capabilities (planning, transcription), with no vendor setting.
    """
    from app.main import _validate_production_settings, build_inference_runtime
    from tests.test_service_hardening import _production_settings

    fixtures = ROOT / "apps/api/tests/fixtures/inference_registry"
    settings = _production_settings(
        process_role="worker",
        ai_registry_file=str(fixtures / "production_remote.json"),
        ai_secrets_bundle_file=str(fixtures / "bundle.json"),
        gpu_workers_enabled=False,
        visual_intelligence_provider="remote",
        matte_provider="local_cpu",
        matte_cpu_model_sha256="0" * 64,
        visual_cpu_face_model_sha256="0" * 64,
        visual_objects_provider="disabled",
        visual_captions_provider="disabled",
        audio_diarization_provider="disabled",
        audio_emotion_provider="disabled",
        audio_sound_provider="yamnet_tflite",
        stem_separation_provider="demucs",
    )

    # Production validation accepts the GPU-free, registry-only configuration.
    _validate_production_settings(settings)

    runtime = build_inference_runtime(settings)
    capabilities = {
        getattr(route.capability, "value", route.capability)
        for route in runtime.route_records.values()
    }
    assert "planning.chat" in capabilities
    assert "transcription.speech_to_text" in capabilities
