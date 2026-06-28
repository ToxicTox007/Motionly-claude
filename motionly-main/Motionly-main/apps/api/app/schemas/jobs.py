from pydantic import BaseModel


class LayerJobStatusResponse(BaseModel):
    status: str
    progress: int = 0
    failure_reason: str | None = None
