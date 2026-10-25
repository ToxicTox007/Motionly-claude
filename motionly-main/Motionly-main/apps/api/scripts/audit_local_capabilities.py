#!/usr/bin/env python3
"""Render a redacted, side-effect-free capability report for local audit.

This command never starts containers, builds or pulls images, writes files, or invokes
providers.  By default GPU support is explicitly
``not-probed``; ``--probe-gpu`` only reads local NVIDIA and Docker metadata.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.local_audit import (  # noqa: E402
    LOCAL_AUDIT_AUTHORIZATION_SECRET_NAME,
    PolicyCompilation,
    compile_local_audit_policy,
)

OVERLAY_PATH = ROOT / "infra" / "docker-compose.local-audit.yml"
DEV_COMPOSE_PATH = ROOT / "infra" / "docker-compose.dev.yml"
BASE_COMPOSE_PATH = ROOT / "infra" / "docker-compose.gpu.yml"
MATRIX_PATH = ROOT / "infra" / "local-audit" / "service-matrix.json"
LOCAL_CADDYFILE_PATH = ROOT / "infra" / "Caddyfile.local-audit"

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
    "TEMPORAL_ADMIN_TOOLS_IMAGE",
)
DIRECT_COMPOSE_VALUES = (
    "COMPOSE_PROJECT_NAME",
    "SECRETS_DIR",
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "SUPABASE_JWT_SECRET",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "APP_DOMAIN",
    "PUBLIC_ORIGIN",
    "TEMPORAL_BOOTSTRAP_ADMIN_UNTIL",
    *APPLICATION_IMAGE_VARIABLES,
)
SECRET_FILE_NAMES = (
    "postgres_password",
    "redis_password",
    "temporal_db_password",
    "supabase_service_role_key",
    "supabase_worker_key",
    "supabase_jwt_secret",
    "admin_api_token",
    "worker_api_token",
    # Inference routes read their credentials from one generic secrets bundle
    # (plan Task 26); no vendor-named key file is mounted. A fixture/local-audit
    # deployment leaves it empty.
    "ai_secrets_bundle",
    "metrics_bearer_token",
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
    LOCAL_AUDIT_AUTHORIZATION_SECRET_NAME,
)
OPTIONAL_EMPTY_SECRET_FILES = frozenset({"ai_secrets_bundle"})
LOCAL_AUDIT_VOLUMES = (
    "motionly-media",
    "motionly-model-cache",
    "supabase-db",
    "temporal-db",
    "supabase-storage",
    "redis-data",
    "caddy-data",
    "caddy-config",
)
LOCAL_IMAGE_NAMES = (
    "motionly-api:dev",
    "motionly-web:dev",
    "motionly-cpu-ffmpeg-worker:dev",
    "motionly-gpu-worker:dev",
    "motionly-gpu-ffmpeg-worker:dev",
    "motionly-demucs-worker:dev",
    "motionly-provider-worker:dev",
    "motionly-planning-worker:dev",
    "motionly-remotion-worker:dev",
    "motionly-temporal-server:local-audit",
)
_PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "changeme",
    "change-me",
    "replace",
    "placeholder",
    "example",
    "your-",
    "todo",
)
_VERSION_PATTERN = re.compile(r"\d+(?:\.\d+){1,3}")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""


CommandRunner = Callable[[tuple[str, ...]], CommandResult]
ToolProbe = Callable[[], dict[str, dict[str, object]]]
ImageProbe = Callable[[dict[str, dict[str, object]]], dict[str, object]]
GpuProbe = Callable[[dict[str, dict[str, object]]], dict[str, object]]
CapacityProbe = Callable[[], tuple[int | None, int | None]]
ComposeProbe = Callable[[Path], str]


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a simple protected Compose env file without shell expansion."""

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def build_capability_report(
    *,
    env: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    probe_gpu: bool = False,
    command_runner: CommandRunner | None = None,
    tool_probe: ToolProbe | None = None,
    image_probe: ImageProbe | None = None,
    gpu_probe: GpuProbe | None = None,
    capacity_probe: CapacityProbe | None = None,
    compose_probe: ComposeProbe | None = None,
) -> dict[str, object]:
    """Build a JSON-safe report without starting any service or remote request."""

    errors: list[str] = []
    values = dict(env if env is not None else os.environ)
    if env_file is not None:
        if not env_file.is_file():
            errors.append("environment_file_missing")
        else:
            values = parse_env_file(env_file)
            errors.extend(_protected_env_file_errors(env_file))

    isolation, isolation_errors = _inspect_isolation_contract()
    errors.extend(isolation_errors)
    input_report, input_errors, authorization = _inspect_protected_inputs(values)
    errors.extend(input_errors)
    environment_errors = _validate_compose_environment(values)
    errors.extend(environment_errors)

    compilation = compile_local_audit_policy(values, authorization=authorization)
    errors.extend(compilation.errors)

    runner = command_runner or _run_command
    tools = tool_probe() if tool_probe is not None else _probe_tools(runner)
    errors.extend(_tool_errors(tools))

    capacities = (capacity_probe or _host_capacity)()
    capacity_report, capacity_errors = _capacity_report(values, capacities)
    errors.extend(capacity_errors)

    if probe_gpu:
        gpu = gpu_probe(tools) if gpu_probe is not None else _probe_gpu(tools, runner)
    else:
        gpu = {"status": "not-probed", "validated": False}
    if not bool(gpu.get("validated")):
        errors.append("gpu_support_not_validated")

    images = image_probe(tools) if image_probe is not None else _probe_images(tools, runner)
    if images.get("status") != "ready":
        errors.append("required_images_not_available")

    compose_config = _compose_config_report(
        env_file=env_file,
        tools=tools,
        command_runner=runner,
        compose_probe=compose_probe,
    )
    if compose_config["status"] != "ready":
        errors.append("rendered_compose_config_not_validated")

    live_effects = _safe_compilation_report(compilation)
    errors = sorted(set(errors))
    return {
        "schema_version": 1,
        "mode": "plan-only",
        "ready": not errors,
        "errors": errors,
        "side_effects": {
            "containers_started": False,
            "images_built": False,
            "images_pulled": False,
            "provider_calls": False,
            "files_written": False,
        },
        "namespace": {
            "compose_project_name": "motionly-local-audit",
            "profile": "local-audit",
            "audit_owned_volume_prefix": "motionly-local-audit-",
            "automatic_volume_deletion": "disabled",
        },
        "isolation": isolation,
        "protected_inputs": input_report,
        "live_effects": live_effects,
        "tools": tools,
        "host_capacity": capacity_report,
        "gpu": gpu,
        "required_images": images,
        "compose_config": compose_config,
    }


