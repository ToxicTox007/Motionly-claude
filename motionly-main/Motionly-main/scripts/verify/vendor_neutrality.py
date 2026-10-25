#!/usr/bin/env python3
"""Vendor-neutrality ratchet scanner (plan Task 2).

Scans runtime, config, UI, infra and test source for the audited vendor names
and for direct vendor SDK imports, and classifies every match as one of:

* ``ACTIVE``       -- a finding that must be removed (fails ``--enforce``).
* ``ALLOWLISTED``  -- matched by ``vendor-neutrality-allowlist.txt`` (docs, lock
  files, the scanner's own files, or an explicit adapter entry); printed with
  its reason.
* ``HISTORICAL``   -- a match in ``supabase/migrations/`` whose timestamp prefix
  is at or before the recorded cutoff; these are immutable applied migrations
  and are reported separately.

Matching is token based, not ``\\b`` regex: ``\\b`` fails on ``snake_case`` and
``CamelCase``. Each line is split into identifier tokens on non-alphanumerics,
then CamelCase and letter/digit boundaries are split, lowercased, and matched as
token *sequences* so ``open`` + ``router`` matches ``OpenRouter`` but ``jinja``
and ``groqish`` (single tokens) do not match ``jina`` / ``groq``.

Registry JSON files (``infra/ai-registry/**`` and ``docs/ai-registry/**`` with a
top-level ``version`` plus one of ``adapters``/``routes``/``models``) are parsed
per field: vendor tokens are configuration data only under ``model_id``,
``base_url``, ``provider``, the offering ``display_name`` and ``description``
display fields (plan Section 2.11), each element of a
``mapping.artifacts.allowed_hosts`` list (operator-configured artifact hosts,
like ``base_url``), and anywhere inside a ``data_processor`` object; in ``id``,
``adapter_id``, ``model_revision``, object keys and every other field they are
ACTIVE. Registry findings carry a JSON pointer.

Standard library only. Run from the repository root.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

# ---------------------------------------------------------------------------
# Audited vendors (data). Each entry is one or more token *sequences*; a line
# matches a vendor when any of its sequences appears as consecutive tokens.
# ``pyannote+ai`` matches pyannoteAI / api.pyannote.ai but NOT bare ``pyannote``
# (the local model id handled by Task 14).
# ---------------------------------------------------------------------------
VENDORS: dict[str, list[tuple[str, ...]]] = {
    "runway": [("runway",)],
    "openrouter": [("openrouter",), ("open", "router")],
    "groq": [("groq",)],
    "jina": [("jina",), ("jina", "ai")],
    "google-vision": [("google", "vision"), ("google", "cloud", "vision")],
    "pyannoteai": [("pyannoteai",), ("pyannote", "ai")],
    "lalal": [("lalal",)],
}

# Vendor SDK import rule: direct imports of these packages in Python under
# apps/api/app are ACTIVE findings regardless of token matching.
SDK_IMPORT_PACKAGES = ("openai", "anthropic")
SDK_IMPORT_ROOT = "apps/api/app"

# File-set fallback excludes (used only when git is unavailable).
WALK_EXCLUDES = {
    "node_modules",
    ".venv",
    ".git",
    "__pycache__",
    ".next",
    "dist",
    "build",
    "coverage",
    "test-results",
    ".remotion-bundle",
}

MAX_FILE_BYTES = 2 * 1024 * 1024  # ~2 MB

# Registry fields whose *values* may legitimately carry vendor/model names.
# ``model_id``/``base_url``/``provider`` identify the operator's chosen model and
# endpoint; ``display_name``/``description`` are the operator's user-facing
# offering display data (plan Section 2.11, Task 29) and, like ``data_processor``
# display data, may name a model or vendor.
REGISTRY_VALUE_ALLOWED_KEYS = {
    "model_id",
    "base_url",
    "provider",
    "display_name",
    "description",
}
# Keys whose *list* values are configuration data (each scalar element is
# allowed, like base_url). Today only ``mapping.artifacts.allowed_hosts``: a
# provider's artifact hosts are operator configuration, exactly like base_url
# (Changelog rev 4 item 8). The key name is specific enough that it does not
# appear elsewhere in a registry document; a list of objects under it would
# still be scanned, because only scalar elements inherit the allowance.
REGISTRY_ALLOWED_LIST_VALUE_KEYS = {"allowed_hosts"}
# Any value anywhere inside an object under this key is allowed.
REGISTRY_ALLOWED_SUBTREE_KEY = "data_processor"

MIGRATIONS_PREFIX = "supabase/migrations/"
ALLOWLIST_FILENAME = "vendor-neutrality-allowlist.txt"
BASELINE_FILENAME = "vendor-neutrality-baseline.json"
SCANNER_FILENAME = "vendor_neutrality.py"
SCANNER_TEST_FILENAME = "test_vendor_neutrality_scanner.py"

ACTIVE = "ACTIVE"
ALLOWLISTED = "ALLOWLISTED"
HISTORICAL = "HISTORICAL"


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    """Split a line into lowercased identifier tokens.

    Non-alphanumeric characters separate tokens; CamelCase boundaries and
    letter/digit boundaries split further. ``RunwayGen3Provider`` becomes
    ``[runway, gen, 3, provider]`` and ``google-vision`` becomes
    ``[google, vision]``.
    """
    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            tokens.append("".join(current).lower())
            current.clear()

    chars = list(text)
    n = len(chars)
    for i, ch in enumerate(chars):
        if not ch.isalnum():
            flush()
            continue
        if current:
            prev = chars[i - 1]
            nxt = chars[i + 1] if i + 1 < n else ""
            prev_is_lower = prev.islower()
            prev_is_digit = prev.isdigit()
            prev_is_alpha = prev.isalpha()
            prev_is_upper = prev.isupper()
            cur_is_upper = ch.isupper()
            cur_is_digit = ch.isdigit()
            cur_is_alpha = ch.isalpha()
            # lower/digit -> Upper   (openRouter -> open|Router)
            if cur_is_upper and (prev_is_lower or prev_is_digit):
                flush()
            # acronym end: UPPER before Upper-then-lower (AIDiarization -> AI|Diarization)
            elif cur_is_upper and prev_is_upper and nxt.islower():
                flush()
            # letter <-> digit boundary (gen3 -> gen|3, v3 -> v|3)
            elif (cur_is_digit and prev_is_alpha) or (cur_is_alpha and prev_is_digit):
                flush()
        current.append(ch)
    flush()
    return tokens


def _sequence_matches(tokens: list[str], seq: tuple[str, ...]) -> list[int]:
    """Return the start indices where ``seq`` appears consecutively in tokens."""
    hits: list[int] = []
    n = len(seq)
    if n == 0:
        return hits
    for i in range(len(tokens) - n + 1):
        if tuple(tokens[i : i + n]) == seq:
            hits.append(i)
    return hits


def match_vendors(text: str) -> list[tuple[str, str]]:
    """Return ``(vendor, matched_token_text)`` pairs found in ``text``.

    ``matched_token_text`` is the sequence joined with ``+`` (e.g. ``open+router``)
    for reporting. One result per vendor at most (deduplicated, sorted).
    """
    tokens = tokenize(text)
    found: dict[str, str] = {}
    for vendor, sequences in VENDORS.items():
        for seq in sequences:
            if _sequence_matches(tokens, seq):
                found.setdefault(vendor, "+".join(seq))
                break
    return sorted(found.items())


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    vendor: str
    token: str
    category: str
    reason: str
    pointer: str = ""  # JSON pointer for registry findings

    def sort_key(self) -> tuple:
        return (self.path, self.line, self.pointer, self.vendor)

    def describe(self) -> str:
        loc = f"{self.path}:{self.line}"
        if self.pointer:
            loc += f" ({self.pointer})"
        return f"{loc} [{self.token}] {self.reason}"


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
@dataclass
class AllowlistEntry:
    glob: str
    category: str
    reason: str
    expires: str = ""


@dataclass
class Allowlist:
    entries: list[AllowlistEntry] = field(default_factory=list)
    migration_cutoff: str = ""

    def match(self, rel_path: str) -> AllowlistEntry | None:
        for entry in self.entries:
            if fnmatch(rel_path, entry.glob):
                return entry
        return None

    def exact(self, rel_path: str) -> AllowlistEntry | None:
        """An entry naming exactly this path (no wildcard), if any."""
        for entry in self.entries:
            if entry.glob == rel_path and not any(ch in entry.glob for ch in "*?["):
                return entry
        return None


def load_allowlist(path: Path) -> Allowlist:
    """Parse the ``<glob> | <category> | <reason> [| expires:<date>]`` format.

    ``cutoff: <prefix>`` records the historical-migration timestamp cutoff.
    Lines beginning with ``#`` and blank lines are ignored.
    """
    allow = Allowlist()
    if not path.exists():
        return allow
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("cutoff:"):
            allow.migration_cutoff = line.split(":", 1)[1].strip()
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            raise ValueError(f"allowlist entry needs <glob> | <category> | <reason>: {raw!r}")
        glob, category, reason = parts[0], parts[1], parts[2]
        if not reason:
            raise ValueError(f"allowlist entry missing a reason: {raw!r}")
        expires = ""
        for extra in parts[3:]:
            if extra.lower().startswith("expires:"):
                expires = extra.split(":", 1)[1].strip()
        allow.entries.append(AllowlistEntry(glob, category, reason, expires))
    return allow


# ---------------------------------------------------------------------------
# File set
# ---------------------------------------------------------------------------
def list_files(root: Path) -> list[str]:
    """Repo-relative file list via ``git ls-files`` with a walk fallback."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        files = [line for line in out.stdout.splitlines() if line]
        if files:
            return sorted(set(files))
    except (OSError, subprocess.CalledProcessError):
        pass
    return _walk_files(root)


