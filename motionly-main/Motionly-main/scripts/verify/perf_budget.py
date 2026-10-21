#!/usr/bin/env python3
"""Enforce the approved CPU-stage resource and performance budgets (Decision D2).

The ``perf`` verify stage runs this against the reports the earlier stages
write:

* ``test-results/cpu-stage/resources.json`` — per-service peak memory and CPU
  and container health from the stack and browser stages
  (:mod:`scripts.verify.resource_sampler`);
* ``test-results/browser/report.json`` — the browser suite's per-setup page-load
  p95 and wall-clock time (:mod:`scripts.verify.browser_report`).

Rules (from tests/cpu-stage/resource-budget.json and Decision D2):

* A service's peak memory or CPU over its approved budget is a breach.
* Restarts, OOM kills and failed one-shots fail the run regardless of budgets
  (they are the ``problems`` the sampler already found, re-checked here).
* A service marked ``pending_baseline`` (e.g. Supabase Realtime from Task 25)
  has no budget yet: it is reported as *pending*, never silently passed, and it
  never fails the run until a baseline is measured and re-approved.
* A per-setup page-load p95 over its navigation budget is a breach; the wall
  time over budget is a breach. Setups listed in ``browser.excluded_navigation``
  (Firefox) have no page-load budget and are reported as excluded.
* A missing or unreadable report is a failure, not a pass. A budget file that is
  not ``status: approved`` fails: the perf stage enforces approved budgets only.

Exit status is 0 only when the run *passed* (no breaches, no health problems,
budgets approved and every report present). Pending services do not change the
status by themselves. Writes a machine-readable verdict to
``test-results/cpu-stage/perf-budget.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUDGET = ROOT / "tests" / "cpu-stage" / "resource-budget.json"
RESOURCES = ROOT / "test-results" / "cpu-stage" / "resources.json"
REPORT = ROOT / "test-results" / "browser" / "report.json"
OUTPUT = ROOT / "test-results" / "cpu-stage" / "perf-budget.json"


class BudgetError(RuntimeError):
    """A report is missing, unreadable, or the budget is not usable."""


def _load(path: Path, label: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BudgetError(f"{label} is missing: {path}") from error
    except (ValueError, OSError) as error:
        raise BudgetError(f"{label} is invalid ({path}): {error}") from error


def check_budget(budget: dict, resources: dict, report: dict) -> dict:
    """Judge one perf run. Pure: no I/O, so the unit tests feed synthetic
    reports. Returns a verdict dict with ``passed`` and the detail lists."""
    failures: list[str] = []
    breaches: list[dict] = []
    pending: list[dict] = []
    excluded: list[dict] = []
    checked: list[str] = []

    if budget.get("status") != "approved":
        failures.append(f"budget_not_approved:{budget.get('status')!r}")
    if budget.get("schema_version") != 1:
        failures.append("budget_schema_invalid")

    # Container health always fails a run, regardless of budgets.
    for problem in resources.get("problems", []) or []:
        failures.append(f"container_health:{problem}")

    # The stack size the reports came from must match the approved budget size.
    if resources.get("size") and resources.get("size") != budget.get("size"):
        failures.append(
            f"size_mismatch:{resources.get('size')!r}!={budget.get('size')!r}"
        )

    measured = resources.get("services", {})
    for name, entry in (budget.get("services") or {}).items():
        if entry.get("pending_baseline"):
            pending.append(
                {
                    "service": name,
                    "reason": entry.get("pending_baseline_reason", "pending baseline"),
                }
            )
            continue
        service = measured.get(name)
        if service is None:
            failures.append(f"service_not_measured:{name}")
            continue
        checked.append(name)
        mem = service.get("peak_memory_bytes")
        cpu = service.get("peak_cpu_percent")
        mem_budget = entry.get("budget_memory_bytes")
        cpu_budget = entry.get("budget_cpu_percent")
        if not _is_number(mem):
            failures.append(f"memory_missing:{name}")
        elif _is_number(mem_budget) and mem > mem_budget:
            breaches.append(
                {"service": name, "metric": "memory_bytes", "value": mem, "budget": mem_budget}
            )
        if not _is_number(cpu):
            failures.append(f"cpu_missing:{name}")
        elif _is_number(cpu_budget) and cpu > cpu_budget:
            breaches.append(
                {"service": name, "metric": "cpu_percent", "value": cpu, "budget": cpu_budget}
            )

    # Page-load p95 and wall time from the browser report.
    browser_budget = budget.get("browser", {})
    excluded_setups = {
        item["project"] for item in browser_budget.get("excluded_navigation", []) or []
    }
    for item in browser_budget.get("excluded_navigation", []) or []:
        excluded.append({"project": item["project"], "reason": item.get("reason", "")})

    nav_budgets = browser_budget.get("budget_navigation_load_p95_ms") or {}
    by_project = report.get("navigation", {}).get("by_project", {})
    for project, limit in nav_budgets.items():
        if project in excluded_setups:
            continue
        load = by_project.get(project, {}).get("load_ms", {}).get("p95")
        if not _is_number(load):
            failures.append(f"navigation_p95_missing:{project}")
        elif _is_number(limit) and load > limit:
            breaches.append(
                {
                    "setup": project,
                    "metric": "navigation_load_p95_ms",
                    "value": load,
                    "budget": limit,
                }
            )
        else:
            checked.append(f"navigation:{project}")

    wall_budget = browser_budget.get("budget_wall_seconds")
    wall = report.get("wall_seconds")
    if _is_number(wall_budget):
        if not _is_number(wall):
            failures.append("wall_seconds_missing")
        elif wall > wall_budget:
            breaches.append(
                {"metric": "wall_seconds", "value": wall, "budget": wall_budget}
            )
        else:
            checked.append("wall_seconds")

    # Container-health problems the browser report also collects.
    for problem in report.get("problems", []) or []:
        failures.append(f"browser_problem:{problem}")

    passed = not failures and not breaches
    return {
        "schema_version": 1,
        "passed": passed,
        "failures": failures,
        "breaches": breaches,
        "pending": pending,
        "excluded": excluded,
        "checked": sorted(set(checked)),
    }


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def run(
    budget_path: Path = BUDGET,
    resources_path: Path = RESOURCES,
    report_path: Path = REPORT,
    output_path: Path | None = OUTPUT,
) -> dict:
    budget = _load(budget_path, "resource budget")
    resources = _load(resources_path, "stack/browser resources report")
    report = _load(report_path, "browser report")
    verdict = check_budget(budget, resources, report)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    return verdict


def _print(verdict: dict) -> None:
    for entry in verdict["pending"]:
        print(f"perf budget: PENDING {entry['service']} — {entry['reason']}")
    for entry in verdict["excluded"]:
        print(f"perf budget: excluded {entry['project']} — {entry['reason']}")
    for breach in verdict["breaches"]:
        subject = breach.get("service") or breach.get("setup") or "stack"
        print(
            f"perf budget: BREACH {subject} {breach['metric']} "
            f"{breach['value']} > {breach['budget']}",
            file=sys.stderr,
        )
    for failure in verdict["failures"]:
        print(f"perf budget: FAIL {failure}", file=sys.stderr)
    checked = ", ".join(verdict["checked"]) or "nothing"
    print(
        f"perf budget: {'PASS' if verdict['passed'] else 'FAIL'} "
        f"({len(verdict['checked'])} budgets within limit: {checked})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--budget", type=Path, default=BUDGET)
    parser.add_argument("--resources", type=Path, default=RESOURCES)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        verdict = run(args.budget, args.resources, args.report, args.output)
    except BudgetError as error:
        print(f"perf budget: FAIL {error}", file=sys.stderr)
        return 2
    _print(verdict)
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
