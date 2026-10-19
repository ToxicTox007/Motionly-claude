#!/usr/bin/env python3
"""Record D2 baselines from a passed ``pnpm verify --stage browser`` run.

Copies each service's peak memory and CPU under browser load
(test-results/cpu-stage/resources.json) and the suite's navigation timings
(test-results/browser/report.json) into tests/cpu-stage/resource-budget.json.
Budgets are never set here: they stay null until the user approves them
(Decision D2), and an approved file is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUDGET = ROOT / "tests" / "cpu-stage" / "resource-budget.json"
RESOURCES = ROOT / "test-results" / "cpu-stage" / "resources.json"
REPORT = ROOT / "test-results" / "browser" / "report.json"


class RecordError(RuntimeError):
    pass


def record(budget: dict, resources: dict, report: dict, *, date: str, machine: str) -> dict:
    if budget.get("status") == "approved":
        raise RecordError("budgets are approved; record new baselines only with a new D2 decision")
    if report.get("problems"):
        raise RecordError(f"the browser run has problems: {report['problems'][:3]}")
    if resources.get("size") != budget.get("size"):
        raise RecordError(f"size {resources.get('size')!r} does not match {budget.get('size')!r}")
    updated = json.loads(json.dumps(budget))
    for name, entry in updated["services"].items():
        measured = resources["services"].get(name, {})
        entry["browser_peak_memory_bytes"] = measured.get("peak_memory_bytes")
        entry["browser_peak_cpu_percent"] = measured.get("peak_cpu_percent")
    updated["browser"] = {
        "wall_seconds": report["wall_seconds"],
        "projects": report["projects"],
        "navigation": report["navigation"],
        "budget_navigation_load_p95_ms": None,
        "budget_wall_seconds": None,
    }
    sampling = resources.get("sampling", {})
    updated["browser_measurement"] = {
        "source": [
            "test-results/cpu-stage/resources.json",
            "test-results/browser/report.json",
        ],
        "date": date,
        "machine": machine,
        "scenario": "the browser suite in five setups against the slim stack",
        "sample_rounds": sampling.get("sample_rounds"),
        "duration_seconds": sampling.get("duration_seconds"),
    }
    return updated


def _scaled(value: float, factor: float, *, decimals: int = 0) -> float:
    """value x factor, rounded up (budgets never come out below the factor)."""
    scale = 10**decimals
    result = math.ceil(round(value * factor * scale, 6)) / scale
    return int(result) if decimals == 0 else result


def approve(
    budget: dict, *, factor: float, exclude_navigation: list[str], reason: str, date: str
) -> dict:
    """Set every budget to factor x the higher of the startup and browser-load
    baselines (Decision D2). Navigation budgets are the p95 load time of each
    setup except the excluded ones, which are recorded with the reason."""
    if budget.get("status") == "approved":
        raise RecordError("budgets are already approved")
    if "browser" not in budget:
        raise RecordError("record browser baselines first (pnpm verify --stage browser)")
    approved = json.loads(json.dumps(budget))
    for entry in approved["services"].values():
        memory = max(
            entry.get("baseline_peak_memory_bytes") or 0,
            entry.get("browser_peak_memory_bytes") or 0,
        )
        cpu = max(
            entry.get("baseline_peak_cpu_percent") or 0, entry.get("browser_peak_cpu_percent") or 0
        )
        entry["budget_memory_bytes"] = _scaled(memory, factor)
        entry["budget_cpu_percent"] = _scaled(cpu, factor, decimals=1)
    browser = approved["browser"]
    browser["budget_navigation_load_p95_ms"] = {
        project: _scaled(stats["load_ms"]["p95"], factor)
        for project, stats in browser["navigation"]["by_project"].items()
        if project not in exclude_navigation and "load_ms" in stats
    }
    browser["budget_wall_seconds"] = _scaled(browser["wall_seconds"], factor)
    browser["excluded_navigation"] = [
        {"project": project, "reason": reason} for project in exclude_navigation
    ]
    approved["status"] = "approved"
    approved["approval"] = {"decision": "D2", "date": date, "factor": factor}
    return approved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--machine", default=f"{platform.node()}, {platform.machine()}")
    parser.add_argument("--approve", type=float, metavar="FACTOR", help="set budgets (D2)")
    parser.add_argument("--exclude-navigation", action="append", default=[], metavar="SETUP")
    parser.add_argument("--reason", default="", help="why the excluded setups are excluded")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.approve:
        try:
            updated = approve(
                json.loads(BUDGET.read_text(encoding="utf-8")),
                factor=args.approve,
                exclude_navigation=args.exclude_navigation,
                reason=args.reason,
                date=time.strftime("%Y-%m-%d", time.gmtime()),
            )
        except RecordError as error:
            print(f"record baselines: {error}", file=sys.stderr)
            return 1
        BUDGET.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        print(f"record baselines: approved budgets (x{args.approve}) in {BUDGET.relative_to(ROOT)}")
        return 0
    try:
        updated = record(
            json.loads(BUDGET.read_text(encoding="utf-8")),
            json.loads(RESOURCES.read_text(encoding="utf-8")),
            json.loads(REPORT.read_text(encoding="utf-8")),
            date=time.strftime("%Y-%m-%d", time.gmtime()),
            machine=args.machine,
        )
    except (RecordError, FileNotFoundError) as error:
        print(f"record baselines: {error}", file=sys.stderr)
        return 1
    BUDGET.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    print(f"record baselines: wrote browser-load baselines to {BUDGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
