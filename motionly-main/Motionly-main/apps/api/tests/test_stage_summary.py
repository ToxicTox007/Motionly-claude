"""The CI job summary for a verify run (scripts/verify/stage_summary.py)
shows every step's result, so a failed browser or k6 run is visible even when
the staging channel does not block on it."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def stage_summary():
    spec = importlib.util.spec_from_file_location(
        "motionly_stage_summary", ROOT / "scripts" / "verify" / "stage_summary.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(results: Path) -> None:
    (results / "verify").mkdir(parents=True)
    summary = {
        "result": "failed",
        "stages": [
            {
                "name": "browser",
                "status": "failed",
                "steps": [
                    {"name": "start the stack", "exit_code": 0, "duration_ms": 1500},
                    {"name": "browser suite", "exit_code": 124, "duration_ms": 3600000},
                    {"name": "verdict", "exit_code": None, "skipped_after_failure": True},
                ],
            },
            {
                "name": "perf",
                "status": "failed",
                "steps": [{"name": "k6 smoke", "exit_code": 1, "duration_ms": 100}],
            },
        ],
    }
    (results / "verify" / "summary.json").write_text(json.dumps(summary))
    (results / "browser").mkdir()
    stats = {"expected": 168, "unexpected": 170, "flaky": 0, "skipped": 0, "interrupted": 2}
    (results / "browser" / "results.json").write_text(json.dumps({"stats": stats}))
    k6_metrics = {
        "checks": {"values": {"passes": 9, "fails": 1}},
        "http_req_failed": {"values": {"rate": 0.1}},
    }
    (results / "k6-summary.json").write_text(json.dumps({"metrics": k6_metrics}))


def test_every_failed_step_and_count_is_listed(stage_summary, tmp_path: Path) -> None:
    _write(tmp_path)
    text = stage_summary.render(tmp_path, title="Browser suite and k6 smoke", channel="staging")
    assert "Release channel: **staging**; this job is advisory" in text
    assert "Overall result: **failed**" in text
    assert "| browser suite | FAILED (timed out) | 3600.0 |" in text
    assert "| verdict | not run (earlier step failed) | 0.0 |" in text
    assert "| k6 smoke | FAILED (exit 1) | 0.1 |" in text
    assert "Browser suite: expected 168, unexpected 170, flaky 0, skipped 0, interrupted 2" in text
    assert "k6 smoke: checks passed 9, failed 1; http_req_failed rate 0.1" in text


def test_production_is_labelled_blocking(stage_summary, tmp_path: Path) -> None:
    _write(tmp_path)
    text = stage_summary.render(tmp_path, title="t", channel="production")
    assert "Release channel: **production**; this job is blocking." in text


def test_a_run_that_wrote_nothing_says_so(stage_summary, tmp_path: Path) -> None:
    text = stage_summary.render(tmp_path, title="t", channel=None)
    assert "No verify summary was written" in text
