from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class EditorialLearningPolicyUpdateRequest(BaseModel):
    memory_enabled: bool
    training_enabled: bool
    policy_version: str = Field(min_length=1, max_length=80)
    expected_revision: int | None = Field(default=None, ge=0)
    retention_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def require_explicit_version(self) -> "EditorialLearningPolicyUpdateRequest":
        if not self.policy_version.strip():
            raise ValueError("A policy version acknowledgement is required")
        return self


class EditorialLearningPolicyResponse(BaseModel):
    workspace_id: UUID
    user_id: UUID
    memory_enabled: bool = False
    training_enabled: bool = False
    policy_version: str = "unacknowledged"
    revision: int = 0
    retention_days: int
    revoked_at: datetime | None = None
    updated_at: datetime | None = None


class EditorialLearningStatusResponse(BaseModel):
    enabled: bool
    policy_version: str
    default_retention_days: int = Field(ge=1, le=365)
    minimum_examples: int = Field(ge=1)


class EditorialLearningDatasetListResponse(BaseModel):
    datasets: list["EditorialLearningDatasetResponse"]


class EditorialLearningRevocationRequest(BaseModel):
    memory: bool = True
    training: bool = True
    reason: str = Field(default="user_request", min_length=1, max_length=160)


class EditorialLearningDatasetResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    user_id: UUID
    status: str
    item_count: int
    manifest_hash: str
    created_at: datetime


class EditorialLearningDeploymentResponse(BaseModel):
    workspace_id: UUID
    project_id: UUID
    user_id: UUID
    active_dataset_id: UUID | None = None
    previous_dataset_id: UUID | None = None
    state: str = "disabled"
    revision: int = 0
    updated_at: datetime | None = None


class EditorialLearningPromotionRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class EditorialLearningRollbackRequest(BaseModel):
    reason: str = Field(default="admin_rollback", min_length=1, max_length=160)