def _walk_files(root: Path) -> list[str]:
    results: list[str] = []
    for dirpath, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = [d for d in dirnames if d not in WALK_EXCLUDES]
        for name in filenames:
            full = Path(dirpath) / name
            results.append(str(full.relative_to(root)))
    return sorted(set(results))


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return True
    return False


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------
def _migration_prefix(rel_path: str) -> str | None:
    """Return the leading numeric timestamp of a migration filename, or None."""
    if not rel_path.startswith(MIGRATIONS_PREFIX):
        return None
    name = rel_path[len(MIGRATIONS_PREFIX) :]
    name = name.split("/")[-1]
    digits: list[str] = []
    for ch in name:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    return "".join(digits) if digits else None


def classify(
    rel_path: str,
    line: int,
    vendor: str,
    token: str,
    allow: Allowlist,
    pointer: str = "",
) -> Finding:
    """Classify a single vendor match. Registry field precedence is applied by
    the caller (registry findings arrive already decided as ACTIVE)."""
    # Historical migrations: at or before the cutoff -> HISTORICAL, else ACTIVE.
    prefix = _migration_prefix(rel_path)
    if prefix is not None:
        cutoff = allow.migration_cutoff
        if cutoff and prefix <= cutoff:
            return Finding(
                rel_path,
                line,
                vendor,
                token,
                HISTORICAL,
                f"historical applied migration (prefix {prefix} <= cutoff {cutoff})",
                pointer,
            )
        # A newer migration is ACTIVE unless the allowlist names that exact file
        # (plan Changelog rev 4 item 7). Globs never cover migrations.
        exact = allow.exact(rel_path)
        if exact is not None:
            return Finding(
                rel_path,
                line,
                vendor,
                token,
                ALLOWLISTED,
                f"{exact.category}: {exact.reason}",
                pointer,
            )
        return Finding(
            rel_path,
            line,
            vendor,
            token,
            ACTIVE,
            f"migration newer than cutoff {cutoff or '(unset)'}",
            pointer,
        )

    entry = allow.match(rel_path)
    if entry is not None:
        return Finding(
            rel_path,
            line,
            vendor,
            token,
            ALLOWLISTED,
            f"{entry.category}: {entry.reason}",
            pointer,
        )
    return Finding(rel_path, line, vendor, token, ACTIVE, "active finding", pointer)


