from typing import Literal

from pydantic import BaseModel, Field


class LaunchFeedbackCreateRequest(BaseModel):
    category: Literal["general", "bug", "render_quality", "billing", "feature_request"]
    message: str = Field(min_length=10, max_length=5000)
    page_url: str | None = Field(default=None, max_length=2000)


class LaunchFeedbackCreateResponse(BaseModel):
    accepted: bool = True
