"""Staging and production release channels stay separate.

scripts/release/release_channel.py classifies a ref. Only an exact
vX.Y.Z-staging.N tag is the staging channel, where the browser suite and k6
smoke job (cpu-stage-browser) is advisory and the release deploys in the
GitHub staging environment. Every other ref keeps every readiness job blocking
and deploys in production. These tests pin the classifier and the workflow
wiring that depends on it, so a staging exception cannot reach a production tag.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT.parents[1] / ".github" / "workflows"
NESTED_READINESS = ROOT / ".github" / "workflows" / "phase-0-safety.yml"
ADVISORY_JOB = "cpu-stage-browser"
# The job's own ref test (repeated from the classifier as a second guard).
ADVISORY_EXPRESSION = (
    "${{ inputs.browser_perf_advisory == true && startsWith(github.ref, 'refs/tags/v') "
    "&& contains(github.ref, '-staging.') }}"
)

STAGING_REFS = [
    "refs/tags/v1.0.7-staging.1",
    "refs/tags/v0.1.0-staging.8",
    "refs/tags/v10.20.30-staging.12",
]
PRODUCTION_REFS = [
    "refs/tags/v1.0.7",
    "refs/tags/v2.0.0",
    "refs/tags/v1.0.7-staging",
    "refs/tags/v1.0.7-staging.",
    "refs/tags/v1.0.7-staging.0",
    "refs/tags/v1.0.7-staging.01",
    "refs/tags/v1.0.7-staging.1-rc",
    "refs/tags/v1.0.7-staging.1.2",
    "refs/tags/v1.0.7-rc.1",
    "refs/tags/v1.0-staging.1",
    "refs/tags/v01.0.7-staging.1",
    "refs/tags/V1.0.7-staging.1",
    "refs/tags/release-v1.0.7-staging.1",
    "refs/tags/v1.0.7-STAGING.1",
    "refs/heads/v1.0.7-staging.1",
    "refs/heads/main",
    "refs/pull/12/merge",
    "v1.0.7-staging.1",
    "refs/tags/v1.0.7-staging.1\n",
    "",
]


@pytest.fixture(scope="module")
def release_channel():
    spec = importlib.util.spec_from_file_location(
        "motionly_release_channel", ROOT / "scripts" / "release" / "release_channel.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow(name: str) -> dict:
    path = WORKFLOWS / name
    if not path.exists():
        pytest.skip(f"{path} is not part of this checkout")
    data = yaml.safe_load(path.read_text("utf-8"))
    data["on"] = data.pop(True, data.get("on"))
    return data


def _job_ref_test(ref: str) -> bool:
    """The cpu-stage-browser job's own ref test, evaluated in Python."""
    return ref.startswith("refs/tags/v") and "-staging." in ref


@pytest.mark.parametrize("ref", STAGING_REFS)
def test_exact_staging_tags_are_the_staging_channel(release_channel, ref: str) -> None:
    assert release_channel.outputs(ref) == {
        "channel": "staging",
        "browser_perf_advisory": "true",
        "deploy_environment": "staging",
    }


@pytest.mark.parametrize("ref", PRODUCTION_REFS)
def test_every_other_ref_is_production_with_blocking_gates(release_channel, ref: str) -> None:
    assert release_channel.outputs(ref) == {
        "channel": "production",
        "browser_perf_advisory": "false",
        "deploy_environment": "production",
    }


@pytest.mark.parametrize("ref", STAGING_REFS + PRODUCTION_REFS)
def test_the_browser_job_is_advisory_only_when_both_guards_agree(release_channel, ref) -> None:
    # Advisory needs the classifier's input AND the job's own ref test.
    advisory = release_channel.outputs(ref)["browser_perf_advisory"] == "true"
    effective = advisory and _job_ref_test(ref)
    assert effective is (ref in STAGING_REFS)


def test_the_cli_writes_github_outputs(release_channel, tmp_path: Path) -> None:
    output = tmp_path / "out"
    assert release_channel.main(["--ref", "refs/tags/v1.0.7", "--github-output", str(output)]) == 0
    assert output.read_text("utf-8") == (
        "channel=production\nbrowser_perf_advisory=false\ndeploy_environment=production\n"
    )


