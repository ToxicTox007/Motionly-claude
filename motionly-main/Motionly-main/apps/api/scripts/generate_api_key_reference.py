#!/usr/bin/env python3
"""Generate the public API reference for ``mtly_`` API keys (Task 24).

Reads the checked-in OpenAPI contract (``packages/shared/openapi.json``) and
emits ``packages/shared/api-key-reference.json`` containing *only* the routes an
Agency API key may call (the allow-list in
``apps/api/app/api/api_key_routes.py``). The web reference page renders this
artifact, so the documentation can never list a route the key cannot actually
reach.

A contract test (``apps/api/tests/test_api_key_reference_contract.py``) fails if
the generated reference and the allow-list disagree, or if the checked-in
reference is stale. Run with ``--check`` in CI, no args to regenerate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]

sys.path.insert(0, str(API_ROOT))

from app.api.api_key_routes import api_key_allowed_routes_sorted  # noqa: E402

OPENAPI = REPO_ROOT / "packages/shared/openapi.json"
OUTPUT = REPO_ROOT / "packages/shared/api-key-reference.json"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def build_reference(openapi: dict) -> dict:
    paths = openapi.get("paths", {})
    routes = []
    for method, path in api_key_allowed_routes_sorted():
        operation = (paths.get(path) or {}).get(method.lower())
        if operation is None:
            raise SystemExit(
                f"Allow-listed route {method} {path} is missing from openapi.json. "
                "Regenerate the OpenAPI contract first."
            )
        routes.append(
            {
                "method": method,
                "path": path,
                "summary": operation.get("summary") or operation.get("operationId") or "",
                "description": (operation.get("description") or "").strip(),
            }
        )
    return {
        "scheme": "Bearer mtly_<key>",
        "note": (
            "Agency-plan API keys may call only the routes listed here. Every "
            "other endpoint returns 403 api_key_route_forbidden. A key also stops "
            "working (403 api_key_creator_inactive) once its creator is no longer an "
            "active owner/admin of the workspace, and (402 plan_feature_required) "
            "while the workspace plan lacks API access."
        ),
        "routes": routes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not OPENAPI.exists():
        print(f"Missing {OPENAPI}; regenerate the OpenAPI contract first.", file=sys.stderr)
        return 1
    openapi = json.loads(OPENAPI.read_text())
    reference = build_reference(openapi)
    text = json.dumps(reference, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != text:
            print(
                f"{OUTPUT.relative_to(REPO_ROOT)} is stale; "
                "run scripts/generate_api_key_reference.py.",
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
