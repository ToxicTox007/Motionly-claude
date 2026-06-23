import asyncio
from types import SimpleNamespace

from fastapi import Response

from app.api.health import DependencyHealth, live, ready
from app.core.config import get_settings


def make_request(request_id: str = "req_test", *, settings=None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings or get_settings())),
        state=SimpleNamespace(request_id=request_id),
    )


def test_live_health_returns_request_id() -> None:
    response = asyncio.run(live(make_request()))

    assert response.model_dump() == {
        "status": "ok",
        "service": "motionly-api",
        "environment": get_settings().app_env,
        "request_id": "req_test",
    }


def test_ready_health_is_ok(tmp_path) -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
            "media_work_dir": str(tmp_path),
            "openrouter_api_key": None,
            "groq_api_key": None,
            "pexels_api_key": None,
            "pixabay_api_key": None,
            "runway_api_key": None,
        }
    )
    http_response = Response()
    response = asyncio.run(
        ready(make_request("req_ready", settings=settings), http_response)
    )

    assert response.status == "ok"
    assert response.service == "motionly-api"
    assert response.request_id == "req_ready"
    assert http_response.status_code == 200
    assert set(response.dependencies) == {
        "redis",
        "supabase",
        "storage",
        "ffmpeg",
        "providers",
        "workers",
    }


def test_ready_health_returns_503_when_a_dependency_is_down(monkeypatch, tmp_path) -> None:
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "redis_url": None,
            "supabase_url": None,
            "supabase_service_role_key": None,
            "media_work_dir": str(tmp_path),
            "openrouter_api_key": None,
            "groq_api_key": None,
            "pexels_api_key": None,
            "pixabay_api_key": None,
            "runway_api_key": None,
        }
    )

    async def ffmpeg_down():
        return DependencyHealth(status="down", message="FFmpeg failed.")

    monkeypatch.setattr("app.api.health._probe_ffmpeg", ffmpeg_down)
    http_response = Response()
    result = asyncio.run(ready(make_request(settings=settings), http_response))

    assert result.status == "not_ready"
    assert http_response.status_code == 503