def _inspect_isolation_contract() -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    try:
        overlay = OVERLAY_PATH.read_text(encoding="utf-8")
        dev_compose = DEV_COMPOSE_PATH.read_text(encoding="utf-8")
        base_compose = BASE_COMPOSE_PATH.read_text(encoding="utf-8")
        caddyfile = LOCAL_CADDYFILE_PATH.read_text(encoding="utf-8")
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid"}, ["local_audit_contract_unreadable"]

    service_names = [
        entry.get("name")
        for entry in matrix.get("services", [])
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]
    profiled_count = sum(
        1 for name in service_names if f"  {name}:\n    profiles: [local-audit]" in overlay
    )
    if "name: motionly-local-audit" not in overlay:
        errors.append("local_audit_project_name_missing")
    if profiled_count != len(service_names):
        errors.append("local_audit_profile_does_not_cover_topology")
    if "127.0.0.1:18080:80" not in overlay or "127.0.0.1:18443:443" not in overlay:
        errors.append("loopback_ingress_ports_missing")
    if "ports: !override" not in overlay:
        errors.append("ingress_ports_not_replaced")
    if "tls internal" not in caddyfile:
        errors.append("local_ingress_must_not_request_public_certificates")
    if 'LOCAL_AUDIT_MODE: "true"' not in overlay:
        errors.append("local_audit_mode_not_enabled_in_overlay")
    expected_default_denial = (
        "LOCAL_AUDIT_EXTERNAL_EFFECTS_ENABLED: ${LOCAL_AUDIT_EXTERNAL_EFFECTS_ENABLED:-false}"
    )
    if expected_default_denial not in overlay:
        errors.append("live_effects_default_not_denied")
    if LOCAL_AUDIT_AUTHORIZATION_SECRET_NAME not in overlay:
        errors.append("local_audit_authorization_secret_missing")
    if "gpus: all" not in base_compose or 'FFMPEG_GPU_ENABLED: "true"' not in base_compose:
        errors.append("gpu_compose_contract_missing")
    if dev_compose.count("build:") < 10 or "infra/temporal/server/Dockerfile" not in overlay:
        errors.append("required_local_image_builds_missing")
    for volume in LOCAL_AUDIT_VOLUMES:
        if f"{volume}:\n    name: motionly-local-audit-" not in overlay:
            errors.append("audit_owned_volume_names_missing")
            break
    if " down -v" in overlay or "docker volume prune" in overlay:
        errors.append("unsafe_volume_teardown_declared")

    return (
        {
            "status": "ready" if not errors else "invalid",
            "profiled_service_count": profiled_count,
            "topology_service_count": len(service_names),
            "loopback_ingress_ports": ["127.0.0.1:18080", "127.0.0.1:18443"],
            "public_certificate_requests": False,
            "audit_owned_volume_count": len(LOCAL_AUDIT_VOLUMES),
            "source_build_definition_count": dev_compose.count("build:") + 1,
        },
        errors,
    )


