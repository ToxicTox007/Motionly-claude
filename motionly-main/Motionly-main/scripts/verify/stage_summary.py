#!/usr/bin/env python3
"""Write a Markdown summary of a ``pnpm verify`` run for the CI job summary.

Reads ``test-results/verify/summary.json`` (written by scripts/verify/verify.mjs)
and, when present, the browser suite's Playwright JSON report and the k6
summary, and prints one table per stage plus the test counts. CI appends the
output to ``$GITHUB_STEP_SUMMARY`` so a failed browser or k6 run is visible on
the run page even when the staging channel does not block on it
(scripts/release/release_channel.py).

Usage::

    python scripts/verify/stage_summary.py [--title TITLE] [--channel CHANNEL]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "test-results"


def _status(step: dict) -> str:
    if step.get("skipped_after_failure"):
        return "not run (earlier step failed)"
    code = step.get("exit_code")
    if code == 0:
        return "passed"
    if code == 124:
        return "FAILED (timed out)"
    return f"FAILED (exit {code})"


def _browser_counts(results: Path) -> str | None:
    report = results / "browser" / "results.json"
    if not report.is_file():
        return None
    stats = json.loads(report.read_text(encoding="utf-8")).get("stats", {})
    keys = ("expected", "unexpected", "flaky", "skipped", "interrupted")
    return ", ".join(f"{key} {stats.get(key, 0)}" for key in keys)


def _k6_checks(results: Path) -> str | None:
    summary = results / "k6-summary.json"
    if not summary.is_file():
        return None
    metrics = json.loads(summary.read_text(encoding="utf-8")).get("metrics", {})
    # k6's handleSummary data nests each metric's numbers under "values".
    checks = metrics.get("checks", {}).get("values", metrics.get("checks", {}))
    failed = metrics.get("http_req_failed", {}).get("values", metrics.get("http_req_failed", {}))
    return (
        f"checks passed {checks.get('passes', '?')}, failed {checks.get('fails', '?')}; "
        f"http_req_failed rate {failed.get('rate', failed.get('value', '?'))}"
    )


def render(results: Path, *, title: str, channel: str | None) -> str:
    summary_path = results / "verify" / "summary.json"
    lines = [f"## {title}", ""]
    if channel:
        blocking = (
            "advisory (does not block the staging release)" if channel == "staging" else "blocking"
        )
        lines += [f"Release channel: **{channel}**; this job is {blocking}.", ""]
    if not summary_path.is_file():
        lines.append("No verify summary was written (the run stopped before any stage ran).")
        return "\n".join(lines) + "\n"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    lines += [f"Overall result: **{summary.get('result', 'unknown')}**", ""]
    for stage in summary.get("stages", []):
        lines += [
            f"### Stage `{stage.get('name')}`: {stage.get('status')}",
            "",
            "| Step | Result | Seconds |",
            "| --- | --- | ---: |",
        ]
        for step in stage.get("steps", []):
            seconds = round((step.get("duration_ms") or 0) / 1000, 1)
            lines.append(f"| {step.get('name')} | {_status(step)} | {seconds} |")
        lines.append("")
    browser = _browser_counts(results)
    lines.append(
        f"Browser suite: {browser}" if browser else "Browser suite: no Playwright JSON report"
    )
    k6 = _k6_checks(results)
    lines.append(f"k6 smoke: {k6}" if k6 else "k6 smoke: no k6 summary")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--title", default="Verify run")
    parser.add_argument("--channel", default=None)
    parser.add_argument("--results", type=Path, default=RESULTS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    sys.stdout.write(render(args.results, title=args.title, channel=args.channel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
