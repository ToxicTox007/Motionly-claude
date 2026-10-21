#!/usr/bin/env python3
"""Map the CPU-stage Definition of Done to the evidence that proves it.

The final step of ``pnpm verify`` (and the perf CI job). Reads the verify
definition (scripts/verify/stages.json), the last run's result
(test-results/verify/summary.json), the JUnit reports each stage wrote
(test-results/junit/*.xml, test-results/browser/junit.xml), the fixture
registry (fixtures/registry.json), the GPU-stage manifest (tests/gpu-stage.json)
and the open decisions, and writes test-results/cpu-stage-evidence.json:

* every one of the 10 Definition-of-done items, mapped to the verify stage and
  step(s) that prove it and the specific tests (JUnit test ids) under them.
  An item whose proving stage did not run in this environment is ``not_run`` —
  never ``passed``; a stage that ran and passed makes the item ``passed``; a
  stage that ran and failed makes it ``failed``;
* for each fixture-backed capability, what this stage proved (the workflow and
  integration contract through the fixture), what it did NOT prove (the
  production quality or availability of the deferred real capability) and where
  that is validated (the GPU stage or a live check);
* every GPU-stage check not yet run and every optional live check;
* the decisions and their status (D1 Node runtime, D2 budgets, D3 music licences, D4
  commits/CI).

The report is descriptive evidence, not a gate: it never invents a pass. Its
own correctness is unit-tested (apps/api/tests/test_evidence_report.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGES = ROOT / "scripts" / "verify" / "stages.json"
SUMMARY = ROOT / "test-results" / "verify" / "summary.json"
JUNIT_DIR = ROOT / "test-results" / "junit"
BROWSER_JUNIT = ROOT / "test-results" / "browser" / "junit.xml"
REGISTRY = ROOT / "fixtures" / "registry.json"
GPU_MANIFEST = ROOT / "tests" / "gpu-stage.json"
OUTPUT = ROOT / "test-results" / "cpu-stage-evidence.json"

# Each Definition-of-done item: the verify stage that proves it, a human label,
# and the JUnit reports (relative to test-results/) whose tests are the proof.
DOD_ITEMS: list[dict] = [
    {
        "id": 1,
        "statement": "A fresh copy passes every automated check.",
        "proving_stage": "all",
        "junit": [],
        "note": (
            "The whole verify run (every stage) is the proof; fresh-copy.sh checks "
            "committed files, so until the user approves a commit (Decision D4) this "
            "proves the working tree, not a fresh checkout."
        ),
    },
    {
        "id": 2,
        "statement": "The database builds from empty and all migrations apply.",
        "proving_stage": "db",
        "junit": ["junit/db-api.xml"],
    },
    {
        "id": 3,
        "statement": "The full CPU stack starts from empty volumes.",
        "proving_stage": "stack",
        "junit": ["junit/stack-api.xml"],
    },
    {
        "id": 4,
        "statement": (
            "Browser tests pass in Chromium, Firefox, WebKit and phone layouts with no "
            "console errors, no API 5xx and no serious/critical axe issues."
        ),
        "proving_stage": "browser",
        "junit": ["browser/junit.xml"],
    },
    {
        "id": 5,
        "statement": (
            "End to end in CPU-stage mode: auth, editor, uploads, rendering, playback, "
            "downloads, sharing, exports, teams, billing, API access, Realtime, music, "
            "learning, variants."
        ),
        "proving_stage": "browser",
        "junit": ["browser/junit.xml"],
        "note": (
            "Each capability runs against the fixture-backed CPU stage; the fixtures "
            "section records what that does and does not prove."
        ),
    },
    {
        "id": 6,
        "statement": "The fixture-leakage gate passes.",
        "proving_stage": "leakage",
        "junit": ["junit/leakage-api.xml", "junit/leakage-worker.xml"],
    },
    {
        "id": 7,
        "statement": (
            "AWS code passes local/static validation (lint + policy rules, CDN proxy "
            "from the edge contract, deploy-and-rollback rehearsal on local stand-ins)."
        ),
        "proving_stage": "aws",
        "junit": ["junit/aws-api.xml"],
    },
    {
        "id": 8,
        "statement": "Unused TS/Python code and uncalled API routes are detected automatically.",
        "proving_stage": "deadcode",
        "junit": [],
    },
    {
        "id": 9,
        "statement": (
            "Performance and resources are within the approved CPU-stage budgets "
            "(Decision D2)."
        ),
        "proving_stage": "perf",
        "junit": [],
    },
    {
        "id": 10,
        "statement": "This evidence report maps every item to the evidence that proves it.",
        "proving_stage": "perf",
        "junit": [],
        "note": "Produced by this script as the final step of the verify run.",
    },
]

# Which capability each fixture registry entry stands in for, and where the real
# thing is validated. Keyed by registry id.
FIXTURE_VALIDATION: dict[str, str] = {
    "visual-analysis": "GPU stage (gpu-inference-worker) and recorded provider contracts",
    "matte": "GPU stage (matte-worker), gpu-stage[local-audit-matte-smoke]",
    "transcription": "GPU stage / live provider contracts (RUN_LIVE_PROVIDER_CONTRACTS)",
    "edit-planner": "live provider contracts (RUN_LIVE_PROVIDER_CONTRACTS)",
    "stock-video": "live provider contracts (Pexels, Pixabay)",
    "stock-embeddings": "GPU stage / live CLIP embeddings",
    "stem-separation": "GPU stage (demucs-worker)",
    "video-generation": "live Runway Gen-3 provider contract",
    "music": "the licensed catalog import (Task 27/28) with real rights-cleared tracks",
    "music-test-track": "the licensed catalog import with real rights-cleared tracks",
    "fixture-media-in-renders": "real media renders in the media stage and GPU stage",
    "cdn-simulator": "the real CloudFront edge (AWS stage validates the edge contract statically)",
    "edge-test-ca": "the real CloudFront certificate in production",
    "release-rehearsal-aws": "the real release against AWS (S3, SSM, GHCR)",
    "release-rehearsal-placeholder-image": "the real GPU-worker image in the GPU stage",
    "release-rehearsal-signing-key": "GitHub keyless signing in the real release",
    "mock-gateway-in-memory-store": "the PostgREST-backed gateway store on the live stack",
    "mock-payment-gateway": "a real UPI + card gateway in production (demo mode only here)",
    "collaboration-notifier": "real Supabase Realtime broadcast on the live stack",
}

OPEN_DECISIONS: list[dict] = [
    {
        "id": "D1",
        "topic": "Node runtime",
        "status": "approved",
        "detail": (
            "Node 24 LTS approved and implemented: the web and Remotion images use the "
            "digest-pinned node:24-bookworm-slim image, the Python images copy Node 24 "
            "from it, CI pins NODE_VERSION 24, and every package declares engines "
            "node >=24 <25; enforced by apps/api/tests/test_node_runtime_contract.py."
        ),
    },
    {
        "id": "D2",
        "topic": "CPU-stage budgets",
        "status": "approved-with-pending-baselines",
        "detail": (
            "Resource and page-load budgets approved 2026-10-08 at factor 1.25. Still "
            "pending a measured baseline on the reference runner, with no budget until "
            "then: supabase-realtime (Task 25) and payment-gateway (Task 22). The perf "
            "stage reports them as pending, never as passed."
        ),
    },
    {
        "id": "D3",
        "topic": "Music licences",
        "status": "policy-approved-track-signoff-pending",
        "detail": (
            "Licence policy approved 2026-10-20: CC0 1.0, or public domain only when "
            "both the composition and the recording are verified "
            "(apps/api/app/config/music-licenses.json). No real tracks imported; each "
            "starter track needs its own sign-off first "
            "(apps/api/app/config/music-starter-signoff.md). Generated test tracks only."
        ),
    },
    {
        "id": "D4",
        "topic": "Commits / CI",
        "status": "open",
        "detail": (
            "Everything since Task 10 is uncommitted on purpose; fresh-copy.sh and the "
            "real fresh-checkout CI proof cannot certify today's work until the user "
            "approves a commit."
        ),
    },
]


def _status_of_stage(summary: dict, stage_name: str) -> str:
    """passed / failed / not_run for one stage in the last verify run."""
    for stage in summary.get("stages", []):
        if stage.get("name") == stage_name:
            status = stage.get("status")
            if status == "passed":
                return "passed"
            if status == "failed":
                return "failed"
            return "not_run"  # not_implemented or anything else
    return "not_run"


def _overall(summary: dict) -> str:
    result = summary.get("result")
    if result == "passed":
        return "passed"
    if result == "failed":
        return "failed"
    return "not_run"


def _junit_tests(junit_dir: Path, browser_junit: Path, rel_paths: list[str]) -> list[str]:
    """Test ids under the given JUnit reports; empty if the report is absent."""
    tests: list[str] = []
    for rel in rel_paths:
        path = browser_junit if rel == "browser/junit.xml" else junit_dir.parent / rel
        if not path.exists():
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            classname = case.get("classname") or ""
            name = case.get("name") or ""
            tests.append(f"{classname}::{name}" if classname else name)
    return tests


def build_evidence(
    stages: dict,
    summary: dict,
    registry: dict,
    gpu_manifest: dict,
    *,
    junit_dir: Path = JUNIT_DIR,
    browser_junit: Path = BROWSER_JUNIT,
) -> dict:
    """Pure: no writing. Assemble the evidence mapping from already-loaded data."""
    stage_names = {stage["name"] for stage in stages.get("stages", [])}
    overall = _overall(summary)
    # Item 1 ("a fresh copy passes EVERY automated check") is only 'passed' when
    # every stage defined in stages.json ran and passed in this run; if any
    # stage did not run (e.g. stack/browser/aws are not runnable here) it is
    # 'not_run', and a failed stage makes it 'failed'. Never inferred from the
    # run's own 'result', which only reflects the stages that were selected.
    per_stage = {name: _status_of_stage(summary, name) for name in stage_names}
    if per_stage and any(status == "failed" for status in per_stage.values()):
        all_stages_status = "failed"
    elif per_stage and all(status == "passed" for status in per_stage.values()):
        all_stages_status = "passed"
    else:
        all_stages_status = "not_run"

    items = []
    for item in DOD_ITEMS:
        stage = item["proving_stage"]
        if stage == "all":
            status = all_stages_status
        else:
            status = _status_of_stage(summary, stage)
        # Only attach the junit tests when the proving stage actually ran this
        # time; a not_run item must not carry stale evidence from a past run.
        tests = (
            _junit_tests(junit_dir, browser_junit, item.get("junit", []))
            if status in {"passed", "failed"}
            else []
        )
        entry = {
            "id": item["id"],
            "statement": item["statement"],
            "proving_stage": stage,
            "status": status,
            "tests": tests,
        }
        if item.get("note"):
            entry["note"] = item["note"]
        if stage != "all" and stage not in stage_names:
            entry["status"] = "not_run"
            entry["tests"] = []
            entry["warning"] = f"stage {stage!r} is not defined in stages.json"
        items.append(entry)

    cpu_stage_proved = "the workflow and integration contract through the fixture"
    fixtures = []
    for entry in registry.get("entries", []):
        rid = entry.get("id")
        fixtures.append(
            {
                "id": rid,
                "capability": entry.get("stands_in_for", ""),
                "kind": entry.get("kind", ""),
                "cpu_stage_proved": cpu_stage_proved,
                "cpu_stage_did_not_prove": (
                    "the production quality or availability of the real capability"
                ),
                "validated_at": FIXTURE_VALIDATION.get(
                    rid, "a GPU-stage check or live provider contract (see tests/gpu-stage.json)"
                ),
                "production": entry.get("production", ""),
            }
        )

    gpu_not_run = [
        {"id": e["id"], "kind": e["kind"], "reason": e["reason"], "location": e["location"]}
        for e in gpu_manifest.get("gpu_stage", [])
    ]
    optional_not_run = [
        {"id": e["id"], "reason": e["reason"], "enable_env": e["enable_env"]}
        for e in gpu_manifest.get("optional", [])
    ]

    return {
        "schema_version": 1,
        "verify_result": overall,
        "verify_started_at": summary.get("started_at"),
        "verify_finished_at": summary.get("finished_at"),
        "definition_of_done": items,
        "fixture_backed_capabilities": fixtures,
        "gpu_stage_checks_not_run": gpu_not_run,
        "optional_live_checks_not_run": optional_not_run,
        "open_decisions": OPEN_DECISIONS,
    }


def run(
    stages_path: Path = STAGES,
    summary_path: Path = SUMMARY,
    registry_path: Path = REGISTRY,
    gpu_path: Path = GPU_MANIFEST,
    output_path: Path | None = OUTPUT,
    *,
    junit_dir: Path = JUNIT_DIR,
    browser_junit: Path = BROWSER_JUNIT,
) -> dict:
    stages = json.loads(stages_path.read_text(encoding="utf-8"))
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else {"stages": [], "result": None}
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    gpu_manifest = json.loads(gpu_path.read_text(encoding="utf-8"))
    evidence = build_evidence(
        stages,
        summary,
        registry,
        gpu_manifest,
        junit_dir=junit_dir,
        browser_junit=browser_junit,
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stages", type=Path, default=STAGES)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--gpu-manifest", type=Path, default=GPU_MANIFEST)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    evidence = run(
        args.stages, args.summary, args.registry, args.gpu_manifest, args.output
    )
    passed = sum(1 for i in evidence["definition_of_done"] if i["status"] == "passed")
    not_run = sum(1 for i in evidence["definition_of_done"] if i["status"] == "not_run")
    failed = sum(1 for i in evidence["definition_of_done"] if i["status"] == "failed")
    try:
        where = args.output.relative_to(ROOT) if args.output else OUTPUT
    except ValueError:
        where = args.output
    print(
        f"evidence report: {passed} passed, {not_run} not_run, {failed} failed "
        f"of {len(evidence['definition_of_done'])} DoD items -> {where}"
    )
    # Never a gate: a failed stage is surfaced by that stage, not here.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
