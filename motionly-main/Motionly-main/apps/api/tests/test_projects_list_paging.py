"""GET /projects search, sort and paging (frontend audit FE-27).

The web app pages through projects on the server instead of filtering a
truncated first page in the browser, so the list endpoint takes ``offset``,
``q`` (a case-insensitive title match) and ``sort``, and reports ``total``.
"""

import asyncio
import json as _json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "local")

from app.api.dependencies import Actor, get_actor  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories.projects import (  # noqa: E402
    InMemoryProjectRepository,
    SupabaseProjectRepository,
)

_USER = UUID("00000000-0000-4000-8000-0000000000c1")
_WORKSPACE = UUID("00000000-0000-4000-8000-0000000000c2")
_OTHER_WORKSPACE = UUID("00000000-0000-4000-8000-0000000000c3")


def _seed(repository: InMemoryProjectRepository, titles: list[str]) -> list[UUID]:
    """Creates projects whose updated_at increases in list order."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ids = []
    for index, title in enumerate(titles):
        project = repository.create_project(workspace_id=_WORKSPACE, created_by=_USER, title=title)
        project.updated_at = base + timedelta(minutes=index)
        ids.append(project.id)
    return ids


@pytest.fixture
def client_and_repository():
    app = create_app()
    app.dependency_overrides[get_actor] = lambda: Actor(
        user_id=_USER, workspace_id=_WORKSPACE, role="owner"
    )
    repository = InMemoryProjectRepository()
    app.state.project_repository = repository
    return TestClient(app), repository


def test_list_reports_the_total_and_pages_with_offset(client_and_repository) -> None:
    client, repository = client_and_repository
    _seed(repository, [f"Reel {index:02d}" for index in range(30)])
    repository.create_project(workspace_id=_OTHER_WORKSPACE, created_by=_USER, title="Other")

    first = client.get("/projects", params={"limit": 20}).json()
    second = client.get("/projects", params={"limit": 20, "offset": 20}).json()

    assert first["total"] == 30
    assert second["total"] == 30
    assert len(first["projects"]) == 20
    assert len(second["projects"]) == 10
    # Newest first by default, and the pages do not overlap.
    assert first["projects"][0]["title"] == "Reel 29"
    assert second["projects"][-1]["title"] == "Reel 00"
    seen = {p["id"] for p in first["projects"]} | {p["id"] for p in second["projects"]}
    assert len(seen) == 30


def test_search_matches_titles_case_insensitively_across_all_projects(
    client_and_repository,
) -> None:
    client, repository = client_and_repository
    _seed(repository, [f"Reel {index:02d}" for index in range(30)] + ["Launch TEASER"])

    found = client.get("/projects", params={"q": "reel 25", "limit": 20}).json()
    assert found["total"] == 1
    assert [p["title"] for p in found["projects"]] == ["Reel 25"]

    teaser = client.get("/projects", params={"q": "  teaser "}).json()
    assert [p["title"] for p in teaser["projects"]] == ["Launch TEASER"]


def test_search_treats_like_wildcards_literally(client_and_repository) -> None:
    client, repository = client_and_repository
    _seed(repository, ["100% real", "100 percent", "snake_case", "snakeXcase"])

    percent = client.get("/projects", params={"q": "100%"}).json()
    underscore = client.get("/projects", params={"q": "e_c"}).json()

    assert [p["title"] for p in percent["projects"]] == ["100% real"]
    assert [p["title"] for p in underscore["projects"]] == ["snake_case"]


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("updated_desc", ["Charlie", "alpha", "Bravo"]),
        ("updated_asc", ["Bravo", "alpha", "Charlie"]),
        ("title_asc", ["alpha", "Bravo", "Charlie"]),
    ],
)
def test_sort_orders(client_and_repository, sort: str, expected: list[str]) -> None:
    client, repository = client_and_repository
    _seed(repository, ["Bravo", "alpha", "Charlie"])

    response = client.get("/projects", params={"sort": sort})

    assert response.status_code == 200
    assert [p["title"] for p in response.json()["projects"]] == expected


@pytest.mark.parametrize(
    "params",
    [
        {"offset": -1},
        {"limit": 0},
        {"limit": 101},
        {"q": "x" * 121},
        {"sort": "created_desc"},
    ],
)
def test_invalid_list_parameters_are_rejected(client_and_repository, params) -> None:
    client, _ = client_and_repository
    assert client.get("/projects", params=params).status_code == 422


def test_an_offset_past_the_end_returns_an_empty_page_with_the_total(
    client_and_repository,
) -> None:
    client, repository = client_and_repository
    _seed(repository, ["One", "Two"])
    body = client.get("/projects", params={"offset": 40}).json()
    assert body == {"projects": [], "total": 2}


class _StubAsyncClient:
    captured: list[dict] = []
    total = "57"

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def request(self, method, url, *, headers=None, params=None, json=None):
        _StubAsyncClient.captured.append({"headers": headers, "params": params})
        body = [
            {
                "id": str(uuid4()),
                "workspace_id": str(_WORKSPACE),
                "created_by": str(_USER),
                "title": "Reel one",
                "status": "draft",
                "active_video_id": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-02T00:00:00+00:00",
                "latest_upload": None,
            }
        ]
        return httpx.Response(
            200,
            content=_json.dumps(body).encode(),
            headers={"content-range": f"20-20/{_StubAsyncClient.total}"},
            request=httpx.Request(method, url),
        )


def _supabase_page(monkeypatch, **kwargs):
    _StubAsyncClient.captured = []
    monkeypatch.setattr(httpx, "AsyncClient", _StubAsyncClient)
    repository = SupabaseProjectRepository(
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    page = asyncio.run(repository.list_projects_page_async(workspace_id=_WORKSPACE, **kwargs))
    return page, _StubAsyncClient.captured[0]


def test_supabase_page_counts_in_the_same_query(monkeypatch) -> None:
    page, request = _supabase_page(monkeypatch, limit=20, offset=20)

    assert len(_StubAsyncClient.captured) == 1
    assert request["headers"]["prefer"].split(",")[-1].strip() == "count=exact"
    assert request["params"]["limit"] == "20"
    assert request["params"]["offset"] == "20"
    assert request["params"]["order"] == "updated_at.desc,id.desc"
    assert "title" not in request["params"]
    assert page.total == 57
    assert len(page.projects) == 1


@pytest.mark.parametrize(
    ("sort", "order"),
    [
        ("updated_desc", "updated_at.desc,id.desc"),
        ("updated_asc", "updated_at.asc,id.asc"),
        ("title_asc", "title.asc,id.asc"),
    ],
)
def test_supabase_page_sort_orders(monkeypatch, sort: str, order: str) -> None:
    _, request = _supabase_page(monkeypatch, sort=sort)
    assert request["params"]["order"] == order


def test_supabase_search_escapes_like_wildcards(monkeypatch) -> None:
    _, request = _supabase_page(monkeypatch, query="100%_off\\")
    assert request["params"]["title"] == "ilike.%100\\%\\_off\\\\%"
