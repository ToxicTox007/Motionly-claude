"""Starter-set tooling CLI (Task 28, D3).

    python -m app.ops.music_starter_set template [--count N] [--out PATH]
    python -m app.ops.music_starter_set checklist [--out PATH]
    python -m app.ops.music_starter_set validate <manifest.json>

``template`` writes an EMPTY candidate manifest (every field present but blank).
``checklist`` writes the per-track sign-off checklist (covers every planner
mood). ``validate`` structurally checks a filled candidate manifest and reports
which planner moods are still uncovered. None of these import anything or touch
a provider — D3 forbids importing a real track until the user signs off.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.services.music_starter_set import (
    MINIMUM_CANDIDATES,
    build_signoff_checklist,
    empty_manifest_template,
    validate_candidate_manifest,
)


def _run_template(args: list[str]) -> int:
    count = MINIMUM_CANDIDATES
    out: str | None = None
    i = 0
    while i < len(args):
        if args[i] == "--count" and i + 1 < len(args):
            count = int(args[i + 1])
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out = args[i + 1]
            i += 2
        else:
            print(f"unknown argument {args[i]!r}", file=sys.stderr)
            return 2
    content = json.dumps(empty_manifest_template(count), indent=2) + "\n"
    if out:
        Path(out).write_text(content, encoding="utf-8")
        print(f"Wrote empty manifest template ({count} slots) to {out}")
    else:
        print(content, end="")
    return 0


def _run_checklist(args: list[str]) -> int:
    out: str | None = None
    i = 0
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out = args[i + 1]
            i += 2
        else:
            print(f"unknown argument {args[i]!r}", file=sys.stderr)
            return 2
    content = build_signoff_checklist()
    if out:
        Path(out).write_text(content, encoding="utf-8")
        print(f"Wrote sign-off checklist to {out}")
    else:
        print(content, end="")
    return 0


def _run_validate(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: ... validate <manifest.json>", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read manifest {args[0]}: {exc}", file=sys.stderr)
        return 2
    result = validate_candidate_manifest(manifest)
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if result.missing_moods:
        print(
            "MISSING MOOD COVERAGE: " + ", ".join(sorted(result.missing_moods)),
            file=sys.stderr,
        )
    if not result.meets_minimum:
        print(
            f"Only {result.entry_count} candidate(s); gather at least "
            f"{MINIMUM_CANDIDATES}.",
            file=sys.stderr,
        )
    if result.ok and result.meets_minimum:
        print(f"OK: {result.entry_count} candidates, every planner mood covered.")
        return 0
    return 1


def run(argv: list[str]) -> int:
    if not argv:
        print("usage: ... {template|checklist|validate} ...", file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "template":
        return _run_template(rest)
    if command == "checklist":
        return _run_checklist(rest)
    if command == "validate":
        return _run_validate(rest)
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
