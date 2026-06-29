from datetime import UTC, datetime

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.api.internal_jobs import require_worker_token

router = APIRouter(tags=["operations"])


class HeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    worker_role: str = Field(min_length=1, max_length=100)
    queues: list[str] = Field(default_factory=list, max_length=50)
    version: str | None = Field(default=None, max_length=100)
    metadata: dict[str, object] = Field(default_factory=dict)


class ProviderSloRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=100)
    success: bool
    latency_ms: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=100)


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request, authorization: str | None = Header(default=None)) -> str:
    token = request.app.state.settings.metrics_bearer_token
    if token and authorization != f"Bearer {token}":
        raise ApiError(401, "metrics_auth_required", "Metrics credentials are required.")
    output = request.app.state.metrics_registry.render()
    for snapshot in request.app.state.queue_registry.snapshots():
        queue = snapshot.queue_name.replace('"', "")
        output += f'motionly_queue_queued{{queue="{queue}"}} {snapshot.queued}\n'
        output += f'motionly_queue_running{{queue="{queue}"}} {snapshot.running}\n'
        output += f'motionly_queue_dead_letter{{queue="{queue}"}} {snapshot.dead_letter}\n'
        output += (
            f'motionly_queue_oldest_seconds{{queue="{queue}"}} '
            f'{snapshot.oldest_queued_seconds or 0}\n'
        )
    ops_repo = getattr(request.app.state, "operations_repository", None)
    if ops_repo is not None:
        cost_by_workspace_provider = {}
        for event in getattr(ops_repo, "cost_events", []):
            ws_id = str(event.get("workspace_id") or "unknown").replace('"', "")
            provider = str(event.get("provider") or "unknown").replace('"', "")
            qty = float(event.get("quantity") or 0.0)
            unit_cost = float(event.get("unit_cost_usd") or 0.0)
            key = (ws_id, provider)
            cost_by_workspace_provider[key] = cost_by_workspace_provider.get(key, 0.0) + (qty * unit_cost)
        for (ws_id, provider), cost in sorted(cost_by_workspace_provider.items()):
            output += f'motionly_cost_usd{{workspace_id="{ws_id}",provider="{provider}"}} {cost:.6f}\n'
    return output


@router.post("/internal/operations/heartbeat")
async def record_worker_heartbeat(
    payload: HeartbeatRequest,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> dict[str, object]:
    await require_worker_token(request, x_motionly_worker_token)
    record = request.app.state.operations_repository.heartbeat(**payload.model_dump())
    return {"status": "ok", "last_seen_at": record.last_seen_at.isoformat()}


@router.post("/internal/operations/provider-slo")
async def record_provider_slo(
    payload: ProviderSloRequest,
    request: Request,
    x_motionly_worker_token: str | None = Header(default=None),
) -> dict[str, str]:
    await require_worker_token(request, x_motionly_worker_token)
    request.app.state.operations_repository.record_provider_slo(
        **payload.model_dump(), created_at=datetime.now(UTC)
    )
    request.app.state.metrics_registry.observe(
        "provider_latency_ms",
        payload.latency_ms,
        provider=payload.provider,
        operation=payload.operation,
        success=str(payload.success).lower(),
    )
    return {"status": "recorded"}
