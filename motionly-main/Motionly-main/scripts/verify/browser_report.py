#!/usr/bin/env python3
"""Gate and summarise a browser-suite run for ``pnpm verify --stage browser``.

Reads Playwright's JSON results (test-results/browser/results.json) and fails
unless every setup ran tests, none failed unexpectedly or flaked, and every
expected failure (``test.fail``) names the task that fixes it ("Task N: ...").
Skips are left to the skip audit, which reads the JUnit report.

It also summarises the navigation timings each test attaches
(tests/browser/support/fixtures.mjs): time to first byte, DOMContentLoaded and
load for every full page load, per setup and per page. These are the browser
baselines for Decision D2. Writes test-results/browser/report.json.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "test-results" / "browser"
PROJECTS = ["chromium", "firefox", "webkit", "pixel-7", "iphone-14"]
METRICS = ("ttfb_ms", "dom_content_loaded_ms", "load_ms")
TASK_REASON = re.compile(r"^Task \d+:\s*\S")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _specs(suite: dict):
    yield from suite.get("specs", [])
    for child in suite.get("suites", []):
        yield from _specs(child)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _stats(values: list[float]) -> dict:
    return {
        "count": len(values),
        "p50": round(_percentile(values, 0.5)),
        "p95": round(_percentile(values, 0.95)),
        "max": round(max(values)),
    }


def _navigation_samples(test: dict) -> list[dict]:
    samples: list[dict] = []
    for result in test.get("results", []):
        for attachment in result.get("attachments", []):
            if attachment.get("name") == "navigation-timings" and attachment.get("body"):
                samples.extend(json.loads(base64.b64decode(attachment["body"])))
    return samples


def summarize(results: dict, projects: list[str]) -> dict:
    per_project = {
        name: {"tests": 0, "passed": 0, "skipped": 0, "expected_failures": 0, "seconds": 0.0}
        for name in projects
    }
    problems: list[str] = []
    expected_failures: dict[str, str] = {}
    navigation: dict[str, list[dict]] = {}

    for suite in results.get("suites", []):
        for spec in _specs(suite):
            for test in spec.get("tests", []):
                project = test["projectName"]
                entry = per_project.setdefault(
                    project,
                    {"tests": 0, "passed": 0, "skipped": 0, "expected_failures": 0, "seconds": 0.0},
                )
                entry["tests"] += 1
                entry["seconds"] += (
                    sum(r.get("duration", 0) for r in test.get("results", [])) / 1000
                )
                status = test["status"]
                fail_marks = [a for a in test.get("annotations", []) if a.get("type") == "fail"]
                if status == "skipped":
                    entry["skipped"] += 1
                elif status == "unexpected":
                    problems.append(f"{project}: {spec['title']} failed")
                elif status == "flaky":
                    problems.append(f"{project}: {spec['title']} is flaky")
                elif fail_marks:
                    entry["expected_failures"] += 1
                    reason = fail_marks[0].get("description", "")
                    expected_failures[spec["title"]] = reason
                    if not TASK_REASON.match(reason):
                        problems.append(
                            f"{project}: {spec['title']} is expected to fail"
                            " without naming its task"
                        )
                else:
                    entry["passed"] += 1
                navigation.setdefault(project, []).extend(_navigation_samples(test))

    for name in projects:
        if per_project[name]["tests"] == 0:
            problems.append(f"{name}: no tests ran")
    unknown = sorted(set(per_project) - set(projects))
    problems.extend(f"{name}: not a configured setup" for name in unknown)

    by_path: dict[str, list[dict]] = {}
    for samples in navigation.values():
        for sample in samples:
            by_path.setdefault(UUID.sub(":id", sample["path"]), []).append(sample)

    def metric_stats(samples: list[dict]) -> dict:
        return {
            metric: _stats([s[metric] for s in samples if isinstance(s.get(metric), (int, float))])
            for metric in METRICS
            if any(isinstance(s.get(metric), (int, float)) for s in samples)
        }

    for entry in per_project.values():
        entry["seconds"] = round(entry["seconds"], 1)
    return {
        "schema_version": 1,
        "wall_seconds": round(results.get("stats", {}).get("duration", 0) / 1000, 1),
        "projects": per_project,
        "expected_failures": [
            {"test": test, "reason": reason} for test, reason in expected_failures.items()
        ],
        "navigation": {
            "by_project": {
                project: metric_stats(samples) for project, samples in navigation.items() if samples
            },
            "by_path": {path: metric_stats(samples) for path, samples in sorted(by_path.items())},
        },
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=RESULTS / "results.json")
    parser.add_argument("--output", type=Path, default=RESULTS / "report.json")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.results.exists():
        print(f"browser report: no results at {args.results}", file=sys.stderr)
        return 2
    summary = summarize(json.loads(args.results.read_text(encoding="utf-8")), PROJECTS)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for name, entry in summary["projects"].items():
        load = summary["navigation"]["by_project"].get(name, {}).get("load_ms")
        timing = f"  load p50 {load['p50']} ms / p95 {load['p95']} ms" if load else ""
        print(
            f"browser report: {name:10} {entry['passed']} passed, "
            f"{entry['expected_failures']} expected failures, {entry['skipped']} skipped, "
            f"{entry['seconds']} s{timing}"
        )
    for failure in summary["expected_failures"]:
        print(f"browser report: expected failure: {failure['test']} ({failure['reason']})")
    for problem in summary["problems"]:
        print(f"browser report: PROBLEM {problem}", file=sys.stderr)
    return 1 if summary["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
