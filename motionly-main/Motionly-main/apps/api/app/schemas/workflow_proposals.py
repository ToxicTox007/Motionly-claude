from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkflowProposalEnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: UUID
    workspace_id: UUID
    project_id: UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkflowProposalEnsureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_run_id: UUID
    proposal_id: UUID
    proposal_revision: int = Field(ge=1)
    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_state: Literal[
        "queued",
        "generating",
        "awaiting_approval",
        "approved",
        "partially_approved",
        "rejected",
        "failed",
    ]
