"""The release agent's GPU-free topology (decision 2026-10-25): a CPU-only
production host (ComputeProfile=cpu) opts in with MOTIONLY_CPU_TOPOLOGY=gpu-free
so apply_release.sh deploys docker-compose.gpu.yml + docker-compose.remote-ai.yml
with the cpu-vision-worker image and, when MOTIONLY_LOCAL_STEMS=enabled, the
Demucs worker. A host without the new keys, and a release recorded before this
change, behave exactly as before.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_aws_release_contract import AGENT, ROOT, _function, _host_env, _run_agent
from tests.test_production_compose_contract import (
    COMPOSE_PATH,
    SECRET_FILE_NAMES,
    _compose_environment,
    _production_values,
    _write_env_file,
)

REMOTE_AI_PATH = ROOT / "infra" / "docker-compose.remote-ai.yml"


# --- A2: host-configuration refusals (exit 78, before anything is written) ------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"MOTIONLY_CPU_TOPOLOGY": "slim"},
            "MOTIONLY_CPU_TOPOLOGY must be base or gpu-free",
        ),
        (
            {"MOTIONLY_LOCAL_STEMS": "maybe", "MOTIONLY_CPU_TOPOLOGY": "gpu-free",
             "MOTIONLY_COMPUTE_PROFILE": "cpu"},
            "MOTIONLY_LOCAL_STEMS must be enabled or disabled",
        ),
        (
            {"MOTIONLY_CPU_TOPOLOGY": "gpu-free", "MOTIONLY_COMPUTE_PROFILE": "gpu"},
            "MOTIONLY_CPU_TOPOLOGY=gpu-free requires MOTIONLY_COMPUTE_PROFILE=cpu",
        ),
        (
            {"MOTIONLY_LOCAL_STEMS": "enabled", "MOTIONLY_CPU_TOPOLOGY": "base",
             "MOTIONLY_COMPUTE_PROFILE": "cpu"},
            "MOTIONLY_LOCAL_STEMS=enabled requires MOTIONLY_CPU_TOPOLOGY=gpu-free",
        ),
    ],
)
def test_the_agent_refuses_an_unsupported_topology_before_doing_anything(
    tmp_path: Path, overrides: dict, message: str
) -> None:
    result = _run_agent(_host_env(tmp_path, **overrides))
    assert result.returncode == 78, result.stderr
    assert message in result.stderr
    assert not (tmp_path / "releases").exists(), "nothing is written before validation"


def test_a_host_without_the_new_keys_is_accepted_exactly_as_before(tmp_path: Path) -> None:
    """A host.env without MOTIONLY_CPU_TOPOLOGY / MOTIONLY_LOCAL_STEMS keeps the
    default base topology and gets past configuration to the first missing tool."""
    result = _run_agent(_host_env(tmp_path), PATH="/usr/bin:/bin")
    assert result.returncode == 127, result.stderr
    assert "is required by the release agent" in result.stderr


def test_host_env_example_documents_both_new_settings() -> None:
    text = (ROOT / "infra" / "compose-host" / "host.env.example").read_text("utf-8")
    assert "MOTIONLY_CPU_TOPOLOGY=base" in text
    assert "MOTIONLY_LOCAL_STEMS=disabled" in text
    assert "gpu-free" in text and "local-stems" in text


# --- A3: validate_release_topology ----------------------------------------------


def _run_topology_check(
    tmp_path: Path, *, topology: str, with_overlay: bool, image: bool
) -> subprocess.CompletedProcess[str]:
    release_dir = tmp_path / "release"
    (release_dir / "bundle" / "infra").mkdir(parents=True)
    if with_overlay:
        overlay = release_dir / "bundle" / "infra" / "docker-compose.remote-ai.yml"
        overlay.write_text("services: {}\n")
    images = {"MOTIONLY_API_IMAGE": "ghcr.io/motionly/api@sha256:" + "a" * 64}
    if image:
        images["MOTIONLY_CPU_VISION_WORKER_IMAGE"] = (
            "ghcr.io/motionly/cpu-vision-worker@sha256:" + "a" * 64
        )
    import json

    (release_dir / "release-manifest.json").write_text(json.dumps({"images": images}))
    agent = AGENT.read_text("utf-8")
    script = "\n".join(
        [
            "set -euo pipefail",
            f"MOTIONLY_CPU_TOPOLOGY={topology}",
            _function(agent, "validate_release_topology"),
            f'validate_release_topology "{release_dir}"',
            'echo accepted',
        ]
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_validate_release_topology_accepts_a_gpu_free_release(tmp_path: Path) -> None:
    result = _run_topology_check(tmp_path, topology="gpu-free", with_overlay=True, image=True)
    assert result.returncode == 0, result.stderr
    assert "accepted" in result.stdout


def test_validate_release_topology_rejects_a_bundle_without_the_overlay(tmp_path: Path) -> None:
    result = _run_topology_check(tmp_path, topology="gpu-free", with_overlay=False, image=True)
    assert result.returncode == 65
    assert "docker-compose.remote-ai.yml" in result.stderr
    assert "accepted" not in result.stdout


def test_validate_release_topology_rejects_a_manifest_without_the_image(tmp_path: Path) -> None:
    result = _run_topology_check(tmp_path, topology="gpu-free", with_overlay=True, image=False)
    assert result.returncode == 65
    assert "MOTIONLY_CPU_VISION_WORKER_IMAGE" in result.stderr
    assert "accepted" not in result.stdout


def test_validate_release_topology_requires_nothing_for_base(tmp_path: Path) -> None:
    result = _run_topology_check(tmp_path, topology="base", with_overlay=False, image=False)
    assert result.returncode == 0, result.stderr
    assert "accepted" in result.stdout


# --- A4: write_release_topology -------------------------------------------------


def _run_write_topology(
    tmp_path: Path, *, profile: str, topology: str, stems: str
) -> Path:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / "compose.env").write_text("MOTIONLY_API_IMAGE=x\n")
    agent = AGENT.read_text("utf-8")
    script = "\n".join(
        [
            "set -euo pipefail",
            "umask 077",
            f"MOTIONLY_COMPUTE_PROFILE={profile}",
            f"MOTIONLY_CPU_TOPOLOGY={topology}",
            f"MOTIONLY_LOCAL_STEMS={stems}",
            _function(agent, "write_release_topology"),
            f'write_release_topology "{release_dir}"',
        ]
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return release_dir


def test_write_release_topology_records_the_three_keys_mode_0600(tmp_path: Path) -> None:
    release_dir = _run_write_topology(
        tmp_path, profile="cpu", topology="gpu-free", stems="disabled"
    )
    topology_env = release_dir / "topology.env"
    assert topology_env.read_text() == (
        "MOTIONLY_COMPUTE_PROFILE=cpu\n"
        "MOTIONLY_CPU_TOPOLOGY=gpu-free\n"
        "MOTIONLY_LOCAL_STEMS=disabled\n"
    )
    assert (topology_env.stat().st_mode & 0o777) == 0o600
    # disabled never touches compose.env.
    assert "CPU_VISION_WORKER_QUEUES" not in (release_dir / "compose.env").read_text()


def test_write_release_topology_moves_demucs_off_cpu_vision_when_stems_enabled(
    tmp_path: Path,
) -> None:
    release_dir = _run_write_topology(
        tmp_path, profile="cpu", topology="gpu-free", stems="enabled"
    )
    compose_env = release_dir / "compose.env"
    assert (
        "CPU_VISION_WORKER_QUEUES=visual_analysis,transcription,matte_extraction\n"
        in compose_env.read_text()
    )
    assert "audio_demucs" not in compose_env.read_text()
    assert (compose_env.stat().st_mode & 0o777) == 0o600


def test_write_release_topology_for_base_keeps_compose_env_unchanged(tmp_path: Path) -> None:
    release_dir = _run_write_topology(
        tmp_path, profile="gpu", topology="base", stems="disabled"
    )
    assert (release_dir / "compose.env").read_text() == "MOTIONLY_API_IMAGE=x\n"
    assert (release_dir / "topology.env").read_text() == (
        "MOTIONLY_COMPUTE_PROFILE=gpu\n"
        "MOTIONLY_CPU_TOPOLOGY=base\n"
        "MOTIONLY_LOCAL_STEMS=disabled\n"
    )


# --- A5: compose_for honours the recorded topology ------------------------------


def _compose_for_argv(
    tmp_path: Path, *, host_profile: str, topology_lines: str | None
) -> subprocess.CompletedProcess[str]:
    """Run compose_for with docker replaced by a recorder that prints its argv."""
    release_dir = tmp_path / "release"
    (release_dir / "bundle" / "infra").mkdir(parents=True)
    (release_dir / "compose.env").write_text("X=1\n")
    if topology_lines is not None:
        (release_dir / "topology.env").write_text(topology_lines)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    docker.chmod(0o755)
    agent = AGENT.read_text("utf-8")
    script = "\n".join(
        [
            "set -euo pipefail",
            f"MOTIONLY_COMPUTE_PROFILE={host_profile}",
            _function(agent, "compose_for"),
            f'compose_for "{release_dir}" up -d',
        ]
    )
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_compose_for_legacy_release_without_topology_env_behaves_as_before(
    tmp_path: Path,
) -> None:
    # cpu host, no topology.env: gpu.yml only, no overlay, no profiles.
    result = _compose_for_argv(tmp_path, host_profile="cpu", topology_lines=None)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "docker-compose.gpu.yml" in out
    assert "docker-compose.remote-ai.yml" not in out
    assert "--profile" not in out


def test_compose_for_legacy_gpu_host_still_passes_profile_gpu(tmp_path: Path) -> None:
    result = _compose_for_argv(tmp_path, host_profile="gpu", topology_lines=None)
    assert result.returncode == 0, result.stderr
    assert "--profile\ngpu" in result.stdout
    assert "docker-compose.remote-ai.yml" not in result.stdout


def test_compose_for_uses_the_recorded_gpu_free_topology_with_local_stems(
    tmp_path: Path,
) -> None:
    result = _compose_for_argv(
        tmp_path,
        host_profile="cpu",
        topology_lines=(
            "MOTIONLY_COMPUTE_PROFILE=cpu\n"
            "MOTIONLY_CPU_TOPOLOGY=gpu-free\n"
            "MOTIONLY_LOCAL_STEMS=enabled\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "docker-compose.gpu.yml" in out
    assert "docker-compose.remote-ai.yml" in out
    # gpu.yml comes before the overlay.
    assert out.index("docker-compose.gpu.yml") < out.index("docker-compose.remote-ai.yml")
    assert "--profile\nlocal-stems" in out
    assert "--profile\ngpu" not in out


def test_compose_for_gpu_free_without_local_stems_adds_no_profile(tmp_path: Path) -> None:
    result = _compose_for_argv(
        tmp_path,
        host_profile="cpu",
        topology_lines=(
            "MOTIONLY_COMPUTE_PROFILE=cpu\n"
            "MOTIONLY_CPU_TOPOLOGY=gpu-free\n"
            "MOTIONLY_LOCAL_STEMS=disabled\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "docker-compose.remote-ai.yml" in result.stdout
    assert "--profile" not in result.stdout


def test_compose_for_fails_closed_when_recorded_profile_differs_from_host(
    tmp_path: Path,
) -> None:
    result = _compose_for_argv(
        tmp_path,
        host_profile="gpu",
        topology_lines=(
            "MOTIONLY_COMPUTE_PROFILE=cpu\n"
            "MOTIONLY_CPU_TOPOLOGY=gpu-free\n"
            "MOTIONLY_LOCAL_STEMS=enabled\n"
        ),
    )
    assert result.returncode != 0
    assert "does not match host" in result.stderr


def test_compose_for_rejects_a_malformed_topology_file(tmp_path: Path) -> None:
    result = _compose_for_argv(
        tmp_path,
        host_profile="cpu",
        topology_lines="MOTIONLY_CPU_TOPOLOGY=gpu-free\nEVIL=$(touch /tmp/pwn)\n",
    )
    assert result.returncode != 0
    assert "unrecognised entry" in result.stderr


def test_compose_for_rejects_a_disallowed_value_in_topology_file(tmp_path: Path) -> None:
    result = _compose_for_argv(
        tmp_path,
        host_profile="cpu",
        topology_lines="MOTIONLY_CPU_TOPOLOGY=slim\n",
    )
    assert result.returncode != 0
    assert "unrecognised entry" in result.stderr


# --- A3/A4 step order in apply --------------------------------------------------


def test_apply_validates_topology_before_write_release_inputs_and_records_after() -> None:
    agent = AGENT.read_text("utf-8")
    apply = agent[agent.index('validate_release_topology "$release_dir"\nvalidate_temporal') :]
    validate = apply.index('validate_release_topology "$release_dir"')
    write_inputs = apply.index('write_release_inputs "$release_dir"')
    write_topology = apply.index('write_release_topology "$release_dir"')
    activate = apply.index('if ! activate_release "$release_dir"; then')
    assert validate < write_inputs < write_topology < activate
    # Preflight also validates topology after fetch, before the canary gate.
    preflight = agent[agent.index('if [[ "$mode" == "preflight" ]]; then') :]
    assert preflight.index('validate_release_topology "$release_dir"') < preflight.index(
        "validate_temporal_canary_gate"
    )


# --- A6: preflight success message names the topology ---------------------------


def test_preflight_success_message_names_the_topology() -> None:
    agent = AGENT.read_text("utf-8")
    assert "topology $MOTIONLY_CPU_TOPOLOGY" in agent
    assert "local stems $MOTIONLY_LOCAL_STEMS" in agent


# --- Real `docker compose config` of the GPU-free production topology ------------


def test_gpu_free_production_compose_renders_the_expected_service_set(tmp_path: Path) -> None:
    """The one real-Compose check: gpu.yml + remote-ai.yml with --profile
    local-stems and production values renders the Demucs worker and no GPU
    service, keeps audio_demucs off cpu-vision-worker, and mounts the AWS static
    keys only on the API and the Bedrock-calling workers."""
    import json

    if shutil.which("docker") is None:
        pytest.fail("docker compose is required to validate the GPU-free topology")

    secret_directory = tmp_path / "secrets"
    secret_directory.mkdir(mode=0o700)
    for secret_name in SECRET_FILE_NAMES:
        (secret_directory / secret_name).write_text(f"test-{secret_name}\n")

    values = _production_values(secret_directory)
    values["MOTIONLY_CPU_VISION_WORKER_IMAGE"] = (
        "ghcr.io/motionly/cpu-vision-worker@sha256:" + "a" * 64
    )
    # The release agent appends this for gpu-free + local stems.
    values["CPU_VISION_WORKER_QUEUES"] = "visual_analysis,transcription,matte_extraction"
    environment_file = tmp_path / ".env"
    _write_env_file(environment_file, values)

    rendered = subprocess.run(
        [
            "docker", "compose", "--project-name", "motionly",
            "--profile", "local-stems",
            "--env-file", str(environment_file),
            "-f", str(COMPOSE_PATH),
            "-f", str(REMOTE_AI_PATH),
            "config", "--format", "json",
        ],
        cwd=ROOT,
        env=_compose_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]

    assert "cpu-vision-worker" in services
    assert "demucs-worker" in services
    gpu_services = {"gpu-inference-worker", "matte-worker", "gpu-ffmpeg-worker"}
    assert not gpu_services & set(services)
    assert not [name for name, service in services.items() if service.get("gpus")]

    queues = services["cpu-vision-worker"]["environment"]["WORKER_QUEUES"]
    assert queues == "visual_analysis,transcription,matte_extraction"
    assert "audio_demucs" not in queues

    aws_keys = {"aws_inference_access_key_id", "aws_inference_secret_access_key"}
    with_aws = {
        name
        for name, service in services.items()
        if aws_keys & {item.get("source") for item in (service.get("secrets") or [])}
    }
    assert with_aws == {"api", "provider-worker", "planning-worker", "cpu-vision-worker"}