# ---------------------------------------------------------------------------
# Registry detection and per-field scan
# ---------------------------------------------------------------------------
REGISTRY_DIRS = ("infra/ai-registry/", "docs/ai-registry/")


def _is_registry_candidate(rel_path: str) -> bool:
    return rel_path.endswith(".json") and any(rel_path.startswith(d) for d in REGISTRY_DIRS)


def _parse_registry(text: str) -> dict | None:
    try:
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    if "version" not in doc:
        return None
    if not any(k in doc for k in ("adapters", "routes", "models")):
        return None
    return doc


def _escape_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def scan_registry(
    rel_path: str, doc: dict, allow: Allowlist, line_lookup
) -> list[Finding]:
    """Walk a parsed registry document; emit ACTIVE findings for vendor tokens.

    ``value_ok`` is true for a scalar directly under ``model_id``/``base_url``/
    ``provider``, and for each scalar element of a list under ``allowed_hosts``
    (``mapping.artifacts.allowed_hosts`` is operator configuration, like
    ``base_url``; Changelog rev 4 item 8). ``in_processor`` is true for
    everything inside a ``data_processor`` object (the whole subtree is
    configuration data). Object keys are always scanned like code, except inside
    ``data_processor``.
    """
    findings: list[Finding] = []

    def emit(needle: str, pointer: str, what: str) -> None:
        for vendor, token in match_vendors(needle):
            findings.append(
                Finding(
                    rel_path,
                    line_lookup(needle),
                    vendor,
                    token,
                    ACTIVE,
                    f"vendor token in registry {what} {pointer}",
                    pointer,
                )
            )

    def walk(node, pointer: str, in_processor: bool, value_ok: bool) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                child_ptr = f"{pointer}/{_escape_pointer(str(key))}"
                if not in_processor:
                    emit(str(key), child_ptr, "key")
                next_in_processor = in_processor or key == REGISTRY_ALLOWED_SUBTREE_KEY
                child_value_ok = (
                    in_processor
                    or key in REGISTRY_VALUE_ALLOWED_KEYS
                    or key in REGISTRY_ALLOWED_LIST_VALUE_KEYS
                )
                walk(val, child_ptr, next_in_processor, child_value_ok)
        elif isinstance(node, list):
            # A list under an allowed value-list key (e.g. mapping.artifacts.
            # allowed_hosts) carries configuration data in each element, so the
            # element's ``value_ok`` is inherited from the list (Changelog rev 4
            # item 8). Every other list clears it, so a list of objects is still
            # scanned like code.
            for idx, item in enumerate(node):
                item_value_ok = value_ok and not isinstance(item, (dict, list))
                walk(item, f"{pointer}/{idx}", in_processor, item_value_ok)
        else:
            if in_processor or value_ok:
                return
            emit(str(node), pointer, "field")

    walk(doc, "", False, False)
    return findings


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------
@dataclass
class ScanResult:
    findings: list[Finding]
    scanned: int
    skipped_binary: int
    skipped_large: int
    skipped_missing: int

    def by_category(self, category: str) -> list[Finding]:
        return [f for f in self.findings if f.category == category]

    def active_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.by_category(ACTIVE):
            counts[f.path] = counts.get(f.path, 0) + 1
        return counts


