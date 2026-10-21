"""Schemas for workspace API keys (Task 24).

Agency-plan workspaces authenticate to the Motionly API with keys of the form
``mtly_<random>``. The raw secret is returned exactly once at creation; only a
SHA-256 fingerprint plus a short display prefix and the last four characters
are ever persisted.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ApiKeyResponse(BaseModel):
    """Non-secret view of an API key, safe to list in the UI."""

    id: UUID
    name: str
    # Display-only recognisers; never the full secret.
    prefix: str
    last4: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreateResponse(BaseModel):
    key: ApiKeyResponse
    # The raw secret (``mtly_...``) is only ever returned here, once. It is
    # never persisted or logged; the UI shows it once with a copy button.
    secret: str


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeyResponse]
