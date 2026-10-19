"""Recording D2 baselines from a browser-stage run (scripts/verify/record_baselines.py).

The browser stage leaves per-service peaks under browser load
(test-results/cpu-stage/resources.json) and navigation timings
(test-results/browser/report.json). The recorder copies them into
tests/cpu-stage/resource-budget.json next to the startup baselines and never
sets a budget: budgets stay null until the user approves them (Decision D2).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def recorder():
    spec = importlib.util.spec_from_file_location(
        "motionly_record_baselines", ROOT / "scripts" / "verify" / "record_baselines.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _budget() -> dict:
    return {
        "schema_version": 1,
        "decision": "D2",
        "basis": "measured peak x 1.25",
        "status": "measured",
        "size": "slim",
        "services": {
            "api": {
                "baseline_peak_memory_bytes": 100,
                "baseline_peak_cpu_percent": 10.0,
                "budget_memory_bytes": None,
                "budget_cpu_percent": None,
            }
        },
    }


def _resources() -> dict:
    return {
        "size": "slim",
        "sampling": {"interval_seconds": 5, "sample_rounds": 200, "duration_seconds": 1000.0},
        "services": {
            "api": {"peak_memory_bytes": 250, "peak_cpu_percent": 61.5, "restart_count": 0},
            "db-migrate": {"peak_memory_bytes": None, "peak_cpu_percent": None},
        },
    }


def _report() -> dict:
    return {
        "wall_seconds": 900.0,
        "projects": {"chromium": {"tests": 29, "passed": 28, "expected_failures": 1}},
        "navigation": {
            "by_project": {
                "chromium": {"load_ms": {"count": 80, "p50": 400, "p95": 900, "max": 1500}}
            },
            "by_path": {"/login": {"load_ms": {"count": 40, "p50": 300, "p95": 600, "max": 800}}},
        },
        "problems": [],
    }


def test_browser_load_peaks_and_timings_are_recorded_without_budgets(recorder) -> None:
    updated = recorder.record(
        _budget(), _resources(), _report(), date="2026-10-07", machine="test box"
    )

    api = updated["services"]["api"]
    assert api["browser_peak_memory_bytes"] == 250
    assert api["browser_peak_cpu_percent"] == 61.5
    assert api["baseline_peak_memory_bytes"] == 100
    assert api["budget_memory_bytes"] is None and api["budget_cpu_percent"] is None
    assert "db-migrate" not in updated["services"]
    browser = updated["browser"]
    assert browser["wall_seconds"] == 900.0
    assert browser["navigation"]["by_project"]["chromium"]["load_ms"]["p95"] == 900
    assert browser["budget_navigation_load_p95_ms"] is None
    assert updated["browser_measurement"] == {
        "source": ["test-results/cpu-stage/resources.json", "test-results/browser/report.json"],
        "date": "2026-10-07",
        "machine": "test box",
        "scenario": "the browser suite in five setups against the slim stack",
        "sample_rounds": 200,
        "duration_seconds": 1000.0,
    }


def test_a_run_with_problems_or_another_size_is_refused(recorder) -> None:
    failing = _report() | {"problems": ["chromium: x failed"]}
    with pytest.raises(recorder.RecordError, match="problems"):
        recorder.record(_budget(), _resources(), failing, date="d", machine="m")
    other = _resources() | {"size": "full"}
    with pytest.raises(recorder.RecordError, match="size"):
        recorder.record(_budget(), other, _report(), date="d", machine="m")


def test_approved_budgets_are_never_overwritten(recorder) -> None:
    approved = _budget() | {"status": "approved"}
    with pytest.raises(recorder.RecordError, match="approved"):
        recorder.record(approved, _resources(), _report(), date="d", machine="m")


def _measured() -> dict:
    budget = _budget()
    budget["services"]["api"].update(browser_peak_memory_bytes=250, browser_peak_cpu_percent=61.5)
    budget["services"]["web"] = {
        "baseline_peak_memory_bytes": 400,
        "baseline_peak_cpu_percent": 20.0,
        "browser_peak_memory_bytes": 300,
        "browser_peak_cpu_percent": 10.0,
        "budget_memory_bytes": None,
        "budget_cpu_percent": None,
    }
    budget["browser"] = {
        "wall_seconds": 898.1,
        "navigation": {
            "by_project": {
                "chromium": {"load_ms": {"count": 47, "p50": 477, "p95": 1781, "max": 2075}},
                "firefox": {"load_ms": {"count": 107, "p50": 24, "p95": 832, "max": 1155}},
            }
        },
        "budget_navigation_load_p95_ms": None,
        "budget_wall_seconds": None,
    }
    return budget


def test_approval_sets_each_budget_to_the_factor_times_the_higher_baseline(recorder) -> None:
    approved = recorder.approve(
        _measured(), factor=1.25, exclude_navigation=["firefox"], reason="unreliable", date="d"
    )

    assert approved["status"] == "approved"
    assert approved["services"]["api"]["budget_memory_bytes"] == 313  # ceil(250 x 1.25)
    assert approved["services"]["api"]["budget_cpu_percent"] == 76.9  # 61.5 x 1.25, rounded up
    assert approved["services"]["web"]["budget_memory_bytes"] == 500  # startup peak is higher
    assert approved["services"]["web"]["budget_cpu_percent"] == 25.0
    browser = approved["browser"]
    assert browser["budget_navigation_load_p95_ms"] == {"chromium": 2227}
    assert browser["budget_wall_seconds"] == 1123
    assert browser["excluded_navigation"] == [{"project": "firefox", "reason": "unreliable"}]
    assert approved["approval"] == {"decision": "D2", "date": "d", "factor": 1.25}


def test_approval_needs_browser_measurements_and_happens_once(recorder) -> None:
    with pytest.raises(recorder.RecordError, match="browser"):
        recorder.approve(_budget(), factor=1.25, exclude_navigation=[], reason="r", date="d")
    approved = recorder.approve(
        _measured(), factor=1.25, exclude_navigation=["firefox"], reason="r", date="d"
    )
    with pytest.raises(recorder.RecordError, match="approved"):
        recorder.approve(approved, factor=1.25, exclude_navigation=[], reason="r", date="d")
