import asyncio
from types import SimpleNamespace

from app.api.health import live, ready
from app.core.config import get_settings


def make_request(request_id: str = "req_test") -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=get_settings())),
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


def test_ready_health_is_ok() -> None:
    response = asyncio.run(ready(make_request("req_ready")))

    assert response.status == "ok"
    assert response.service == "motionly-api"
    assert response.request_id == "req_ready"