def _inspect_protected_inputs(
    values: Mapping[str, str],
) -> tuple[dict[str, object], list[str], str | None]:
    errors: list[str] = []
    secret_directory_value = str(values.get("SECRETS_DIR") or "").strip()
    report: dict[str, object] = {
        "secret_directory": "missing",
        "required_secret_file_count": len(SECRET_FILE_NAMES),
        "present_secret_file_count": 0,
        "invalid_secret_file_count": 0,
        "authorization": {"present": False},
    }
    if not secret_directory_value:
        return report, ["secrets_directory_required"], None

    try:
        secret_directory = Path(secret_directory_value).expanduser().resolve()
    except OSError:
        return report, ["secrets_directory_invalid"], None
    if not secret_directory.is_dir():
        return report, ["secrets_directory_invalid"], None
    if _is_within(secret_directory, ROOT):
        return report, ["secrets_directory_must_be_outside_repository"], None

    protection_errors = _protected_path_errors(secret_directory, is_directory=True)
    errors.extend(protection_errors)
    present = 0
    invalid = 0
    authorization: str | None = None
    for name in SECRET_FILE_NAMES:
        path = secret_directory / name
        if not path.is_file():
            invalid += 1
            continue
        present += 1
        errors.extend(_protected_path_errors(path, is_directory=False, secret_file=True))
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            invalid += 1
            continue
        if name not in OPTIONAL_EMPTY_SECRET_FILES and _is_placeholder(value, min_length=8):
            invalid += 1
        if name == LOCAL_AUDIT_AUTHORIZATION_SECRET_NAME:
            authorization = value

    authorization_present = bool(
        authorization and not _is_placeholder(authorization, min_length=16)
    )
    report.update(
        {
            "secret_directory": "protected" if not protection_errors else "unprotected",
            "present_secret_file_count": present,
            "invalid_secret_file_count": invalid,
            "authorization": {"present": authorization_present},
        }
    )
    if present != len(SECRET_FILE_NAMES):
        errors.append("required_secret_files_missing")
    if invalid:
        errors.append("secret_file_missing_or_placeholder")
    return report, errors, authorization


