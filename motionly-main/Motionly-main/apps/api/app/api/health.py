from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    request_id: str


@router.get("/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="motionly-api",
        environment=request.app.state.settings.app_env,
        request_id=request.state.request_id,
    )


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="motionly-api",
        environment=request.app.state.settings.app_env,
        request_id=request.state.request_id,
    )
