#!/usr/bin/env python3
"""Fail any API route that no product code calls and that is not an approved
external entry point.

Part of the ``deadcode`` verify stage (Task 32). The API contract
(``packages/shared/openapi.json``) is the single source of truth for the live
route surface. A route is *reachable* when at least one of the following holds:

* a **product caller** references its path — the web app, the worker, the
  renderer, the shared packages, the release/ops scripts, or the API's own
  in-process orchestration callers (Temporal activities, worker-media signing,
  internal service-to-service calls);
* it is an **external entry point** listed by name in
  ``scripts/verify/external-callers.json`` with a reason (health probes,
  metrics scrape, the payment-gateway webhook, the public share/watch/review
  surface, and the Agency API-key surface, which only off-repo API-key clients
  call);
* it is in the **GPU-stage allow-list** (``tests/gpu-stage-allowlist.json``,
  ``kind: api_route``), i.e. a route the CPU stage never exercises but the GPU
  stage does.

Browser specs and the Python/JS test suites are NOT product callers: a route
that only a test touches is dead product surface and must be deleted or
justified. (The route-enforcement inventory still pins the route's role; this
check is specifically about *who in production reaches it*.)

A route that is neither called by product code nor on either allow-list is a
failure: either delete the handler (smallest change, tests updated) or, if a
caller genuinely exists in a form this scanner cannot see, list it in
``external-callers.json`` with a reason.

Exit status is 0 only when every route is reachable. A missing or unreadable
contract/allow-list is a failure, not a pass. Writes a machine-readable verdict
to ``test-results/deadcode/route-callers.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "packages" / "shared" / "openapi.json"
EXTERNAL = ROOT / "scripts" / "verify" / "external-callers.json"
GPU_ALLOWLIST = ROOT / "tests" / "gpu-stage-allowlist.json"
OUTPUT = ROOT / "test-results" / "deadcode" / "route-callers.json"

# Product source roots scanned for call sites. Tests and browser specs are
# excluded by EXCLUDE_DIRS below: they are not product callers.
CALLER_ROOTS = (
    "apps/web/app",
    "apps/web/components",
    "apps/web/lib",
    "apps/web/hooks",
    "apps/web/middleware.ts",
    "apps/worker/src",
    "apps/renderer/src",
    "packages/shared/src",
    "apps/api/app",
    "scripts",
)

# Directory names anywhere in a path that disqualify it as a product caller.
EXCLUDE_DIRS = frozenset(
    {
        "tests",
        "test",
        "__tests__",
        "e2e",
        "browser",
        "node_modules",
        "__pycache__",
        ".next",
        "dist",
        "build",
        "generated",  # packages/shared/src/generated/api.ts is the typed client, not a caller
    }
)

# Only scan source-code files; a .json/.md mention is documentation, not a call.
CALLER_SUFFIXES = frozenset({".ts", ".tsx", ".mjs", ".js", ".jsx", ".py"})

# A test file by name, even outside a tests/ dir (e.g. foo.test.mjs, test_foo.py).
_TEST_FILE = re.compile(r"(^test_.*\.py$)|(\.test\.[mc]?[jt]sx?$)|(\.spec\.[mc]?[jt]sx?$)")


class RouteCallerError(RuntimeError):
    """A contract or allow-list is missing, unreadable, or malformed."""


def _load(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RouteCallerError(f"{label} is missing: {path}") from error
    except (ValueError, OSError) as error:
        raise RouteCallerError(f"{label} is invalid ({path}): {error}") from error


# ---------------------------------------------------------------------------
# Route model
# ---------------------------------------------------------------------------

# A path parameter in an OpenAPI template: {project_id} or {object_path:path}.
_PARAM = re.compile(r"\{[^}]+\}")


def _route_matcher(path: str) -> re.Pattern[str]:
    """Compile an OpenAPI path template into a regex that matches a normalized
    call-site path.

    Literal route segments must match their exact text; a ``{param}`` matches
    one non-slash segment OR the sentinel ``\x00`` (a call-site interpolation,
    e.g. ``${projectId}``); a ``{param:path}`` catch-all matches the remainder.

    Literal segments are matched strictly (not by a sentinel) on purpose: a
    computed final segment like ``${command}`` is resolved in extraction
    (:func:`_expand_dynamic_segments`) into its literal alternatives, so the
    matcher never has to guess which literal a sentinel stands for. This keeps
    the check from falsely marking an uncalled route reachable.
    """
    catch_all = bool(re.search(r"\{[^}]+:path\}", path))
    stripped = re.sub(r"\{[^}]+:path\}.*$", "\x01", path) if catch_all else path

    segments = stripped.split("/")
    pattern_parts: list[str] = []
    for segment in segments:
        if segment == "":
            pattern_parts.append("")
            continue
        if segment == "\x01":
            pattern_parts.append(r"[^?#]*")
            continue
        if _PARAM.fullmatch(segment):
            pattern_parts.append(r"(?:\x00|[^/?#]+)")
        else:
            inner = ""
            last = 0
            for m in _PARAM.finditer(segment):
                inner += re.escape(segment[last : m.start()])
                inner += r"(?:\x00|[^/?#]+)"
                last = m.end()
            inner += re.escape(segment[last:])
            pattern_parts.append(inner)
    pattern = "/".join(pattern_parts)
    return re.compile("^" + pattern + r"(?:[/?#].*)?$")


def load_routes(contract: dict) -> list[tuple[str, str]]:
    """Return sorted ``(METHOD, path)`` pairs from an OpenAPI contract."""
    paths = contract.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise RouteCallerError("OpenAPI contract has no paths")
    routes: set[tuple[str, str]] = set()
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in item:
            upper = method.upper()
            if upper in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                routes.add((upper, path))
    return sorted(routes)


# ---------------------------------------------------------------------------
# Call-site scanning
# ---------------------------------------------------------------------------

# A URL-ish path literal inside source: a run beginning with "/" that looks like
# an API path. We capture the path up to the first quote/backtick/whitespace or
# a template-literal boundary. Template interpolations ``${...}`` are collapsed
# to the sentinel \x00 before matching so they line up with a {param} segment.
SENTINEL = "\x00"

# JS template interpolation ``${...}`` and Python f-string ``{...}``. URL
# interpolations do not nest braces (``${encodeURIComponent(id)}`` has only
# parens), so a non-brace body is sufficient and avoids mis-balancing the rest
# of a source file.
_JS_INTERP = re.compile(r"\$\{[^{}]*\}")
_PY_INTERP = re.compile(r"\{[^{}]*\}")

# A call-site path token: a leading slash followed by path characters, where an
# interpolation ``${...}`` (JS) or ``{...}`` (f-string / OpenAPI-style literal)
# counts as a single segment. We stop at quotes, backticks, whitespace, parens
# and commas — the characters that terminate a path literal in source.
_PATH_TOKEN = re.compile(r"/(?:\$\{[^{}]*\}|\{[^{}]*\}|[A-Za-z0-9_\-/.:?=&%~])*")


def _normalize_call_path(token: str) -> str:
    """Collapse interpolations to the sentinel and strip a query/fragment so a
    call-site path lines up with an OpenAPI template segment-by-segment."""
    token = _JS_INTERP.sub(SENTINEL, token)  # `${projectId}` -> sentinel
    token = _PY_INTERP.sub(SENTINEL, token)  # f-string {project_id} -> sentinel
    token = re.sub("\x00+", SENTINEL, token)  # adjacent interpolations -> one
    token = token.split("?", 1)[0].split("#", 1)[0]
    return token


def _is_test_path(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    return bool(_TEST_FILE.search(path.name))


def iter_caller_files(root: Path, caller_roots=CALLER_ROOTS) -> list[Path]:
    """Every product source file under the caller roots, excluding tests."""
    files: list[Path] = []
    for rel in caller_roots:
        base = root / rel
        if base.is_file():
            if base.suffix in CALLER_SUFFIXES and not _is_test_path(base):
                files.append(base)
            continue
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in CALLER_SUFFIXES:
                continue
            rel_path = path.relative_to(root)
            if _is_test_path(rel_path):
                continue
            files.append(path)
    return files


# A ``NAME = <string|template>`` assignment (JS const/let/var or Python). The
# value is captured so we can pull a path prefix out of it. We only trust the
# first assignment to a name per file (good enough for the base-path pattern).
_ASSIGN = re.compile(
    r"""(?:^|[^.\w])(?:const|let|var)?\s*(?P<name>[A-Za-z_$][\w$]*)\s*=\s*
        (?P<q>[`'"])(?P<val>(?:\\.|(?!(?P=q)).)*)(?P=q)""",
    re.VERBOSE | re.MULTILINE,
)

# A token that starts with an interpolated variable then continues with a
# literal path suffix: ``${NAME}/reset-links`` or ``{NAME}/init``.
_VAR_SUFFIX = re.compile(
    r"\$?\{(?P<name>[A-Za-z_$][\w$]*)\}(?P<suffix>(?:/(?:\$?\{[^{}]*\}|[A-Za-z0-9_\-.:?=&%~]+))*)"
)


def _prefix_from_value(value: str) -> str | None:
    """Pull a normalized leading path prefix out of an assigned string value.

    The value may be a plain literal (``/admin/users``) or a template whose last
    path-looking portion we want (``${env.apiBaseUrl}/projects/${id}/x`` ->
    ``/projects/\x00/x``). Returns ``None`` when no ``/path`` prefix is present.
    """
    # Normalize interpolations to the sentinel, then take the last run that
    # starts at a slash (the API path, after any ${base-url} prefix).
    normalized = _normalize_call_path(value)
    slash = normalized.find("/")
    if slash < 0:
        return None
    candidate = normalized[slash:]
    # A pure "/" or an obviously non-API value is not a useful prefix.
    if len(candidate) < 2:
        return None
    return candidate


def _resolve_prefix_vars(text: str) -> dict[str, str]:
    """Map local variable names to the normalized path prefix they hold."""
    prefixes: dict[str, str] = {}
    for match in _ASSIGN.finditer(text):
        name = match.group("name")
        if name in prefixes:
            continue
        prefix = _prefix_from_value(match.group("val"))
        if prefix is not None:
            prefixes[name] = prefix
    return prefixes


# A ``${...}`` interpolation whose body we can resolve to concrete string
# literal(s): a bare string ``${"ban"}`` or a ternary ``${c ? "ban" : "unban"}``.
_INTERP_BODY = re.compile(r"\$\{(?P<body>[^{}]*)\}")
_STR_LITERAL = re.compile(r"""['"`]([A-Za-z0-9_\-]+)['"`]""")


def _expand_token_alternatives(token: str) -> set[str]:
    """Expand a raw path token into every literal it can resolve to by
    substituting ``${...}`` interpolations whose body is a quoted string or a
    ternary of quoted strings. ``/admin/users/${banned ? "ban" : "unban"}`` ->
    ``{/admin/users/ban, /admin/users/unban}``. Interpolations we cannot
    resolve are left intact (they normalize to a sentinel later)."""
    alternatives = {token}
    for match in _INTERP_BODY.finditer(token):
        body = match.group("body")
        literals = _STR_LITERAL.findall(body)
        if not literals:
            continue
        residue = _STR_LITERAL.sub("", body)
        is_pure_literals = not re.search(r"[A-Za-z0-9_$]", residue)
        is_ternary = "?" in body and ":" in body
        # Expand a bare string (`${"ban"}`) or a ternary whose arms are string
        # literals (`${cond ? "ban" : "unban"}`). Anything else (a bare variable,
        # a function call) is left for sentinel handling.
        if not (is_pure_literals or is_ternary):
            continue
        whole = match.group(0)
        expanded: set[str] = set()
        for alt in alternatives:
            for literal in literals:
                expanded.add(alt.replace(whole, literal, 1))
        alternatives = expanded
    return alternatives


def _blank(text: str, start: int, end: int) -> str:
    """Replace text[start:end] with spaces, keeping newlines (and so line shape)."""
    return text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]


def _strip_python_non_code(text: str) -> str | None:
    """Blank Python comments and statement-level strings (docstrings). Returns
    None when the text is not valid Python, so the caller can fall back."""
    import io
    import tokenize

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return None
    line_starts = [0]
    for line in text.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def offset(position: tuple[int, int]) -> int:
        row, col = position
        return line_starts[row - 1] + col

    skip = {tokenize.NL, tokenize.COMMENT}
    statement_start = {tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
    spans: list[tuple[int, int]] = []
    significant = [t for t in tokens if t.type not in skip]
    for index, token in enumerate(significant):
        if token.type != tokenize.STRING:
            continue
        before = significant[index - 1].type if index else tokenize.ENCODING
        after = (
            significant[index + 1].type if index + 1 < len(significant) else tokenize.ENDMARKER
        )
        if before in statement_start and after in {tokenize.NEWLINE, tokenize.ENDMARKER}:
            spans.append((offset(token.start), offset(token.end)))
    spans += [(offset(t.start), offset(t.end)) for t in tokens if t.type == tokenize.COMMENT]
    for start, end in sorted(spans, reverse=True):
        text = _blank(text, start, end)
    return text


# Comments that start a line. Trailing comments are left alone: telling them
# apart from `//` or `#` inside a string needs a real tokenizer, and a URL in a
# string is exactly what the scanner must keep.
_LINE_COMMENT = re.compile(r"^[ \t]*(?://|#).*$", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"^[ \t]*/\*[\s\S]*?\*/", re.MULTILINE)
_LINE_DOCSTRING = re.compile(r"^[ \t]*[rRuU]?(\"\"\"|''')[\s\S]*?\1", re.MULTILINE)
# A route's own declaration names its path; it is not a call to it.
_ROUTE_DECLARATION = re.compile(
    r"""(?:@\w+\.(?:get|post|put|patch|delete|head|options|api_route|websocket)\(\s*
        |APIRouter\([^()]*?\bprefix\s*=\s*)
        [rRuU]?(?P<q>["'])(?:\\.|(?!(?P=q)).)*(?P=q)""",
    re.VERBOSE,
)


def strip_non_code(text: str, language: str | None = None) -> str:
    """Blank what mentions a path without calling it: comments, docstrings and
    route declarations. ``language`` is "python", "javascript" or None (unknown:
    apply the rules that are safe for both)."""
    if language in (None, "python"):
        stripped = _strip_python_non_code(text)
        if stripped is None:
            for match in reversed(list(_LINE_DOCSTRING.finditer(text))):
                text = _blank(text, match.start(), match.end())
        else:
            text = stripped
    for pattern in (_BLOCK_COMMENT, _LINE_COMMENT, _ROUTE_DECLARATION):
        for match in reversed(list(pattern.finditer(text))):
            text = _blank(text, match.start(), match.end())
    return text


def extract_call_paths(text: str, language: str | None = None) -> set[str]:
    """Normalized API-path literals that appear in one source file.

    Comments, docstrings and route declarations are ignored
    (:func:`strip_non_code`): a handler naming its own path does not call it.

    Handles the common "base-path variable" pattern, where a file assigns a
    path prefix to a local constant and then appends literal suffixes:

        const BASE = "/admin/users";
        fetch(`${env.apiBaseUrl}${BASE}/reset-links`)    # -> /admin/users/reset-links

        const base = `${env.apiBaseUrl}/projects/${id}/broll/library/multipart`;
        editorApiRequest(`${base}/init`)                 # -> /projects/.../multipart/init

    We resolve simple ``NAME = <string>`` assignments whose normalized value is
    a path prefix, then expand any token that begins with ``${NAME}`` /
    ``{NAME}`` using that prefix. Unresolvable composition is simply not matched
    (review catches a false "dead"; it is never a false pass).
    """
    text = strip_non_code(text, language)
    prefixes = _resolve_prefix_vars(text)
    found: set[str] = set()
    for match in _PATH_TOKEN.finditer(text):
        token = match.group(0)
        if len(token) < 2:
            continue
        for alt in _expand_token_alternatives(token):
            found.add(_normalize_call_path(alt))

    # Expand ``${NAME}suffix`` / ``{NAME}suffix`` occurrences into full paths.
    for match in _VAR_SUFFIX.finditer(text):
        name = match.group("name")
        suffix = match.group("suffix") or ""
        prefix = prefixes.get(name)
        if prefix is None:
            continue
        for alt in _expand_token_alternatives(prefix + suffix):
            combined = _normalize_call_path(alt)
            if combined.startswith("/"):
                found.add(combined)
    return found


def scan_callers(root: Path, caller_roots=CALLER_ROOTS) -> set[str]:
    """Every normalized API-path literal found across product source."""
    paths: set[str] = set()
    for file in iter_caller_files(root, caller_roots):
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        language = "python" if file.suffix == ".py" else "javascript"
        paths |= extract_call_paths(text, language)
    return paths


# ---------------------------------------------------------------------------
# Allow-lists
# ---------------------------------------------------------------------------


def load_external(external: dict) -> dict[tuple[str, str], str]:
    """``(METHOD, path) -> reason`` for approved external entry points."""
    entries = external.get("entries")
    if not isinstance(entries, list):
        raise RouteCallerError("external-callers.json has no 'entries' list")
    out: dict[tuple[str, str], str] = {}
    for entry in entries:
        method = str(entry.get("method", "")).upper()
        path = entry.get("path", "")
        reason = (entry.get("reason") or "").strip()
        if not method or not path:
            raise RouteCallerError(f"external-callers entry missing method/path: {entry!r}")
        if not reason:
            raise RouteCallerError(f"external-callers entry for {method} {path} has no reason")
        out[(method, path)] = reason
    return out


def load_gpu_routes(allowlist: dict) -> set[tuple[str, str]]:
    """GPU-stage allow-list entries of kind ``api_route``.

    Each ``locations`` entry may be ``METHOD /path`` or just ``/path`` (any
    method). Returns the concrete ``(METHOD, path)`` pairs; an entry without a
    method is expanded to ``("*", path)`` and matched method-agnostically.
    """
    routes: set[tuple[str, str]] = set()
    for entry in allowlist.get("entries", []):
        if entry.get("kind") != "api_route":
            continue
        for loc in entry.get("locations", []):
            loc = str(loc).strip()
            parts = loc.split(None, 1)
            if len(parts) == 2 and parts[0].upper() in {
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            }:
                routes.add((parts[0].upper(), parts[1]))
            else:
                routes.add(("*", loc))
    return routes


# ---------------------------------------------------------------------------
# Judgement (pure)
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    passed: bool
    total: int
    reachable_by_caller: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    gpu_stage: list[str] = field(default_factory=list)
    dead: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "total_routes": self.total,
            "reachable_by_product_caller": sorted(self.reachable_by_caller),
            "external_entry_points": sorted(self.external),
            "gpu_stage_exempt": sorted(self.gpu_stage),
            "dead_routes": sorted(self.dead),
            "failures": sorted(self.failures),
        }


def _gpu_matches(method: str, path: str, gpu: set[tuple[str, str]]) -> bool:
    return (method, path) in gpu or ("*", path) in gpu


def check_routes(
    routes: list[tuple[str, str]],
    call_paths: set[str],
    external: dict[tuple[str, str], str],
    gpu: set[tuple[str, str]],
) -> Verdict:
    """Judge the route surface. Pure: callers pass in the scanned paths and the
    allow-lists, so the unit tests can plant routes and call sites."""
    verdict = Verdict(passed=True, total=len(routes))
    live = set(routes)

    # An external/GPU allow-list that names a route which no longer exists is a
    # failure: stale allow-lists hide real dead code.
    for key in external:
        if key not in live:
            verdict.failures.append(f"external-callers lists a route that does not exist: {key}")
    for method, path in gpu:
        if method == "*":
            if not any(p == path for _, p in live):
                verdict.failures.append(
                    f"gpu-stage allow-list lists a route path that does not exist: {path}"
                )
        elif (method, path) not in live:
            verdict.failures.append(
                f"gpu-stage allow-list lists a route that does not exist: {method} {path}"
            )

    # Precompile route matchers once.
    matchers = {(m, p): _route_matcher(p) for (m, p) in routes}

    for method, path in routes:
        label = f"{method} {path}"
        if _gpu_matches(method, path, gpu):
            verdict.gpu_stage.append(label)
            continue
        if (method, path) in external:
            verdict.external.append(label)
            continue
        matcher = matchers[(method, path)]
        if any(matcher.match(candidate) for candidate in call_paths):
            verdict.reachable_by_caller.append(label)
            continue
        verdict.dead.append(label)

    if verdict.dead:
        verdict.passed = False
    if verdict.failures:
        verdict.passed = False
    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(root: Path = ROOT) -> Verdict:
    contract = _load(root / "packages" / "shared" / "openapi.json", "OpenAPI contract")
    external_doc = _load(root / "scripts" / "verify" / "external-callers.json", "external-callers")
    gpu_doc = _load(root / "tests" / "gpu-stage-allowlist.json", "gpu-stage allow-list")

    routes = load_routes(contract)
    external = load_external(external_doc)
    gpu = load_gpu_routes(gpu_doc)
    call_paths = scan_callers(root)
    return check_routes(routes, call_paths, external, gpu)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the full verdict as JSON to stdout",
    )
    args = parser.parse_args(argv)

    try:
        verdict = run()
    except RouteCallerError as error:
        print(f"route-callers: {error}", file=sys.stderr)
        return 2

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(verdict.to_dict(), indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2))

    if verdict.failures:
        for failure in sorted(verdict.failures):
            print(f"route-callers: {failure}", file=sys.stderr)
    if verdict.dead:
        print(
            "route-callers: the following live API routes have no product caller "
            "and are not approved external entry points. Delete the handler (and "
            "update its tests) or, if a real caller exists, add it to "
            "scripts/verify/external-callers.json with a reason:",
            file=sys.stderr,
        )
        for route in sorted(verdict.dead):
            print(f"  - {route}", file=sys.stderr)

    if verdict.passed:
        print(
            f"route-callers: all {verdict.total} routes reachable "
            f"({len(verdict.reachable_by_caller)} by product callers, "
            f"{len(verdict.external)} external, {len(verdict.gpu_stage)} GPU-stage).",
        )
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
