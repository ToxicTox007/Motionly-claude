import json
from typing import Any
from uuid import UUID

from fastapi import Response, status
from pydantic import BaseModel

from app.services.queue_guard import require_worker_queue


def serialize_layer_job_payload(*, payload: BaseModel, user_id: UUID | None) -> str:
    return json.dumps(
        {
            "payload": payload.model_dump(mode="json"),
            "user_id": str(user_id) if user_id else None,
        }
    )


def prepare_heavy_job_enqueue(*, request) -> None:
    require_worker_queue(request)


def build_queued_layer_response_fields(
    *,
    project_id: UUID,
    job_id: str,
    response: Response | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if response is not None:
        response.status_code = status.HTTP_202_ACCEPTED
    fields: dict[str, Any] = {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/projects/{project_id}/jobs/{job_id}",
    }
    if extra:
        fields.update(extra)
    return fields
