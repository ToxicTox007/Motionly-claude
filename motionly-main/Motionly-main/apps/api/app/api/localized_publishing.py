from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.api.captions import LANGUAGE_NAMES, translate_segments_if_requested
from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError
from app.repositories.localized_publishing import InMemoryLocalizedPublishingRepository
from app.schemas.broll import StockOrientation, StockProviderKey
from app.schemas.captions import CaptionSegment
from app.schemas.localized_publishing import (
    AssetCollectionCreateRequest,
    AssetCollectionItemRequest,
    AssetCollectionListResponse,
    AssetCollectionResponse,
    AssetFavoriteUpdateRequest,
    AssetLibraryClipRecipe,
    AssetLibraryItemCreateRequest,
    AssetLibraryItemDeleteResponse,
    AssetLibraryItemListResponse,
    AssetLibraryItemResponse,
    AssetLibraryKind,
    AssetLibraryMediaState,
    AssetLibraryMediaUrlResponse,
    AssetLibraryStyleRecipe,
    AssetReuseResponse,
    LocalizedOverlayTranslation,
    LocalizedPublishPackageCreateRequest,
    LocalizedPublishPackageListResponse,
    LocalizedPublishPackageResponse,
    LocalizedPublishPackageReviewRequest,
    LocalizedReviewStatus,
    SubtitleFormat,
)
from app.schemas.music import MusicProviderKey
from app.schemas.scale import ScaleResourceStatus
from app.services.asset_library_references import (
    AssetLibraryReferenceBinding,
    allowed_asset_library_buckets,
    is_asset_library_media,
    resolve_asset_library_reference,
)
from app.services.object_references import ObjectReferenceService
from app.services.subtitles import subtitle_content

router = APIRouter(tags=["localized publishing and asset library"])
ActorDep = Annotated[Actor, Depends(get_actor)]


def _repository(request: Request) -> InMemoryLocalizedPublishingRepository:
    repository = getattr(request.app.state, "localized_publishing_repository", None)
    if repository is None:
        raise ApiError(
            503, "localized_publishing_unavailable", "Localized publishing is unavailable."
        )
    return repository


def _caption_repository(request: Request):
    return request.app.state.caption_repository


def _plan_record(request: Request, *, project_id: UUID, workspace_id: UUID, plan_id: UUID | None):
    plans = request.app.state.plan_repository
    if plan_id:
        return plans.get_plan(project_id=project_id, workspace_id=workspace_id, plan_id=plan_id)
    return plans.active_plan(project_id=project_id, workspace_id=workspace_id)


def _localizable_overlays(plan) -> list[tuple[str, str]]:
    if plan is None:
        return []
    overlays: list[tuple[str, str]] = []
    for element in plan.plan.manual_elements:
        if element.type not in {"text", "caption", "graphic"}:
            continue
        content = element.properties.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        overlays.append((element.id, content.strip()))
    return overlays


