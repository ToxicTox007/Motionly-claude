from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.renders import RenderJobResponse
from app.services.queue import QueueJobStatus


class QueueJobResponse(BaseModel):
    id: str
    kind: str
    queue_name: str
    priority: int
    attempts: int
    max_attempts: int
    status: QueueJobStatus
    enqueued_at: datetime
    workspace_id: str | None = None
    project_id: str | None = None
    resource_id: str | None = None
    render_engine: str | None = None
    trace_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class QueueSnapshotResponse(BaseModel):
    queue_name: str
    queued: int
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    dead_letter: int
    canceled: int
    oldest_queued_seconds: int | None = None


class AdminOverviewResponse(BaseModel):
    service: str = "motionly-api"
    environment: str
    request_id: str
    queues: list[QueueSnapshotResponse]
    recent_renders: list[RenderJobResponse]
    launch_metrics: dict[str, Any] = Field(default_factory=dict)
    operations_dashboard: dict[str, Any] = Field(default_factory=dict)
    provider_costs: dict[str, Any] = Field(default_factory=dict)
    provider_slos: list[dict[str, Any]] = Field(default_factory=list)
    render_failure_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    health: dict[str, Any] = Field(default_factory=dict)


class QueueListResponse(BaseModel):
    queues: list[QueueSnapshotResponse]
    jobs: list[QueueJobResponse]
    total_jobs: int = 0
    limit: int = 50


class QueueActionResponse(BaseModel):
    job: QueueJobResponse


class QueueBulkActionResponse(BaseModel):
    jobs: list[QueueJobResponse]


class QueueDeadLetterRequest(BaseModel):
    timeout_seconds: int = Field(default=1_800, ge=1, le=86_400)


class RenderAdminActionResponse(BaseModel):
    job: RenderJobResponse


class RenderJobAdminDetailResponse(BaseModel):
    job: RenderJobResponse
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    provider_costs: dict[str, Any] = Field(default_factory=dict)
