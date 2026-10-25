"""The AWS Bedrock static keys reach only the inference containers.

The dedicated inference IAM user's static access keys (docs/aws) are mounted as
the Compose secrets ``aws_inference_access_key_id`` and
``aws_inference_secret_access_key``. They must appear in exactly the API and the
worker services that call a Bedrock route (api, provider-worker,
planning-worker, gpu-inference-worker in the base topology and cpu-vision-worker
in the GPU-free remote-ai overlay) and never in a media-only worker or an infra
service. No service may expose them as ``AWS_*`` environment variables or an
``env_file`` either.

Each topology is checked twice: once from a static merge of the Compose files
(anchors expanded by the YAML loader, ``extends`` resolved for cpu-vision-worker)
and once from ``docker compose config --format json`` with every profile enabled,
so a drift in either the written file or Compose's own merge fails the contract.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.test_production_compose_contract import (
    SECRET_FILE_NAMES,
    _compose_environment,
    _production_values,
    _write_env_file,
)

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
BASE = INFRA / "docker-compose.gpu.yml"
REMOTE_AI = INFRA / "docker-compose.remote-ai.yml"
CPU_STAGE = INFRA / "docker-compose.cpu-stage.yml"
REGISTRY = INFRA / "ai-registry" / "registry.json"

AWS_SECRETS = ("aws_inference_access_key_id", "aws_inference_secret_access_key")

# Containers that call a Bedrock inference route and therefore mount the keys.
ALLOWED_SERVICES = {
    "api",
    "provider-worker",
    "planning-worker",
    "gpu-inference-worker",
    "cpu-vision-worker",
}
# Media, rendering and infra services that must never receive AWS credentials.
FORBIDDEN_SERVICES = {
    "cpu-ffmpeg-worker",
    "gpu-ffmpeg-worker",
    "matte-worker",
    "remotion-worker",
    "demucs-worker",
    "web",
    "ingress",
    "postgres",
    "redis",
    "db-migrate",
    "workflow-reconciler",
    "volume-permissions",
    "temporal-dispatcher",
    "temporal-worker",
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


def _static_secrets(files: list[Path]) -> dict[str, set[str]]:
    """Per-service secret sources from a static merge of the given Compose files.

    Later files override earlier ones; ``extends`` is resolved against the base
    file so cpu-vision-worker inherits cpu-ffmpeg-worker's media secrets and
    appends its own.
    """
    base_services = _yaml(BASE)["services"]
    merged: dict[str, dict] = {}
    secret_lists: dict[str, list[str]] = {}
    for path in files:
        for name, service in _yaml(path)["services"].items():
            service = service or {}
            merged.setdefault(name, {}).update(service)
            # Compose merges the per-service `secrets` list additively across
            # files rather than replacing it, so accumulate rather than overwrite.
            secret_lists.setdefault(name, []).extend(service.get("secrets") or [])
    effective: dict[str, set[str]] = {}
    for name in merged:
        secrets: list[str] = []
        extends = merged[name].get("extends")
        if isinstance(extends, dict) and extends.get("file") == "docker-compose.gpu.yml":
            secrets.extend(base_services[extends["service"]].get("secrets") or [])
        secrets.extend(secret_lists.get(name) or [])
        effective[name] = set(secrets)
    return effective


def _overlay_env_names() -> set[str]:
    """Names the remote-ai overlay reads, cleared so the env file alone drives them."""
    overlay = _yaml(REMOTE_AI)
    return set(overlay.get("x-remote-ai-environment") or {})


def _rendered_secrets(environment_file: Path, files: list[Path], profiles: list[str]) -> dict:
    if shutil.which("docker") is None:
        pytest.fail("docker compose is required to validate AWS credential isolation")
    file_arguments = [part for path in files for part in ("-f", str(path))]
    profile_arguments = [part for profile in profiles for part in ("--profile", profile)]
    removed = {"MOTIONLY_CPU_VISION_WORKER_IMAGE", *_overlay_env_names()}
    environment = {
        key: value for key, value in _compose_environment().items() if key not in removed
    }
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", str(environment_file),
            *file_arguments, *profile_arguments, "config", "--format", "json",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    services = json.loads(result.stdout)["services"]
    return {
        name: {item["source"] for item in service.get("secrets") or []}
        for name, service in services.items()
    }


def _write_inputs(tmp_path: Path, **overrides: str) -> Path:
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700, exist_ok=True)
    for name in SECRET_FILE_NAMES:
        (secrets / name).write_text(f"test-{name}\n")
    values = _production_values(secrets)
    values["MOTIONLY_CPU_VISION_WORKER_IMAGE"] = (
        f"ghcr.io/motionly/cpu-vision-worker@sha256:{'b' * 64}"
    )
    values.update(overrides)
    environment_file = tmp_path / ".env"
    _write_env_file(environment_file, values)
    return environment_file


def _assert_isolated(effective: dict[str, set[str]]) -> None:
    present = set(effective)
    for name, secrets in effective.items():
        aws = {secret for secret in secrets if secret in AWS_SECRETS}
        if name in ALLOWED_SERVICES:
            assert aws == set(AWS_SECRETS), (name, sorted(secrets))
        else:
            assert not aws, (name, sorted(aws))
    # Every allowed service present in this topology actually carries both keys.
    for name in ALLOWED_SERVICES & present:
        assert {s for s in effective[name] if s in AWS_SECRETS} == set(AWS_SECRETS)
    # Nothing outside the allowed set carries a key.
    carriers = {name for name, secrets in effective.items() if set(AWS_SECRETS) & secrets}
    assert carriers <= ALLOWED_SERVICES, sorted(carriers - ALLOWED_SERVICES)
    # No forbidden service present in the topology carries a key.
    assert not (FORBIDDEN_SERVICES & carriers), sorted(FORBIDDEN_SERVICES & carriers)


# -- static merge --------------------------------------------------------------------


def test_static_base_topology_mounts_the_keys_only_in_inference_services() -> None:
    _assert_isolated(_static_secrets([BASE]))


def test_static_remote_ai_topology_adds_the_keys_to_cpu_vision_worker_only() -> None:
    effective = _static_secrets([BASE, REMOTE_AI])
    assert set(AWS_SECRETS) <= effective["cpu-vision-worker"]
    _assert_isolated(effective)


def test_static_cpu_stage_topology_keeps_the_keys_in_the_inference_services() -> None:
    effective = _static_secrets([BASE, CPU_STAGE])
    # The CPU stage adds no cpu-vision-worker; the base inference services keep
    # the keys and every CPU-stage fixture/media worker stays without them.
    _assert_isolated(effective)


# -- docker compose config -----------------------------------------------------------


def test_rendered_base_topology_isolates_the_keys(tmp_path: Path) -> None:
    effective = _rendered_secrets(_write_inputs(tmp_path), [BASE], ["gpu"])
    assert set(AWS_SECRETS) <= effective["gpu-inference-worker"]
    _assert_isolated(effective)


def test_rendered_remote_ai_topology_isolates_the_keys(tmp_path: Path) -> None:
    effective = _rendered_secrets(
        _write_inputs(tmp_path), [BASE, REMOTE_AI], ["gpu", "local-stems"]
    )
    assert set(AWS_SECRETS) <= effective["cpu-vision-worker"]
    _assert_isolated(effective)


# -- no AWS_* environment variables or env_file anywhere -----------------------------


def test_no_infra_service_exposes_aws_environment_or_an_env_file() -> None:
    for path in sorted(INFRA.glob("*.yml")):
        document = _yaml(path)
        services = document.get("services")
        if not isinstance(services, dict):
            continue
        for name, service in services.items():
            if not isinstance(service, dict):
                continue
            environment = service.get("environment") or {}
            if isinstance(environment, dict):
                offenders = [key for key in environment if str(key).startswith("AWS_")]
            else:  # a list of KEY=VALUE strings
                offenders = [item for item in environment if str(item).startswith("AWS_")]
            assert not offenders, (path.name, name, offenders)
            assert "env_file" not in service, (path.name, name)


def test_registry_references_only_the_two_file_refs_for_aws_access_key_auth() -> None:
    if not REGISTRY.exists():
        pytest.skip("infra/ai-registry/registry.json is written by the registry stage")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    refs: set[str] = set()
    for adapter in registry.get("adapters", []):
        auth = adapter.get("auth") or {}
        if auth.get("type") != "aws_access_key":
            continue
        refs.add(auth.get("access_key_id_ref"))
        refs.add(auth.get("secret_ref"))
    assert refs <= {"file:aws_inference_access_key_id", "file:aws_inference_secret_access_key"}, (
        sorted(refs)
    )
    # No AWS static key id appears literally anywhere in the registry.
    assert not re.search(r"(AKIA|ASIA)[A-Z0-9]{16}", REGISTRY.read_text(encoding="utf-8"))