def _validate_compose_environment(values: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    for name in DIRECT_COMPOSE_VALUES:
        if _is_placeholder(values.get(name), min_length=1):
            errors.append("required_compose_input_missing_or_placeholder")
            break
    if values.get("COMPOSE_PROJECT_NAME") != "motionly-local-audit":
        errors.append("compose_project_name_not_isolated")
    # One public origin; its host must be a local audit name too.
    origin_host = urlsplit(str(values.get("PUBLIC_ORIGIN") or "")).hostname
    if not _is_local_audit_domain(values.get("APP_DOMAIN")) or not _is_local_audit_domain(
        origin_host
    ):
        errors.append("production_domain_not_allowed")
    if not _valid_rfc3339_utc(values.get("TEMPORAL_BOOTSTRAP_ADMIN_UNTIL")):
        errors.append("temporal_bootstrap_expiry_invalid")
    if not _as_bool(values.get("LOCAL_AUDIT_REQUIRE_GPU")):
        errors.append("gpu_required_by_local_audit_profile")
    return errors


def _probe_tools(command_runner: CommandRunner) -> dict[str, dict[str, object]]:
    checks = {
        "docker": (("docker", "--version"), None),
        "compose": (("docker", "compose", "version", "--short"), None),
        "node": (("node", "--version"), "22"),
        "pnpm": (("pnpm", "--version"), "9.15.4"),
        "python": (("python", "--version"), "3.12"),
        "supabase": (("supabase", "--version"), "2.104.0"),
        "ffmpeg": (("ffmpeg", "-version"), None),
    }
    report: dict[str, dict[str, object]] = {}
    for name, (command, required_version) in checks.items():
        result = command_runner(command)
        observed = _version_from_output(result.stdout)
        if result.returncode != 0:
            status = "missing"
        elif required_version and not _version_matches(observed, required_version):
            status = "version-mismatch"
        else:
            status = "ready"
        entry: dict[str, object] = {"status": status}
        if required_version:
            entry["required_version"] = required_version
        if observed:
            entry["observed_version"] = observed
        report[name] = entry

    chromium_commands = (
        ("chromium", "--version"),
        ("chromium-browser", "--version"),
        ("google-chrome", "--version"),
        ("chrome", "--version"),
    )
    for command in chromium_commands:
        result = command_runner(command)
        if result.returncode == 0:
            report["chromium"] = {
                "status": "ready",
                "observed_version": _version_from_output(result.stdout) or "reported",
            }
            break
    else:
        report["chromium"] = {"status": "missing"}
    return report


def _tool_errors(tools: Mapping[str, Mapping[str, object]]) -> list[str]:
    errors = []
    for name, report in tools.items():
        if report.get("status") != "ready":
            errors.append(f"tool_{name}_not_ready")
    return errors


def _probe_images(
    tools: Mapping[str, Mapping[str, object]],
    command_runner: CommandRunner,
) -> dict[str, object]:
    if tools.get("docker", {}).get("status") != "ready":
        return {"status": "not-probed", "required_image_count": len(LOCAL_IMAGE_NAMES)}
    available = 0
    for image_name in LOCAL_IMAGE_NAMES:
        result = command_runner(("docker", "image", "inspect", image_name, "--format", "{{.Id}}"))
        if result.returncode == 0:
            available += 1
    return {
        "status": "ready" if available == len(LOCAL_IMAGE_NAMES) else "missing",
        "required_image_count": len(LOCAL_IMAGE_NAMES),
        "available_image_count": available,
        "operation": "local-metadata-only",
    }


def _probe_gpu(
    tools: Mapping[str, Mapping[str, object]],
    command_runner: CommandRunner,
) -> dict[str, object]:
    nvidia = command_runner(
        ("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader")
    )
    if nvidia.returncode != 0:
        return {"status": "unavailable", "validated": False, "gpu_count": 0}
    docker_runtime = CommandResult(returncode=1)
    if tools.get("docker", {}).get("status") == "ready":
        docker_runtime = command_runner(("docker", "info", "--format", "{{json .Runtimes}}"))
    gpu_count = len([line for line in nvidia.stdout.splitlines() if line.strip()])
    validated = gpu_count > 0 and docker_runtime.returncode == 0
    return {
        "status": "ready" if validated else "container-access-unavailable",
        "validated": validated,
        "gpu_count": gpu_count,
        "docker_runtime_metadata": "available" if docker_runtime.returncode == 0 else "unavailable",
    }


def _host_capacity() -> tuple[int | None, int | None]:
    try:
        free_disk = shutil.disk_usage(ROOT).free
    except OSError:
        free_disk = None
    return free_disk, _total_memory_bytes()


def _capacity_report(
    values: Mapping[str, str],
    capacities: tuple[int | None, int | None],
) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    min_disk_gb = _positive_float(values.get("LOCAL_AUDIT_MIN_FREE_DISK_GB"))
    min_memory_gb = _positive_float(values.get("LOCAL_AUDIT_MIN_MEMORY_GB"))
    if min_disk_gb is None or min_memory_gb is None:
        return {"status": "invalid"}, ["capacity_thresholds_required"]
    free_disk, total_memory = capacities
    disk_ready = free_disk is not None and free_disk >= int(min_disk_gb * 1024**3)
    memory_ready = total_memory is not None and total_memory >= int(min_memory_gb * 1024**3)
    if not disk_ready:
        errors.append("insufficient_free_disk_capacity")
    if not memory_ready:
        errors.append("insufficient_memory_capacity")
    return (
        {
            "status": "ready" if not errors else "insufficient",
            "minimum_free_disk_gb": min_disk_gb,
            "minimum_memory_gb": min_memory_gb,
            "free_disk_gb": _bytes_to_gb(free_disk),
            "total_memory_gb": _bytes_to_gb(total_memory),
        },
        errors,
    )


def _compose_config_report(
    *,
    env_file: Path | None,
    tools: Mapping[str, Mapping[str, object]],
    command_runner: CommandRunner,
    compose_probe: ComposeProbe | None,
) -> dict[str, object]:
    if env_file is None or not env_file.is_file():
        return {"status": "not-probed"}
    if compose_probe is not None:
        return {"status": compose_probe(env_file)}
    if tools.get("compose", {}).get("status") != "ready":
        return {"status": "not-probed"}
    result = command_runner(
        (
            "docker",
            "compose",
            "--project-name",
            "motionly-local-audit",
            "--env-file",
            str(env_file),
            "-f",
            str(BASE_COMPOSE_PATH),
            "-f",
            str(DEV_COMPOSE_PATH),
            "-f",
            str(OVERLAY_PATH),
            "--profile",
            "local-audit",
            "config",
            "-q",
        )
    )
    return {
        "status": "ready" if result.returncode == 0 else "invalid",
        "operation": "render-only",
    }


def _safe_compilation_report(compilation: PolicyCompilation) -> dict[str, object]:
    if compilation.policy is None:
        return {
            "status": "blocked",
            "validation_errors": list(compilation.errors),
        }
    report = compilation.policy.safe_report()
    report["status"] = (
        "ready" if compilation.policy.external_effects_enabled else "blocked-by-default"
    )
    return report


def _protected_env_file_errors(path: Path) -> list[str]:
    if _is_within(path.resolve(), ROOT):
        return ["environment_file_must_be_outside_repository"]
    return _protected_path_errors(path, is_directory=False)


def _protected_path_errors(
    path: Path, *, is_directory: bool, secret_file: bool = False
) -> list[str]:
    if os.name == "nt":
        return [] if _windows_acl_is_private(path) else ["protected_input_acl_not_private"]
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return ["protected_input_unreadable"]
    # Directories and the env file must be private. Secret files may be
    # read-only for everyone (non-root containers read their mounts) because
    # their private directory already keeps other host users out.
    blocked = 0o022 if secret_file and not is_directory else 0o077
    if mode & blocked:
        return ["protected_input_permissions_not_private"]
    return []


def _windows_acl_is_private(path: Path) -> bool:
    """Reject broadly-readable Windows ACLs without emitting ACLs or paths."""

    try:
        result = subprocess.run(
            ["icacls", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    broad_principals = (
        "everyone",
        "authenticated users",
        "builtin\\users",
        "builtin\\guests",
    )
    lowered = result.stdout.lower()
    return not any(principal in lowered for principal in broad_principals)


def _run_command(command: tuple[str, ...]) -> CommandResult:
    candidates = [command]
    if os.name == "nt" and not Path(command[0]).suffix:
        candidates.append((f"{command[0]}.cmd", *command[1:]))
    first_result: CommandResult | None = None
    for candidate in candidates:
        try:
            result = subprocess.run(
                list(candidate),
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        command_result = CommandResult(
            returncode=result.returncode,
            stdout=result.stdout + result.stderr,
        )
        if command_result.returncode == 0:
            return command_result
        if first_result is None:
            first_result = command_result
    return first_result or CommandResult(returncode=1)


def _total_memory_bytes() -> int | None:
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return None
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def _version_from_output(value: str) -> str | None:
    match = _VERSION_PATTERN.search(value)
    return match.group(0) if match else None


def _version_matches(observed: str | None, required: str) -> bool:
    if observed is None:
        return False
    if required.count(".") == 0:
        return observed.split(".", 1)[0] == required
    if required.count(".") == 1:
        return observed.startswith(f"{required}.") or observed == required
    return observed == required


def _is_placeholder(value: object, *, min_length: int) -> bool:
    candidate = str(value or "").strip()
    if len(candidate) < min_length:
        return True
    lowered = candidate.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _is_local_audit_domain(value: object) -> bool:
    candidate = str(value or "").strip().lower().rstrip(".")
    return any(
        (
            candidate == "localhost",
            candidate.endswith(".localhost"),
            candidate.endswith(".test"),
            candidate.endswith(".invalid"),
        )
    )


def _valid_rfc3339_utc(value: object) -> bool:
    candidate = str(value or "").strip()
    if not candidate.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(candidate.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def _as_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _bytes_to_gb(value: int | None) -> float | None:
    return round(value / 1024**3, 2) if value is not None else None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="render capability evidence without starting services or remote requests",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="protected local Compose environment file outside the repository",
    )
    parser.add_argument(
        "--probe-gpu",
        action="store_true",
        help="read local NVIDIA and Docker runtime metadata; never start a container",
    )
    args = parser.parse_args(argv)
    if not args.plan_only:
        parser.error("--plan-only is required; this command never starts runtime services")
    report = build_capability_report(env_file=args.env_file, probe_gpu=args.probe_gpu)
    json.dump(report, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
