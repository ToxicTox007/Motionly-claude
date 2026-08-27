from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.renders import RenderEngine


class WorkflowDraftRenderEnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: UUID
    workspace_id: UUID
    project_id: UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkflowDraftRenderEnsureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: UUID
    render_job_id: UUID
    props_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_engine: RenderEngine
    render_queue: Literal["render", "render_gpu", "render_remotion"]
    source_plan_revision: int = Field(ge=1)
    source_media_revision: int = Field(ge=1)
