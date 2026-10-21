"""CPU-stage gate wiring (Task 32): the perf stage, the evidence report and CI.

Pins that scripts/verify/stages.json and .github/workflows/phase-0-safety.yml
run the perf budget gate, the k6 smoke SLO gate, the dead-code stage and the
evidence report from a fresh checkout, with all five browser setups.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"


def _stages() -> dict:
    return json.loads((ROOT / "scripts" / "verify" / "stages.json").read_text("utf-8"))


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _stage(name: str) -> dict:
    return next(s for s in _stages()["stages"] if s["name"] == name)


def test_perf_stage_is_implemented_and_runs_the_smoke_and_budget_gates() -> None:
    perf = _stage("perf")
    assert perf["status"] == "implemented"
    runs = [step["run"] for step in perf["steps"]]
    joined = "\n".join(runs)

    assert "cpu_stage_stack.py build" in joined
    assert "cpu_stage_stack.py up" in joined
    assert "perf_stage.py smoke" in joined
    assert "cpu_stage_stack.py verdict" in joined
    assert "perf_stage.py budget" in joined
    # The smoke runs against a live stack, before the verdict writes resources.
    assert joined.index("perf_stage.py smoke") < joined.index("cpu_stage_stack.py verdict")
    assert joined.index("cpu_stage_stack.py verdict") < joined.index("perf_stage.py budget")
    down = next(step for step in perf["steps"] if "cpu_stage_stack.py down" in step["run"])
    assert down.get("always") is True


def test_perf_stage_tears_down_with_anonymous_volumes_covered_by_the_stack_helper() -> None:
    # The perf stage reuses cpu_stage_stack.py down, which removes volumes; it
    # must not introduce a bare `docker rm` without -v.
    for step in _stage("perf")["steps"]:
        assert "docker rm " not in step["run"] or "-v" in step["run"]


def test_slo_contract_forbids_any_non_2xx() -> None:
    contract = json.loads(
        (ROOT / "scripts" / "perf" / "slo-contract.cpu-stage.json").read_text("utf-8")
    )
    assert contract["thresholds"]["http_error_rate"] == 0.0
    assert contract["thresholds"]["status_5xx_rate"] == 0.0


def test_gpu_stage_allowlist_documents_its_schema_and_lists_the_kept_symbols() -> None:
    allow = json.loads(
        (ROOT / "tests" / "gpu-stage-allowlist.json").read_text("utf-8")
    )
    assert allow["schema_version"] == 1
    assert "schema" in allow and "entries[]" in allow["schema"]
    ids = {entry["id"] for entry in allow["entries"]}
    assert {"gpu-autoscaling-helpers", "gpu-queue-routing", "dockerfile-gpu"} <= ids
    for entry in allow["entries"]:
        assert entry["reason"].strip(), entry
        assert entry["exercised_by"].strip(), entry
        assert entry["kind"] in {
            "python_symbol",
            "python_module",
            "ts_symbol",
            "ts_module",
            "api_route",
            "compose_service",
            "dockerfile",
            "file",
        }, entry
    # The autoscaling helpers named in the plan are all present.
    helpers = next(e for e in allow["entries"] if e["id"] == "gpu-autoscaling-helpers")
    assert {"autoscale_decision", "should_timeout_job", "oom_alert_event"} <= set(
        helpers["symbols"]
    )


def test_verify_runs_the_evidence_report_as_its_final_step() -> None:
    verify = (ROOT / "scripts" / "verify" / "verify.mjs").read_text("utf-8")
    assert "evidence_report.py" in verify
    # It runs after summary.json is written, so it reads this run's result.
    assert verify.index("summary.json") < verify.index("evidence_report.py")


def test_ci_cpu_stage_verify_runs_deadcode_from_a_fresh_checkout() -> None:
    job = _workflow("phase-0-safety.yml")["jobs"]["cpu-stage-verify"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "deadcode" in commands
    assert "pnpm install --frozen-lockfile" in commands


def test_ci_browser_job_runs_perf_with_every_browser_setup_and_k6() -> None:
    job = _workflow("phase-0-safety.yml")["jobs"]["cpu-stage-browser"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "pnpm verify --stage browser,perf" in commands
    assert "playwright install --with-deps chromium firefox webkit" in commands
    assert "install -y k6" in commands
    uploads = [step for step in job["steps"] if "upload-artifact" in str(step.get("uses", ""))]
    assert uploads and uploads[0].get("if") == "always()"
