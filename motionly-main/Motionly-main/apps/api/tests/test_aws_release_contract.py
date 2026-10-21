"""The AWS release path (Task 9): GitHub publishes a signed release to S3 and
asks the instance, over SSM Run Command, to apply it; the host agent fetches it
from S3, verifies it, reads secrets from SSM Parameter Store into files, and
starts it with Compose, rolling back when the release does not become healthy.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-aws.yml"
AGENT = ROOT / "scripts" / "deploy" / "apply_release.sh"
RUN_ON_INSTANCE = ROOT / "scripts" / "deploy" / "run_on_instance.sh"
BOOTSTRAP = ROOT / "scripts" / "deploy" / "bootstrap_compose_host.sh"
HOST_ENV = ROOT / "infra" / "compose-host" / "host.env.example"
RUNBOOK = ROOT / "docs" / "ops" / "compose-vm-deployment.md"
CONTRACT = ROOT / "infra" / "aws" / "edge-contract.json"


def _workflow() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text("utf-8"))
    workflow["on"] = workflow.pop(True, workflow.get("on"))
    return workflow


def test_the_azure_deploy_path_is_gone() -> None:
    assert not (ROOT / ".github" / "workflows" / "deploy-compose-vm.yml").exists()
    for path in (WORKFLOW, AGENT, BOOTSTRAP, HOST_ENV, RUNBOOK, RUN_ON_INSTANCE):
        text = path.read_text("utf-8").lower()
        for retired in (
            "az ",
            "azure/login",
            "key vault",
            "keyvault",
            "managed identity",
            "storage blob",
        ):
            assert retired not in text, (path.name, retired)


def test_deploy_workflow_uses_github_oidc_and_ssm_run_command_with_no_secrets() -> None:
    text = WORKFLOW.read_text("utf-8")
    workflow = _workflow()
    assert workflow["on"]["workflow_run"]["workflows"] == ["Supply chain"]
    assert workflow["permissions"]["id-token"] == "write"
    assert "secrets." not in text
    jobs = workflow["jobs"]
    assert list(jobs) == ["publish-and-preflight", "apply"]
    assert jobs["apply"]["needs"] == ["publish-and-preflight"]
    for job in jobs.values():
        assert job["environment"] == "production"
        steps = job["steps"]
        login = next(s for s in steps if "configure-aws-credentials" in str(s.get("uses", "")))
        assert login["with"]["role-to-assume"] == "${{ vars.AWS_DEPLOY_ROLE_ARN }}"
        assert login["with"]["aws-region"] == "ap-south-1"
    publish = "\n".join(s.get("run", "") for s in jobs["publish-and-preflight"]["steps"])
    assert "--validate-bundle" in publish
    # Immutable publication: never overwrite, and the manifest goes last.
    assert "aws s3api put-object" in publish and "--if-none-match '*'" in publish
    order = "release-bundle.tar.gz release-manifest.pem release-manifest.sig release-manifest.json"
    assert f"for name in {order}; do" in publish
    assert "scripts/deploy/run_on_instance.sh" in publish and "--preflight" in publish
    apply = "\n".join(s.get("run", "") for s in jobs["apply"]["steps"])
    assert "scripts/deploy/run_on_instance.sh" in apply and "--preflight" not in apply


def test_run_on_instance_waits_for_the_command_and_fails_unless_it_succeeded() -> None:
    script = RUN_ON_INSTANCE.read_text("utf-8")
    assert "aws ssm send-command" in script
    assert "--document-name AWS-RunShellScript" in script
    assert "aws ssm get-command-invocation" in script
    assert '"Success"' in script
    assert "^[0-9a-f]{7,64}$" in script, "only a release id reaches the remote command"
    subprocess.run(["bash", "-n", str(RUN_ON_INSTANCE)], check=True)


def test_agent_reads_releases_from_s3_and_secrets_from_ssm_into_files() -> None:
    agent = AGENT.read_text("utf-8")
    assert "aws s3 cp" in agent
    assert "aws ssm get-parameter --with-decryption" in agent
    assert '"/motionly/${MOTIONLY_ENVIRONMENT}"' in agent
    # The origin secret uses the edge contract's SSM name.
    assert "edge/origin-verify" in agent and "edge/origin-verify-previous" in agent
    assert "cosign verify-blob" in agent and "--certificate-identity-regexp" in agent
    assert "--password-stdin" in agent
    assert "fetch_secret_to_file" in agent and "fetch_base64_secret_to_file" in agent
    assert "rollback_previous_release" in agent
    assert "--profile gpu" in agent and "MOTIONLY_COMPUTE_PROFILE" in agent
    host_env = HOST_ENV.read_text("utf-8")
    for name in (
        "MOTIONLY_ENVIRONMENT",
        "MOTIONLY_AWS_REGION",
        "MOTIONLY_RELEASE_BUCKET",
        "MOTIONLY_COMPUTE_PROFILE",
        "MOTIONLY_REGISTRY",
        "MOTIONLY_APP_DOMAIN",
    ):
        assert f"{name}=" in host_env, name


def _host_env(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "MOTIONLY_RELEASE_ROOT": str(tmp_path / "releases"),
        "MOTIONLY_AGENT_ROOT": str(tmp_path / "agent"),
        "MOTIONLY_ENVIRONMENT": "production",
        "MOTIONLY_AWS_REGION": "ap-south-1",
        "MOTIONLY_RELEASE_BUCKET": "motionly-releases",
        "MOTIONLY_COMPUTE_PROFILE": "gpu",
        "MOTIONLY_REGISTRY": "ghcr.io",
        "MOTIONLY_REGISTRY_USERNAME": "motionly-bot",
        "MOTIONLY_COSIGN_IDENTITY_REGEXP": "^https://github.com/o/r/.github/workflows/supply-chain.yml@refs/tags/v.*$",
        "MOTIONLY_APP_DOMAIN": "d111111abcdef8.cloudfront.net",
        "MOTIONLY_BILLING_MODE": "disabled",
        "MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE": "temporalio/admin-tools@sha256:" + "a" * 64,
        "MOTIONLY_TEMPORAL_CANARY_OVERLAP_PROOF_FILE": str(tmp_path / "proof"),
        "MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL": "2026-10-08T00:00:00Z",
        **overrides,
    }
    path = tmp_path / "host.env"
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    return path


def _run_agent(host_env: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "MOTIONLY_HOST_CONFIG": str(host_env), **environment}
    return subprocess.run(
        [
            "bash",
            str(AGENT),
            "--release-id",
            "0123456789abcdef0123456789abcdef01234567",
            "--preflight",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.parametrize(
    ("overrides", "environment", "message"),
    [
        (
            {"MOTIONLY_ENVIRONMENT": "dev"},
            {},
            "MOTIONLY_ENVIRONMENT must be production, staging or rehearsal",
        ),
        ({"MOTIONLY_COMPUTE_PROFILE": "tpu"}, {}, "MOTIONLY_COMPUTE_PROFILE must be gpu or cpu"),
        (
            {"MOTIONLY_COSIGN_PUBLIC_KEY": "/k.pub"},
            {},
            "MOTIONLY_COSIGN_PUBLIC_KEY is only allowed in rehearsal",
        ),
        (
            {"MOTIONLY_COMPOSE_OVERLAY": "infra/x.yml"},
            {},
            "MOTIONLY_COMPOSE_OVERLAY is only allowed in rehearsal",
        ),
        (
            {},
            {"AWS_ENDPOINT_URL_S3": "http://127.0.0.1:1"},
            "AWS endpoint overrides are only allowed in rehearsal",
        ),
        (
            {"MOTIONLY_REGISTRY": "127.0.0.1:5000"},
            {},
            "a loopback registry is only allowed in rehearsal",
        ),
        ({"MOTIONLY_REGISTRY_USERNAME": ""}, {}, "MOTIONLY_REGISTRY_USERNAME is required"),
        (
            {"MOTIONLY_REHEARSAL_EXTRA_SECRETS": "edge-tls-cert"},
            {},
            "MOTIONLY_REHEARSAL_EXTRA_SECRETS is only allowed in rehearsal",
        ),
        (
            {},
            {"COMPOSE_PROFILES": "slim"},
            "COMPOSE_* environment overrides are only allowed in rehearsal",
        ),
        ({"MOTIONLY_RELEASE_BUCKET": ""}, {}, "MOTIONLY_RELEASE_BUCKET is required"),
    ],
)
def test_the_agent_refuses_unsafe_host_configuration_before_doing_anything(
    tmp_path: Path, overrides: dict, environment: dict, message: str
) -> None:
    result = _run_agent(_host_env(tmp_path, **overrides), **environment)
    assert result.returncode == 78, result.stderr
    assert message in result.stderr
    assert not (tmp_path / "releases").exists(), "nothing is written before validation"


def test_rehearsal_permits_the_test_infrastructure_switches(tmp_path: Path) -> None:
    host_env = _host_env(
        tmp_path,
        MOTIONLY_ENVIRONMENT="rehearsal",
        MOTIONLY_REGISTRY="127.0.0.1:15001",
        MOTIONLY_REGISTRY_USERNAME="",
        MOTIONLY_COSIGN_PUBLIC_KEY=str(tmp_path / "k.pub"),
        MOTIONLY_COMPOSE_OVERLAY="infra/docker-compose.cpu-stage.yml",
    )
    result = _run_agent(
        host_env, AWS_ENDPOINT_URL_S3="http://127.0.0.1:15000", PATH="/usr/bin:/bin"
    )
    # It gets past configuration and stops at the first missing tool.
    assert result.returncode == 127, result.stderr
    assert "is required by the release agent" in result.stderr


def test_the_bootstrap_installs_the_agent_for_aws_without_ssh() -> None:
    bootstrap = BOOTSTRAP.read_text("utf-8")
    assert "for command in docker aws cosign python3 install" in bootstrap
    assert "apply-release" in bootstrap and "temporal-canary-gate" in bootstrap
    instance_bootstrap = (ROOT / "infra" / "aws" / "bootstrap.sh").read_text("utf-8")
    assert "COSIGN_SHA256=" in instance_bootstrap and "COSIGN_VERSION=v2.4.3" in instance_bootstrap


def test_a_release_that_fails_health_checks_restores_the_current_release() -> None:
    """`current` changes only after a release becomes healthy, so a failed
    activation must re-activate `current` (the last good release), not
    `previous` (the one before it). The operator --rollback goes to previous.
    The rehearsal (scripts/release/rehearse_release.py) proves it end to end."""
    agent = AGENT.read_text("utf-8")
    failure = agent[agent.index('if ! activate_release "$release_dir"; then') :]
    branch = failure.split("\nfi\n", 1)[0]
    assert "restore_current_release" in branch
    assert "rollback_previous_release" not in branch
    restore = agent[agent.index("restore_current_release() {") :]
    restore = restore[: restore.index("\n}\n")]
    assert 'readlink -f "$current_link"' in restore
    assert "activate_release" in restore


def _function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    return source[start : source.index("\n}\n", start) + 3]


@pytest.mark.parametrize("failing", ["config", "pull", "up", "exec"])
def test_activation_fails_when_any_compose_step_fails_even_inside_an_if(failing: str) -> None:
    """Found by the release rehearsal: bash ignores `set -e` inside a function
    called from an `if` condition, so a failed `compose up --wait` was ignored
    and a broken release was declared live. Each step must fail the function
    explicitly."""
    agent = AGENT.read_text("utf-8")
    script = "\n".join(
        [
            "set -euo pipefail",
            "MOTIONLY_COMPUTE_PROFILE=cpu",
            f'compose_for() {{ shift; echo "compose $1" >>"$CALLS"; [[ "$1" != {failing} ]]; }}',
            _function(agent, "activate_release"),
            'if ! activate_release /release; then echo "activation failed"; fi',
        ]
    )
    calls = Path(os.environ.get("TMPDIR", "/tmp")) / f"motionly-activation-{failing}-{os.getpid()}"
    calls.unlink(missing_ok=True)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "CALLS": str(calls)},
    )
    steps = calls.read_text().split() if calls.exists() else []
    calls.unlink(missing_ok=True)

    assert result.returncode == 0, result.stderr
    assert "activation failed" in result.stdout
    order = ["config", "pull", "up", "exec"]
    assert [s for s in steps if s != "compose"] == order[: order.index(failing) + 1]


@pytest.mark.parametrize("failing", ["validate_release", "validate_release_bundle", "s3_download"])
def test_a_release_that_fails_verification_or_download_is_never_accepted(
    failing: str, tmp_path: Path
) -> None:
    """`release_dir="$(fetch_release)"` runs in a command substitution, where
    bash drops `set -e` unless inherit_errexit is on: a failed signature check
    used to be ignored and the unverified release accepted."""
    agent = AGENT.read_text("utf-8")
    header = agent[: agent.index("usage() {")]
    stubs = {
        "s3_download": 'touch "$2"',
        "validate_release": "true",
        "validate_release_bundle": "true",
        "tar": 'mkdir -p "$4/infra"; touch "$4/infra/docker-compose.gpu.yml"',
    }
    stubs[failing] = "echo verification-failed >&2; false"
    script = "\n".join(
        [
            header,
            f'releases_dir="{tmp_path}/releases"; release_id=0123abcd; mkdir -p "$releases_dir"',
            *(f"{name}() {{ {body}; }}" for name, body in stubs.items()),
            _function(agent, "fetch_release"),
            'release_dir="$(fetch_release)"',
            'echo "accepted $release_dir"',
        ]
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert result.returncode != 0
    assert "accepted" not in result.stdout
    assert not (tmp_path / "releases" / "0123abcd").exists()
    assert list((tmp_path / "releases").iterdir()) == [], "no scratch directory is left behind"


def test_every_tool_the_host_installs_as_an_executable_declares_its_interpreter() -> None:
    """Found by the release rehearsal: generate_release_manifest.py had no
    shebang, so the installed `generate-release-manifest` ran under bash and
    manifest verification could never succeed."""
    bootstrap = (ROOT / "scripts" / "deploy" / "bootstrap_compose_host.sh").read_text("utf-8")
    installed = re.findall(r'install -m 755 "\$source_root/([^"]+)"', bootstrap)
    assert "scripts/release/generate_release_manifest.py" in installed
    for relative in installed:
        first = (ROOT / relative).read_text("utf-8").splitlines()[0]
        assert first in {"#!/usr/bin/env bash", "#!/usr/bin/env python3"}, relative


def test_the_host_installs_only_tools_the_release_agent_runs() -> None:
    bootstrap = (ROOT / "scripts" / "deploy" / "bootstrap_compose_host.sh").read_text("utf-8")
    agent = AGENT.read_text("utf-8")
    installed = re.findall(
        r'install -m 755 "\$source_root/[^"]+" "\$agent_root/bin/([^"]+)"', bootstrap
    )
    assert "apply-release" in installed
    unused = [name for name in installed if name != "apply-release" and f"bin/{name}" not in agent]
    assert unused == []


def test_the_release_agent_writes_every_secret_file_production_compose_mounts() -> None:
    """A secret production Compose mounts but the agent never writes stops the
    release at `compose up` (bind source path does not exist)."""
    import re

    import yaml

    compose = yaml.safe_load((ROOT / "infra" / "docker-compose.gpu.yml").read_text("utf-8"))
    mounted = {
        Path(entry["file"]).name for entry in compose["secrets"].values() if "file" in entry
    }
    agent = (ROOT / "scripts" / "deploy" / "apply_release.sh").read_text("utf-8")
    written = set(re.findall(r'"\$secrets_dir/([a-z0-9_]+)"', agent))
    for loop in re.findall(r"for name in ([a-z0-9 -]+); do\n\s+fetch_base64_secret_to_file", agent):
        written |= {name.replace("-", "_") for name in loop.split()}
    assert sorted(mounted - written) == []