def test_supply_chain_passes_the_classified_channel_to_readiness() -> None:
    jobs = _workflow("supply-chain.yml")["jobs"]
    classify = jobs["release-channel"]
    assert any(
        'scripts/release/release_channel.py --ref "$REF"' in step.get("run", "")
        and step.get("env", {}).get("REF") == "${{ github.ref }}"
        for step in classify["steps"]
    )
    readiness = jobs["readiness"]
    assert readiness["uses"] == "./.github/workflows/production-readiness.yml"
    assert readiness["needs"] == ["release-channel"]
    assert readiness["with"] == {
        "browser_perf_advisory": (
            "${{ needs.release-channel.outputs.browser_perf_advisory == 'true' }}"
        )
    }


def test_release_images_still_wait_for_every_gate() -> None:
    jobs = _workflow("supply-chain.yml")["jobs"]
    assert sorted(jobs["build-scan-sign"]["needs"]) == ["locked-dependencies", "readiness"]
    assert jobs["release-manifest"]["needs"] == ["build-scan-sign"]
    # Only the existing diagnostic CodeQL job may fail without blocking.
    tolerant = sorted(name for name, job in jobs.items() if "continue-on-error" in job)
    assert tolerant == ["sast"]
    report = jobs["readiness-report"]
    assert report["if"] == "always()" and "build-scan-sign" not in str(report)


@pytest.mark.parametrize("path", [WORKFLOWS / "production-readiness.yml", NESTED_READINESS])
def test_only_the_browser_job_can_be_advisory_and_only_on_staging_refs(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"{path} is not part of this checkout")
    data = yaml.safe_load(path.read_text("utf-8"))
    jobs = data["jobs"]
    tolerant = {
        name: job["continue-on-error"] for name, job in jobs.items() if "continue-on-error" in job
    }
    assert tolerant == {ADVISORY_JOB: ADVISORY_EXPRESSION}
    browser = jobs[ADVISORY_JOB]
    # It still runs every browser and k6 step and always reports its result.
    assert any(step.get("run") == "pnpm verify --stage browser,perf" for step in browser["steps"])
    upload, summary = browser["steps"][-2], browser["steps"][-1]
    assert upload["name"] == "Upload browser results" and upload["if"] == "always()"
    assert summary["id"] == "result" and summary["if"] == "always()"
    assert "GITHUB_STEP_SUMMARY" in summary["run"] and "::error" in summary["run"]
    assert browser["outputs"] == {"result": "${{ steps.result.outputs.result }}"}


def test_the_advisory_input_defaults_to_blocking() -> None:
    triggers = _workflow("production-readiness.yml")["on"]
    advisory = triggers["workflow_call"]["inputs"]["browser_perf_advisory"]
    assert advisory["type"] == "boolean" and advisory["default"] is False
    outputs = triggers["workflow_call"]["outputs"]
    assert outputs["browser_perf_result"]["value"] == "${{ jobs.cpu-stage-browser.outputs.result }}"


def test_deploys_go_to_the_environment_the_channel_names() -> None:
    jobs = _workflow("deploy-aws.yml")["jobs"]
    route = jobs["route"]
    assert any(
        'scripts/release/release_channel.py --ref "refs/tags/$TAG"' in step.get("run", "")
        and step.get("env", {}).get("TAG") == "${{ github.event.workflow_run.head_branch }}"
        for step in route["steps"]
    )
    assert "github.event.workflow_run.conclusion == 'success'" in route["if"]
    for name in ("publish-and-preflight", "apply"):
        assert "route" in jobs[name]["needs"]
        assert jobs[name]["environment"] == "${{ needs.route.outputs.environment }}"


def _example_identity_regexp() -> str:
    example = (ROOT / "infra" / "compose-host" / "host.env.example").read_text("utf-8")
    line = next(
        line for line in example.splitlines() if line.startswith("MOTIONLY_COSIGN_IDENTITY_REGEXP=")
    )
    return line.split("=", 1)[1]


@pytest.mark.parametrize(
    ("ref", "accepted"),
    [
        ("refs/tags/v1.0.7", True),
        ("refs/tags/v10.2.30", True),
        ("refs/tags/v1.0.7-staging.1", False),
        ("refs/tags/v1.0.7-rc.1", False),
        ("refs/heads/main", False),
    ],
)
def test_a_production_host_refuses_releases_signed_for_staging_tags(ref: str, accepted) -> None:
    # The agent sources host.env with bash, so check the value bash produces.
    value = subprocess.run(
        [
            "bash",
            "-c",
            "MOTIONLY_COSIGN_IDENTITY_REGEXP="
            + _example_identity_regexp()
            + '; printf %s "$MOTIONLY_COSIGN_IDENTITY_REGEXP"',
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    identity = f"https://github.com/o/r/.github/workflows/supply-chain.yml@{ref}"
    assert bool(re.search(value, identity)) is accepted
