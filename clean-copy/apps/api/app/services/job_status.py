from uuid import UUID

from fastapi import Request

from app.schemas.jobs import LayerJobStatusResponse
from app.services.queue import QueueJobStatus, QueueRegistry


def resolve_layer_job_status(
    *,
    request: Request,
    project_id: UUID,
    workspace_id: UUID,
    job_id: str,
) -> LayerJobStatusResponse:
    registry: QueueRegistry = request.app.state.queue_registry
    job = registry.jobs.get(job_id)

    if job is not None:
        if job.project_id != project_id or job.workspace_id != workspace_id:
            return LayerJobStatusResponse(
                status="failed",
                progress=0,
                failure_reason="Job not found for this project.",
            )
        if job.status == QueueJobStatus.SUCCEEDED:
            return LayerJobStatusResponse(status="succeeded", progress=100)
        if job.status == QueueJobStatus.RUNNING:
            return LayerJobStatusResponse(status="running", progress=50)
        if job.status in {QueueJobStatus.FAILED, QueueJobStatus.DEAD_LETTER}:
            return LayerJobStatusResponse(
                status="failed",
                progress=0,
                failure_reason="Worker job failed.",
            )
        if job.status == QueueJobStatus.CANCELED:
            return LayerJobStatusResponse(
                status="failed",
                progress=0,
                failure_reason="Worker job was canceled.",
            )
        return LayerJobStatusResponse(status="queued", progress=0)

    return LayerJobStatusResponse(
        status="failed",
        progress=0,
        failure_reason="Job not found.",
    )
