"""Task 13 / B28: caption tracks load in ONE Supabase query.

The old list_caption_tracks(_async) ran one query for the tracks and then one
query per track for its segments (N+1). This test caps the Supabase call count
to exactly 1 for a multi-track list by embedding caption_segments.
"""

import asyncio
import json as _json
from uuid import uuid4

import httpx

from app.repositories.captions import (
    CAPTION_TRACK_LIST_COLUMNS,
    SupabaseCaptionRepository,
)
from app.repositories.uploads import InMemoryUploadRepository
from app.services.request_counters import RequestCounters, current_request_counters


def _track_row(project_id, workspace_id, *, segment_count):
    track_id = str(uuid4())
    return {
        "id": track_id,
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "transcript_id": str(uuid4()),
        "preset": "bold_influencer",
        "schema_version": 1,
        "style": {},
        "hide_filler_words": False,
        "language": "original",
        "source_track_id": None,
        "review_status": "approved",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
        "caption_segments": [
            {
                "id": str(uuid4()),
                "segment_index": index,
                "start_ms": index * 1000,
                "end_ms": index * 1000 + 900,
                "text": f"segment {index}",
                "line_count": 1,
                "words": [],
                "highlight_words": [],
            }
            for index in range(segment_count)
        ],
    }


class _StubAsyncClient:
    captured: list = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def request(self, method, url, *, headers=None, params=None, json=None):
        _StubAsyncClient.captured.append({"method": method, "params": params})
        project_id = params["project_id"].split("eq.", 1)[1]
        workspace_id = params["workspace_id"].split("eq.", 1)[1]
        body = [
            _track_row(project_id, workspace_id, segment_count=3),
            _track_row(project_id, workspace_id, segment_count=2),
        ]
        return httpx.Response(
            200,
            content=_json.dumps(body).encode(),
            request=httpx.Request(method, url),
        )


def test_list_caption_tracks_embeds_segments_in_a_single_query(monkeypatch) -> None:
    _StubAsyncClient.captured = []
    monkeypatch.setattr(httpx, "AsyncClient", _StubAsyncClient)

    repository = SupabaseCaptionRepository(
        upload_repository=InMemoryUploadRepository(),
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )

    counters = RequestCounters()
    token = current_request_counters.set(counters)
    try:
        tracks = asyncio.run(
            repository.list_caption_tracks_async(
                project_id=uuid4(),
                workspace_id=uuid4(),
            )
        )
    finally:
        current_request_counters.reset(token)

    # ONE query for N tracks and all their segments (no N+1).
    assert counters.supabase_calls == 1
    assert len(_StubAsyncClient.captured) == 1
    assert _StubAsyncClient.captured[0]["params"]["select"] == CAPTION_TRACK_LIST_COLUMNS

    # Segments were parsed from the embed, ordered by index.
    assert len(tracks) == 2
    assert [segment.index for segment in tracks[0].segments] == [0, 1, 2]
    assert [segment.index for segment in tracks[1].segments] == [0, 1]
    assert tracks[0].segments[0].text == "segment 0"
