from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.billing import build_billing_status
from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError
from app.schemas.billing import BillingPlanKey
from app.schemas.media_uploads import (
    MediaUploadCompleteRequest,
    MediaUploadSessionResponse,
    PartCompleteRequest,
    PartCompleteResponse,
    PartUploadUrl,
)
from app.schemas.scale import (
    AudioIntelligenceResponse,
    BatchCreateRequest,
    BrandKitCreateRequest,
    BrandKitUpdateRequest,
    CollaborationManifestUpdateRequest,
    GeneratedBrollRequest,
    ModelTrainingRequest,
    SavedTemplateCreateRequest,
    SavedTemplateUpdateRequest,
    ScaleResourceListResponse,
    ScaleResourceResponse,
    ScaleResourceStatus,
    StemSelectionUpdateRequest,
    StemSeparationRequest,
    StyleProfileCreateRequest,
    StyleReferenceCompleteRequest,
    StyleReferenceInitRequest,
    StyleReferenceInitResponse,
    StyleReferenceMultipartInitResponse,
    StyleTrainRequest,
)
from app.services.billing import PLAN_DEFINITIONS
from app.services.object_references import ObjectReferenceService
from app.services.offload import run_blocking

router = APIRouter(tags=["Phase 5 intelligence and scale"])
ActorDep = Annotated[Actor, Depends(get_actor)]


async def _repository_call(repository, async_name: str, sync_name: str, **kwargs):
    async_method = getattr(repository, async_name, None)
    if async_method is not None:
        return await async_method(**kwargs)
    return getattr(repository, sync_name)(**kwargs)


async def _scale_create(request: Request, **values):
    return await _repository_call(
        request.app.state.scale_repository,
        "create_async",
        "create",
        **values,
    )


async def _scale_get(request: Request, *, resource_id: UUID, workspace_id: UUID):
    return await _repository_call(
        request.app.state.scale_repository,
        "get_async",
        "get",
        resource_id=resource_id,
        workspace_id=workspace_id,
    )


async def _scale_list(
    request: Request,
    *,
    workspace_id: UUID,
    kind: str | None = None,
    project_id: UUID | None = None,
    limit: int = 100,
):
    return await _repository_call(
        request.app.state.scale_repository,
        "list_async",
        "list",
        workspace_id=workspace_id,
        kind=kind,
        project_id=project_id,
        limit=limit,
    )


async def _scale_latest(
    request: Request,
    *,
    workspace_id: UUID,
    kind: str,
    project_id: UUID | None = None,
    status: ScaleResourceStatus | None = None,
):
    return await _repository_call(
        request.app.state.scale_repository,
        "latest_async",
        "latest",
        workspace_id=workspace_id,
        kind=kind,
        project_id=project_id,
        status=status,
    )


async def _scale_update(request: Request, *, record, **values):
    return await _repository_call(
        request.app.state.scale_repository,
        "update_async",
        "update",
        record=record,
        **values,
    )


async def _scale_update_if_revision(
    request: Request,
    *,
    record,
    expected_revision: int,
    **values,
):
    return await _repository_call(
        request.app.state.scale_repository,
        "update_if_revision_async",
        "update_if_revision",
        record=record,
        expected_revision=expected_revision,
        **values,
    )


