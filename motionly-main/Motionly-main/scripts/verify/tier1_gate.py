#!/usr/bin/env python3
"""Tier 1 gate: the neutrality proof of plan Task 27, as one executable check.

Runs the Task 27 required checks as written in the plan:

1. ``vendor_neutrality.py --enforce`` (no ACTIVE scanner finding);
2. no vendor environment variable or secret name in apps/api/app, apps/web,
   apps/worker, infra, .github and scripts;
3. the old payload keys are not written by apps/api/app, apps/web or
   packages/shared;
4. no vendor SDK import in apps/api/app;
5. the capability, gateway and deployment test selection passes.

The text checks read the files ``git ls-files --cached --others
--exclude-standard`` lists (so ignored build output such as node_modules is not
source), skip binary files, and exclude ``*.lock``, ``__pycache__`` and the
gate's own pattern files, as the plan's ``grep`` excludes the scanner.

Every finding is classified. ``gated:task19-item2`` means the finding names a
vendor listed in ``tier1-gated-items.json`` in a file listed there: Task 19
item 2 material that is deleted only after its mandatory live run (Task 17).
Anything else is ``unexpected``.

Exit codes: 0 when every check passes (the Tier 1 gate is met); 2 when the only
failures are gated findings; 1 when any unexpected finding or failure exists.

    python scripts/verify/tier1_gate.py [--json PATH] [--skip-pytest]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
VERIFY_DIR = Path("scripts") / "verify"
GATED_ITEMS = VERIFY_DIR / "tier1-gated-items.json"
GATED = "gated:task19-item2"
UNEXPECTED = "unexpected"

ENV_PATTERN = re.compile(r"RUNWAY|OPENROUTER|GROQ|JINA|GOOGLE_VISION|PYANNOTEAI|LALAL")
ENV_DIRS = ("apps/api/app", "apps/web", "apps/worker", "infra", ".github", "scripts")
PAYLOAD_PATTERN = re.compile(r'runway_task_id|runway_gateway_route|provider="runway"')
PAYLOAD_DIRS = ("apps/api/app", "apps/web", "packages/shared")
SDK_PATTERN = re.compile(r"^\s*(from|import) (openai|anthropic)\b")
SDK_DIRS = ("apps/api/app",)
PYTEST_SELECTION = "inference or gateway or capability or gpu_free or compose"
# The pattern files of the scanner and of this gate (the plan's grep excludes
# vendor_neutrality.py for the same reason).
SELF_FILES = frozenset(
    {
        "scripts/verify/vendor_neutrality.py",
        "scripts/verify/tier1_gate.py",
        "scripts/verify/tier1-gated-items.json",
    }
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    token: str
    classification: str


@dataclass
class CheckResult:
    name: str
    command: str
    findings: list[Finding] = field(default_factory=list)
    failed: bool = False  # a non-finding failure (for example a test run)
    detail: str = ""

    @property
    def unexpected(self) -> list[Finding]:
        return [f for f in self.findings if f.classification == UNEXPECTED]

    @property
    def gated(self) -> list[Finding]:
        return [f for f in self.findings if f.classification == GATED]

    @property
    def status(self) -> str:
        if self.failed or self.unexpected:
            return "FAIL"
        if self.gated:
            return "GATED"
        return "PASS"


@dataclass(frozen=True)
class GatedItems:
    vendors: frozenset[str]
    env_tokens: frozenset[str]
    paths: tuple[str, ...]

    def covers_path(self, path: str) -> bool:
        return any(fnmatch(path, pattern) for pattern in self.paths)


def load_gated_items(root: Path) -> GatedItems:
    path = root / GATED_ITEMS
    if not path.exists():
        return GatedItems(frozenset(), frozenset(), ())
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("paths", []):
        if not str(entry.get("reason", "")).strip():
            raise ValueError(f"gated item without a reason: {entry!r}")
    return GatedItems(
        vendors=frozenset(data.get("vendors", [])),
        env_tokens=frozenset(data.get("env_tokens", [])),
        paths=tuple(str(entry["path"]) for entry in data.get("paths", [])),
    )


def _scanner(root: Path) -> ModuleType:
    script = root / VERIFY_DIR / "vendor_neutrality.py"
    if not script.exists():
        script = ROOT / VERIFY_DIR / "vendor_neutrality.py"
    name = "motionly_vendor_neutrality_gate"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _source_files(root: Path, scanner: ModuleType, dirs: tuple[str, ...]) -> list[str]:
    files = []
    for rel in scanner.list_files(root):
        if not any(rel == d or rel.startswith(d.rstrip("/") + "/") for d in dirs):
            continue
        if rel in SELF_FILES or rel.endswith(".lock") or "/__pycache__/" in f"/{rel}":
            continue
        files.append(rel)
    return files


def _grep(
    root: Path, files: list[str], pattern: re.Pattern[str]
) -> list[tuple[str, int, str]]:
    matches: list[tuple[str, int, str]] = []
    for rel in files:
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue  # binary, as grep -I
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for match in pattern.finditer(line):
                matches.append((rel, number, match.group(0)))
    return matches


def check_scanner(root: Path, scanner: ModuleType, gated: GatedItems) -> CheckResult:
    allow = scanner.load_allowlist(root / VERIFY_DIR / scanner.ALLOWLIST_FILENAME)
    result = scanner.scan(root, allow)
    findings = [
        Finding(
            item.path,
            item.line,
            item.vendor,
            GATED
            if item.vendor in gated.vendors and gated.covers_path(item.path)
            else UNEXPECTED,
        )
        for item in result.by_category(scanner.ACTIVE)
    ]
    return CheckResult(
        "vendor-neutrality scan (--enforce)",
        "python scripts/verify/vendor_neutrality.py --enforce",
        findings,
    )


def check_env_names(root: Path, scanner: ModuleType, gated: GatedItems) -> CheckResult:
    files = _source_files(root, scanner, ENV_DIRS)
    findings = [
        Finding(
            path,
            line,
            token,
            GATED if token in gated.env_tokens and gated.covers_path(path) else UNEXPECTED,
        )
        for path, line, token in _grep(root, files, ENV_PATTERN)
    ]
    return CheckResult(
        "vendor environment and secret names",
        "grep -RInE 'RUNWAY|OPENROUTER|GROQ|JINA|GOOGLE_VISION|PYANNOTEAI|LALAL' "
        + " ".join(ENV_DIRS),
        findings,
    )


def check_payload_keys(root: Path, scanner: ModuleType) -> CheckResult:
    files = _source_files(root, scanner, PAYLOAD_DIRS)
    findings = [
        Finding(path, line, token, UNEXPECTED)
        for path, line, token in _grep(root, files, PAYLOAD_PATTERN)
    ]
    return CheckResult(
        "old payload keys",
        "grep -RInE 'runway_task_id|runway_gateway_route|provider=\"runway\"' "
        + " ".join(PAYLOAD_DIRS),
        findings,
    )


def check_sdk_imports(root: Path, scanner: ModuleType) -> CheckResult:
    files = [f for f in _source_files(root, scanner, SDK_DIRS) if f.endswith(".py")]
    findings = [
        Finding(path, line, token.strip(), UNEXPECTED)
        for path, line, token in _grep(root, files, SDK_PATTERN)
    ]
    return CheckResult(
        "vendor SDK imports",
        "grep -RInE '^\\s*(from|import) (openai|anthropic)\\b' apps/api/app",
        findings,
    )


def check_pytest(root: Path) -> CheckResult:
    api = root / "apps" / "api"
    python = api / ".venv" / "bin" / "python"
    command = [
        str(python if python.exists() else Path(sys.executable)),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-k",
        PYTEST_SELECTION,
    ]
    completed = subprocess.run(
        command, cwd=api, capture_output=True, text=True, env=_test_env()
    )
    tail = (completed.stdout.strip().splitlines() or [""])[-1]
    return CheckResult(
        "capability, gateway and deployment tests",
        f"(cd apps/api && python -m pytest -k '{PYTEST_SELECTION}')",
        failed=completed.returncode != 0,
        detail=tail,
    )


def _test_env() -> dict[str, str]:
    import os

    environment = dict(os.environ)
    environment["APP_ENV"] = "test"
    return environment


def run_gate(root: Path, *, skip_pytest: bool = False) -> list[CheckResult]:
    scanner = _scanner(root)
    gated = load_gated_items(root)
    results = [
        check_scanner(root, scanner, gated),
        check_env_names(root, scanner, gated),
        check_payload_keys(root, scanner),
        check_sdk_imports(root, scanner),
    ]
    if skip_pytest:
        results.append(
            CheckResult(
                "capability, gateway and deployment tests",
                "skipped (--skip-pytest)",
                failed=True,
                detail="not run; the gate cannot pass without it",
            )
        )
    else:
        results.append(check_pytest(root))
    return results


def exit_code(results: list[CheckResult]) -> int:
    if any(r.failed or r.unexpected for r in results):
        return 1
    if any(r.gated for r in results):
        return 2
    return 0


def render(results: list[CheckResult]) -> str:
    lines = ["Tier 1 gate (plan Task 27)", "=" * 60]
    for result in results:
        lines.append(
            f"{result.status:5} {result.name}: {len(result.unexpected)} unexpected, "
            f"{len(result.gated)} gated" + (f" — {result.detail}" if result.detail else "")
        )
    for result in results:
        if not result.findings:
            continue
        lines.append("")
        lines.append(f"{result.name}  [{result.command}]")
        for finding in sorted(
            result.findings, key=lambda f: (f.classification, f.path, f.line, f.token)
        ):
            lines.append(
                f"  {finding.classification:18} {finding.path}:{finding.line}:{finding.token}"
            )
    code = exit_code(results)
    verdict = {
        0: "PASS: the Tier 1 neutrality gate is met.",
        2: "BLOCKED: only Task 19 item 2 material remains (waits for the Task 17 live runs).",
        1: "FAIL: unexpected findings or failures.",
    }[code]
    lines += ["", verdict]
    return "\n".join(lines)


def to_json(results: list[CheckResult]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "exit_code": exit_code(results),
        "checks": [
            {
                "name": r.name,
                "command": r.command,
                "status": r.status,
                "detail": r.detail,
                "findings": [asdict(f) for f in r.findings],
            }
            for r in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", metavar="PATH", help="write the evidence as JSON")
    parser.add_argument("--root", default=None, help="repository root (default: auto-detect)")
    parser.add_argument(
        "--skip-pytest", action="store_true", help="skip the test selection (never passes)"
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else ROOT
    results = run_gate(root, skip_pytest=args.skip_pytest)
    print(render(results))
    if args.json:
        Path(args.json).write_text(
            json.dumps(to_json(results), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
