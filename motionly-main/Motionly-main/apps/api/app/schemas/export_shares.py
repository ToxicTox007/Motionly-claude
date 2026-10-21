"""Schemas for shareable export links (Task 16).

A share link grants a signed-out viewer the ability to watch, and optionally
download, a finished export. The secret token is returned exactly once at
creation; only a SHA-256 fingerprint is ever persisted. The viewer page keeps
the token in the URL fragment so it never reaches request logs.
"""

from datetime import datetime
from enum import IntEnum
from uuid import UUID

from pydantic import BaseModel, Field


class ShareLinkExpiry(IntEnum):
    """Allowed share-link lifetimes, in days."""

    ONE_DAY = 1
    SEVEN_DAYS = 7
    THIRTY_DAYS = 30


class ExportShareLinkCreateRequest(BaseModel):
    expires_in_days: ShareLinkExpiry = ShareLinkExpiry.SEVEN_DAYS
    allow_download: bool = True


class ExportShareLinkResponse(BaseModel):
    id: UUID
    export_id: UUID
    allow_download: bool
    expires_at: datetime
    revoked_at: datetime | None = None
    last_accessed_at: datetime | None = None
    created_at: datetime


class ExportShareLinkCreateResponse(BaseModel):
    link: ExportShareLinkResponse
    # The secret is only ever returned here, once. It lives in the viewer page's
    # URL fragment (#t=...) and is never sent during navigation or logged.
    url: str
    token: str


class ExportShareLinkListResponse(BaseModel):
    links: list[ExportShareLinkResponse]


class PublicExportResolveRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class PublicExportResponse(BaseModel):
    """What a signed-out viewer can see for a shared export."""

    title: str
    version: int
    width: int
    height: int
    fps: int
    duration_ms: int | None = None
    allow_download: bool
    # Signed, short-lived media URL for the in-page player.
    media_url: str
    media_expires_at: datetime
    expires_at: datetime
    # Present only when downloads are allowed; carries a Content-Disposition
    # attachment filename so the browser saves a sensibly named file.
    download_url: str | None = None
    download_filename: str | None = None
