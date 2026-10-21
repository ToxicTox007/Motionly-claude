"""Export share-link endpoints (Task 16).

Workspace members create, list, and revoke links for a finished export. A
public, rate-limited, enumeration-safe resolve endpoint lets a signed-out
viewer watch (and optionally download) the export. The secret token travels in
the watch page's URL fragment and is posted to ``/public/exports/resolve``.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth import enforce_hourly_limit
from app.api.dependencies import (
    Actor,
    get_actor,
    get_export_share_repository,
    get_render_repository,
    get_storage_signer,
)
from app.api.errors import ApiError
from app.repositories.export_shares import InMemoryExportShareRepository, ResolvedExportShareLink
from app.repositories.renders import InMemoryRenderRepository
from app.schemas.export_shares import (
    ExportShareLinkCreateRequest,
    ExportShareLinkCreateResponse,
    ExportShareLinkListResponse,
    ExportShareLinkResponse,
    PublicExportResolveRequest,
    PublicExportResponse,
)
from app.services.export_download import export_download_filename
from app.services.offload import run_blocking
from app.services.storage import StorageSigner

router = APIRouter(tags=["export share links"])

ActorDep = Annotated[Actor, Depends(get_actor)]
ExportShareRepositoryDep = Annotated[
    InMemoryExportShareRepository, Depends(get_export_share_repository)
]
RenderRepositoryDep = Annotated[InMemoryRenderRepository, Depends(get_render_repository)]
StorageSignerDep = Annotated[StorageSigner, Depends(get_storage_signer)]

# The viewer page keeps the secret in the URL fragment so it never reaches
# server logs; the client reads it and posts it to the resolve endpoint.
WATCH_PATH = "/watch"


async def _repository_call(repository: Any, async_name: str, sync_name: str, **kwargs: Any):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return await run_blocking(getattr(repository, sync_name), **kwargs)


@router.post(
    "/exports/{export_id}/share-links",
    response_model=ExportShareLinkCreateResponse,
    status_code=201,
)
async def create_export_share_link(
    export_id: UUID,
    payload: ExportShareLinkCreateRequest,
    actor: ActorDep,
    repository: ExportShareRepositoryDep,
) -> ExportShareLinkCreateResponse:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=int(payload.expires_in_days))
    link = await _repository_call(
        repository,
        "create_share_link_async",
        "create_share_link",
        export_id=export_id,
        workspace_id=actor.workspace_id,
        token=token,
        expires_at=expires_at,
        allow_download=payload.allow_download,
        created_by=actor.user_id,
    )
    return ExportShareLinkCreateResponse(
        link=link.to_response(),
        url=f"{WATCH_PATH}#t={token}",
        token=token,
    )


@router.get("/exports/{export_id}/share-links", response_model=ExportShareLinkListResponse)
async def list_export_share_links(
    export_id: UUID,
    actor: ActorDep,
    repository: ExportShareRepositoryDep,
) -> ExportShareLinkListResponse:
    links = await _repository_call(
        repository,
        "list_share_links_async",
        "list_share_links",
        export_id=export_id,
        workspace_id=actor.workspace_id,
    )
    return ExportShareLinkListResponse(links=[link.to_response() for link in links])


@router.post(
    "/exports/{export_id}/share-links/{share_link_id}/revoke",
    response_model=ExportShareLinkResponse,
)
async def revoke_export_share_link(
    export_id: UUID,
    share_link_id: UUID,
    actor: ActorDep,
    repository: ExportShareRepositoryDep,
) -> ExportShareLinkResponse:
    link = await _repository_call(
        repository,
        "revoke_share_link_async",
        "revoke_share_link",
        share_link_id=share_link_id,
        workspace_id=actor.workspace_id,
    )
    if link.export_id != export_id:
        # Keep the failure identical to an unknown link.
        raise ApiError(404, "export_share_link_not_found", "Share link was not found.")
    return link.to_response()


async def _resolve(*, repository: Any, token: str) -> ResolvedExportShareLink:
    try:
        return await _repository_call(
            repository,
            "resolve_share_link_async",
            "resolve_share_link",
            token=token,
        )
    except ApiError as exc:
        # All capability lookup failures stay indistinguishable to prevent
        # token or link-state enumeration.
        if exc.status_code in {403, 404}:
            raise ApiError(
                404, "export_share_link_not_found", "Share link was not found."
            ) from exc
        raise


@router.post("/public/exports/resolve", response_model=PublicExportResponse)
async def resolve_public_export(
    payload: PublicExportResolveRequest,
    request: Request,
    response: Response,
    repository: ExportShareRepositoryDep,
    storage_signer: StorageSignerDep,
) -> PublicExportResponse:
    response.headers["Cache-Control"] = "no-store, private"
    settings = request.app.state.settings
    # Every attempt, including refused ones, counts, bounding token probing.
    await enforce_hourly_limit(
        request,
        action="export_share_resolve",
        per_viewer=getattr(settings, "export_share_resolve_rate_limit_per_hour", 120),
        overall=getattr(settings, "export_share_resolve_global_rate_limit_per_hour", 6000),
    )
    resolved = await _resolve(repository=repository, token=payload.token)
    export = resolved.export
    render_repository: InMemoryRenderRepository = request.app.state.render_repository
    title = _project_title(render_repository, export)

    ttl = min(
        settings.export_signed_url_ttl_seconds,
        StorageSigner.MAX_DOWNLOAD_TTL_SECONDS,
    )
    now = datetime.now(UTC)
    media = await run_blocking(
        storage_signer.create_signed_download,
        bucket=export.storage_bucket,
        path=export.storage_path,
        expires_in_seconds=ttl,
    )
    download_url: str | None = None
    download_filename: str | None = None
    if resolved.link.allow_download:
        extension = getattr(export.format, "value", str(export.format))
        download_filename = export_download_filename(
            title=title, version=export.version, extension=extension
        )
        signed_download = await run_blocking(
            storage_signer.create_signed_download,
            bucket=export.storage_bucket,
            path=export.storage_path,
            expires_in_seconds=ttl,
            download=download_filename,
        )
        download_url = signed_download.url

    return PublicExportResponse(
        title=title,
        version=export.version,
        width=export.width,
        height=export.height,
        fps=export.fps,
        duration_ms=export.duration_ms,
        allow_download=resolved.link.allow_download,
        media_url=media.url,
        media_expires_at=now + timedelta(seconds=ttl),
        expires_at=resolved.link.expires_at,
        download_url=download_url,
        download_filename=download_filename,
    )


def _project_title(render_repository: InMemoryRenderRepository, export: Any) -> str:
    try:
        project = render_repository.caption_repository.get_project_for_workspace(
            project_id=export.project_id, workspace_id=export.workspace_id
        )
        title = getattr(project, "title", None)
        if isinstance(title, str) and title.strip():
            return title.strip()
    except Exception:
        # A missing or inaccessible project must not leak tenant detail to the
        # viewer; fall back to a neutral label.
        pass
    return "Motionly export"
