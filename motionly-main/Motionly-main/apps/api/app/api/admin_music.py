"""Task 27: admin music catalog API (import + listing with rights status).

Every route is behind :func:`app.api.admin.require_admin`. Admins import
rights-cleared tracks (the importer refuses any track without a complete,
verified rights record and a licence on the D3 allow-list) and view the catalog
with each track's rights status and a short-lived signed preview link.

The audio itself lives in the private ``music-catalog`` storage bucket
(migration 202610191301): the service role writes it; browsers never read it
directly. The admin catalog page reaches previews only through signed links the
API mints here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.admin import require_admin
from app.api.dependencies import Actor, get_storage_signer
from app.api.errors import ApiError
from app.core.logging import get_logger
from app.repositories.music_catalog import SupabaseCatalogTrackRepository
from app.schemas.admin_music import (
    MusicCatalogListResponse,
    MusicCatalogTrackResponse,
)
from app.services.music_audio import LibrosaAudioAnalyzer
from app.services.music_catalog_import import (
    AudioAnalyzer,
    load_license_allow_list,
)
from app.services.storage import StorageSigner

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/music", tags=["admin"])

AdminActorDep = Annotated[Actor, Depends(require_admin)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]

# Previews are short-lived; the bucket is private so the link is the only way in.
_PREVIEW_TTL_SECONDS = 300
_MUSIC_BUCKET = "music-catalog"


def _catalog_repository(request: Request) -> SupabaseCatalogTrackRepository:
    settings = request.app.state.settings
    if not (settings.supabase_url and settings.supabase_service_role_key):
        raise ApiError(
            503,
            "music_catalog_unavailable",
            "The music catalog is not configured for this environment.",
        )
    return SupabaseCatalogTrackRepository(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )


def _analyzer(request: Request) -> AudioAnalyzer:
    # Tests inject a deterministic analyzer via app.state; production uses
    # librosa, which is installed only in the CPU worker image.
    injected = getattr(request.app.state, "music_audio_analyzer", None)
    return injected if injected is not None else LibrosaAudioAnalyzer()


def _preview_url(signer: StorageSigner, audio_path: str) -> str | None:
    if not audio_path:
        return None
    try:
        signed = signer.create_signed_download(
            bucket=_MUSIC_BUCKET,
            path=audio_path,
            expires_in_seconds=_PREVIEW_TTL_SECONDS,
        )
    except ApiError:
        return None
    return signed.url


def _to_track_response(
    row: dict[str, object],
    *,
    signer: StorageSigner,
) -> MusicCatalogTrackResponse:
    audio_path = str(row.get("audio_path") or row.get("storage_path") or "")
    return MusicCatalogTrackResponse(
        provider_track_id=str(row.get("provider_track_id") or ""),
        title=str(row.get("title") or ""),
        license=str(row.get("license") or ""),
        license_version=_opt_str(row.get("license_version")),
        rights_status=str(row.get("rights_status") or "unknown"),
        composition_rights_status=_opt_str(row.get("composition_rights_status")),
        recording_rights_status=_opt_str(row.get("recording_rights_status")),
        rights_holder=_opt_str(row.get("rights_holder")),
        verified_by=_opt_str(row.get("verified_by")),
        verified_at=row.get("verified_at"),  # type: ignore[arg-type]
        source_page_url=_opt_str(row.get("source_page_url")),
        duration_ms=_opt_int(row.get("duration_ms")),
        bpm=_opt_int(row.get("bpm")),
        energy=_opt_float(row.get("energy")),
        mood_tags=[str(tag) for tag in (row.get("mood_tags") or [])],
        is_test_track=bool(row.get("is_test_track", False)),
        preview_url=_preview_url(signer, audio_path),
    )


def _opt_str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _opt_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _opt_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@router.get("/tracks", response_model=MusicCatalogListResponse)
async def list_catalog_tracks(
    request: Request,
    _actor: AdminActorDep,
    signer: StorageSignerDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> MusicCatalogListResponse:
    repository = _catalog_repository(request)
    rows = repository.list_catalog_tracks(limit=limit)
    allow_list = load_license_allow_list()
    return MusicCatalogListResponse(
        tracks=[_to_track_response(row, signer=signer) for row in rows],
        allow_list_approved=allow_list.approved,
    )
