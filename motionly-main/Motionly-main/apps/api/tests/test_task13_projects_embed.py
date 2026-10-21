"""Task 13 / B29: GET /projects returns each project's latest upload in ONE
Supabase query (an embed), so the dashboard and projects page no longer make one
extra /uploads/{id} request per project.

The test caps the Supabase call count per request using the existing
app.services.request_counters instrumentation.
"""

import asyncio
import json as _json
from uuid import uuid4

import httpx

from app.repositories.projects import PROJECT_LIST_COLUMNS, SupabaseProjectRepository
from app.services.request_counters import RequestCounters, current_request_counters


class _StubAsyncClient:
    """Minimal httpx.AsyncClient stand-in that records requests."""

    captured: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def request(self, method, url, *, headers=None, params=None, json=None):
        _StubAsyncClient.captured.append({"method": method, "url": url, "params": params})
        workspace_id = str(uuid4())
        body = [
            {
                "id": str(uuid4()),
                "workspace_id": workspace_id,
                "created_by": str(uuid4()),
                "title": "Reel one",
                "status": "ready",
                "active_video_id": str(uuid4()),
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-02T00:00:00+00:00",
                "latest_upload": {
                    "id": str(uuid4()),
                    "status": "ready",
                    "original_filename": "clip.mp4",
                    "metadata": {"duration_ms": 12000, "warnings": ["low_audio"]},
                },
            }
        ]
        return httpx.Response(
            200,
            content=_json.dumps(body).encode(),
            request=httpx.Request(method, url),
        )


def test_list_projects_embeds_latest_upload_in_a_single_query(monkeypatch) -> None:
    _StubAsyncClient.captured = []
    monkeypatch.setattr(httpx, "AsyncClient", _StubAsyncClient)

    repository = SupabaseProjectRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )

    counters = RequestCounters()
    token = current_request_counters.set(counters)
    try:
        projects = asyncio.run(repository.list_projects_async(workspace_id=uuid4(), limit=12))
    finally:
        current_request_counters.reset(token)

    # ONE Supabase call for the whole list, not 1 + N.
    assert counters.supabase_calls == 1
    assert len(_StubAsyncClient.captured) == 1
    # The single query embeds the upload via the FK.
    assert _StubAsyncClient.captured[0]["params"]["select"] == PROJECT_LIST_COLUMNS

    # The embedded upload is parsed and exposed on the response.
    assert len(projects) == 1
    upload = projects[0].latest_upload
    assert upload is not None
    assert upload.status == "ready"
    assert upload.original_filename == "clip.mp4"
    assert upload.duration_ms == 12000
    assert upload.warnings == ["low_audio"]


def test_project_without_active_upload_has_no_latest_upload(monkeypatch) -> None:
    class _NoUploadClient(_StubAsyncClient):
        async def request(self, method, url, *, headers=None, params=None, json=None):
            _StubAsyncClient.captured.append({"method": method, "url": url, "params": params})
            body = [
                {
                    "id": str(uuid4()),
                    "workspace_id": str(uuid4()),
                    "created_by": str(uuid4()),
                    "title": "Empty reel",
                    "status": "draft",
                    "active_video_id": None,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                    "latest_upload": None,
                }
            ]
            return httpx.Response(
                200, content=_json.dumps(body).encode(), request=httpx.Request(method, url)
            )

    _StubAsyncClient.captured = []
    monkeypatch.setattr(httpx, "AsyncClient", _NoUploadClient)

    repository = SupabaseProjectRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    counters = RequestCounters()
    token = current_request_counters.set(counters)
    try:
        projects = asyncio.run(repository.list_projects_async(workspace_id=uuid4(), limit=12))
    finally:
        current_request_counters.reset(token)

    assert counters.supabase_calls == 1
    assert projects[0].latest_upload is None
