"""Validate a declarative inference registry file (plan Task 4).

    python -m app.ops.validate_inference_registry <file>[,<file>...] \
        [--app-env production] [--gpu-workers-enabled] [--json]

The command loads the registry file (or a comma-separated list of fragments,
merged exactly as ``AI_REGISTRY_FILE`` is at startup), runs every Section 2.6 rule, and prints the
diagnostics sorted by (severity, object, code). It exits 1 only when there is at
least one error, and 0 otherwise (warnings alone are a clean exit). It never
prints a secret value: the loader reads only secret reference *names*, so a
reference such as ``bundle:operator-chat-endpoint`` is shown as-is but no file or
bundle is opened.

``--app-env`` defaults to the API's APP_ENV; it decides, for example, whether
missing data-processor metadata on a remote route is a warning or an error.
``--gpu-workers-enabled`` mirrors ``GPU_WORKERS_ENABLED`` and controls whether
``requires_gpu`` routes are dropped with a warning. ``--json`` prints the
diagnostics as a JSON array instead of human-readable lines.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.services.inference_registry import (
    Diagnostic,
    load_registry_sources,
    validate_registry,
)


class _EnvFallbackSettings:
    """Read-only view of the registry flags from the process environment.

    Used when :func:`app.core.config.get_settings` cannot be constructed (for
    example a CLI run without the API's full production settings). The registry
    rules only need these three flags; ``--app-env`` still overrides the
    environment.
    """

    def __init__(self) -> None:
        import os

        self.app_env = os.environ.get("APP_ENV", "production")
        self.ai_block_noncommercial = os.environ.get(
            "AI_BLOCK_NONCOMMERCIAL", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.ai_workspace_scoped_routes = os.environ.get(
            "AI_WORKSPACE_SCOPED_ROUTES", ""
        ).strip().lower() in {"1", "true", "yes", "on"}


def _load_settings() -> object:
    try:
        return get_settings()
    except Exception:  # noqa: BLE001 - CLI must run without full settings
        return _EnvFallbackSettings()


def _diagnostic_dict(diagnostic: Diagnostic) -> dict[str, str]:
    return {
        "object_kind": diagnostic.object_kind,
        "object_id": diagnostic.object_id,
        "severity": diagnostic.severity,
        "code": diagnostic.code,
        "message": diagnostic.message,
        "path": diagnostic.path,
    }


def _sort_key(diagnostic: Diagnostic) -> tuple[int, str, str, str]:
    return (
        0 if diagnostic.is_error else 1,
        diagnostic.object_id,
        diagnostic.code,
        diagnostic.path,
    )


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.validate_inference_registry",
        description="Validate a declarative inference registry file.",
    )
    parser.add_argument(
        "file",
        help="registry JSON file, or a comma-separated list of fragments (as AI_REGISTRY_FILE)",
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
        help="print diagnostics as a JSON array",
    )
    args = parser.parse_args(argv)

    settings = _load_settings()
    app_env = args.app_env or str(getattr(settings, "app_env", "production"))
    block_noncommercial = bool(getattr(settings, "ai_block_noncommercial", False))
    workspace_scoped = bool(getattr(settings, "ai_workspace_scoped_routes", False))

    source = args.file if "," in args.file else Path(args.file)
    document, load_diagnostics = load_registry_sources(source)
    if document is None:
        _emit(load_diagnostics, as_json=args.as_json)
        return 1

    validation = validate_registry(
        document,
        app_env=app_env,
        gpu_workers_enabled=args.gpu_workers_enabled,
        block_noncommercial=block_noncommercial,
        workspace_scoped_routes_enabled=workspace_scoped,
    )
    _emit(list(validation.diagnostics), as_json=args.as_json)
    return 1 if validation.errors() else 0


def _emit(diagnostics: list[Diagnostic], *, as_json: bool) -> None:
    ordered = sorted(diagnostics, key=_sort_key)
    if as_json:
        print(json.dumps([_diagnostic_dict(d) for d in ordered], indent=2))
        return
    if not ordered:
        print("registry is valid: no diagnostics")
        return
    for diagnostic in ordered:
        print(
            f"{diagnostic.severity.upper()} [{diagnostic.code}] "
            f"{diagnostic.object_kind}:{diagnostic.object_id} "
            f"at {diagnostic.path}: {diagnostic.message}"
        )
    errors = sum(1 for d in ordered if d.is_error)
    warnings = len(ordered) - errors
    print(f"{errors} error(s), {warnings} warning(s)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
