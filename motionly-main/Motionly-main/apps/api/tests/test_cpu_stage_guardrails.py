"""CPU-stage guardrail contracts.

The CPU-only stage must make GPU-dependent validation impossible to run by
accident and impossible to skip silently. These tests pin that contract:

* every GPU-stage check is declared in ``tests/gpu-stage.json``;
* the CI job that starts the GPU topology lives only in a manually triggered,
  approval-protected workflow;
* the skip audit rejects undeclared skips; and
* ``pnpm verify`` exposes the planned stages.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
GPU_STAGE_MANIFEST = ROOT / "tests" / "gpu-stage.json"
SKIP_AUDIT = ROOT / "scripts" / "verify" / "skip_audit.py"
VERIFY_STAGES = (
    "static",
    "unit",
    "db",
    "contracts",
    "media",
    "stack",
    "browser",
    "leakage",
    "aws",
    "deadcode",
    "perf",
)


def _load_skip_audit():
    spec = importlib.util.spec_from_file_location("motionly_skip_audit", SKIP_AUDIT)
    assert spec and spec.loader, "scripts/verify/skip_audit.py is missing"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML (YAML 1.1) parses the bare ``on`` key as boolean True.
    return workflow.get("on") or workflow.get(True) or {}


def test_gpu_stage_manifest_declares_every_deferred_check_with_a_reason() -> None:
    manifest = json.loads(GPU_STAGE_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["gate_env"] == "RUN_GPU_STAGE_TESTS"
    entries = manifest["gpu_stage"]
    assert entries, "the GPU-stage manifest must not be empty"
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids)), "GPU-stage entry ids must be unique"
    for entry in entries:
        assert entry["kind"] in {"ci_job", "cli_probe", "runtime_smoke", "test"}, entry
        assert entry["reason"].strip(), entry
        assert entry["location"].strip(), entry
    assert "release-compose-integration" in ids
    for entry in manifest["optional"]:
        assert entry["reason"].strip() and entry["enable_env"].strip(), entry


def test_gpu_topology_ci_job_runs_only_in_the_protected_manual_gpu_workflow() -> None:
    supply_chain = _workflow("supply-chain.yml")
    assert "release-compose-integration" not in supply_chain["jobs"]
    for path in WORKFLOWS.glob("*.yml"):
        if path.name == "gpu-stage.yml":
            continue
        for job_name, job in _workflow(path.name)["jobs"].items():
            runs_on = job.get("runs-on")
            labels = runs_on if isinstance(runs_on, list) else [runs_on]
            assert "gpu" not in labels, f"{path.name}:{job_name} must not target GPU runners"

    gpu = _workflow("gpu-stage.yml")
    triggers = _triggers(gpu)
    assert set(triggers) == {"workflow_dispatch"}, "GPU stage must be manual-only"
    job = gpu["jobs"]["release-compose-integration"]
    assert job["environment"] == "gpu-stage", "GPU stage must require the protected environment"
    assert "gpu" in job["runs-on"]


def test_gpu_worker_image_is_still_built_and_scanned_but_never_started_in_ci() -> None:
    workflow = (WORKFLOWS / "supply-chain.yml").read_text(encoding="utf-8")
    assert "component: gpu-worker" in workflow
    assert "dockerfile: apps/worker/Dockerfile.gpu" in workflow
    for path in WORKFLOWS.glob("*.yml"):
        if path.name == "gpu-stage.yml":
            continue
        source = path.read_text(encoding="utf-8")
        assert "docker-compose.gpu.yml" not in source or "up -d" not in source, path.name


def test_skip_audit_rejects_undeclared_skips_and_accepts_declared_ones() -> None:
    audit = _load_skip_audit()
    manifest = {
        "schema_version": 1,
        "gate_env": "RUN_GPU_STAGE_TESTS",
        "gpu_stage": [{"id": "nvenc", "kind": "test", "reason": "needs NVENC", "location": "x"}],
        "optional": [
            {
                "id": "live-providers",
                "match": "Live provider contracts are disabled",
                "reason": "optional live check",
                "enable_env": "RUN_LIVE_PROVIDER_CONTRACTS",
            }
        ],
    }
    declared = [
        audit.Skip(source="pytest", test_id="t::a", reason="gpu-stage[nvenc]: needs NVENC"),
        audit.Skip(source="pytest", test_id="t::b", reason="Live provider contracts are disabled"),
    ]
    assert audit.unexpected_skips(declared, manifest) == []

    undeclared = [audit.Skip(source="node", test_id="w::c", reason="true")]
    assert audit.unexpected_skips(undeclared, manifest) == undeclared

    unknown_gpu_id = [
        audit.Skip(source="pytest", test_id="t::d", reason="gpu-stage[missing]: nope")
    ]
    assert audit.unexpected_skips(unknown_gpu_id, manifest) == unknown_gpu_id


def test_skip_audit_rejects_skips_of_optional_checks_the_stage_requires(tmp_path: Path) -> None:
    audit = _load_skip_audit()
    manifest = {
        "gpu_stage": [],
        "optional": [
            {
                "id": "supabase-repository-contracts",
                "match": "Set RUN_SUPABASE_CONTRACTS=1",
                "reason": "needs local Supabase",
                "enable_env": "RUN_SUPABASE_CONTRACTS",
            }
        ],
    }
    skip = audit.Skip(
        source="pytest", test_id="t::db", reason="Set RUN_SUPABASE_CONTRACTS=1 against a stack."
    )
    assert audit.unexpected_skips([skip], manifest) == []
    assert audit.unexpected_skips([skip], manifest, required={"supabase-repository-contracts"}) == [
        skip
    ]
    with pytest.raises(ValueError, match="unknown optional check"):
        audit.unexpected_skips([], manifest, required={"not-declared"})

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = tmp_path / "db.xml"
    report.write_text(
        """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">
        <testcase classname="tests.integration.test_db" name="test_contract" time="0.0">
          <skipped type="pytest.skip" message="Set RUN_SUPABASE_CONTRACTS=1 against a stack."/>
        </testcase></testsuite></testsuites>""",
        encoding="utf-8",
    )
    base_args = ["--manifest", str(manifest_path), "--pytest", str(report)]
    assert audit.main(base_args) == 0
    assert audit.main([*base_args, "--require", "supabase-repository-contracts"]) == 1


def test_skip_audit_parses_pytest_and_node_junit_reports(tmp_path: Path) -> None:
    audit = _load_skip_audit()
    pytest_report = tmp_path / "pytest.xml"
    pytest_report.write_text(
        """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">
        <testcase classname="tests.test_x" name="test_ok" time="0.1"/>
        <testcase classname="tests.test_x" name="test_skip" time="0.0">
          <skipped type="pytest.skip" message="gpu-stage[nvenc]: needs NVENC">reason</skipped>
        </testcase></testsuite></testsuites>""",
        encoding="utf-8",
    )
    node_report = tmp_path / "node.xml"
    node_report.write_text(
        """<?xml version="1.0" encoding="utf-8"?><testsuites>
        <testcase name="gated" classname="test" file="/repo/a.test.mjs">
          <skipped type="skipped" message="true"/></testcase></testsuites>""",
        encoding="utf-8",
    )
    skips = audit.parse_junit(pytest_report, source="pytest") + audit.parse_junit(
        node_report, source="node"
    )
    assert [skip.reason for skip in skips] == ["gpu-stage[nvenc]: needs NVENC", "true"]


def test_stages_that_enable_an_optional_check_require_it_in_their_skip_audit() -> None:
    manifest = json.loads((ROOT / "tests" / "gpu-stage.json").read_text(encoding="utf-8"))
    stages = json.loads((ROOT / "scripts" / "verify" / "stages.json").read_text("utf-8"))
    for stage in stages["stages"]:
        enabled_envs = {key for step in stage["steps"] for key in step.get("env", {})}
        audits = [step["run"] for step in stage["steps"] if "skip_audit.py" in step["run"]]
        for entry in manifest["optional"]:
            if entry["enable_env"] in enabled_envs:
                assert audits, f"stage {stage['name']} enables {entry['id']} but has no skip audit"
                assert all(f"--require {entry['id']}" in run for run in audits), (
                    f"stage {stage['name']} enables {entry['enable_env']} but its skip audit "
                    f"does not --require {entry['id']}"
                )


def test_root_package_exposes_pnpm_verify_with_every_planned_stage() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["verify"].startswith("node scripts/verify/verify.mjs")
    stages = json.loads((ROOT / "scripts" / "verify" / "stages.json").read_text("utf-8"))
    assert tuple(stage["name"] for stage in stages["stages"]) == VERIFY_STAGES
    for stage in stages["stages"]:
        assert stage["status"] in {"implemented", "not_implemented"}, stage
        if stage["status"] == "implemented":
            assert stage["steps"], stage


def test_pytest_declares_the_gpu_stage_marker(pytestconfig: pytest.Config) -> None:
    markers = pytestconfig.getini("markers")
    assert any(line.startswith("gpu_stage(id):") for line in markers), markers


def test_ci_runs_the_same_verify_stages_on_a_fresh_checkout() -> None:
    workflow = _workflow("phase-0-safety.yml")
    job = workflow["jobs"]["cpu-stage-verify"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "pnpm verify --stage static,unit,leakage,contracts,media" in commands
    assert "apt-get install -y ffmpeg" in commands, "media tools must be installed, not skipped"
    assert "requirements-dev.lock" in commands
    uploads = [step for step in job["steps"] if "upload-artifact" in str(step.get("uses", ""))]
    assert uploads and uploads[0].get("if") == "always()"


def test_ci_container_health_run_passes_api_settings_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shlex

    from app.core.config import Settings

    job = _workflow("phase-0-safety.yml")["jobs"]["containers"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    run_line = next(
        line
        for line in commands.splitlines()
        if "docker run" in line and "motionly-api:test" in line
    )
    words = shlex.split(run_line)
    values = dict(
        words[index + 1].split("=", 1)
        for index, word in enumerate(words)
        if word in {"-e", "--env"}
    )
    for name in list(os.environ):
        if name.startswith(("SUPABASE_", "APP_ENV", "SKIP_API_AUTH")):
            monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)  # raises if the container would refuse to start
    assert settings.app_env == values["APP_ENV"]


def test_ci_supabase_cli_version_matches_the_pinned_dev_dependency() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    workflow = _workflow("phase-0-safety.yml")
    assert workflow["env"]["SUPABASE_CLI_VERSION"] == package["devDependencies"]["supabase"]


def test_ci_starts_the_cpu_stage_stack_from_empty_volumes() -> None:
    job = _workflow("phase-0-safety.yml")["jobs"]["cpu-stage-stack"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])

    assert "pnpm verify --stage stack" in commands
    assert "requirements-dev.lock" in commands
    assert "pnpm install --frozen-lockfile" in commands
    uploads = [step for step in job["steps"] if "upload-artifact" in str(step.get("uses", ""))]
    assert uploads and uploads[0].get("if") == "always()"
    assert uploads[0]["with"]["path"] == "test-results/"


def test_ci_builds_the_database_from_empty_with_the_db_verify_stage() -> None:
    job = _workflow("phase-0-safety.yml")["jobs"]["empty-database-and-repositories"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    # One definition of the database checks: scripts/verify/stages.json.
    assert "pnpm verify --stage db" in commands
    assert "supabase db reset" not in commands
    assert "supabase test db" not in commands
    assert "requirements-dev.lock" in commands
    uploads = [step for step in job["steps"] if "upload-artifact" in str(step.get("uses", ""))]
    assert uploads and uploads[0].get("if") == "always()"


def test_unit_stage_runs_the_temporal_server_wrapper_go_tests() -> None:
    stages = json.loads((ROOT / "scripts" / "verify" / "stages.json").read_text("utf-8"))
    unit = next(stage for stage in stages["stages"] if stage["name"] == "unit")
    go_steps = [step for step in unit["steps"] if step.get("cwd") == "infra/temporal/server"]

    assert go_steps and "go vet ./..." in go_steps[0]["run"]
    assert "go test ./..." in go_steps[0]["run"]
    job = _workflow("phase-0-safety.yml")["jobs"]["cpu-stage-verify"]
    setup_go = [step for step in job["steps"] if "actions/setup-go" in str(step.get("uses", ""))]
    assert setup_go and setup_go[0]["with"]["go-version-file"] == "infra/temporal/server/go.mod"


def test_browser_stage_runs_the_suite_against_a_fresh_stack_and_gates_it() -> None:
    stages = json.loads((ROOT / "scripts" / "verify" / "stages.json").read_text("utf-8"))
    browser = next(stage for stage in stages["stages"] if stage["name"] == "browser")
    runs = [step["run"] for step in browser["steps"]]

    assert browser["status"] == "implemented"
    order = [
        "cpu_stage_stack.py build",
        "cpu_stage_stack.py up",
        "cpu_stage_stack.py mark browser",
        "--config playwright.browser.config.mjs",
        "browser_report.py",
        "cpu_stage_stack.py logscan --since-mark browser",
        "cpu_stage_stack.py verdict",
        "cpu_stage_stack.py down",
        "skip_audit.py",
    ]
    positions = [next(i for i, run in enumerate(runs) if text in run) for text in order]
    assert positions == sorted(positions), runs
    down = next(step for step in browser["steps"] if "cpu_stage_stack.py down" in step["run"])
    assert down.get("always") is True
    audit = next(run for run in runs if "skip_audit.py" in run)
    assert "--node {results}/browser/junit.xml" in audit
    suite = next(
        step for step in browser["steps"] if "playwright.browser.config.mjs" in step["run"]
    )
    assert suite.get("cwd") == "apps/web"
    assert "--retries" not in suite["run"], "the suite never retries"


def test_ci_runs_the_browser_stage_in_every_engine_and_drops_the_old_e2e_job() -> None:
    workflow = _workflow("phase-0-safety.yml")
    assert "browser-creator-journey" not in workflow["jobs"]
    for job in workflow["jobs"].values():
        assert "browser-creator-journey" not in (job.get("needs") or [])
    job = workflow["jobs"]["cpu-stage-browser"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])

    assert "pnpm verify --stage browser" in commands
    assert "playwright install --with-deps chromium firefox webkit" in commands
    assert "apt-get install -y ffmpeg" in commands
    assert "requirements-dev.lock" in commands
    uploads = [step for step in job["steps"] if "upload-artifact" in str(step.get("uses", ""))]
    assert uploads and uploads[0].get("if") == "always()"
    assert uploads[0]["with"]["path"] == "test-results/"
    package = json.loads((ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    assert "test:e2e" not in package["scripts"]
    assert package["scripts"]["test:browser"] == (
        "playwright test --config playwright.browser.config.mjs"
    )


def test_leakage_stage_runs_the_fixture_gate_and_audits_its_skips() -> None:
    stages = json.loads((ROOT / "scripts" / "verify" / "stages.json").read_text("utf-8"))
    leakage = next(stage for stage in stages["stages"] if stage["name"] == "leakage")
    runs = "\n".join(step["run"] for step in leakage["steps"])

    assert leakage["status"] == "implemented"
    assert "tests/test_fixture_leakage.py" in runs
    assert "--junitxml={results}/junit/leakage-api.xml" in runs
    assert "--test-name-pattern" in runs and "src/ml-execution.test.mjs" in runs
    assert "skip_audit.py" in runs and "leakage-api.xml" in runs
    assert (ROOT / "fixtures" / "registry.json").is_file()


def test_aws_stage_validates_statically_and_rehearses_the_release_without_calling_aws() -> None:
    stages = json.loads((ROOT / "scripts" / "verify" / "stages.json").read_text("utf-8"))
    aws = next(stage for stage in stages["stages"] if stage["name"] == "aws")
    runs = [step["run"] for step in aws["steps"]]
    joined = "\n".join(runs)

    assert aws["status"] == "implemented"
    for text in (
        "scripts/aws/install-tools.sh",
        "render_template.py --check",
        "cfn-lint --regions ap-south-1",
        "cfn-guard validate --rules infra/aws/guard/motionly.guard",
        "bash -n infra/aws/bootstrap.sh scripts/deploy/apply_release.sh "
        "scripts/deploy/run_on_instance.sh",
        "--require aws-static-tools",
        "cpu_stage_stack.py build",
        "scripts/release/rehearse_release.py",
    ):
        assert text in joined, text
    # The rehearsal runs after the images exist; static checks come first.
    assert joined.index("cfn-guard") < joined.index("cpu_stage_stack.py build")
    assert joined.index("cpu_stage_stack.py build") < joined.index("rehearse_release.py")
    assert "aws cloudformation" not in joined
    job = _workflow("phase-0-safety.yml")["jobs"]["cpu-stage-verify"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "pnpm verify --stage static,unit,leakage,contracts,media" in commands
    rehearsal = _workflow("phase-0-safety.yml")["jobs"]["aws-release-rehearsal"]
    commands = "\n".join(step.get("run", "") for step in rehearsal["steps"])
    assert "pnpm verify --stage aws" in commands
    uploads = [
        step for step in rehearsal["steps"] if "upload-artifact" in str(step.get("uses", ""))
    ]
    assert uploads and uploads[0].get("if") == "always()"


def test_verify_stages_remove_containers_with_their_anonymous_volumes() -> None:
    """Found at the Phase B checkpoint: `docker rm -f` of a `--rm` container
    skips the automatic volume cleanup, so every contracts run left the Redis
    image's anonymous /data volume behind."""
    stages = json.loads((ROOT / "scripts" / "verify" / "stages.json").read_text("utf-8"))
    removals = [
        command
        for stage in stages["stages"]
        for step in stage.get("steps", [])
        for command in re.findall(r"docker rm [^;&|]*", step["run"])
    ]
    assert removals
    for command in removals:
        assert re.search(r"(^|\s)(-v|--volumes|-fv|-vf)(\s|$)", command), command


def test_verify_steps_run_the_pinned_pnpm_even_when_another_is_installed() -> None:
    """turbo runs `pnpm run <script>` per package; a different global pnpm
    refuses this project (ERR_PNPM_BAD_PM_VERSION), so every step gets a shim
    for the pinned version first on PATH."""
    script = (ROOT / "scripts" / "verify" / "verify.mjs").read_text("utf-8")
    assert 'const shimDir = join(verifyDir, ".bin");' in script
    assert "exec corepack pnpm@${pinnedPnpm}" in script
    assert "PATH: `${pinnedPnpm ? `${shimDir}:` : \"\"}" in script
