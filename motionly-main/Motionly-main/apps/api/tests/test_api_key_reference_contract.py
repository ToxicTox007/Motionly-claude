"""The generated API reference must list exactly the API-key allow-list (Task 24).

If the reference (``packages/shared/api-key-reference.json``) and the live
allow-list (``app.api.api_key_routes.API_KEY_ALLOWED_ROUTES``) disagree, this
fails — so the documentation can never advertise a route a key cannot reach,
and can never omit one it can.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.api.api_key_routes import API_KEY_ALLOWED_ROUTES

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE = REPO_ROOT / "packages/shared/api-key-reference.json"


def test_reference_lists_exactly_the_allow_list():
    assert REFERENCE.exists(), (
        "packages/shared/api-key-reference.json is missing; run "
        "apps/api/scripts/generate_api_key_reference.py"
    )
    reference = json.loads(REFERENCE.read_text())
    documented = {(route["method"], route["path"]) for route in reference["routes"]}
    assert documented == set(API_KEY_ALLOWED_ROUTES), (
        "API reference and the API-key allow-list disagree: "
        f"only in reference={sorted(documented - set(API_KEY_ALLOWED_ROUTES))}, "
        f"only in allow-list={sorted(set(API_KEY_ALLOWED_ROUTES) - documented)}"
    )


def test_reference_documents_the_scheme():
    reference = json.loads(REFERENCE.read_text())
    assert reference["scheme"].startswith("Bearer mtly_")
    assert reference["routes"], "reference must list at least one route"
