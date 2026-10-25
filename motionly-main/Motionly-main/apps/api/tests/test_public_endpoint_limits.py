"""Unauthenticated write endpoints are bounded per viewer and overall per hour."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "local")

from app.core.middleware import InMemoryRateLimiter  # noqa: E402
from app.main import create_app  # noqa: E402

VIEWER = {"x-motionly-viewer-ip": "203.0.113.10"}
OTHER_VIEWER = {"x-motionly-viewer-ip": "203.0.113.11"}


@pytest.fixture
def client():
    app = create_app()
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.settings.supabase_url = None
    app.state.settings.supabase_service_role_key = None
    app.state.settings.rate_limit_per_minute = 10_000
    return TestClient(app, raise_server_exceptions=False), app


def _feedback(client, headers):
    return client.post(
        "/feedback",
        json={"category": "bug", "message": "The preview and export differ."},
        headers=headers,
    )


def test_feedback_is_limited_per_viewer(client) -> None:
    http, app = client
    app.state.settings.feedback_rate_limit_per_hour = 2

    assert [_feedback(http, VIEWER).status_code for _ in range(2)] == [201, 201]
    limited = _feedback(http, VIEWER)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "feedback_rate_limited"
    # Another network is unaffected.
    assert _feedback(http, OTHER_VIEWER).status_code == 201
    assert len(app.state.launch_feedback) == 3


def test_feedback_is_limited_overall(client) -> None:
    http, app = client
    app.state.settings.feedback_global_rate_limit_per_hour = 2
    statuses = [
        _feedback(http, {"x-motionly-viewer-ip": f"203.0.113.{20 + index}"}).status_code
        for index in range(3)
    ]
    assert statuses == [201, 201, 429]


def test_public_review_endpoints_are_limited_per_viewer(client) -> None:
    http, app = client
    app.state.settings.public_review_rate_limit_per_hour = 3
    link = uuid4()
    body = {"token": "x" * 43}

    statuses = [
        http.post(f"/public/reviews/{link}/resolve", json=body, headers=VIEWER).status_code,
        http.post(
            f"/public/reviews/{link}/comments",
            json={**body, "body": "Looks good", "author_name": "Client"},
            headers=VIEWER,
        ).status_code,
        http.post(
            f"/public/reviews/{link}/decision",
            json={
                **body,
                "decision": "approved",
                "expected_status_revision": 1,
                "idempotency_key": "k" * 16,
            },
            headers=VIEWER,
        ).status_code,
    ]
    # Unknown link: 404 (or 422 for a shape the schema refuses), never 429 yet.
    assert all(status in (404, 422) for status in statuses), statuses
    limited = http.post(f"/public/reviews/{link}/resolve", json=body, headers=VIEWER)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "public_review_rate_limited"
    assert (
        http.post(f"/public/reviews/{link}/resolve", json=body, headers=OTHER_VIEWER).status_code
        == 404
    )
