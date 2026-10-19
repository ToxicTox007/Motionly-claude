"""Task 3: the CPU-stage stack runs the production topology without GPU hardware.

``infra/docker-compose.cpu-stage.yml`` is applied on top of the production
``docker-compose.gpu.yml``. These tests pin what it may change: environment
(fixtures, labelled, never production), sizes, loopback-only ingress and
memory caps. They also cover the generated inputs and the resource sampler's
verdict, which fails a run on any restart, OOM kill or failed one-shot.
"""

from __future__ import annotations

import importlib.util
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "infra" / "docker-compose.gpu.yml"
OVERLAY = ROOT / "infra" / "docker-compose.cpu-stage.yml"
STACK = ROOT / "tests" / "cpu-stage" / "stack.json"
BUDGET = ROOT / "tests" / "cpu-stage" / "resource-budget.json"
WRITER = ROOT / "scripts" / "release" / "write_compose_test_env.py"
SAMPLER = ROOT / "scripts" / "verify" / "resource_sampler.py"
QUEUE_REGISTRY = ROOT / "apps" / "worker" / "src" / "queue-common.mjs"
GPU_SERVICES = {"gpu-inference-worker", "matte-worker", "gpu-ffmpeg-worker"}
GPU_ONLY_QUEUES = {"render_gpu"}
FIXTURE_ENVIRONMENT = {
    "APP_ENV": "cpu-stage",
    "ML_EXECUTION_MODE": "test_fixture",
    "ALLOW_FIXTURE_PROVIDERS": "true",
    "VISUAL_PROVIDER": "fixture",
    "TRANSCRIPTION_PROVIDER": "fixture",
    "EDIT_PLANNER_PROVIDER": "fixture",
    "STEM_SEPARATION_PROVIDER": "fixture",
    "STOCK_VIDEO_PROVIDER": "fixture",
    "FFMPEG_GPU_ENABLED": "false",
    "MODEL_PREFETCH": "false",
}


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that accepts Compose's merge tags (!override, !reset)."""


def _compose_tag(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _compose_tag)


