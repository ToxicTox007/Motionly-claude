"""Task 3: the production Compose topology starts from empty volumes.

* The Supabase Postgres image keeps its own init scripts (roles, auth and
  storage schemas). Mounting the migrations over /docker-entrypoint-initdb.d
  hid them, so an empty disk could never start (finding N1).
* A one-shot ``db-migrate`` service applies pending migrations on every
  ``docker compose up`` and records them where the Supabase CLI does. Every
  application service waits for it to succeed.
* GPU services run only with ``--profile gpu``, so the CPU stage can start the
  same topology without GPU hardware.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = ROOT / "infra" / "docker-compose.gpu.yml"
ROLES_SQL = ROOT / "infra" / "db" / "roles.sql"
MIGRATE_SCRIPT = ROOT / "infra" / "db" / "migrate.sh"
GPU_STAGE_WORKFLOW = ROOT / ".github" / "workflows" / "gpu-stage.yml"

GPU_SERVICES = ("gpu-inference-worker", "matte-worker", "gpu-ffmpeg-worker")
# Services that read or write application tables.
DATABASE_CLIENTS = (
    "api",
    "temporal-dispatcher",
    "temporal-worker",
    "workflow-reconciler",
    "cpu-ffmpeg-worker",
    "gpu-inference-worker",
    "matte-worker",
    "gpu-ffmpeg-worker",
    "demucs-worker",
    "provider-worker",
    "planning-worker",
    "remotion-worker",
)
HEALTHY = {"condition": "service_healthy"}
COMPLETED = {"condition": "service_completed_successfully"}


def _services() -> dict[str, dict]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]


def test_auth_issues_tokens_the_api_and_postgrest_accept() -> None:
    """Without these, GoTrue's defaults are empty: a password sign-in looks the
    user up with audience "" (so users created by the admin API, audience
    "authenticated", cannot sign in), new users get role "", PostgREST rejects
    their tokens (role "" does not exist), and the API rejects any token whose
    audience is not "authenticated". Verified on the CPU-stage stack.
    """
    environment = _services()["supabase-auth"]["environment"]

    assert environment["GOTRUE_JWT_AUD"] == "authenticated"
    assert environment["GOTRUE_JWT_DEFAULT_GROUP_NAME"] == "authenticated"
    assert environment["GOTRUE_JWT_ADMIN_ROLES"] == "service_role"


def test_postgres_keeps_the_image_init_scripts_on_an_empty_disk() -> None:
    postgres = _services()["postgres"]
    targets = [volume.split(":")[1] for volume in postgres["volumes"]]

    assert "/docker-entrypoint-initdb.d" not in targets
    assert (
        "./db/roles.sql:/docker-entrypoint-initdb.d/init-scripts/99-roles.sql:ro"
        in (postgres["volumes"])
    )


def test_role_passwords_come_from_the_postgres_secret() -> None:
    roles = ROLES_SQL.read_text(encoding="utf-8")

    # Init scripts run as the postgres OS user, which cannot open root-owned
    # secret files; the entrypoint exports the password from the secret as root.
    assert '\\set pgpass `echo "$POSTGRES_PASSWORD"`' in roles
    assert "/run/secrets" not in roles
    # The roles that Auth, REST and Storage log in as (see their database URLs).
    for role in ("authenticator", "supabase_auth_admin", "supabase_storage_admin"):
        assert f"alter user {role} with password :'pgpass';" in roles
    services = _services()
    assert (
        "supabase_auth_admin:" in services["supabase-auth"]["environment"]["GOTRUE_DB_DATABASE_URL"]
    )
    assert "authenticator:" in services["supabase-rest"]["environment"]["PGRST_DB_URI"]
    assert "supabase_storage_admin:" in services["supabase-storage"]["environment"]["DATABASE_URL"]


def test_storage_file_backend_names_the_folder_it_stores_objects_in() -> None:
    # storage-api's file backend keeps objects under GLOBAL_S3_BUCKET. Unset,
    # writes land in a folder named "undefined" and every delete fails with
    # 500 (path.resolve receives undefined), so media can never be removed.
    environment = _services()["supabase-storage"]["environment"]

    assert environment["STORAGE_BACKEND"] == "file"
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]{2,62}", str(environment.get("GLOBAL_S3_BUCKET", "")))


def test_db_migrate_runs_after_auth_and_storage_create_their_schemas() -> None:
    services = _services()
    migrate = services["db-migrate"]

    assert migrate["image"] == services["postgres"]["image"]
    assert migrate["restart"] == "no"
    assert migrate["entrypoint"] == ["/bin/sh", "/opt/motionly-db/migrate.sh"]
    # Auth, Storage and Realtime each create their own schema at startup; the
    # migrations attach policies to those schemas (migration 202610191101 adds
    # the private-channel RLS to realtime.messages), so db-migrate must wait for
    # all three to be healthy before it runs.
    assert migrate["depends_on"] == {
        "postgres": HEALTHY,
        "supabase-auth": HEALTHY,
        "supabase-storage": HEALTHY,
        "supabase-realtime": HEALTHY,
    }
    assert "../supabase/migrations:/opt/motionly-db/migrations:ro" in migrate["volumes"]
    assert "./db/migrate.sh:/opt/motionly-db/migrate.sh:ro" in migrate["volumes"]
    assert migrate["secrets"] == ["postgres_password"]
    assert migrate["networks"] == ["app"]
    for service in ("supabase-auth", "supabase-storage", "supabase-realtime"):
        assert services[service]["healthcheck"]["test"], service


def test_every_database_client_waits_for_migrations_to_succeed() -> None:
    services = _services()

    for service in DATABASE_CLIENTS:
        depends_on = services[service]["depends_on"]
        assert isinstance(depends_on, dict), service
        assert depends_on.get("db-migrate") == COMPLETED, service


def test_migration_runner_is_ordered_locked_and_recorded_like_the_supabase_cli() -> None:
    script = MIGRATE_SCRIPT.read_text(encoding="utf-8")

    assert "cat /run/secrets/postgres_password" in script
    assert "supabase_migrations.schema_migrations" in script
    assert "pg_advisory_lock" in script
    assert "ON_ERROR_STOP=1" in script
    assert "LC_ALL=C sort" in script
    assert "notify pgrst, 'reload schema'" in script
    # Production never loads development seed data.
    assert "seed" not in script.lower()


def test_gpu_services_start_only_with_the_gpu_profile() -> None:
    services = _services()

    for name, service in services.items():
        if name in GPU_SERVICES:
            assert service["profiles"] == ["gpu"], name
            assert service["gpus"] == "all", name
        else:
            assert "profiles" not in service, name
            assert "gpus" not in service, name


def test_gpu_stage_workflow_starts_the_gpu_profile() -> None:
    workflow = yaml.safe_load(GPU_STAGE_WORKFLOW.read_text(encoding="utf-8"))
    commands = "\n".join(
        step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]
    )
    compose_commands = [
        line.strip()
        for line in commands.splitlines()
        if "docker compose" in line and (" up " in line or " pull" in line)
    ]

    assert compose_commands
    for command in compose_commands:
        assert "--profile gpu" in command, command


def test_production_deploy_starts_the_gpu_profile() -> None:
    agent = (ROOT / "scripts" / "deploy" / "apply_release.sh").read_text(encoding="utf-8")

    # ComputeProfile=gpu (the production instance) starts the GPU services.
    assert '[[ "$MOTIONLY_COMPUTE_PROFILE" != gpu ]] || profiles=(--profile gpu)' in agent
    assert 'docker compose --project-name motionly "${profiles[@]}"' in agent


def test_secret_files_are_readable_by_non_root_container_users() -> None:
    """Compose mounts file secrets with the host mode and ignores uid/gid/mode.

    The API and workers run as uid 10001 and Postgres init as the postgres user,
    so every writer creates read-only 0444 files in a 0700 directory.
    """
    agent = (ROOT / "scripts" / "deploy" / "apply_release.sh").read_text(encoding="utf-8")
    local_setup = (ROOT / "setup-local-env.sh").read_text(encoding="utf-8")
    secrets_doc = (ROOT / "docs" / "security" / "secrets.md").read_text(encoding="utf-8")

    assert "destination.chmod(0o600)" not in agent
    assert agent.count("destination.chmod(0o444)") == 2
    assert "target.chmod(0o444)" in agent
    assert "chmod 444 *" in local_setup and "chmod 700 ." in local_setup
    assert "mode `0444`" in secrets_doc and "mode `0700`" in secrets_doc


def test_postgres_health_checks_wait_for_the_tcp_server_not_the_init_socket() -> None:
    """During first-boot init the image runs a socket-only server; dependents
    connect over TCP, so only a TCP probe means ready."""
    services = _services()
    postgres_services = [
        name
        for name, service in services.items()
        if "supabase/postgres" in service["image"] and "healthcheck" in service
    ]

    assert set(postgres_services) == {"postgres", "temporal-postgres"}
    for name in postgres_services:
        assert services[name]["healthcheck"]["test"] == [
            "CMD-SHELL",
            "pg_isready -U postgres -h 127.0.0.1",
        ], name


def test_temporal_database_bootstrap_sends_sql_with_variables_on_stdin() -> None:
    """psql interpolates :'variables' only in scripts read from stdin or files,
    never in --command strings, so the bootstrap must not use --command."""
    script = (ROOT / "infra" / "temporal" / "init-postgres.sh").read_text(encoding="utf-8")

    assert "--command" not in script
    assert " -c " not in script
    assert "\\gexec" in script
    assert "create database %I owner temporal" in script


def test_temporal_advertises_its_container_ip_and_listens_on_every_interface() -> None:
    """Ringpop rejects a host name as broadcastAddress; the frontend must be
    reachable from other containers and from the loopback health check."""
    temporal = ROOT / "infra" / "temporal"
    template = yaml.safe_load(
        (temporal / "config" / "production.yaml.tmpl")
        .read_text(encoding="utf-8")
        .replace("{{TEMPORAL_DB_PASSWORD}}", '"password"')
    )
    start = (temporal / "start-server.sh").read_text(encoding="utf-8")

    assert template["global"]["membership"]["broadcastAddress"] == "{{TEMPORAL_BROADCAST_ADDRESS}}"
    for service in ("frontend", "matching", "history", "worker"):
        assert template["services"][service]["rpc"]["bindOnIP"] == "0.0.0.0", service
    assert "hostname -i" in start
    assert "{{TEMPORAL_BROADCAST_ADDRESS}}" in start
    assert "--config-template" in start


def test_web_runtime_does_not_boot_through_a_package_manager() -> None:
    """The production app network has no public egress. Corepack downloads pnpm
    on first use (the build-time copy lives in root's cache, not the runtime
    user's), so the web container must start Next directly with node."""
    dockerfile = (ROOT / "apps" / "web" / "Dockerfile").read_text(encoding="utf-8")
    runtime_stage = dockerfile.split("\nFROM ", 1)[1]

    assert "corepack" not in runtime_stage
    assert 'CMD ["pnpm"' not in runtime_stage
    # The standalone server: server.js from the traced build, run by node.
    assert runtime_stage.rstrip().endswith('WORKDIR /app\nCMD ["node", "apps/web/server.js"]')


def test_web_runtime_ships_only_the_standalone_server() -> None:
    """FE-42: the runtime image holds the standalone output (server.js and the
    traced node_modules), the static assets and public/, never the whole build
    stage; and it still refuses to start without valid configuration."""
    web = ROOT / "apps" / "web"
    dockerfile = (web / "Dockerfile").read_text(encoding="utf-8")
    runtime_stage = dockerfile.split("\nFROM ", 1)[1]

    assert "COPY --from=build --chown=node:node /app ./" not in runtime_stage
    for source, target in (
        ("/app/apps/web/.next/standalone", "./"),
        ("/app/apps/web/.next/static", "./apps/web/.next/static"),
        ("/app/apps/web/public", "./apps/web/public"),
    ):
        assert f"COPY --from=build --chown=node:node {source} {target}" in runtime_stage
    # server.js listens on HOSTNAME, which Docker sets to the container id.
    assert "HOSTNAME=0.0.0.0" in runtime_stage

    next_config = (web / "next.config.ts").read_text(encoding="utf-8")
    assert 'output: "standalone"' in next_config
    assert "outputFileTracingRoot: MONOREPO_ROOT" in next_config
    # The standalone server does not run next.config.ts; instrumentation.ts
    # validates the runtime configuration at startup and exits non-zero.
    instrumentation = (web / "instrumentation.ts").read_text(encoding="utf-8")
    assert 'import("./lib/startup-config")' in instrumentation
    startup = (web / "lib" / "startup-config.ts").read_text(encoding="utf-8")
    assert "parseServerConfig(environment)" in startup
    assert "process.exit(1)" in startup