def _translate_texts(
    *,
    values: list[str],
    source_language: str,
    target_language: str,
    model_gateway: Any,
    workspace_id: UUID,
    project_id: UUID,
) -> list[str]:
    if not values or source_language == target_language:
        return values
    segments = [
        CaptionSegment(index=index, start_ms=index * 2, end_ms=index * 2 + 1, text=value)
        for index, value in enumerate(values)
    ]
    translated = translate_segments_if_requested(
        segments=segments,
        source_language=source_language,
        output_language=target_language,
        model_gateway=model_gateway,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return [segment.text for segment in translated]


@router.post(
    "/projects/{project_id}/localized-publish-packages",
    response_model=LocalizedPublishPackageResponse,
    status_code=201,
)
async def create_localized_publish_package(
    project_id: UUID,
    payload: LocalizedPublishPackageCreateRequest,
    request: Request,
    actor: ActorDep,
) -> LocalizedPublishPackageResponse:
    caption_repository = _caption_repository(request)
    caption_repository.get_project_for_workspace(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    source_track = (
        caption_repository.get_caption_track(
            project_id=project_id,
            track_id=payload.source_caption_track_id,
            workspace_id=actor.workspace_id,
        )
        if payload.source_caption_track_id
        else None
    )
    if source_track is None:
        tracks = caption_repository.list_caption_tracks(
            project_id=project_id,
            workspace_id=actor.workspace_id,
        )
        source_track = next(
            (
                track
                for track in tracks
                if getattr(track, "review_status", "approved") == "approved"
            ),
            tracks[0] if tracks else None,
        )
    if source_track is None or not source_track.segments:
        raise ApiError(
            422, "caption_segments_empty", "Generate captions before localizing a publish package."
        )

    transcript = caption_repository.get_transcript(
        project_id=project_id,
        transcript_id=source_track.transcript_id,
        workspace_id=actor.workspace_id,
    )
    source_language = getattr(source_track, "language", None) or transcript.language
    target_language = payload.language.value
    if target_language not in LANGUAGE_NAMES:
        raise ApiError(422, "caption_language_unsupported", "Choose a supported publish language.")
    model_gateway = getattr(request.app.state, "model_gateway", None)
    translated_segments = translate_segments_if_requested(
        segments=source_track.segments,
        source_language=source_language,
        output_language=target_language,
        model_gateway=model_gateway,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    translated_track = caption_repository.create_caption_track(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        transcript_id=source_track.transcript_id,
        preset=source_track.preset,
        schema_version=source_track.schema_version,
        style=source_track.style,
        hide_filler_words=source_track.hide_filler_words,
        language=target_language,
        source_track_id=source_track.id,
        review_status="needs_review",
        segments=translated_segments,
    )

    plan = _plan_record(
        request,
        project_id=project_id,
        workspace_id=actor.workspace_id,
        plan_id=payload.edit_plan_id,
    )
    overlays = _localizable_overlays(plan)
    translated_overlays = _translate_texts(
        values=[content for _, content in overlays],
        source_language=source_language,
        target_language=target_language,
        model_gateway=model_gateway,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    project = caption_repository.get_project_for_workspace(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    source_title = (payload.source_title or project.title).strip()
    source_description = (
        payload.source_description
        or " ".join(segment.text for segment in source_track.segments[:4])
    ).strip()
    localized_metadata = _translate_texts(
        values=[source_title, source_description],
        source_language=source_language,
        target_language=target_language,
        model_gateway=model_gateway,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    package = _repository(request).create_package(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        source_caption_track_id=source_track.id,
        caption_track_id=translated_track.id,
        edit_plan_id=plan.id if plan else None,
        language=payload.language,
        review_status=LocalizedReviewStatus.NEEDS_REVIEW,
        overlay_translations=[
            LocalizedOverlayTranslation(source_element_id=element_id, content=content)
            for (element_id, _), content in zip(overlays, translated_overlays, strict=True)
        ],
        title_suggestions=[localized_metadata[0]]
        if localized_metadata and localized_metadata[0]
        else [],
        description_suggestions=[localized_metadata[1]]
        if len(localized_metadata) > 1 and localized_metadata[1]
        else [],
    )
    return package.response()


@router.get(
    "/projects/{project_id}/localized-publish-packages",
    response_model=LocalizedPublishPackageListResponse,
)
async def list_localized_publish_packages(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> LocalizedPublishPackageListResponse:
    _caption_repository(request).get_project_for_workspace(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    packages = _repository(request).list_packages(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        limit=limit,
    )
    return LocalizedPublishPackageListResponse(
        packages=[package.response() for package in packages]
    )


@router.patch(
    "/projects/{project_id}/localized-publish-packages/{package_id}/review",
    response_model=LocalizedPublishPackageResponse,
)
async def review_localized_publish_package(
    project_id: UUID,
    package_id: UUID,
    payload: LocalizedPublishPackageReviewRequest,
    request: Request,
    actor: ActorDep,
) -> LocalizedPublishPackageResponse:
    repository = _repository(request)
    package = repository.get_package(
        package_id=package_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    overlays = payload.overlay_translations
    if overlays is not None:
        allowed = {item.source_element_id for item in package.overlay_translations}
        if any(item.source_element_id not in allowed for item in overlays):
            raise ApiError(
                422,
                "localized_overlay_unknown",
                "A reviewed overlay does not belong to this package.",
            )
    updated = repository.update_package(
        package=package,
        review_status=payload.review_status,
        selected_title=payload.selected_title,
        selected_description=payload.selected_description,
        overlay_translations=overlays,
    )
    caption_repository = _caption_repository(request)
    track = caption_repository.get_caption_track(
        project_id=project_id,
        track_id=updated.caption_track_id,
        workspace_id=actor.workspace_id,
    )
    caption_repository.update_caption_track(
        track=track,
        preset=track.preset,
        style=track.style,
        hide_filler_words=track.hide_filler_words,
        segments=track.segments,
        language=getattr(track, "language", updated.language.value),
        source_track_id=(
            getattr(track, "source_track_id", None) or updated.source_caption_track_id
        ),
        review_status=(
            "approved"
            if updated.review_status == LocalizedReviewStatus.APPROVED
            else "needs_review"
        ),
    )
    return updated.response()


@router.get(
    "/projects/{project_id}/localized-publish-packages/{package_id}/subtitles",
    response_class=Response,
)
async def download_localized_subtitles(
    project_id: UUID,
    package_id: UUID,
    request: Request,
    actor: ActorDep,
    format: SubtitleFormat = SubtitleFormat.SRT,
) -> Response:
    package = _repository(request).get_package(
        package_id=package_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    track = _caption_repository(request).get_caption_track(
        project_id=project_id,
        track_id=package.caption_track_id,
        workspace_id=actor.workspace_id,
    )
    filename = f"subtitles-{package.language.value}.{format.value}"
    return Response(
        content=subtitle_content(segments=track.segments, format=format),
        media_type="text/vtt" if format == SubtitleFormat.VTT else "application/x-subrip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _object_reference_service(request: Request) -> ObjectReferenceService:
    service = getattr(request.app.state, "object_reference_service", None)
    if service is None:
        raise ApiError(503, "asset_library_unavailable", "Asset media references are unavailable.")
    return service


def _reference_id(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _asset_library_buckets(request: Request) -> frozenset[str]:
    return allowed_asset_library_buckets(getattr(request.app.state, "settings", None))


async def _resolve_asset_library_media(
    *,
    item,
    request: Request,
):
    repository = _repository(request)
    try:
        return await resolve_asset_library_reference(
            binding=AssetLibraryReferenceBinding(
                workspace_id=item.workspace_id,
                source_project_id=item.source_project_id,
                source_asset_id=item.source_asset_id,
                object_reference_id=item.object_reference_id,
                kind=item.kind,
                media_state=item.media_state,
            ),
            object_reference_service=_object_reference_service(request),
            allowed_buckets=_asset_library_buckets(request),
        )
    except ApiError as exc:
        if is_asset_library_media(item.kind):
            repository.quarantine_asset_item(item=item, reason=exc.code)
        raise


async def _create_index_reference(
    *,
    request: Request,
    workspace_id: UUID,
    project_id: UUID,
    bucket: object,
    storage_path: object,
    owner_type: str,
    owner_id: UUID,
    object_type: str,
    mime_type: str | None,
    size_bytes: int | None = None,
) -> UUID:
    canonical_bucket = str(bucket or "")
    canonical_path = str(storage_path or "")
    expected_prefix = f"workspaces/{workspace_id}/projects/{project_id}/"
    if (
        canonical_bucket not in _asset_library_buckets(request)
        or not canonical_path.startswith(expected_prefix)
    ):
        raise ApiError(
            409,
            "asset_library_media_invalid",
            "Server media is outside the configured project storage boundary.",
        )
    reference = await _object_reference_service(request).create_reference(
        workspace_id=workspace_id,
        project_id=project_id,
        storage_bucket=canonical_bucket,
        storage_path=canonical_path,
        owner_type=owner_type,
        owner_id=owner_id,
        object_type=object_type,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )
    return reference.id


async def _index_media_item(
    *,
    request: Request,
    kind: AssetLibraryKind,
    name: str,
    description: str,
    workspace_id: UUID,
    source_project_id: UUID,
    source_asset_id: UUID,
    source_key: str,
    object_reference_id: UUID | None,
    media_mime_type: str | None,
    media_width: int | None,
    media_height: int | None,
    media_duration_ms: int | None,
    create_reference: dict[str, object] | None = None,
):
    repository = _repository(request)
    try:
        reference_id = object_reference_id
        if reference_id is None and create_reference is not None:
            reference_id = await _create_index_reference(
                request=request,
                workspace_id=workspace_id,
                project_id=source_project_id,
                owner_id=source_asset_id,
                **create_reference,
            )
        if reference_id is None:
            raise ApiError(
                409,
                "asset_library_reference_missing",
                "No durable source reference is available for this media item.",
            )
        await resolve_asset_library_reference(
            binding=AssetLibraryReferenceBinding(
                workspace_id=workspace_id,
                source_project_id=source_project_id,
                source_asset_id=source_asset_id,
                object_reference_id=reference_id,
                kind=kind,
                media_state=AssetLibraryMediaState.VERIFIED,
            ),
            object_reference_service=_object_reference_service(request),
            allowed_buckets=_asset_library_buckets(request),
        )
    except ApiError as exc:
        return repository.upsert_asset_item(
            workspace_id=workspace_id,
            kind=kind,
            name=name,
            description=description,
            source_project_id=source_project_id,
            source_asset_id=source_asset_id,
            media_state=AssetLibraryMediaState.QUARANTINED,
            quarantine_reason=exc.code,
            media_mime_type=media_mime_type,
            media_width=media_width,
            media_height=media_height,
            media_duration_ms=media_duration_ms,
            source_key=source_key,
        )
    return repository.upsert_asset_item(
        workspace_id=workspace_id,
        kind=kind,
        name=name,
        description=description,
        source_project_id=source_project_id,
        source_asset_id=source_asset_id,
        object_reference_id=reference_id,
        media_state=AssetLibraryMediaState.VERIFIED,
        media_mime_type=media_mime_type,
        media_width=media_width,
        media_height=media_height,
        media_duration_ms=media_duration_ms,
        source_key=source_key,
    )


@router.post(
    "/projects/{project_id}/asset-library/index",
    response_model=AssetLibraryItemListResponse,
)
async def index_project_assets(
    project_id: UUID,
    request: Request,
    actor: ActorDep,
) -> AssetLibraryItemListResponse:
    caption_repository = _caption_repository(request)
    project = caption_repository.get_project_for_workspace(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    repository = _repository(request)
    indexed = []
    upload = caption_repository.get_active_upload(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    indexed.append(
        await _index_media_item(
            request=request,
            kind=AssetLibraryKind.UPLOAD,
            name=upload.original_filename,
            description="Project source upload",
            workspace_id=actor.workspace_id,
            source_project_id=project_id,
            source_asset_id=upload.id,
            source_key=f"upload:{upload.id}",
            object_reference_id=upload.source_object_reference_id,
            media_mime_type=upload.mime_type,
            media_width=upload.metadata.width,
            media_height=upload.metadata.height,
            media_duration_ms=upload.metadata.duration_ms,
        )
    )

    broll_repository = request.app.state.broll_repository
    for asset in broll_repository.list_library_assets(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=100,
    ):
        metadata = asset.raw_metadata
        indexed.append(
            await _index_media_item(
                request=request,
                kind=AssetLibraryKind.BROLL,
                name=asset.title,
                description="Reusable B-roll",
                workspace_id=actor.workspace_id,
                source_project_id=project_id,
                source_asset_id=asset.id,
                source_key=f"broll:{asset.id}",
                object_reference_id=_reference_id(metadata.get("object_reference_id")),
                media_mime_type="video/mp4",
                media_width=asset.width,
                media_height=asset.height,
                media_duration_ms=asset.duration_ms,
                create_reference={
                    "bucket": metadata.get("storage_bucket"),
                    "storage_path": metadata.get("storage_path"),
                    "owner_type": "stock_asset",
                    "object_type": "broll_asset",
                    "mime_type": "video/mp4",
                },
            )
        )

    render_repository = request.app.state.render_repository
    for export in render_repository.list_exports(
        project_id=project_id,
        workspace_id=actor.workspace_id,
        limit=100,
    ):
        indexed.append(
            await _index_media_item(
                request=request,
                kind=AssetLibraryKind.EXPORT,
                name=f"{project.title} export v{export.version}",
                description="Past export, ready to reuse as a B-roll asset",
                workspace_id=actor.workspace_id,
                source_project_id=project_id,
                source_asset_id=export.id,
                source_key=f"export:{export.id}",
                object_reference_id=None,
                media_mime_type=f"video/{export.format.value}",
                media_width=export.width,
                media_height=export.height,
                media_duration_ms=export.duration_ms,
                create_reference={
                    "bucket": export.storage_bucket,
                    "storage_path": export.storage_path,
                    "owner_type": "export",
                    "object_type": "export",
                    "mime_type": f"video/{export.format.value}",
                    "size_bytes": export.file_size_bytes,
                },
            )
        )

    music_repository = request.app.state.music_repository
    selection = music_repository.latest_selection(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    if selection and selection.music_track_id:
        track = music_repository.get_track(
            project_id=project_id,
            workspace_id=actor.workspace_id,
            track_id=selection.music_track_id,
        )
        indexed.append(
            repository.upsert_asset_item(
                workspace_id=actor.workspace_id,
                kind=AssetLibraryKind.MUSIC,
                name=track.title,
                description="Reusable licensed music selection",
                source_project_id=project_id,
                source_asset_id=track.id,
                media_state=AssetLibraryMediaState.NOT_APPLICABLE,
                source_key=f"music:{track.id}",
            )
        )

    plan = _plan_record(
        request, project_id=project_id, workspace_id=actor.workspace_id, plan_id=None
    )
    if plan:
        for cut in plan.plan.cuts:
            indexed.append(
                repository.upsert_asset_item(
                    workspace_id=actor.workspace_id,
                    kind=AssetLibraryKind.CLIP,
                    name=f"{project.title}: {cut.reason}",
                    description="Saved source clip range",
                    source_project_id=project_id,
                    media_state=AssetLibraryMediaState.NOT_APPLICABLE,
                    recipe=AssetLibraryClipRecipe(
                        start_ms=cut.start_ms,
                        end_ms=cut.end_ms,
                        reason=cut.reason,
                    ),
                    source_key=f"clip:{plan.id}:{cut.start_ms}:{cut.end_ms}",
                )
            )
        indexed.append(
            repository.upsert_asset_item(
                workspace_id=actor.workspace_id,
                kind=AssetLibraryKind.STYLE,
                name=f"{project.title} style",
                description="Reuse this project’s caption, effects, and pacing settings",
                source_project_id=project_id,
                source_asset_id=plan.id,
                media_state=AssetLibraryMediaState.NOT_APPLICABLE,
                recipe=AssetLibraryStyleRecipe(
                    style=plan.plan.style.value,
                    target_duration_s=plan.plan.target_duration_s,
                    broll_density=plan.broll_density.value,
                    effects=[str(effect) for effect in plan.plan.effects],
                    color_grade=str(plan.plan.color_grade or ""),
                ),
                source_key=f"style:{plan.id}:{plan.version}",
            )
        )
    return AssetLibraryItemListResponse(items=[item.response() for item in indexed])


@router.get("/asset-library/items", response_model=AssetLibraryItemListResponse)
async def list_asset_library_items(
    request: Request,
    actor: ActorDep,
    query: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> AssetLibraryItemListResponse:
    items = _repository(request).list_asset_items(
        workspace_id=actor.workspace_id,
        query=query,
        limit=limit,
    )
    return AssetLibraryItemListResponse(items=[item.response() for item in items])


@router.post("/asset-library/items", response_model=AssetLibraryItemResponse, status_code=201)
async def create_asset_library_item(
    payload: AssetLibraryItemCreateRequest,
    request: Request,
    actor: ActorDep,
) -> AssetLibraryItemResponse:
    if payload.kind not in {AssetLibraryKind.CLIP, AssetLibraryKind.STYLE}:
        raise ApiError(
            409,
            "asset_library_media_server_indexed",
            "Managed media must be indexed from a server-owned project record.",
        )
    if payload.source_project_id is not None:
        _caption_repository(request).get_project_for_workspace(
            project_id=payload.source_project_id,
            workspace_id=actor.workspace_id,
        )
    item = _repository(request).upsert_asset_item(
        workspace_id=actor.workspace_id,
        kind=payload.kind,
        name=payload.name.strip(),
        description=payload.description.strip(),
        source_project_id=payload.source_project_id,
        source_asset_id=payload.source_asset_id,
        media_state=AssetLibraryMediaState.NOT_APPLICABLE,
    )
    return item.response()


@router.get(
    "/asset-library/items/{item_id}/media-url",
    response_model=AssetLibraryMediaUrlResponse,
)
async def get_asset_library_media_url(
    item_id: UUID,
    request: Request,
    actor: ActorDep,
) -> AssetLibraryMediaUrlResponse:
    item = _repository(request).get_asset_item(item_id=item_id, workspace_id=actor.workspace_id)
    reference = await _resolve_asset_library_media(item=item, request=request)
    signed = await _object_reference_service(request).get_signed_url(
        workspace_id=item.workspace_id,
        project_id=item.source_project_id,
        reference_id=reference.id,
        principal_id=actor.user_id,
    )
    return AssetLibraryMediaUrlResponse(
        object_reference_id=signed.reference_id,
        url=signed.url,
        expires_at=signed.expires_at,
        ttl_seconds=signed.ttl_seconds,
    )


@router.delete(
    "/asset-library/items/{item_id}",
    response_model=AssetLibraryItemDeleteResponse,
)
async def delete_asset_library_item(
    item_id: UUID,
    request: Request,
    actor: ActorDep,
) -> AssetLibraryItemDeleteResponse:
    repository = _repository(request)
    item = repository.get_asset_item(item_id=item_id, workspace_id=actor.workspace_id)
    if is_asset_library_media(item.kind):
        # This removes the library index only; the validated source reference is
        # intentionally retained for the source project and its retention policy.
        await _resolve_asset_library_media(item=item, request=request)
    repository.delete_asset_item(item=item)
    return AssetLibraryItemDeleteResponse(status="removed", id=item_id)


@router.patch("/asset-library/items/{item_id}/favorite", response_model=AssetLibraryItemResponse)
async def update_asset_favorite(
    item_id: UUID,
    payload: AssetFavoriteUpdateRequest,
    request: Request,
    actor: ActorDep,
) -> AssetLibraryItemResponse:
    repository = _repository(request)
    item = repository.get_asset_item(item_id=item_id, workspace_id=actor.workspace_id)
    return repository.set_favorite(item=item, is_favorite=payload.is_favorite).response()


@router.get("/asset-library/collections", response_model=AssetCollectionListResponse)
async def list_asset_collections(request: Request, actor: ActorDep) -> AssetCollectionListResponse:
    collections = _repository(request).list_collections(workspace_id=actor.workspace_id)
    return AssetCollectionListResponse(
        collections=[collection.response() for collection in collections]
    )


@router.post("/asset-library/collections", response_model=AssetCollectionResponse, status_code=201)
async def create_asset_collection(
    payload: AssetCollectionCreateRequest,
    request: Request,
    actor: ActorDep,
) -> AssetCollectionResponse:
    return (
        _repository(request)
        .create_collection(
            workspace_id=actor.workspace_id,
            name=payload.name.strip(),
        )
        .response()
    )


@router.post(
    "/asset-library/collections/{collection_id}/items", response_model=AssetCollectionResponse
)
async def add_asset_collection_item(
    collection_id: UUID,
    payload: AssetCollectionItemRequest,
    request: Request,
    actor: ActorDep,
) -> AssetCollectionResponse:
    return (
        _repository(request)
        .add_collection_item(
            collection_id=collection_id,
            item_id=payload.asset_id,
            workspace_id=actor.workspace_id,
        )
        .response()
    )


@router.post(
    "/asset-library/items/{item_id}/reuse/projects/{project_id}", response_model=AssetReuseResponse
)
async def reuse_asset_in_project(
    item_id: UUID,
    project_id: UUID,
    request: Request,
    actor: ActorDep,
) -> AssetReuseResponse:
    repository = _repository(request)
    item = repository.get_asset_item(item_id=item_id, workspace_id=actor.workspace_id)
    _caption_repository(request).get_project_for_workspace(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    if item.kind in {AssetLibraryKind.BROLL, AssetLibraryKind.EXPORT, AssetLibraryKind.UPLOAD}:
        reference = await _resolve_asset_library_media(item=item, request=request)
        width = item.media_width or 1080
        height = item.media_height or 1920
        target = request.app.state.broll_repository.upsert_asset(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            provider=StockProviderKey.USER_LIBRARY,
            provider_asset_id=f"workspace-library-{reference.id}",
            title=item.name,
            thumbnail_url=None,
            preview_video_url=None,
            download_url=None,
            license="user_owned",
            width=width,
            height=height,
            duration_ms=item.media_duration_ms or 0,
            orientation=(
                StockOrientation.PORTRAIT
                if height > width
                else StockOrientation.LANDSCAPE
                if width > height
                else StockOrientation.SQUARE
            ),
            score=1.0,
            raw_metadata={
                "status": "ready",
                "object_reference_id": str(reference.id),
                "source_project_id": str(reference.project_id),
                # These values are derived only after durable reference
                # verification; the reference ID remains the authority.
                "storage_bucket": reference.storage_bucket,
                "storage_path": reference.storage_path,
                "reused_library_item_id": str(item.id),
            },
        )
        return AssetReuseResponse(
            status="imported",
            target_asset_id=target.id,
            message="Asset imported into this project’s B-roll library.",
        )
    if item.kind == AssetLibraryKind.MUSIC:
        if item.source_project_id is None or item.source_asset_id is None:
            raise ApiError(
                409,
                "asset_library_media_unavailable",
                "This music item is missing its server-owned source record.",
            )
        track = request.app.state.music_repository.get_track(
            project_id=item.source_project_id,
            workspace_id=actor.workspace_id,
            track_id=item.source_asset_id,
        )
        target = request.app.state.music_repository.upsert_track(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            provider=MusicProviderKey(track.provider),
            provider_track_id=str(track.provider_track_id),
            title=str(track.title),
            license=str(track.license),
            duration_ms=int(track.duration_ms),
            mood_tags=list(track.mood_tags),
            bpm=track.bpm,
            energy=track.energy,
            vocals=bool(track.vocals),
            loopable=bool(track.loopable),
            beat_map=list(track.beat_map),
            raw_metadata={
                **track.raw_metadata,
                "reused_library_item_id": str(item.id),
            },
        )
        return AssetReuseResponse(
            status="imported",
            target_asset_id=target.id,
            message="Music track imported; select it in the Audio panel.",
        )
    if item.kind == AssetLibraryKind.STYLE:
        if not isinstance(item.recipe, AssetLibraryStyleRecipe):
            raise ApiError(
                409,
                "asset_library_recipe_unavailable",
                "This style recipe is unavailable pending recovery.",
            )
        template = request.app.state.scale_repository.create(
            workspace_id=actor.workspace_id,
            project_id=None,
            kind="saved_template",
            name=f"{item.name} reuse",
            status=ScaleResourceStatus.READY,
            payload={"settings": item.recipe.model_dump(mode="json")},
        )
        return AssetReuseResponse(
            status="template_saved",
            saved_template_id=template.id,
            message="Style recipe saved as a workspace template.",
        )
    raise ApiError(
        409,
        "asset_reuse_unsupported",
        "This library item is informational and cannot be imported directly.",
    )