def _yaml(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # noqa: S506


def _canonical_queues() -> set[str]:
    source = QUEUE_REGISTRY.read_text(encoding="utf-8")
    block = source.split("export const allQueueNames = [", 1)[1].split("];", 1)[0]
    return set(re.findall(r'"([a-z_]+)"', block))


def _merged() -> dict[str, dict]:
    """Overlay services merged onto base services (environment and profiles)."""
    base = _yaml(BASE)["services"]
    overlay = _yaml(OVERLAY)["services"]
    merged = {name: dict(service) for name, service in base.items()}
    for name, service in overlay.items():
        target = merged.setdefault(name, {})
        environment = dict(target.get("environment") or {})
        environment.update(service.get("environment") or {})
        target.update({key: value for key, value in service.items() if key != "environment"})
        target["environment"] = environment
    return merged


def _queues(service: dict) -> set[str]:
    return set(str(service["environment"].get("WORKER_QUEUES", "")).split(",")) - {""}


def _started(merged: dict[str, dict], size: str) -> dict[str, dict]:
    return {
        name: service
        for name, service in merged.items()
        if not service.get("profiles") or size in service["profiles"]
    }


def test_overlay_is_a_named_cpu_stage_project_that_builds_nothing_and_pulls_no_app_images() -> None:
    overlay = _yaml(OVERLAY)
    source = OVERLAY.read_text(encoding="utf-8")

    assert overlay["name"] == "motionly-cpu-stage"
    assert "build:" not in source
    assert "gpus:" not in source
    assert not set(overlay["services"]) & GPU_SERVICES


@pytest.mark.parametrize("size", ["slim", "full"])
def test_each_size_serves_every_cpu_queue_exactly_once_and_no_gpu_service(size: str) -> None:
    started = _started(_merged(), size)
    served: list[str] = []
    for service in started.values():
        served.extend(_queues(service))

    assert not set(started) & GPU_SERVICES
    assert set(served) == _canonical_queues() - GPU_ONLY_QUEUES
    assert len(served) == len(set(served)), sorted(served)


@pytest.mark.parametrize("size", ["slim", "full"])
def test_every_application_process_runs_cpu_stage_fixtures_and_never_production(size: str) -> None:
    started = _started(_merged(), size)
    # The web app runs no models or providers; it only needs its environment.
    assert started["web"]["environment"]["APP_ENV"] == "cpu-stage"
    application = [
        name
        for name, service in started.items()
        if name != "web"
        and ("APP_ENV" in service["environment"] or "PROCESS_ROLE" in service["environment"])
    ]

    assert {"api", "temporal-dispatcher", "temporal-worker", "workflow-reconciler"} <= set(
        application
    )
    for name in application:
        environment = started[name]["environment"]
        for key, value in FIXTURE_ENVIRONMENT.items():
            assert environment.get(key) == value, (name, key)
        assert environment.get("ML_WORKER_ENABLED") in (None, "false"), name


def test_ingress_is_loopback_only_and_plain_http_without_acme() -> None:
    overlay = _yaml(OVERLAY)["services"]
    ingress = overlay["ingress"]

    assert ingress["ports"] == ["127.0.0.1:18080:80"]
    assert "./Caddyfile.cpu-stage:/etc/caddy/Caddyfile:ro" in ingress["volumes"]
    caddyfile = (ROOT / "infra" / "Caddyfile.cpu-stage").read_text(encoding="utf-8")
    assert "auto_https off" in caddyfile
    # One plain-HTTP site for any host; the shared routes split it by path
    # (test_single_origin_edge.py).
    sites = re.findall(r"^(\S+) \{$", caddyfile, flags=re.MULTILINE)
    assert sites == [":80"]
    # Only the ingress (direct-origin checks) and the CDN simulator publish
    # ports (test_origin_guard.py).
    for name, service in overlay.items():
        if name not in {"ingress", "edge"}:
            assert "ports" not in service, name


@pytest.mark.parametrize("size", ["slim", "full"])
def test_stack_checker_expects_a_heartbeat_from_every_worker_the_size_starts(size: str) -> None:
    spec = importlib.util.spec_from_file_location(
        "motionly_cpu_stage_stack", ROOT / "scripts" / "verify" / "cpu_stage_stack.py"
    )
    assert spec and spec.loader
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    workers = {name for name, service in _started(_merged(), size).items() if _queues(service)}

    assert checker.WORKER_SERVICES[size] == workers
    assert set(json.loads(STACK.read_text())["one_shots"]) == {
        name
        for name, service in _started(_merged(), size).items()
        if service.get("restart") == "no"
    }


def test_every_started_service_has_a_memory_cap() -> None:
    merged = _merged()
    for size in ("slim", "full"):
        for name, service in _started(merged, size).items():
            assert re.fullmatch(r"\d+m", str(service.get("mem_limit", ""))), (size, name)


def test_stack_config_tags_every_compose_image_variable_with_local_images() -> None:
    stack = json.loads(STACK.read_text(encoding="utf-8"))
    base = BASE.read_text(encoding="utf-8")
    variables = set(re.findall(r"\$\{(MOTIONLY_[A-Z_]+_IMAGE):\?", base))

    assert set(stack["image_variables"]) == variables
    built = set(stack["images"])
    for variable, tag in stack["image_variables"].items():
        if variable in {"MOTIONLY_GPU_WORKER_IMAGE", "MOTIONLY_GPU_FFMPEG_WORKER_IMAGE"}:
            assert tag.endswith(":gpu-stage-only") and tag not in built, variable
        else:
            assert tag in built, variable
    for tag, image in stack["images"].items():
        assert tag.endswith(":cpu-stage"), tag
        assert (ROOT / image["dockerfile"]).is_file(), tag


def test_cpu_stage_writer_creates_private_random_inputs_without_live_provider_keys(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "compose.env"
    secrets_dir = tmp_path / "secrets"
    runs = []
    for suffix in ("a", "b"):
        result = subprocess.run(
            [
                sys.executable,
                str(WRITER),
                "--cpu-stage",
                str(STACK),
                "--env-file",
                str(env_file.with_suffix(f".{suffix}")),
                "--secrets-dir",
                str(secrets_dir.with_name(f"secrets-{suffix}")),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        runs.append(
            dict(
                line.split("=", 1)
                for line in env_file.with_suffix(f".{suffix}").read_text().splitlines()
            )
        )

    first, second = runs
    stack = json.loads(STACK.read_text(encoding="utf-8"))
    for variable, tag in stack["image_variables"].items():
        assert first[variable] == tag
    assert first["TEMPORAL_ADMIN_TOOLS_IMAGE"].startswith("temporalio/admin-tools@sha256:")
    assert first["PUBLIC_ORIGIN"] == stack["public_origin"] == "https://127.0.0.1:18443"
    assert first["APP_DOMAIN"] == "127.0.0.1"
    assert "API_DOMAIN" not in first and "SUPABASE_DOMAIN" not in first
    assert first["POSTGRES_PASSWORD"] != second["POSTGRES_PASSWORD"]
    assert re.fullmatch(r"[0-9a-f]{32,}", first["POSTGRES_PASSWORD"])
    secrets_a = secrets_dir.with_name("secrets-a")
    assert stat.S_IMODE(secrets_a.stat().st_mode) == 0o700
    for name in ("groq_api_key", "openrouter_api_key", "runway_api_key", "azure_openai_api_key"):
        assert (secrets_a / name).read_text(encoding="utf-8") == "", name
    assert (secrets_a / "postgres_password").read_text().strip() == first["POSTGRES_PASSWORD"]
    # Read-only for every container user (Compose mounts file secrets with the
    # host mode); the 0700 directory keeps other host users out.
    for path in secrets_a.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o444, path.name


def _load_sampler():
    spec = importlib.util.spec_from_file_location("motionly_resource_sampler", SAMPLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sampler_parses_docker_stats_units() -> None:
    sampler = _load_sampler()

    assert sampler.parse_bytes("512MiB") == 512 * 1024**2
    assert sampler.parse_bytes("1.5GiB") == int(1.5 * 1024**3)
    assert sampler.parse_bytes("900kB") == 900_000
    assert sampler.parse_bytes("0B") == 0
    assert sampler.parse_percent("123.45%") == pytest.approx(123.45)


def test_sampler_verdict_fails_on_restarts_oom_kills_and_failed_one_shots() -> None:
    sampler = _load_sampler()
    healthy = {
        "api": {"restart_count": 0, "oom_killed": False, "running": True, "exit_code": 0},
        "db-migrate": {"restart_count": 0, "oom_killed": False, "running": False, "exit_code": 0},
    }
    assert sampler.verdict(healthy, one_shots={"db-migrate"}) == []

    broken = {
        "api": {"restart_count": 2, "oom_killed": False, "running": True, "exit_code": 0},
        "web": {"restart_count": 0, "oom_killed": True, "running": True, "exit_code": 0},
        "worker": {"restart_count": 0, "oom_killed": False, "running": False, "exit_code": 1},
        "db-migrate": {"restart_count": 0, "oom_killed": False, "running": False, "exit_code": 3},
    }
    problems = sampler.verdict(broken, one_shots={"db-migrate"})
    assert problems == [
        "api restarted 2 time(s)",
        "db-migrate exited with code 3",
        "web was OOM-killed",
        "worker is not running (exit code 1)",
    ]


def test_resource_budget_awaits_decision_d2_and_lists_measured_services() -> None:
    budget = json.loads(BUDGET.read_text(encoding="utf-8"))

    assert budget["decision"] == "D2"
    assert budget["basis"] == "measured peak x 1.25"
    assert budget["status"] in {"pending-measurement", "measured", "approved"}
    entries = budget["services"].values()
    if budget["status"] != "approved":
        # Budgets are enforced only after the user approves them (Decision D2).
        assert all(entry["budget_memory_bytes"] is None for entry in entries)
        assert all(entry["budget_cpu_percent"] is None for entry in entries)
        if "browser" in budget:
            assert budget["browser"]["budget_navigation_load_p95_ms"] is None
            assert budget["browser"]["budget_wall_seconds"] is None
    if budget["status"] == "approved":
        # Decision D2: factor x the higher of the startup and browser-load peaks.
        factor = budget["approval"]["factor"]
        assert budget["approval"]["decision"] == "D2" and factor == 1.25
        for entry in entries:
            memory = max(entry["baseline_peak_memory_bytes"], entry["browser_peak_memory_bytes"])
            assert entry["budget_memory_bytes"] >= memory * factor
            assert entry["budget_memory_bytes"] < memory * factor + 1
        browser = budget["browser"]
        assert browser["excluded_navigation"] == [
            {"project": "firefox", "reason": browser["excluded_navigation"][0]["reason"]}
        ]
        assert "firefox" not in browser["budget_navigation_load_p95_ms"]
        assert set(browser["budget_navigation_load_p95_ms"]) == {
            "chromium",
            "webkit",
            "pixel-7",
            "iphone-14",
        }
        assert browser["budget_wall_seconds"] >= browser["wall_seconds"] * factor
    if budget["status"] != "pending-measurement":
        assert budget["measurement"]["source"] == "test-results/cpu-stage/resources.json"
        assert all(isinstance(entry["baseline_peak_memory_bytes"], int) for entry in entries)
    stack = json.loads(STACK.read_text(encoding="utf-8"))
    started = _started(_merged(), budget["size"])
    assert set(budget["services"]) == set(started) - set(stack["one_shots"])
