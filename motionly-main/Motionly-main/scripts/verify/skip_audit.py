#!/usr/bin/env python3
"""Fail CPU-stage verification when a test is skipped without a declared reason.

Every skipped test must either:

* carry a ``gpu-stage[<id>]`` reason whose id is declared in
  ``tests/gpu-stage.json`` under ``gpu_stage`` (GPU hardware or GPU-stage
  infrastructure is deferred), or
* match the ``match`` text of an ``optional`` entry in the same manifest (an
  external account or local service the current verify stage did not enable).

Anything else (missing ffmpeg, missing cv2, a bare ``skip: true``, ...) is an
unexpected skip and fails the run, so a CI image without a required tool can
never pass silently. A stage that enables an optional check passes
``--require <id>``; a skip of that check is then unexpected too.

Usage::

    python scripts/verify/skip_audit.py --manifest tests/gpu-stage.json \\
        --pytest test-results/junit/api.xml --node test-results/junit/web.xml ...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

GPU_STAGE_REASON = re.compile(r"^gpu-stage\[([a-z0-9][a-z0-9-]*)\]:\s*\S")


@dataclass(frozen=True)
class Skip:
    source: str
    test_id: str
    reason: str


def parse_junit(path: Path, *, source: str) -> list[Skip]:
    """Return every skipped test case recorded in a JUnit XML report."""
    root = ElementTree.parse(path).getroot()
    skips: list[Skip] = []
    for case in root.iter("testcase"):
        skipped = case.find("skipped")
        if skipped is None:
            continue
        reason = (skipped.get("message") or skipped.text or "").strip()
        classname = case.get("classname") or case.get("file") or ""
        test_id = f"{classname}::{case.get('name', '')}"
        skips.append(Skip(source=source, test_id=test_id, reason=reason))
    return skips


def _declared(skip: Skip, manifest: dict, required: set[str]) -> bool:
    match = GPU_STAGE_REASON.match(skip.reason)
    if match:
        gpu_ids = {entry["id"] for entry in manifest.get("gpu_stage", [])}
        return match.group(1) in gpu_ids
    return any(
        entry["match"] in skip.reason and entry["id"] not in required
        for entry in manifest.get("optional", [])
    )


def unexpected_skips(
    skips: list[Skip], manifest: dict, required: set[str] | frozenset[str] = frozenset()
) -> list[Skip]:
    optional_ids = {entry["id"] for entry in manifest.get("optional", [])}
    unknown = set(required) - optional_ids
    if unknown:
        raise ValueError(f"unknown optional check(s): {', '.join(sorted(unknown))}")
    return [skip for skip in skips if not _declared(skip, manifest, set(required))]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pytest", type=Path, action="append", default=[])
    parser.add_argument("--node", type=Path, action="append", default=[])
    parser.add_argument("--summary", type=Path, help="write a JSON summary here")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="OPTIONAL_ID",
        help="an optional check this stage enables; skipping it fails the audit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    reports = [(path, "pytest") for path in args.pytest] + [(path, "node") for path in args.node]
    if not reports:
        print("skip audit: no JUnit reports were provided", file=sys.stderr)
        return 2
    skips: list[Skip] = []
    for path, source in reports:
        if not path.exists():
            print(f"skip audit: report not found: {path}", file=sys.stderr)
            return 2
        skips.extend(parse_junit(path, source=source))
    try:
        unexpected = unexpected_skips(skips, manifest, set(args.require))
    except ValueError as error:
        print(f"skip audit: {error}", file=sys.stderr)
        return 2
    summary = {
        "reports": [str(path) for path, _ in reports],
        "required": sorted(args.require),
        "skipped": len(skips),
        "declared": len(skips) - len(unexpected),
        "unexpected": [skip.__dict__ for skip in unexpected],
    }
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"skip audit: {summary['skipped']} skipped, {summary['declared']} declared, "
        f"{len(unexpected)} unexpected"
    )
    for skip in unexpected:
        print(f"  UNEXPECTED [{skip.source}] {skip.test_id}: {skip.reason!r}", file=sys.stderr)
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
