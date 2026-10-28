#!/usr/bin/env python3
"""Fail when the root CI workflow drifts from the nested workflow it mirrors.

The git repository root is one level above this monorepo, and GitHub only runs
workflows from ``<git root>/.github/workflows``. The nested
``.github/workflows/phase-0-safety.yml`` stays the contract the API tests pin;
``<git root>/.github/workflows/production-readiness.yml`` is the copy CI
executes. They may differ only in how paths reach the monorepo:

* the root copy runs from ``motionly-main/Motionly-main`` (``defaults.run``), so
  paths outside ``run:`` (cache keys, artifacts, step ``working-directory``)
  carry that prefix;
* ``actions/setup-node`` gets the lockfile path for its pnpm cache;
* the secret scan mounts ``${{ github.workspace }}`` instead of ``$PWD``;
* triggers, ``concurrency`` and ``defaults`` belong to each copy.

Every job id, ``needs``, ``if``, ``services``, runner, environment, step name,
``uses``, ``with`` and ``run`` must otherwise match. The adapted root copies of
``supply-chain.yml`` and ``deploy-aws.yml`` are reported for information only.

Usage::

    python scripts/verify/workflow_parity.py [--nested FILE] [--root FILE]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

MONOREPO = Path(__file__).resolve().parents[2]
GIT_ROOT = MONOREPO.parents[1]
PREFIX = "motionly-main/Motionly-main/"
NESTED = MONOREPO / ".github" / "workflows" / "phase-0-safety.yml"
ROOT_COPY = GIT_ROOT / ".github" / "workflows" / "production-readiness.yml"
INFORMATIONAL = ("supply-chain.yml", "deploy-aws.yml")

JOB_KEYS = (
    "name",
    "needs",
    "if",
    "services",
    "runs-on",
    "timeout-minutes",
    "environment",
    "env",
    "strategy",
)
STEP_KEYS = ("name", "id", "if", "uses", "run", "env", "with", "working-directory")


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as True.
    data.pop(True, None)
    data.pop("on", None)
    return data


def _normalise(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(PREFIX, "").replace("${{ github.workspace }}", "$PWD")
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalise(item) for key, item in value.items()}
    return value


def _step(step: dict[str, Any], uses_pnpm_cache_key: bool) -> dict[str, Any]:
    picked = {key: _normalise(step[key]) for key in STEP_KEYS if key in step}
    with_ = picked.get("with")
    if (
        uses_pnpm_cache_key
        and isinstance(with_, dict)
        and with_.get("cache") == "pnpm"
        and with_.get("cache-dependency-path") == "pnpm-lock.yaml"
    ):
        picked["with"] = {k: v for k, v in with_.items() if k != "cache-dependency-path"}
    return picked


def _job(job: dict[str, Any], *, root_copy: bool) -> dict[str, Any]:
    picked = {key: _normalise(job[key]) for key in JOB_KEYS if key in job}
    picked["steps"] = [_step(step, root_copy) for step in job.get("steps", [])]
    return picked


def compare(nested: dict[str, Any], root: dict[str, Any]) -> list[str]:
    """Return human-readable differences that are not allowed adaptations."""
    problems: list[str] = []
    for key in ("env", "permissions"):
        if _normalise(nested.get(key)) != _normalise(root.get(key)):
            problems.append(f"workflow {key} differs")
    nested_jobs = nested.get("jobs", {})
    root_jobs = root.get("jobs", {})
    for job_id in sorted(set(nested_jobs) - set(root_jobs)):
        problems.append(f"job {job_id} is missing from the root copy")
    for job_id in sorted(set(root_jobs) - set(nested_jobs)):
        problems.append(f"job {job_id} exists only in the root copy")
    for job_id in sorted(set(nested_jobs) & set(root_jobs)):
        left = _job(nested_jobs[job_id], root_copy=False)
        right = _job(root_jobs[job_id], root_copy=True)
        for key in JOB_KEYS:
            if left.get(key) != right.get(key):
                problems.append(f"job {job_id}: {key} differs")
        left_steps, right_steps = left["steps"], right["steps"]
        if len(left_steps) != len(right_steps):
            problems.append(
                f"job {job_id}: {len(left_steps)} steps nested, {len(right_steps)} in root"
            )
            continue
        for index, (a, b) in enumerate(zip(left_steps, right_steps, strict=True)):
            if a != b:
                label = a.get("name") or a.get("uses") or f"#{index + 1}"
                fields = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
                problems.append(f"job {job_id}: step {label!r} differs in {', '.join(fields)}")
    return problems


def informational(nested_dir: Path, root_dir: Path) -> list[str]:
    notes: list[str] = []
    for name in INFORMATIONAL:
        nested_path, root_path = nested_dir / name, root_dir / name
        if not (nested_path.exists() and root_path.exists()):
            continue
        nested_jobs = set(_load(nested_path).get("jobs", {}))
        root_jobs = set(_load(root_path).get("jobs", {}))
        if nested_jobs != root_jobs:
            notes.append(
                f"{name}: jobs only nested {sorted(nested_jobs - root_jobs)}, "
                f"only root {sorted(root_jobs - nested_jobs)}"
            )
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--nested", type=Path, default=NESTED)
    parser.add_argument("--root", type=Path, default=ROOT_COPY)
    args = parser.parse_args(argv)

    if not args.root.exists():
        print(
            f"workflow parity: not applicable ({args.root} does not exist; "
            "this checkout has no wrapper repository root)"
        )
        return 0
    problems = compare(_load(args.nested), _load(args.root))
    for note in informational(args.nested.parent, args.root.parent):
        print(f"workflow parity (info): {note}")
    if problems:
        print(f"workflow parity: {args.root.name} drifted from {args.nested.name}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"workflow parity: {args.root.name} matches {args.nested.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
