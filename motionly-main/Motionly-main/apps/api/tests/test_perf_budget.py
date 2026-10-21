"""CPU-stage perf budget checker (scripts/verify/perf_budget.py).

The ``perf`` verify stage enforces the approved Decision D2 budgets
(tests/cpu-stage/resource-budget.json) against the reports the stack and
browser stages write. These tests feed synthetic reports: a passing run, a
budget breach, a container-health failure, a pending-baseline service and a
missing report.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def perf():
    spec = importlib.util.spec_from_file_location(
        "motionly_perf_budget", ROOT / "scripts" / "verify" / "perf_budget.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _budget() -> dict:
    return {
        "schema_version": 1,
        "status": "approved",
        "size": "slim",
        "services": {
            "api": {"budget_memory_bytes": 300, "budget_cpu_percent": 40.0},
            "supabase-realtime": {
                "pending_baseline": True,
                "pending_baseline_reason": "Task 25 added Supabase Realtime; no baseline yet.",
                "budget_memory_bytes": None,
                "budget_cpu_percent": None,
            },
        },
        "browser": {
            "budget_navigation_load_p95_ms": {"chromium": 2000, "firefox": 1000},
            "budget_wall_seconds": 1100,
            "excluded_navigation": [{"project": "firefox", "reason": "implausible timings"}],
        },
    }


def _resources(**overrides) -> dict:
    base = {
        "size": "slim",
        "services": {
            "api": {
                "peak_memory_bytes": 250,
                "peak_cpu_percent": 31.0,
                "restart_count": 0,
                "oom_killed": False,
                "running": True,
                "exit_code": 0,
            }
        },
        "problems": [],
    }
    base.update(overrides)
    return base


def _report(**overrides) -> dict:
    base = {
        "wall_seconds": 900.0,
        "navigation": {
            "by_project": {
                "chromium": {"load_ms": {"p50": 477, "p95": 1781, "max": 2075}},
                "firefox": {"load_ms": {"p50": 24, "p95": 832, "max": 1155}},
            }
        },
        "problems": [],
    }
    base.update(overrides)
    return base


def test_a_run_within_budget_passes(perf) -> None:
    verdict = perf.check_budget(_budget(), _resources(), _report())

    assert verdict["passed"] is True
    assert verdict["failures"] == []
    assert verdict["breaches"] == []
    assert "api" in verdict["checked"]
    assert "navigation:chromium" in verdict["checked"]
    assert "wall_seconds" in verdict["checked"]


def test_a_service_over_memory_or_cpu_is_a_breach(perf) -> None:
    resources = _resources(
        services={
            "api": {
                "peak_memory_bytes": 400,
                "peak_cpu_percent": 55.0,
                "restart_count": 0,
                "oom_killed": False,
                "running": True,
                "exit_code": 0,
            }
        }
    )

    verdict = perf.check_budget(_budget(), resources, _report())

    assert verdict["passed"] is False
    metrics = {(b["service"], b["metric"]) for b in verdict["breaches"]}
    assert ("api", "memory_bytes") in metrics
    assert ("api", "cpu_percent") in metrics


def test_a_page_load_or_wall_time_over_budget_is_a_breach(perf) -> None:
    report = _report(
        navigation={"by_project": {"chromium": {"load_ms": {"p95": 2500}}}},
        wall_seconds=1200.0,
    )

    verdict = perf.check_budget(_budget(), _resources(), report)

    assert verdict["passed"] is False
    metrics = {b["metric"] for b in verdict["breaches"]}
    assert "navigation_load_p95_ms" in metrics
    assert "wall_seconds" in metrics


def test_firefox_navigation_is_excluded_not_breached(perf) -> None:
    # Firefox p95 (832) is under its listed budget (1000) here, but even an
    # over-budget Firefox must be excluded, never a breach.
    report = _report(
        navigation={
            "by_project": {
                "chromium": {"load_ms": {"p95": 1781}},
                "firefox": {"load_ms": {"p95": 9999}},
            }
        }
    )

    verdict = perf.check_budget(_budget(), _resources(), report)

    assert verdict["passed"] is True
    assert {"project": "firefox", "reason": "implausible timings"} in verdict["excluded"]
    assert not any(b.get("setup") == "firefox" for b in verdict["breaches"])


def test_a_pending_baseline_service_is_reported_not_passed_silently(perf) -> None:
    verdict = perf.check_budget(_budget(), _resources(), _report())

    pending = {entry["service"] for entry in verdict["pending"]}
    assert "supabase-realtime" in pending
    # Pending services are never in the checked (passed) set and never breach.
    assert "supabase-realtime" not in verdict["checked"]
    assert not any(
        b.get("service") == "supabase-realtime" for b in verdict["breaches"]
    )


def test_oom_kills_and_restarts_always_fail(perf) -> None:
    resources = _resources(problems=["api was OOM-killed", "cpu-worker restarted 2 time(s)"])

    verdict = perf.check_budget(_budget(), resources, _report())

    assert verdict["passed"] is False
    assert any("OOM-killed" in f for f in verdict["failures"])
    assert any("restarted" in f for f in verdict["failures"])


def test_a_measured_service_missing_from_the_report_fails(perf) -> None:
    resources = _resources(services={})

    verdict = perf.check_budget(_budget(), resources, _report())

    assert verdict["passed"] is False
    assert "service_not_measured:api" in verdict["failures"]


def test_unapproved_budget_fails(perf) -> None:
    verdict = perf.check_budget(_budget() | {"status": "measured"}, _resources(), _report())

    assert verdict["passed"] is False
    assert any(f.startswith("budget_not_approved") for f in verdict["failures"])


def test_a_missing_report_fails_and_is_never_a_pass(perf, tmp_path: Path) -> None:
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps(_budget()), encoding="utf-8")
    resources = tmp_path / "resources.json"
    resources.write_text(json.dumps(_resources()), encoding="utf-8")
    missing = tmp_path / "does-not-exist.json"

    assert perf.main(
        ["--budget", str(budget), "--resources", str(resources), "--report", str(missing),
         "--output", str(tmp_path / "out.json")]
    ) == 2


def test_main_writes_a_verdict_and_returns_nonzero_on_breach(perf, tmp_path: Path) -> None:
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps(_budget()), encoding="utf-8")
    resources = tmp_path / "resources.json"
    resources.write_text(
        json.dumps(
            _resources(
                services={
                    "api": {
                        "peak_memory_bytes": 999,
                        "peak_cpu_percent": 1.0,
                        "restart_count": 0,
                        "oom_killed": False,
                        "running": True,
                        "exit_code": 0,
                    }
                }
            )
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    output = tmp_path / "perf-budget.json"

    rc = perf.main(
        ["--budget", str(budget), "--resources", str(resources), "--report", str(report),
         "--output", str(output)]
    )

    assert rc == 1
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["passed"] is False
    assert any(b["metric"] == "memory_bytes" for b in written["breaches"])


def test_the_real_budget_file_is_approved_and_lists_the_pending_service(perf) -> None:
    budget = json.loads(
        (ROOT / "tests" / "cpu-stage" / "resource-budget.json").read_text(encoding="utf-8")
    )
    assert budget["status"] == "approved"
    # D2: no baseline exists yet for these services; none may be invented.
    for name in ("supabase-realtime", "payment-gateway"):
        entry = budget["services"][name]
        assert entry["pending_baseline"] is True, name
        assert entry["pending_baseline_reason"], name
        measured = [k for k in entry if k.startswith(("baseline_", "budget_", "browser_"))]
        assert all(entry[key] is None for key in measured), name
