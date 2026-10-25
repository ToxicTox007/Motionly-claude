"""Preflight an inference registry before it is filled with real AWS ids (S5).

    python -m app.ops.preflight_inference_registry <file>[,<file>...] \
        [--app-env ENV] [--gpu-workers-enabled] [--json]

The shipped ``infra/ai-registry/registry.json`` is checked into the repository
with ``<PLACEHOLDER>`` tokens (account id, bucket names, the BDA project id, ...)
and the literal pricing marker ``"FILL FROM AWS PRICING PAGE"`` in its numeric
price fields, so it is deliberately *not* a runnable registry yet. This command
is the gate an operator runs after substituting the real values: it

1. scans every JSON string value and object key for placeholder tokens matching
   ``<[A-Z][A-Z0-9_]*>`` and for the exact pricing marker, reporting each with
   its JSON path so the operator knows exactly what is still unfilled;
2. fails (error) anywhere a value *looks like* a real static AWS access key id
   (``(AKIA|ASIA)[A-Z0-9]{16}``) or is otherwise an inlined key/secret rather
   than a ``file:``/``bundle:`` secret reference — a registry must never carry a
   real credential;
3. builds a throwaway *substituted* copy of the document (each placeholder and
   the pricing marker replaced with a syntactically valid dummy) and runs the
   **same** Section 2.6 validation the ``validate_inference_registry`` CLI runs,
   so a schema error is caught even while the real values are still unknown.

Exit codes:

* ``1`` if the substituted document has any schema **error** (from the shared
  validator), or if a value looks like a real AWS key id / inlined secret;
* in **production** (``APP_ENV=production`` or ``--app-env production``) also
  ``1`` if any placeholder token or the pricing marker still remains — a
  production deployment must run a fully filled registry;
* otherwise ``0``, with any remaining placeholders reported as warnings.

Nothing here reads a secret value: the loader reads only secret reference
*names*, and the placeholder scan inspects the file's own JSON strings. This
command never changes the behaviour of :mod:`app.ops.validate_inference_registry`.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from app.services.inference_registry import (
    Diagnostic,
    validate_registry,
)

# A placeholder token is an all-caps ``<NAME>`` such as ``<ACCOUNT_ID>`` or
# ``<STAGING_BUCKET>``. The pricing marker is a separate exact string living in
# the numeric price fields.
PLACEHOLDER_TOKEN_RE = re.compile(r"<[A-Z][A-Z0-9_]*>")
PRICING_MARKER = "FILL FROM AWS PRICING PAGE"
# A value that looks like a real static AWS access key id. A registry must
# reference credentials by ``file:``/``bundle:`` ref, never inline one.
AWS_KEY_ID_RE = re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")

# Per-token syntactically valid dummies for the substituted copy. A token that
# is not listed falls back to :func:`_default_dummy_for_token`.
_PLACEHOLDER_DUMMIES: dict[str, str] = {
    "<ACCOUNT_ID>": "000000000000",
    "<STAGING_BUCKET>": "preflight-staging-bucket",
    "<VIDEO_OUTPUT_BUCKET>": "preflight-video-output-bucket",
    "<BDA_PROJECT_ID>": "preflight-bda-project-id",
}


class PreflightLine:
    """One human-readable preflight finding (not a schema diagnostic)."""

    PLACEHOLDER = "PLACEHOLDER"
    ERROR = "ERROR"
    WARNING = "WARNING"

    def __init__(self, kind: str, path: str, message: str) -> None:
        self.kind = kind
        self.path = path
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path, "message": self.message}

    def render(self) -> str:
        return f"{self.kind} at {self.path}: {self.message}"


def _default_dummy_for_token(token: str) -> str:
    """A generic lowercase dummy for an unknown placeholder token.

    Bucket-ish tokens must stay S3-name-safe (lowercase ``[a-z0-9-]``); this
    derives such a value from the token name so an unexpected ``<NAME>`` still
    substitutes into a syntactically valid document.
    """
    body = token.strip("<>").lower().replace("_", "-")
    body = re.sub(r"[^a-z0-9-]", "-", body)
    return f"preflight-{body}" if body else "preflight-dummy"


def _substitute_scalar(value: object) -> object:
    """Replace placeholders and the pricing marker inside one JSON scalar.

    Strings that are exactly the pricing marker become a small positive price
    number (``0.000001``) so the price fields type-check. Other strings have
    every ``<TOKEN>`` replaced by its dummy. Non-strings pass through unchanged.
    """
    if not isinstance(value, str):
        return value
    if value == PRICING_MARKER:
        return 0.000001

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return _PLACEHOLDER_DUMMIES.get(token, _default_dummy_for_token(token))

    return PLACEHOLDER_TOKEN_RE.sub(_replace, value)


def _substitute_document(node: object) -> object:
    """Return a deep copy of ``node`` with every placeholder/marker substituted."""
    if isinstance(node, dict):
        return {key: _substitute_document(val) for key, val in node.items()}
    if isinstance(node, list):
        return [_substitute_document(item) for item in node]
    return _substitute_scalar(node)


def _walk_strings(node: object, pointer: str, lines: list[PreflightLine]) -> None:
    """Collect placeholder/marker/AWS-key findings from every JSON string.

    Scans both object keys and scalar string values. Placeholder tokens and the
    pricing marker become ``PLACEHOLDER`` lines; a value that matches the AWS key
    id shape becomes an ``ERROR`` line (a registry must reference a secret, never
    inline a key).
    """
    if isinstance(node, dict):
        for key, val in node.items():
            child = f"{pointer}.{key}" if pointer else key
            _scan_string(str(key), f"{child} (key)", lines)
            _walk_strings(val, child, lines)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _walk_strings(item, f"{pointer}[{index}]", lines)
    elif isinstance(node, str):
        _scan_string(node, pointer, lines)


def _scan_string(value: str, path: str, lines: list[PreflightLine]) -> None:
    for token in sorted(set(PLACEHOLDER_TOKEN_RE.findall(value))):
        lines.append(
            PreflightLine(
                PreflightLine.PLACEHOLDER,
                path,
                f"placeholder {token} must be replaced with the real value",
            )
        )
    if PRICING_MARKER in value:
        lines.append(
            PreflightLine(
                PreflightLine.PLACEHOLDER,
                path,
                f"pricing marker {PRICING_MARKER!r} must be replaced with a "
                "number from the AWS pricing page",
            )
        )
    if AWS_KEY_ID_RE.search(value):
        lines.append(
            PreflightLine(
                PreflightLine.ERROR,
                path,
                "value looks like a static AWS access key id; reference the "
                "credential with a file:/bundle: secret ref, never inline it",
            )
        )


def _diagnostic_dict(diagnostic: Diagnostic) -> dict[str, str]:
    return {
        "object_kind": diagnostic.object_kind,
        "object_id": diagnostic.object_id,
        "severity": diagnostic.severity,
        "code": diagnostic.code,
        "message": diagnostic.message,
        "path": diagnostic.path,
    }


def _load_raw_payloads(
    sources: list[str],
) -> tuple[list[tuple[str, object]], list[Diagnostic]]:
    """Parse each source file's raw JSON for the placeholder scan.

    The placeholder scan must see the *file's own* strings (with the pricing
    marker and ``<TOKEN>`` placeholders intact), which the typed loader would
    reject, so the raw JSON is read here directly. Returns a list of aligned
    ``(source, decoded payload)`` pairs (only the files that parsed) and any
    read/parse error diagnostics.
    """
    pairs: list[tuple[str, object]] = []
    diagnostics: list[Diagnostic] = []
    for source in sources:
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            diagnostics.append(
                Diagnostic(
                    "document", source, "error", "invalid_json",
                    f"could not read registry file: {exc}", "$",
                )
            )
            continue
        try:
            pairs.append((source, json.loads(text)))
        except json.JSONDecodeError as exc:
            diagnostics.append(
                Diagnostic(
                    "document", source, "error", "invalid_json",
                    f"registry is not valid JSON: {exc}", "$",
                )
            )
    return pairs, diagnostics


def _split_sources(spec: str) -> list[str]:
    return [part.strip() for part in spec.split(",") if part.strip()]


def _load_settings() -> object:
    from app.ops.validate_inference_registry import _load_settings as _load

    return _load()


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.preflight_inference_registry",
        description="Preflight an inference registry before it carries real AWS ids.",
    )
    parser.add_argument(
        "file",
        help="registry JSON file, or a comma-separated list of fragments",
    )
    parser.add_argument(
        "--app-env",
        default=None,
        help="environment for the Section 2.6 rules (defaults to APP_ENV)",
    )
    parser.add_argument(
        "--gpu-workers-enabled",
        action="store_true",
        help="treat GPU workers as enabled (requires_gpu routes stay active)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the preflight result as JSON",
    )
    args = parser.parse_args(argv)

    settings = _load_settings()
    app_env = args.app_env or str(getattr(settings, "app_env", "production"))
    block_noncommercial = bool(getattr(settings, "ai_block_noncommercial", False))
    workspace_scoped = bool(getattr(settings, "ai_workspace_scoped_routes", False))
    is_production = app_env == "production"

    sources = _split_sources(args.file)
    preflight_lines: list[PreflightLine] = []
    schema_diagnostics: list[Diagnostic] = []

    # 1) Scan the raw file strings for placeholders / markers / inlined keys.
    payload_pairs, load_errors = _load_raw_payloads(sources)
    for source, payload in payload_pairs:
        _walk_strings(payload, f"{source}:$", preflight_lines)

    # 2) Build the substituted copy and run the shared Section 2.6 validation.
    if load_errors:
        schema_diagnostics.extend(load_errors)
    else:
        substituted = [
            _substitute_document(copy.deepcopy(payload))
            for _source, payload in payload_pairs
        ]
        # Merge the substituted fragments exactly as AI_REGISTRY_FILE would, by
        # loading each decoded mapping through the shared loader in order.
        document, merge_diags = _merge_substituted(substituted)
        if document is None:
            schema_diagnostics.extend(merge_diags)
        else:
            validation = validate_registry(
                document,
                app_env=app_env,
                gpu_workers_enabled=args.gpu_workers_enabled,
                block_noncommercial=block_noncommercial,
                workspace_scoped_routes_enabled=workspace_scoped,
            )
            schema_diagnostics.extend(validation.diagnostics)

    schema_errors = [d for d in schema_diagnostics if d.is_error]
    placeholder_lines = [
        line for line in preflight_lines if line.kind == PreflightLine.PLACEHOLDER
    ]
    error_lines = [line for line in preflight_lines if line.kind == PreflightLine.ERROR]

    # Exit-code decision (see module docstring).
    exit_code = 0
    if schema_errors or error_lines:
        exit_code = 1
    elif is_production and placeholder_lines:
        exit_code = 1

    # In non-production a remaining placeholder is a warning, not an error.
    if not is_production:
        for line in placeholder_lines:
            line.kind = PreflightLine.WARNING

    _emit(
        preflight_lines,
        schema_diagnostics,
        app_env=app_env,
        exit_code=exit_code,
        as_json=args.as_json,
    )
    return exit_code


def _merge_substituted(payloads: list[object]):
    """Load and merge substituted payloads via the shared registry loader.

    A single payload loads directly; several are merged exactly as
    ``AI_REGISTRY_FILE`` merges fragments, so the preflight sees the same merged
    document the runtime would.
    """
    from app.services.inference_registry import (
        load_registry_document,
        merge_registry_documents,
    )

    if not payloads:
        return load_registry_document({})
    if len(payloads) == 1:
        return load_registry_document(payloads[0])  # type: ignore[arg-type]
    documents = []
    for payload in payloads:
        document, diagnostics = load_registry_document(payload)  # type: ignore[arg-type]
        if document is None:
            return None, diagnostics
        documents.append(document)
    return merge_registry_documents(documents)


def _emit(
    preflight_lines: list[PreflightLine],
    schema_diagnostics: list[Diagnostic],
    *,
    app_env: str,
    exit_code: int,
    as_json: bool,
) -> None:
    ordered_lines = sorted(
        preflight_lines,
        key=lambda line: (
            {"ERROR": 0, "PLACEHOLDER": 1, "WARNING": 2}.get(line.kind, 3),
            line.path,
            line.message,
        ),
    )
    ordered_diags = sorted(
        schema_diagnostics,
        key=lambda d: (0 if d.is_error else 1, d.object_id, d.code, d.path),
    )
    if as_json:
        print(
            json.dumps(
                {
                    "app_env": app_env,
                    "exit_code": exit_code,
                    "preflight": [line.as_dict() for line in ordered_lines],
                    "diagnostics": [_diagnostic_dict(d) for d in ordered_diags],
                },
                indent=2,
            )
        )
        return
    for line in ordered_lines:
        print(line.render())
    for diagnostic in ordered_diags:
        print(
            f"{diagnostic.severity.upper()} [{diagnostic.code}] "
            f"{diagnostic.object_kind}:{diagnostic.object_id} "
            f"at {diagnostic.path}: {diagnostic.message}"
        )
    errors = sum(1 for d in ordered_diags if d.is_error)
    warnings = len(ordered_diags) - errors
    # In development, unfilled placeholders are emitted as WARNING lines; count
    # them too so the summary reports how many values still need filling.
    placeholders = sum(
        1
        for line in ordered_lines
        if line.kind in (PreflightLine.PLACEHOLDER, PreflightLine.WARNING)
    )
    preflight_errors = sum(
        1 for line in ordered_lines if line.kind == PreflightLine.ERROR
    )
    print(
        f"preflight ({app_env}): {placeholders} placeholder(s), "
        f"{preflight_errors} preflight error(s); "
        f"schema {errors} error(s), {warnings} warning(s)",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