def _sdk_import_hit(rel_path: str, stripped: str) -> bool:
    if not rel_path.startswith(SDK_IMPORT_ROOT) or not rel_path.endswith(".py"):
        return False
    s = stripped.lstrip()
    for pkg in SDK_IMPORT_PACKAGES:
        if s.startswith(f"from {pkg} ") or s.startswith(f"from {pkg}."):
            return True
        if s == f"import {pkg}" or s.startswith(f"import {pkg} ") or s.startswith(f"import {pkg}."):
            return True
        if s.startswith(f"import {pkg},") or s.startswith(f"from {pkg},"):
            return True
    return False


def scan_file(root: Path, rel_path: str, allow: Allowlist) -> tuple[list[Finding], str]:
    """Scan one file. Returns (findings, status) where status is one of
    ``ok``, ``binary``, ``large``, ``missing``."""
    full = root / rel_path
    try:
        size = full.stat().st_size
    except OSError:
        return [], "missing"
    if size > MAX_FILE_BYTES:
        return [], "large"
    try:
        data = full.read_bytes()
    except OSError:
        return [], "missing"
    if _looks_binary(data):
        return [], "binary"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")

    lines = text.splitlines()

    # Registry documents are scanned per field (precedence over docs allowance).
    if _is_registry_candidate(rel_path):
        doc = _parse_registry(text)
        if doc is not None:
            def line_lookup(needle: str, _lines=lines) -> int:
                for i, ln in enumerate(_lines, start=1):
                    if needle and needle in ln:
                        return i
                return 1

            return scan_registry(rel_path, doc, allow, line_lookup), "ok"
        # Non-registry JSON falls through to normal directory rules.

    findings: list[Finding] = []
    for lineno, line in enumerate(lines, start=1):
        for vendor, token in match_vendors(line):
            findings.append(classify(rel_path, lineno, vendor, token, allow))
        if _sdk_import_hit(rel_path, line):
            findings.append(
                classify(rel_path, lineno, "sdk-import", "import", allow)
            )
    return findings, "ok"


