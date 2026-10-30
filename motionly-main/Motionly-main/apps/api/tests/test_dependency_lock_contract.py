"""Dependency reproducibility contracts for the Python service."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "apps" / "api"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "phase-0-safety.yml"
LOCK_PATHS = (
    API_ROOT / "requirements.lock",
    API_ROOT / "requirements-dev.lock",
    API_ROOT / "requirements-ml.lock",
)
PACKAGE_LINE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")


def _locked_versions(lock_path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in lock_path.read_text().splitlines():
        match = PACKAGE_LINE.match(line)
        if match:
            versions[match.group(1).lower()] = match.group(2)
    return versions


def test_all_python_lock_entries_are_exact_and_hash_complete() -> None:
    for lock_path in LOCK_PATHS:
        source = lock_path.read_text()
        assert "--generate-hashes" in source, lock_path
        package_starts = list(re.finditer(r"^[A-Za-z0-9_.-]+==[^\s\\]+", source, re.MULTILINE))
        assert package_starts, lock_path
        for index, package_start in enumerate(package_starts):
            if index + 1 < len(package_starts):
                next_start = package_starts[index + 1].start()
            else:
                next_start = len(source)
            package_block = source[package_start.start() : next_start]
            assert "--hash=sha256:" in package_block, package_block.splitlines()[0]


def test_visual_ml_declares_the_hash_locked_numpy_scipy_sklearn_window() -> None:
    pyproject = tomllib.loads((API_ROOT / "pyproject.toml").read_text())
    visual_ml = pyproject["project"]["optional-dependencies"]["visual-ml"]

    assert "numpy>=1.26.4,<2.0" in visual_ml
    assert "scipy>=1.17.1,<1.18" in visual_ml
    assert "scikit-learn>=1.9.0,<1.10" in visual_ml

    versions = _locked_versions(API_ROOT / "requirements-ml.lock")
    assert versions["numpy"] == "1.26.4"
    assert versions["scipy"] == "1.17.1"
    assert versions["scikit-learn"] == "1.9.0"


def test_readiness_workflow_installs_application_python_only_from_hash_locks() -> None:
    workflow = WORKFLOW_PATH.read_text()

    assert "./apps/api[dev]" not in workflow
    assert "'.[dev]'" not in workflow
    assert '".[dev]"' not in workflow

    installs = [line.strip() for line in workflow.splitlines() if "python -m pip install" in line]
    assert installs
    for install in installs:
        assert "--require-hashes" in install
        assert "requirements-dev.lock" in install

    assert workflow.count("apps/api/requirements-dev.lock") >= 11
    assert workflow.count("apps/api/requirements-ml.lock") >= 6
    assert "pypa/gh-action-pip-audit@v1.1.0" in workflow
    assert "inputs: apps/api/requirements.lock" in workflow
