"""The root CI workflow mirrors the nested phase-0-safety.yml (FE-03)."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify" / "workflow_parity.py"
NESTED = ROOT / ".github" / "workflows" / "phase-0-safety.yml"
ROOT_COPY = ROOT.parents[1] / ".github" / "workflows" / "production-readiness.yml"


def _parity():
    spec = importlib.util.spec_from_file_location("motionly_workflow_parity", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _nested() -> dict:
    data = yaml.safe_load(NESTED.read_text(encoding="utf-8"))
    data.pop(True, None)
    return data


def _adapted(nested: dict) -> dict:
    """The nested workflow with only the allowed root-copy adaptations."""
    prefix = "motionly-main/Motionly-main/"
    root = copy.deepcopy(nested)
    for job in root["jobs"].values():
        for step in job.get("steps", []):
            with_ = step.get("with")
            if isinstance(with_, dict) and with_.get("cache") == "pnpm":
                with_["cache-dependency-path"] = f"{prefix}pnpm-lock.yaml"
            if isinstance(with_, dict) and isinstance(with_.get("path"), str):
                with_["path"] = prefix + with_["path"]
            if "working-directory" in step:
                step["working-directory"] = prefix + step["working-directory"]
    return root


def test_allowed_adaptations_are_not_drift() -> None:
    parity = _parity()
    nested = _nested()
    assert parity.compare(nested, _adapted(nested)) == []


def test_a_changed_run_command_is_drift() -> None:
    parity = _parity()
    nested = _nested()
    root = _adapted(nested)
    step = next(s for s in root["jobs"]["quality-and-contracts"]["steps"] if "run" in s)
    step["run"] = "echo skipped"
    problems = parity.compare(nested, root)
    assert any("quality-and-contracts" in problem and "run" in problem for problem in problems)


def test_a_missing_job_and_changed_needs_are_drift() -> None:
    parity = _parity()
    nested = _nested()
    root = _adapted(nested)
    del root["jobs"]["cpu-stage-browser"]
    root["jobs"]["staging-canary"]["needs"] = ["containers"]
    problems = parity.compare(nested, root)
    assert "job cpu-stage-browser is missing from the root copy" in problems
    assert "job staging-canary: needs differs" in problems


def test_the_committed_root_copy_matches_when_present() -> None:
    parity = _parity()
    if not ROOT_COPY.exists():
        return  # Standalone monorepo checkout: there is no wrapper root.
    assert parity.main(["--nested", str(NESTED), "--root", str(ROOT_COPY)]) == 0