def scan(root: Path, allow: Allowlist) -> ScanResult:
    files = list_files(root)
    all_findings: list[Finding] = []
    scanned = 0
    skipped_binary = skipped_large = skipped_missing = 0
    for rel in files:
        findings, status = scan_file(root, rel, allow)
        if status == "binary":
            skipped_binary += 1
            continue
        if status == "large":
            skipped_large += 1
            continue
        if status == "missing":
            skipped_missing += 1
            continue
        scanned += 1
        all_findings.extend(findings)
    all_findings.sort(key=lambda f: f.sort_key())
    return ScanResult(
        findings=all_findings,
        scanned=scanned,
        skipped_binary=skipped_binary,
        skipped_large=skipped_large,
        skipped_missing=skipped_missing,
    )


# ---------------------------------------------------------------------------
# Baseline (ratchet)
# ---------------------------------------------------------------------------
def build_baseline(result: ScanResult) -> dict:
    counts = result.active_counts()
    return {
        "schema_version": 1,
        "description": (
            "Per-file ACTIVE vendor-neutrality counts. The ratchet fails if any "
            "file's count rises or a new file gains findings. Regenerate with "
            "scripts/verify/vendor_neutrality.py --update-baseline."
        ),
        "active_counts": {path: counts[path] for path in sorted(counts)},
    }


def load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    return dict(doc.get("active_counts", {}))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _top_level_area(rel_path: str) -> str:
    parts = rel_path.split("/")
    if len(parts) >= 2:
        return "/".join(parts[:2]) if parts[0] in {"apps", "packages", "infra"} else parts[0]
    return parts[0]


def render_report(result: ScanResult) -> str:
    out: list[str] = []
    out.append("Vendor-neutrality scan")
    out.append("=" * 60)
    out.append(
        f"scanned {result.scanned} files; skipped "
        f"{result.skipped_binary} binary, {result.skipped_large} large (>2MB), "
        f"{result.skipped_missing} missing"
    )
    out.append("")

    active = result.by_category(ACTIVE)
    allowlisted = result.by_category(ALLOWLISTED)
    historical = result.by_category(HISTORICAL)

    out.append("Summary by category:")
    out.append(f"  ACTIVE      {len(active)}")
    out.append(f"  ALLOWLISTED {len(allowlisted)}")
    out.append(f"  HISTORICAL  {len(historical)}")
    out.append("")

    area_counts: Counter[str] = Counter()
    for f in active:
        area_counts[_top_level_area(f.path)] += 1
    if area_counts:
        out.append("ACTIVE findings by area:")
        for area in sorted(area_counts):
            out.append(f"  {area}: {area_counts[area]}")
        out.append("")

    def dump(title: str, items: list[Finding]) -> None:
        out.append(f"{title} ({len(items)}):")
        for f in items:
            out.append(f"  {f.describe()}")
        out.append("")

    dump("ACTIVE", active)
    dump("ALLOWLISTED", allowlisted)
    dump("HISTORICAL MIGRATION", historical)
    return "\n".join(out)