@router.get(
    "/projects/{project_id}/phase5/audio-intelligence",
    response_model=AudioIntelligenceResponse,
)
async def get_audio_intelligence(
    project_id: UUID, actor: ActorDep, request: Request
) -> AudioIntelligenceResponse:
    transcript = request.app.state.caption_repository.latest_transcript(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    if not transcript:
        raise ApiError(404, "transcript_not_found", "Transcribe this project first.")
    return AudioIntelligenceResponse(
        project_id=project_id,
        audio_intelligence=dict(transcript.raw_payload.get("audio_intelligence") or {}),
    )


@router.get(
    "/projects/{project_id}/phase5/resources",
    response_model=ScaleResourceListResponse,
)
async def list_project_scale_resources(
    project_id: UUID,
    actor: ActorDep,
    request: Request,
    kind: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ScaleResourceListResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    resources = await _scale_list(
        request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind=kind,
        limit=limit,
    )
    return ScaleResourceListResponse(resources=[item.to_response() for item in resources])


@router.get("/phase5/resources/{resource_id}", response_model=ScaleResourceResponse)
async def get_scale_resource(
    resource_id: UUID, actor: ActorDep, request: Request
) -> ScaleResourceResponse:
    return (
        await _scale_get(
            request,
            resource_id=resource_id,
            workspace_id=actor.workspace_id,
        )
    ).to_response()


@router.post(
    "/projects/{project_id}/phase5/stems",
    response_model=ScaleResourceResponse,
    status_code=202,
)
async def separate_audio_stems(
    project_id: UUID,
    payload: StemSeparationRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    request.app.state.upload_repository.get_active_upload(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    record = request.app.state.scale_repository.create(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="stem_separation",
        name="Source audio stems",
        status=ScaleResourceStatus.QUEUED,
        payload=payload.model_dump(mode="json"),
    )
    request.app.state.scale_queue.enqueue_resource(
        resource_id=record.id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        resource_kind=record.kind,
    )
    return record.to_response()


@router.patch(
    "/projects/{project_id}/phase5/stems/{resource_id}",
    response_model=ScaleResourceResponse,
)
async def select_audio_stem(
    project_id: UUID,
    resource_id: UUID,
    payload: StemSelectionUpdateRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id,
        workspace_id=actor.workspace_id,
    )
    record = await _scale_get(
        request,
        resource_id=resource_id,
        workspace_id=actor.workspace_id,
    )
    if record.project_id != project_id:
        raise ApiError(404, "stem_separation_not_found", "Stem separation was not found.")
    if record.kind != "stem_separation":
        raise ApiError(409, "stem_resource_kind_invalid", "Resource is not a stem separation.")
    if record.status != ScaleResourceStatus.READY:
        raise ApiError(
            409,
            "stem_separation_not_ready",
            "Wait for stem separation to finish before selecting an audio stem.",
        )

    selected = record.result.get(payload.active)
    expected_path = (
        f"workspaces/{record.workspace_id}/projects/{project_id}/stems/"
        f"{record.id}/{payload.active}.wav"
    )
    if (
        not isinstance(selected, dict)
        or not isinstance(selected.get("bucket"), str)
        or not selected["bucket"]
        or not isinstance(selected.get("path"), str)
        or selected["path"] != expected_path
    ):
        raise ApiError(
            409,
            "stem_artifact_unavailable",
            "The selected stem does not have a durable audio artifact.",
        )

    object_storage = getattr(request.app.state, "object_storage", None)
    if object_storage is None or not callable(getattr(object_storage, "inspect_object", None)):
        raise ApiError(
            503,
            "stem_artifact_verification_unavailable",
            "Stem artifact verification is temporarily unavailable.",
        )
    try:
        await run_blocking(
            object_storage.inspect_object,
            bucket=selected["bucket"],
            path=selected["path"],
        )
    except ApiError as exc:
        if exc.code in {"storage_object_missing", "storage_path_invalid"}:
            raise ApiError(
                409,
                "stem_artifact_unavailable",
                "The selected stem does not have a durable audio artifact.",
            ) from exc
        raise ApiError(
            502,
            "stem_artifact_verification_failed",
            "The selected stem could not be verified in storage.",
        ) from exc

    updated = await _scale_update_if_revision(
        request,
        record=record,
        expected_revision=payload.expected_revision,
        result={**record.result, "active": payload.active},
        revision=record.revision + 1,
    )
    return updated.to_response()


@router.post(
    "/projects/{project_id}/phase5/generated-broll",
    response_model=ScaleResourceResponse,
    status_code=202,
)
async def generate_runway_broll(
    project_id: UUID,
    payload: GeneratedBrollRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    plan = (
        build_billing_status(actor.workspace_id, request.app.state.billing_repository).current_plan
        if request.app.state.settings.billing_enabled
        else PLAN_DEFINITIONS[BillingPlanKey.AGENCY]
    )
    if not plan.entitlements.premium_effects:
        raise ApiError(402, "premium_feature_required", "Runway B-roll requires Pro or Agency.")
    if (
        request.app.state.settings.app_env == "production"
        and getattr(
            request.app.state,
            "runway_gateway_route",
            None,
        )
        is None
    ):
        raise ApiError(
            503,
            "runway_not_configured",
            "Configure a generation.video_submit gateway route first.",
        )
    record = request.app.state.scale_repository.create(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="generated_broll",
        name=payload.prompt[:120],
        status=ScaleResourceStatus.QUEUED,
        payload=payload.model_dump(mode="json"),
    )
    request.app.state.scale_queue.enqueue_resource(
        resource_id=record.id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        resource_kind=record.kind,
    )
    return record.to_response()


@router.post(
    "/projects/{project_id}/phase5/style-profiles",
    response_model=ScaleResourceResponse,
    status_code=201,
)
async def create_style_profile(
    project_id: UUID,
    payload: StyleProfileCreateRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    return request.app.state.scale_repository.create(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="style_profile",
        name=payload.name,
        status=ScaleResourceStatus.DRAFT,
        payload={"target_style": payload.target_style.value, "references": []},
    ).to_response()


def _style_media_service(request: Request):
    service = getattr(request.app.state, "media_upload_session_service", None)
    if service is None:
        raise ApiError(
            503, "media_upload_session_unavailable", "Style uploads require media sessions."
        )
    return service


def _style_reference_service(request: Request) -> ObjectReferenceService:
    service = getattr(request.app.state, "object_reference_service", None)
    if service is None:
        raise ApiError(
            503, "object_reference_unavailable", "Style media references are unavailable."
        )
    return service


async def _style_session_response(session, service) -> MediaUploadSessionResponse:
    pending = [
        PartUploadUrl(
            part_number=number,
            upload_url=signed.url,
            upload_headers=signed.headers,
            expires_in_seconds=signed.expires_in_seconds,
        )
        for number, signed in await service.create_pending_part_urls(session)
    ]
    return MediaUploadSessionResponse(
        session_id=session.id,
        owner_id=session.owner_id,
        status=session.status,
        total_parts=session.part_count,
        completed_parts=session.completed_parts,
        uploaded_bytes=session.uploaded_bytes,
        file_size_bytes=session.declared_size_bytes,
        part_size_bytes=session.part_size_bytes,
        pending_part_urls=pending,
        expires_at=session.expires_at,
        object_reference_id=session.object_reference_id,
        verified_size_bytes=session.verified_size_bytes,
        verified_sha256=session.verified_sha256,
    )


async def _require_owned_style_reference_session(
    *,
    service,
    profile,
    session_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
):
    """Check profile/session linkage before a route can mutate multipart state."""
    session = await service.inspect(
        session_id=session_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    linked = any(
        item.get("media_upload_session_id") == str(session.id)
        for item in profile.payload.get("references", [])
    )
    if (
        session.owner_type != "style_profile"
        or session.owner_id != profile.id
        or session.object_type != "style_reference"
        or not linked
    ):
        raise ApiError(404, "media_upload_session_not_found", "Style upload session was not found.")
    return session


async def _init_style_reference_media(
    *,
    project_id: UUID,
    profile_id: UUID,
    payload: StyleReferenceInitRequest,
    actor: Actor,
    request: Request,
):
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    if profile.project_id != project_id:
        raise ApiError(422, "style_profile_project_mismatch", "Style profile project differs.")
    suffix = Path(payload.filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise ApiError(422, "style_reference_type_invalid", "Upload MP4, MOV, M4V, or WebM.")
    references = list(profile.payload.get("references") or [])
    active_reference_count = sum(item.get("status") != "deleted" for item in references)
    if active_reference_count >= 5:
        raise ApiError(
            409, "style_reference_limit", "A style profile uses exactly five references."
        )
    reference_id = uuid4()
    path = (
        f"workspaces/{actor.workspace_id}/projects/{project_id}/style-references/"
        f"{profile.id}/{reference_id}{suffix}"
    )
    service = _style_media_service(request)
    session = await service.initiate(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        owner_type="style_profile",
        owner_id=profile.id,
        object_type="style_reference",
        final_storage_bucket=request.app.state.settings.upload_storage_bucket,
        final_storage_path=path,
        filename=payload.filename,
        mime_type=payload.mime_type,
        file_size_bytes=payload.file_size_bytes,
        sha256_hash=payload.sha256_hash,
    )
    references.append(
        {
            "id": str(reference_id),
            "filename": payload.filename,
            "status": "uploading",
            "media_upload_session_id": str(session.id),
            "object_reference_id": None,
            "verified_size_bytes": None,
            "verified_sha256": None,
        }
    )
    try:
        profile = await _scale_update_if_revision(
            request,
            record=profile,
            expected_revision=profile.revision,
            payload={**profile.payload, "references": references},
            revision=profile.revision + 1,
        )
    except Exception:
        # Do not compensate a session when the profile PATCH merely lost its
        # response. A fresh read proves whether its durable owner link committed.
        try:
            reconciled = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
            if any(
                item.get("id") == str(reference_id)
                and item.get("media_upload_session_id") == str(session.id)
                for item in reconciled.payload.get("references", [])
            ):
                return reconciled, reference_id, session
        except Exception:
            pass
        try:
            await service.abort(
                session_id=session.id,
                workspace_id=actor.workspace_id,
                project_id=project_id,
            )
        except Exception:
            # The generic session remains durable and will be retried by expiry/
            # retention even if an immediate compensation call is unavailable.
            pass
        raise
    return profile, reference_id, session


async def _complete_style_reference_media(
    *,
    project_id: UUID,
    profile_id: UUID,
    reference_id: UUID,
    sha256_hash: str | None,
    actor: Actor,
    request: Request,
) -> ScaleResourceResponse:
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    if profile.project_id != project_id:
        raise ApiError(422, "style_profile_project_mismatch", "Style profile project differs.")
    references = list(profile.payload.get("references") or [])
    reference = next((item for item in references if item.get("id") == str(reference_id)), None)
    if reference is None:
        raise ApiError(404, "style_reference_not_found", "Style reference was not found.")
    session_value = reference.get("media_upload_session_id")
    if not session_value:
        raise ApiError(
            409, "style_reference_session_required", "Style reference lacks a durable session."
        )
    session = await _style_media_service(request).inspect(
        session_id=UUID(str(session_value)),
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    if (
        session.owner_type != "style_profile"
        or session.owner_id != profile.id
        or session.object_type != "style_reference"
    ):
        raise ApiError(
            409, "style_reference_session_invalid", "Style reference session is invalid."
        )
    completion = await _style_media_service(request).complete(
        session_id=session.id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        sha256_hash=sha256_hash,
    )
    reference.update(
        {
            "status": "verified",
            "object_reference_id": str(completion.reference.id),
            "verified_size_bytes": completion.session.verified_size_bytes,
            "verified_sha256": completion.session.verified_sha256,
            "storage_quota_reservation_id": str(completion.session.quota_reservation_id),
        }
    )
    profile = await _scale_update_if_revision(
        request,
        record=profile,
        expected_revision=profile.revision,
        payload={**profile.payload, "references": references},
        revision=profile.revision + 1,
    )
    return profile.to_response()


@router.post(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/init",
    response_model=StyleReferenceInitResponse,
    status_code=201,
)
async def init_style_reference(
    project_id: UUID,
    profile_id: UUID,
    payload: StyleReferenceInitRequest,
    actor: ActorDep,
    request: Request,
) -> StyleReferenceInitResponse:
    # Legacy one-PUT facade over the same verified multipart state machine.
    profile, reference_id, session = await _init_style_reference_media(
        project_id=project_id,
        profile_id=profile_id,
        payload=payload,
        actor=actor,
        request=request,
    )
    first = (await _style_media_service(request).create_pending_part_urls(session))[0][1]
    return StyleReferenceInitResponse(
        profile=profile.to_response(),
        reference_id=reference_id,
        signed_upload_url=first.url,
        upload_headers=first.headers,
        expires_in_seconds=first.expires_in_seconds,
        session_id=session.id,
        part_size_bytes=session.part_size_bytes,
    )


@router.post(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/init",
    response_model=StyleReferenceMultipartInitResponse,
    status_code=201,
)
async def init_style_reference_multipart(
    project_id: UUID,
    profile_id: UUID,
    payload: StyleReferenceInitRequest,
    actor: ActorDep,
    request: Request,
) -> StyleReferenceMultipartInitResponse:
    profile, reference_id, session = await _init_style_reference_media(
        project_id=project_id,
        profile_id=profile_id,
        payload=payload,
        actor=actor,
        request=request,
    )
    return StyleReferenceMultipartInitResponse(
        profile=profile.to_response(),
        reference_id=reference_id,
        session=await _style_session_response(session, _style_media_service(request)),
    )


@router.get(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/{session_id}",
    response_model=MediaUploadSessionResponse,
)
async def get_style_reference_multipart(
    project_id: UUID,
    profile_id: UUID,
    session_id: UUID,
    actor: ActorDep,
    request: Request,
) -> MediaUploadSessionResponse:
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    if profile.project_id != project_id:
        raise ApiError(422, "style_profile_project_mismatch", "Style profile project differs.")
    service = _style_media_service(request)
    await _require_owned_style_reference_session(
        service=service,
        profile=profile,
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    session = await service.resume(
        session_id=session_id, workspace_id=actor.workspace_id, project_id=project_id
    )
    return await _style_session_response(session, service)


@router.post(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/{session_id}/parts/{part_number}/complete",
    response_model=PartCompleteResponse,
)
async def confirm_style_reference_multipart_part(
    project_id: UUID,
    profile_id: UUID,
    session_id: UUID,
    part_number: int,
    payload: PartCompleteRequest,
    actor: ActorDep,
    request: Request,
) -> PartCompleteResponse:
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    if profile.project_id != project_id:
        raise ApiError(422, "style_profile_project_mismatch", "Style profile project differs.")
    service = _style_media_service(request)
    await _require_owned_style_reference_session(
        service=service,
        profile=profile,
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    confirmed = await service.confirm_part(
        session_id=session_id,
        part_number=part_number,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        etag=payload.etag,
    )
    return PartCompleteResponse(
        session_id=confirmed.session_id,
        part_number=confirmed.part_number,
        status="uploaded",
        verified_size_bytes=confirmed.verified_size_bytes,
        completed_parts=confirmed.completed_parts,
        uploaded_bytes=confirmed.uploaded_bytes,
    )


@router.post(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/{session_id}/complete",
    response_model=ScaleResourceResponse,
)
async def complete_style_reference_multipart(
    project_id: UUID,
    profile_id: UUID,
    session_id: UUID,
    payload: MediaUploadCompleteRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    reference = next(
        (
            item
            for item in profile.payload.get("references", [])
            if item.get("media_upload_session_id") == str(session_id)
        ),
        None,
    )
    if reference is None:
        raise ApiError(404, "style_reference_not_found", "Style reference was not found.")
    return await _complete_style_reference_media(
        project_id=project_id,
        profile_id=profile_id,
        reference_id=UUID(str(reference["id"])),
        sha256_hash=payload.sha256_hash,
        actor=actor,
        request=request,
    )


@router.post(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/multipart/{session_id}/abort",
    response_model=MediaUploadSessionResponse,
)
async def abort_style_reference_multipart(
    project_id: UUID,
    profile_id: UUID,
    session_id: UUID,
    actor: ActorDep,
    request: Request,
) -> MediaUploadSessionResponse:
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    if profile.project_id != project_id:
        raise ApiError(422, "style_profile_project_mismatch", "Style profile project differs.")
    service = _style_media_service(request)
    await _require_owned_style_reference_session(
        service=service,
        profile=profile,
        session_id=session_id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    session = await service.abort(
        session_id=session_id, workspace_id=actor.workspace_id, project_id=project_id
    )
    references = list(profile.payload.get("references") or [])
    for reference in references:
        if reference.get("media_upload_session_id") == str(session.id):
            reference["status"] = session.status.value
    await _scale_update_if_revision(
        request,
        record=profile,
        expected_revision=profile.revision,
        payload={**profile.payload, "references": references},
        revision=profile.revision + 1,
    )
    return await _style_session_response(session, service)


@router.post(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/complete",
    response_model=ScaleResourceResponse,
)
async def complete_style_reference(
    project_id: UUID,
    profile_id: UUID,
    payload: StyleReferenceCompleteRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    return await _complete_style_reference_media(
        project_id=project_id,
        profile_id=profile_id,
        reference_id=payload.reference_id,
        sha256_hash=None,
        actor=actor,
        request=request,
    )


@router.delete(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/references/{reference_id}",
    response_model=ScaleResourceResponse,
)
async def delete_style_reference(
    project_id: UUID,
    profile_id: UUID,
    reference_id: UUID,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    """Retire a style reference while retaining quota until physical cleanup finishes."""
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    if profile.project_id != project_id:
        raise ApiError(422, "style_profile_project_mismatch", "Style profile project differs.")
    references = list(profile.payload.get("references") or [])
    reference = next((item for item in references if item.get("id") == str(reference_id)), None)
    if reference is None:
        raise ApiError(404, "style_reference_not_found", "Style reference was not found.")
    session_value = reference.get("media_upload_session_id")
    if not session_value:
        raise ApiError(
            409, "style_reference_session_required", "Style reference lacks a durable session."
        )

    service = _style_media_service(request)
    session = await _require_owned_style_reference_session(
        service=service,
        profile=profile,
        session_id=UUID(str(session_value)),
        workspace_id=actor.workspace_id,
        project_id=project_id,
    )
    if session.status.value == "completed":
        await service.delete_completed(
            session_id=session.id,
            workspace_id=actor.workspace_id,
            project_id=project_id,
        )
    else:
        session = await service.abort(
            session_id=session.id,
            workspace_id=actor.workspace_id,
            project_id=project_id,
        )
    reference["status"] = "deleted"
    reference["deletion_requested_at"] = datetime.now(UTC).isoformat()
    if session.status.value != "completed":
        reference["failure_code"] = session.failure_code
    profile = await _scale_update_if_revision(
        request,
        record=profile,
        expected_revision=profile.revision,
        payload={**profile.payload, "references": references},
        revision=profile.revision + 1,
    )
    return profile.to_response()


@router.post(
    "/projects/{project_id}/phase5/style-profiles/{profile_id}/train",
    response_model=ScaleResourceResponse,
    status_code=202,
)
async def train_style_profile(
    project_id: UUID,
    profile_id: UUID,
    payload: StyleTrainRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    del payload
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    references = [
        item for item in profile.payload.get("references") or [] if item.get("status") != "deleted"
    ]
    if len(references) != 5:
        raise ApiError(
            409,
            "style_references_incomplete",
            "Style training requires exactly five verified references.",
        )
    reference_service = _style_reference_service(request)
    allowed_bucket = request.app.state.settings.upload_storage_bucket
    expected_prefix = (
        f"workspaces/{actor.workspace_id}/projects/{project_id}/style-references/{profile.id}/"
    )
    reference_ids: set[UUID] = set()
    for item in references:
        reference_id = item.get("object_reference_id")
        stable_id = item.get("id")
        if (
            item.get("status") != "verified"
            or not reference_id
            or not stable_id
            or item.get("verified_size_bytes") is None
            or not item.get("verified_sha256")
        ):
            raise ApiError(
                409,
                "style_references_incomplete",
                "Every style reference must be active and server verified.",
            )
        try:
            durable_id = UUID(str(reference_id))
        except ValueError as exc:
            raise ApiError(
                409, "style_reference_invalid", "Style reference identity is invalid."
            ) from exc
        if durable_id in reference_ids:
            raise ApiError(409, "style_reference_invalid", "Style references must be distinct.")
        reference_ids.add(durable_id)
        durable = await reference_service.get_active_reference(
            workspace_id=actor.workspace_id,
            project_id=project_id,
            reference_id=durable_id,
        )
        if (
            durable.owner_type != "style_profile"
            or durable.owner_id != profile.id
            or durable.object_type != "style_reference"
            or durable.storage_bucket != allowed_bucket
            or not durable.storage_path.startswith(expected_prefix)
            or not durable.storage_path.split("/")[-1].startswith(f"{stable_id}.")
            or durable.size_bytes != int(item["verified_size_bytes"])
            or durable.sha256 != str(item["verified_sha256"])
        ):
            raise ApiError(
                409,
                "style_reference_invalid",
                "Style references must be active, owned, and verified project media.",
            )
    profile = await _scale_update(
        request,
        record=profile,
        status=ScaleResourceStatus.QUEUED,
        error_message=None,
        revision=profile.revision + 1,
    )
    request.app.state.scale_queue.enqueue_resource(
        resource_id=profile.id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        resource_kind=profile.kind,
    )
    return profile.to_response()


@router.get("/phase5/templates", response_model=ScaleResourceListResponse)
async def list_saved_templates(
    actor: ActorDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ScaleResourceListResponse:
    records = await _scale_list(
        request,
        workspace_id=actor.workspace_id,
        kind="saved_template",
        limit=limit,
    )
    return ScaleResourceListResponse(resources=[item.to_response() for item in records])


@router.post("/phase5/templates", response_model=ScaleResourceResponse, status_code=201)
async def create_saved_template(
    payload: SavedTemplateCreateRequest, actor: ActorDep, request: Request
) -> ScaleResourceResponse:
    return (
        await _scale_create(
            request,
            workspace_id=actor.workspace_id,
            project_id=None,
            kind="saved_template",
            name=payload.name,
            status=ScaleResourceStatus.READY,
            payload={"settings": payload.settings.model_dump(mode="json")},
        )
    ).to_response()


@router.patch("/phase5/templates/{template_id}", response_model=ScaleResourceResponse)
async def update_saved_template(
    template_id: UUID,
    payload: SavedTemplateUpdateRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    record = await _scale_get(
        request,
        resource_id=template_id,
        workspace_id=actor.workspace_id,
    )
    if record.kind != "saved_template":
        raise ApiError(409, "saved_template_required", "Resource is not a saved template.")
    values = {"revision": record.revision + 1}
    if payload.name is not None:
        values["name"] = payload.name
    if payload.settings is not None:
        values["payload"] = {"settings": payload.settings.model_dump(mode="json")}
    return (await _scale_update(request, record=record, **values)).to_response()


@router.delete("/phase5/templates/{template_id}", status_code=204)
async def delete_saved_template(template_id: UUID, actor: ActorDep, request: Request) -> Response:
    request.app.state.scale_repository.delete(
        resource_id=template_id, workspace_id=actor.workspace_id
    )
    return Response(status_code=204)


async def _validate_brand_kit_references(
    request: Request,
    *,
    workspace_id: UUID,
    settings,
) -> None:
    for resource_id, expected_kind, field_name in (
        (settings.style_profile_id, "style_profile", "style_profile_id"),
        (settings.saved_template_id, "saved_template", "saved_template_id"),
    ):
        if resource_id is None:
            continue
        resource = await _scale_get(
            request,
            resource_id=resource_id,
            workspace_id=workspace_id,
        )
        if resource.kind != expected_kind:
            raise ApiError(
                422,
                "brand_kit_reference_invalid",
                f"{field_name} must reference a {expected_kind.replace('_', ' ')}.",
            )


@router.get("/phase5/brand-kits", response_model=ScaleResourceListResponse)
async def list_brand_kits(
    actor: ActorDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ScaleResourceListResponse:
    records = await _scale_list(
        request,
        workspace_id=actor.workspace_id,
        kind="brand_kit",
        limit=limit,
    )
    return ScaleResourceListResponse(resources=[item.to_response() for item in records])


@router.post("/phase5/brand-kits", response_model=ScaleResourceResponse, status_code=201)
async def create_brand_kit(
    payload: BrandKitCreateRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    await _validate_brand_kit_references(
        request,
        workspace_id=actor.workspace_id,
        settings=payload.settings,
    )
    record = await _scale_create(
        request,
        workspace_id=actor.workspace_id,
        project_id=None,
        kind="brand_kit",
        name=payload.name,
        status=ScaleResourceStatus.READY,
        payload={"settings": payload.settings.model_dump(mode="json")},
    )
    return record.to_response()


@router.patch("/phase5/brand-kits/{brand_kit_id}", response_model=ScaleResourceResponse)
async def update_brand_kit(
    brand_kit_id: UUID,
    payload: BrandKitUpdateRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    record = await _scale_get(
        request,
        resource_id=brand_kit_id,
        workspace_id=actor.workspace_id,
    )
    if record.kind != "brand_kit":
        raise ApiError(409, "brand_kit_required", "Resource is not a Brand Kit.")
    if payload.settings is not None:
        await _validate_brand_kit_references(
            request,
            workspace_id=actor.workspace_id,
            settings=payload.settings,
        )
    values: dict[str, object] = {"revision": record.revision + 1}
    if payload.name is not None:
        values["name"] = payload.name
    if payload.settings is not None:
        values["payload"] = {"settings": payload.settings.model_dump(mode="json")}
    if payload.expected_revision is not None:
        updated = await _scale_update_if_revision(
            request,
            record=record,
            expected_revision=payload.expected_revision,
            **values,
        )
    else:
        updated = await _scale_update(request, record=record, **values)
    return updated.to_response()


@router.delete("/phase5/brand-kits/{brand_kit_id}", status_code=204)
async def delete_brand_kit(
    brand_kit_id: UUID,
    actor: ActorDep,
    request: Request,
) -> Response:
    record = await _scale_get(
        request,
        resource_id=brand_kit_id,
        workspace_id=actor.workspace_id,
    )
    if record.kind != "brand_kit":
        raise ApiError(409, "brand_kit_required", "Resource is not a Brand Kit.")
    async_delete = getattr(request.app.state.scale_repository, "delete_async", None)
    if async_delete is not None:
        await async_delete(resource_id=brand_kit_id, workspace_id=actor.workspace_id)
    else:
        request.app.state.scale_repository.delete(
            resource_id=brand_kit_id,
            workspace_id=actor.workspace_id,
        )
    return Response(status_code=204)


@router.get(
    "/projects/{project_id}/phase5/collaboration",
    response_model=ScaleResourceResponse,
)
async def get_collaboration_manifest(
    project_id: UUID, actor: ActorDep, request: Request
) -> ScaleResourceResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    record = await _scale_latest(
        request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="collaboration_manifest",
    )
    if not record:
        record = await _scale_create(
            request,
            workspace_id=actor.workspace_id,
            project_id=project_id,
            kind="collaboration_manifest",
            name="Shared edit manifest",
            status=ScaleResourceStatus.READY,
            payload={"manifest": {}},
            revision=0,
        )
    return record.to_response()


@router.patch(
    "/projects/{project_id}/phase5/collaboration",
    response_model=ScaleResourceResponse,
)
async def update_collaboration_manifest(
    project_id: UUID,
    payload: CollaborationManifestUpdateRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    record = await _scale_latest(
        request,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="collaboration_manifest",
    )
    if not record:
        if payload.base_revision != 0:
            raise ApiError(409, "collaboration_revision_conflict", "Reload the shared edit.")
        record = await _scale_create(
            request,
            workspace_id=actor.workspace_id,
            project_id=project_id,
            kind="collaboration_manifest",
            name="Shared edit manifest",
            status=ScaleResourceStatus.READY,
            payload={"manifest": payload.manifest},
            revision=1,
        )
        return record.to_response()
    if record.revision != payload.base_revision:
        raise ApiError(
            409,
            "collaboration_revision_conflict",
            "Another editor saved a newer revision. Reload before saving.",
            details={"current_revision": record.revision},
        )
    return (
        await _scale_update_if_revision(
            request,
            record=record,
            expected_revision=payload.base_revision,
            payload={"manifest": payload.manifest},
            revision=record.revision + 1,
        )
    ).to_response()


@router.post(
    "/projects/{project_id}/phase5/model-training",
    response_model=ScaleResourceResponse,
    status_code=202,
)
async def start_model_training(
    project_id: UUID,
    payload: ModelTrainingRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    if not getattr(request.app.state.settings, "editorial_learning_enabled", False):
        raise ApiError(
            403,
            "training_policy_disabled",
            "Editorial training is disabled for this deployment.",
        )
    learning_repository = getattr(request.app.state, "editorial_learning_repository", None)
    if learning_repository is None:
        raise ApiError(
            503,
            "editorial_learning_unavailable",
            "Editorial training governance is unavailable.",
        )
    minimum_examples = max(
        payload.minimum_examples,
        int(getattr(request.app.state.settings, "editorial_learning_minimum_examples", 100)),
    )
    dataset = learning_repository.freeze_dataset(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        user_id=actor.user_id,
        minimum_examples=minimum_examples,
    )
    record = request.app.state.scale_repository.create(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="model_training",
        name=payload.name,
        status=ScaleResourceStatus.QUEUED,
        payload={
            **payload.model_dump(mode="json"),
            "minimum_examples": minimum_examples,
            "editorial_dataset_id": str(dataset.id),
            "editorial_dataset_manifest_hash": dataset.manifest_hash,
            "editorial_user_id": str(actor.user_id),
        },
    )
    request.app.state.scale_queue.enqueue_resource(
        resource_id=record.id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        resource_kind=record.kind,
    )
    return record.to_response()


@router.post("/phase5/batches", response_model=ScaleResourceResponse, status_code=202)
async def create_batch(
    payload: BatchCreateRequest, actor: ActorDep, request: Request
) -> ScaleResourceResponse:
    record = request.app.state.scale_repository.create(
        workspace_id=actor.workspace_id,
        project_id=payload.project_ids[0],
        kind="batch",
        name=payload.name,
        status=ScaleResourceStatus.QUEUED,
        payload={**payload.model_dump(mode="json"), "creator_user_id": str(actor.user_id)},
        result={
            "items": [
                {"project_id": str(project_id), "status": "queued"}
                for project_id in payload.project_ids
            ],
            "completed": 0,
            "total": len(payload.project_ids),
        },
    )
    for project_id in payload.project_ids:
        request.app.state.project_repository.get_project(
            project_id=project_id, workspace_id=actor.workspace_id
        )
    request.app.state.scale_queue.enqueue_resource(
        resource_id=record.id,
        workspace_id=actor.workspace_id,
        project_id=payload.project_ids[0],
        resource_kind="batch",
    )
    return record.to_response()


@router.post("/phase5/batches/{batch_id}/cancel", response_model=ScaleResourceResponse)
async def cancel_batch(batch_id: UUID, actor: ActorDep, request: Request) -> ScaleResourceResponse:
    record = request.app.state.scale_repository.get(
        resource_id=batch_id, workspace_id=actor.workspace_id
    )
    if record.kind != "batch":
        raise ApiError(409, "batch_required", "This resource is not a batch.")
    if record.status in {ScaleResourceStatus.COMPLETED, ScaleResourceStatus.PARTIAL}:
        raise ApiError(409, "batch_already_finished", "This batch has already finished.")
    job_id = f"phase5--{batch_id}"
    queue_registry = request.app.state.queue_registry
    if queue_registry.get_job(job_id) is not None:
        queue_registry.cancel(job_id)
    return request.app.state.scale_repository.update(
        record=record,
        status=ScaleResourceStatus.CANCELED,
        revision=record.revision + 1,
    ).to_response()


def _profile(request: Request, *, profile_id: UUID, workspace_id: UUID):
    profile = request.app.state.scale_repository.get(
        resource_id=profile_id, workspace_id=workspace_id
    )
    if profile.kind != "style_profile":
        raise ApiError(409, "style_profile_required", "Resource is not a style profile.")
    return profile
