"""Contracts for the GPU-free remote AI Compose overlay (infra/docker-compose.remote-ai.yml).

The overlay runs on top of the unchanged production topology: no GPU service and
no Demucs worker start by default, one cpu-vision worker serves their queues, and
the switches it sets pass production validation without a GPU worker.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.core.config import Settings
from app.main import _validate_production_settings
from app.services.ai_features import ai_feature_report, gpu_worker_switches
from tests.test_production_compose_contract import (
    APPLICATION_IMAGE_VARIABLES,
    SECRET_FILE_NAMES,
    _compose_environment,
    _production_values,
    _write_env_file,
)
from tests.test_service_hardening import _production_settings

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "infra" / "docker-compose.gpu.yml"
OVERLAY = ROOT / "infra" / "docker-compose.remote-ai.yml"
GPU_SERVICES = {"gpu-inference-worker", "matte-worker", "gpu-ffmpeg-worker"}
ORCHESTRATION = {"temporal-dispatcher", "temporal-worker", "workflow-reconciler"}
INTERPOLATION = re.compile(r"^\$\{([A-Z0-9_]+)(:-|:\?)([^}]*)\}$")


class _Loader(yaml.SafeLoader):
    pass


def _construct(loader, _suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_Loader.add_multi_constructor("!", _construct)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


def _remote_environment() -> dict[str, str]:
    return _load(OVERLAY)["x-remote-ai-environment"]


def _defaults() -> dict[str, str]:
    """Container setting -> value the overlay passes when the operator sets nothing."""
    values = {}
    for name, raw in _remote_environment().items():
        match = INTERPOLATION.match(str(raw))
        if match is None:
            values[name] = str(raw)
        elif match.group(2) == ":-":
            values[name] = match.group(3)
    return values


def _media_services() -> set[str]:
    """Base services that run the app with the shared media environment."""
    return {
        name
        for name, service in _load(BASE)["services"].items()
        if "WORKER_API_BASE_URL" in (service.get("environment") or {})
        and name not in ORCHESTRATION
    }


def _alias_to_field() -> dict[str, str]:
    return {field.alias: name for name, field in Settings.model_fields.items() if field.alias}


# -- static contract -------------------------------------------------------------------


def test_every_app_service_gets_the_same_switches_and_provider_keys() -> None:
    services = _load(OVERLAY)["services"]
    remote = _remote_environment()

    assert _media_services() | {"cpu-vision-worker"} == set(services)
    for name, service in services.items():
        environment = service["environment"]
        for key, value in remote.items():
            assert environment[key] == value, (name, key)
        # Remote object detection, diarization and stems are registry routes now
        # (plan Task 17), so the overlay mounts no per-service vendor secret.
        # The one exception is cpu-vision-worker: it calls Bedrock routes, so it
        # mounts exactly the two AWS static-key secrets on top of the media
        # secrets it inherits from cpu-ffmpeg-worker (docs/aws, S3 isolation).
        if name == "cpu-vision-worker":
            assert service["secrets"] == [
                "aws_inference_access_key_id",
                "aws_inference_secret_access_key",
            ], name
        else:
            assert "secrets" not in service, name
    # Orchestration roles read none of the provider switches.
    assert not ORCHESTRATION & set(services)


def test_every_overlay_setting_is_a_real_setting() -> None:
    aliases = _alias_to_field()
    for name in _remote_environment():
        assert name in aliases, name


def test_the_overlay_declares_no_vendor_secret_and_no_vendor_name() -> None:
    from tests.support.vendor_names import names_vendor, vendors_in

    overlay_text = OVERLAY.read_text(encoding="utf-8")
    overlay = _load(OVERLAY)
    # No overlay-level secrets block (the former vendor key files are gone); the
    # base topology supplies the one generic registry bundle.
    assert "secrets" not in overlay
    # Absence check through the neutrality matcher so this test spells no vendor
    # name (plan Task 19 item 2, tests/support/vendor_names.py).
    assert not names_vendor(overlay_text), vendors_in(overlay_text)


def test_no_vendor_price_env_vars_remain() -> None:
    prices = {
        name: raw for name, raw in _remote_environment().items() if "_COST_" in name
    }

    # Remote route prices live in the registry, not as overlay env variables.
    assert prices == {}


def test_default_switches_need_no_gpu_and_pass_production_validation() -> None:
    aliases = _alias_to_field()
    values = {
        aliases[name]: value
        for name, value in _defaults().items()
        if name in aliases and name != "MODEL_GATEWAY_FEATURE_BUDGETS_USD"
    }
    values.update(
        {
            "gpu_workers_enabled": values["gpu_workers_enabled"] == "true",
        }
    )
    assert values["gpu_workers_enabled"] is False

    for role in ("api", "worker"):
        settings = _production_settings(process_role=role, **values)
        assert gpu_worker_switches(settings) == []
        _validate_production_settings(settings)
        report = ai_feature_report(settings)
        assert {entry["execution"] for entry in report.values()} <= {
            "cpu_worker", "remote", "disabled"
        }
        assert all(entry["configured"] for entry in report.values())


def test_cpu_vision_worker_serves_the_gpu_and_demucs_queues() -> None:
    base = _load(BASE)["services"]
    worker = _load(OVERLAY)["services"]["cpu-vision-worker"]
    replaced = {
        queue
        for name in ("gpu-inference-worker", "matte-worker", "demucs-worker")
        for queue in base[name]["environment"]["WORKER_QUEUES"].split(",")
    }

    assert worker["extends"] == {"file": "docker-compose.gpu.yml", "service": "cpu-ffmpeg-worker"}
    assert worker["image"].startswith("${MOTIONLY_CPU_VISION_WORKER_IMAGE:?")
    queues = INTERPOLATION.match(worker["environment"]["WORKER_QUEUES"])
    assert queues and queues.group(1) == "CPU_VISION_WORKER_QUEUES"
    assert set(queues.group(3).split(",")) == replaced
    # The image declares CPU vision; Compose never claims it for another image.
    assert "CPU_VISION_ENABLED" not in worker["environment"]
    assert "gpus" not in worker and "profiles" not in worker
    dockerfile = (ROOT / "apps" / "worker" / "Dockerfile.python").read_text(encoding="utf-8")
    assert "ENV CPU_VISION_ENABLED=${CPU_VISION_ENABLED}" in dockerfile
    workflow = (ROOT / ".github" / "workflows" / "supply-chain.yml").read_text(encoding="utf-8")
    assert "component: cpu-vision-worker" in workflow


def test_switch_back_services_start_only_with_their_profile() -> None:
    services = _load(OVERLAY)["services"]
    source = OVERLAY.read_text(encoding="utf-8")

    for name, profile in (
        ("gpu-inference-worker", "gpu-inference"),
        ("matte-worker", "gpu-matte"),
        ("gpu-ffmpeg-worker", "gpu-render"),
    ):
        assert services[name]["profiles"] == ["gpu", profile], name
    assert services["demucs-worker"]["profiles"] == ["local-stems"]
    assert source.count("profiles: !override") == len(GPU_SERVICES)
    gpu_environment = services["gpu-inference-worker"]["environment"]
    gpu_queues = INTERPOLATION.match(gpu_environment["WORKER_QUEUES"])
    assert gpu_queues and gpu_queues.group(1) == "GPU_INFERENCE_WORKER_QUEUES"
    assert gpu_queues.group(3) == "visual_analysis"


# -- rendered with docker compose ----------------------------------------------------


def _write_inputs(tmp_path: Path, **overrides: str | None) -> Path:
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700, exist_ok=True)
    for name in SECRET_FILE_NAMES:
        (secrets / name).write_text(f"test-{name}\n")
    values = _production_values(secrets)
    values["MOTIONLY_CPU_VISION_WORKER_IMAGE"] = (
        f"ghcr.io/motionly/cpu-vision-worker@sha256:{'b' * 64}"
    )
    for name, value in overrides.items():
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value
    environment_file = tmp_path / ".env"
    _write_env_file(environment_file, values)
    return environment_file


def _compose(environment_file: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("docker") is None:
        pytest.fail("docker compose is required to validate the remote AI Compose overlay")
    removed = set(_remote_environment()) | {"MOTIONLY_CPU_VISION_WORKER_IMAGE"}
    environment = {
        key: value for key, value in _compose_environment().items() if key not in removed
    }
    return subprocess.run(
        [
            "docker", "compose", "--env-file", str(environment_file),
            "-f", str(BASE), "-f", str(OVERLAY), *arguments,
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _rendered(environment_file: Path, *profiles: str) -> dict:
    arguments = [part for profile in profiles for part in ("--profile", profile)]
    result = _compose(environment_file, *arguments, "config", "--format", "json")
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_the_default_stack_starts_no_gpu_service(tmp_path: Path) -> None:
    services = _rendered(_write_inputs(tmp_path))["services"]

    assert "cpu-vision-worker" in services
    assert not (GPU_SERVICES | {"demucs-worker"}) & set(services)
    assert not [name for name, service in services.items() if service.get("gpus")]
    worker = services["cpu-vision-worker"]
    assert worker["environment"]["WORKER_QUEUES"] == (
        "visual_analysis,transcription,matte_extraction,audio_demucs"
    )
    # extends keeps the worker's health check, dependencies, volumes and network.
    assert worker["healthcheck"]["test"] == services["cpu-ffmpeg-worker"]["healthcheck"]["test"]
    assert set(worker["depends_on"]) == {"api", "db-migrate", "volume-permissions"}
    assert list(worker["networks"]) == ["app"]
    # The API keeps its own keys and the workers keep the restricted database key.
    api_secrets = {item["source"] for item in services["api"]["secrets"]}
    assert {"admin_api_token", "supabase_service_role_key"} <= api_secrets
    assert worker["environment"]["SUPABASE_SERVICE_ROLE_KEY_FILE"] == (
        "/run/secrets/supabase_worker_key"
    )
    assert services["api"]["environment"]["FFMPEG_GPU_ENABLED"] == "false"


def test_every_secret_a_rendered_service_reads_is_mounted(tmp_path: Path) -> None:
    services = _rendered(
        _write_inputs(tmp_path), "gpu", "local-stems"
    )["services"]

    problems = []
    for name, service in services.items():
        mounted = {item["source"] for item in service.get("secrets") or []}
        text = json.dumps([service.get("environment") or {}, service.get("entrypoint") or []])
        for secret in sorted(set(re.findall(r"/run/secrets/([a-z0-9_]+)", text))):
            if secret not in mounted:
                problems.append(f"{name} reads /run/secrets/{secret} but does not mount it")
    assert problems == []


@pytest.mark.parametrize(
    ("profile", "started"),
    [
        ("gpu", GPU_SERVICES),
        ("gpu-inference", {"gpu-inference-worker"}),
        ("gpu-matte", {"matte-worker"}),
        ("gpu-render", {"gpu-ffmpeg-worker"}),
        ("local-stems", {"demucs-worker"}),
    ],
)
def test_each_switch_back_profile_starts_only_its_service(
    tmp_path: Path, profile: str, started: set[str]
) -> None:
    services = _rendered(_write_inputs(tmp_path), profile)["services"]

    assert (GPU_SERVICES | {"demucs-worker"}) & set(services) == started
    for name in started:
        assert services[name]["environment"]["VISUAL_PROVIDER"] == "remote"
    if "gpu-ffmpeg-worker" in started:
        assert services["gpu-ffmpeg-worker"]["environment"]["FFMPEG_GPU_ENABLED"] == "true"


def test_switches_and_queues_follow_the_operator_environment(tmp_path: Path) -> None:
    services = _rendered(
        _write_inputs(
            tmp_path,
            VISUAL_PROVIDER="local_ml",
            GPU_WORKERS_ENABLED="true",
            CPU_VISION_WORKER_QUEUES="transcription,matte_extraction,audio_demucs",
        ),
        "gpu-inference",
    )["services"]

    for name in ("api", "cpu-vision-worker", "gpu-inference-worker"):
        assert services[name]["environment"]["VISUAL_PROVIDER"] == "local_ml"
        assert services[name]["environment"]["GPU_WORKERS_ENABLED"] == "true"
    assert services["cpu-vision-worker"]["environment"]["WORKER_QUEUES"] == (
        "transcription,matte_extraction,audio_demucs"
    )
    assert services["gpu-inference-worker"]["environment"]["WORKER_QUEUES"] == "visual_analysis"


def test_the_overlay_fails_closed_without_the_cpu_vision_image(tmp_path: Path) -> None:
    missing = "MOTIONLY_CPU_VISION_WORKER_IMAGE"
    result = _compose(_write_inputs(tmp_path, **{missing: None}), "config", "-q")

    assert result.returncode != 0
    assert missing in result.stderr + result.stdout


def test_the_base_topology_is_unchanged_by_the_overlay_inputs(tmp_path: Path) -> None:
    """Every image the release manifest supplies is still required by the base file."""
    environment_file = _write_inputs(tmp_path)
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(environment_file), "-f", str(BASE),
         "config", "--format", "json"],
        cwd=ROOT,
        env=_compose_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert "cpu-vision-worker" not in services
    assert "demucs-worker" in services
    assert all("VISUAL_PROVIDER" not in (s.get("environment") or {}) for s in services.values())
    assert "MOTIONLY_CPU_VISION_WORKER_IMAGE" not in APPLICATION_IMAGE_VARIABLES