def result_to_json(result: ScanResult) -> dict:
    return {
        "schema_version": 1,
        "scanned": result.scanned,
        "skipped": {
            "binary": result.skipped_binary,
            "large": result.skipped_large,
            "missing": result.skipped_missing,
        },
        "counts": {
            ACTIVE: len(result.by_category(ACTIVE)),
            ALLOWLISTED: len(result.by_category(ALLOWLISTED)),
            HISTORICAL: len(result.by_category(HISTORICAL)),
        },
        "findings": [
            {
                "path": f.path,
                "line": f.line,
                "pointer": f.pointer,
                "vendor": f.vendor,
                "token": f.token,
                "category": f.category,
                "reason": f.reason,
            }
            for f in result.findings
        ],
    }


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
def run_ratchet(result: ScanResult, baseline: dict[str, int]) -> tuple[int, str]:
    counts = result.active_counts()
    risen: list[str] = []
    new_files: list[str] = []
    fell: list[str] = []
    for path in sorted(counts):
        current = counts[path]
        prior = baseline.get(path)
        if prior is None:
            new_files.append(f"  {path}: {current} (new)")
        elif current > prior:
            risen.append(f"  {path}: {prior} -> {current}")
        elif current < prior:
            fell.append(f"  {path}: {prior} -> {current}")
    for path in sorted(baseline):
        if path not in counts:
            fell.append(f"  {path}: {baseline[path]} -> 0 (cleared)")

    lines: list[str] = []
    if fell:
        lines.append("Files whose ACTIVE count fell (run --update-baseline to record):")
        lines.extend(fell)
    if risen or new_files:
        lines.append("Ratchet FAILED: ACTIVE findings increased.")
        if risen:
            lines.append("Files whose count rose:")
            lines.extend(risen)
        if new_files:
            lines.append("New files with findings:")
            lines.extend(new_files)
        return 1, "\n".join(lines)
    lines.insert(0, "Ratchet OK: no file's ACTIVE count rose.")
    return 0, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="print the full report (default)")
    mode.add_argument("--ratchet", action="store_true", help="fail if ACTIVE counts rise")
    mode.add_argument("--update-baseline", action="store_true", help="rewrite the baseline")
    mode.add_argument("--enforce", action="store_true", help="fail if any ACTIVE finding exists")
    parser.add_argument("--json", metavar="PATH", help="write the full result as JSON")
    parser.add_argument("--root", default=None, help="repository root (default: auto-detect)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _repo_root()
    verify_dir = root / "scripts" / "verify"
    allow = load_allowlist(verify_dir / ALLOWLIST_FILENAME)
    result = scan(root, allow)

    if args.json:
        Path(args.json).write_text(
            json.dumps(result_to_json(result), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.update_baseline:
        baseline = build_baseline(result)
        (verify_dir / BASELINE_FILENAME).write_text(
            json.dumps(baseline, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"baseline written: {len(baseline['active_counts'])} files with ACTIVE findings"
        )
        return 0

    if args.enforce:
        active = result.by_category(ACTIVE)
        print(render_report(result))
        if active:
            print(f"\nENFORCE FAILED: {len(active)} ACTIVE finding(s).")
            return 1
        print("\nENFORCE OK: no ACTIVE findings.")
        return 0

    if args.ratchet:
        baseline = load_baseline(verify_dir / BASELINE_FILENAME)
        code, text = run_ratchet(result, baseline)
        print(text)
        return code

    # Default: report (exit 0 always).
    print(render_report(result))
    return 0


def _repo_root() -> Path:
    # scripts/verify/vendor_neutrality.py -> repo root is two levels up.
    here = Path(__file__).resolve()
    return here.parents[2]


if __name__ == "__main__":
    sys.exit(main())
