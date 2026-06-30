from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.billing import build_billing_status
from app.api.dependencies import Actor, get_actor
from app.api.errors import ApiError
from app.schemas.billing import BillingPlanKey
from app.schemas.scale import (
    AudioIntelligenceResponse,
    BatchCreateRequest,
    CollaborationManifestUpdateRequest,
    GeneratedBrollRequest,
    ModelTrainingRequest,
    SavedTemplateCreateRequest,
    SavedTemplateUpdateRequest,
    ScaleResourceListResponse,
    ScaleResourceResponse,
    ScaleResourceStatus,
    StemSeparationRequest,
    StyleProfileCreateRequest,
    StyleReferenceCompleteRequest,
    StyleReferenceInitRequest,
    StyleReferenceInitResponse,
    StyleTrainRequest,
)
from app.services.billing import PLAN_DEFINITIONS
from app.services.scale import FixtureRunwayProvider

router = APIRouter(tags=["Phase 5 intelligence and scale"])
ActorDep = Annotated[Actor, Depends(get_actor)]


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
) -> ScaleResourceListResponse:
    request.app.state.project_repository.get_project(
        project_id=project_id, workspace_id=actor.workspace_id
    )
    resources = request.app.state.scale_repository.list(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind=kind,
    )
    return ScaleResourceListResponse(resources=[item.to_response() for item in resources])


@router.get("/phase5/resources/{resource_id}", response_model=ScaleResourceResponse)
async def get_scale_resource(
    resource_id: UUID, actor: ActorDep, request: Request
) -> ScaleResourceResponse:
    return request.app.state.scale_repository.get(
        resource_id=resource_id, workspace_id=actor.workspace_id
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
    if request.app.state.settings.app_env == "production" and isinstance(
        request.app.state.runway_provider, FixtureRunwayProvider
    ):
        raise ApiError(503, "runway_not_configured", "Configure RUNWAY_API_KEY first.")
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
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    if profile.project_id != project_id:
        raise ApiError(422, "style_profile_project_mismatch", "Style profile project differs.")
    suffix = Path(payload.filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise ApiError(422, "style_reference_type_invalid", "Upload MP4, MOV, M4V, or WebM.")
    references = list(profile.payload.get("references") or [])
    if len(references) >= 5:
        raise ApiError(
            409, "style_reference_limit", "A style profile uses exactly five references."
        )
    reference_id = uuid4()
    bucket = request.app.state.settings.upload_storage_bucket
    path = f"workspaces/{actor.workspace_id}/style-references/{profile.id}/{reference_id}{suffix}"
    signed = request.app.state.storage_signer.create_signed_upload(
        bucket=bucket,
        path=path,
        mime_type=payload.mime_type,
        expires_in_seconds=request.app.state.settings.upload_signed_url_ttl_seconds,
    )
    references.append(
        {
            "id": str(reference_id),
            "filename": payload.filename,
            "bucket": bucket,
            "path": path,
            "status": "uploading",
            "file_size_bytes": payload.file_size_bytes,
        }
    )
    profile = request.app.state.scale_repository.update(
        record=profile,
        payload={**profile.payload, "references": references},
        revision=profile.revision + 1,
    )
    return StyleReferenceInitResponse(
        profile=profile.to_response(),
        reference_id=reference_id,
        signed_upload_url=signed.url,
        upload_headers=signed.headers,
        expires_in_seconds=request.app.state.settings.upload_signed_url_ttl_seconds,
    )


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
    profile = _profile(request, profile_id=profile_id, workspace_id=actor.workspace_id)
    if profile.project_id != project_id:
        raise ApiError(422, "style_profile_project_mismatch", "Style profile project differs.")
    references = list(profile.payload.get("references") or [])
    found = False
    for reference in references:
        if reference.get("id") == str(payload.reference_id):
            reference["status"] = "ready"
            found = True
    if not found:
        raise ApiError(404, "style_reference_not_found", "Style reference was not found.")
    return request.app.state.scale_repository.update(
        record=profile,
        payload={**profile.payload, "references": references},
        revision=profile.revision + 1,
    ).to_response()


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
    references = profile.payload.get("references") or []
    if len([item for item in references if item.get("status") == "ready"]) < 5:
        raise ApiError(409, "style_references_incomplete", "Upload five references first.")
    profile = request.app.state.scale_repository.update(
        record=profile, status=ScaleResourceStatus.QUEUED, error_message=None
    )
    request.app.state.scale_queue.enqueue_resource(
        resource_id=profile.id,
        workspace_id=actor.workspace_id,
        project_id=project_id,
        resource_kind=profile.kind,
    )
    return profile.to_response()


@router.get("/phase5/templates", response_model=ScaleResourceListResponse)
async def list_saved_templates(actor: ActorDep, request: Request) -> ScaleResourceListResponse:
    records = request.app.state.scale_repository.list(
        workspace_id=actor.workspace_id, kind="saved_template"
    )
    return ScaleResourceListResponse(resources=[item.to_response() for item in records])


@router.post("/phase5/templates", response_model=ScaleResourceResponse, status_code=201)
async def create_saved_template(
    payload: SavedTemplateCreateRequest, actor: ActorDep, request: Request
) -> ScaleResourceResponse:
    return request.app.state.scale_repository.create(
        workspace_id=actor.workspace_id,
        project_id=None,
        kind="saved_template",
        name=payload.name,
        status=ScaleResourceStatus.READY,
        payload={"settings": payload.settings.model_dump(mode="json")},
    ).to_response()


@router.patch("/phase5/templates/{template_id}", response_model=ScaleResourceResponse)
async def update_saved_template(
    template_id: UUID,
    payload: SavedTemplateUpdateRequest,
    actor: ActorDep,
    request: Request,
) -> ScaleResourceResponse:
    record = request.app.state.scale_repository.get(
        resource_id=template_id, workspace_id=actor.workspace_id
    )
    if record.kind != "saved_template":
        raise ApiError(409, "saved_template_required", "Resource is not a saved template.")
    values = {"revision": record.revision + 1}
    if payload.name is not None:
        values["name"] = payload.name
    if payload.settings is not None:
        values["payload"] = {"settings": payload.settings.model_dump(mode="json")}
    return request.app.state.scale_repository.update(record=record, **values).to_response()


@router.delete("/phase5/templates/{template_id}", status_code=204)
async def delete_saved_template(template_id: UUID, actor: ActorDep, request: Request) -> Response:
    request.app.state.scale_repository.delete(
        resource_id=template_id, workspace_id=actor.workspace_id
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
    record = request.app.state.scale_repository.latest(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="collaboration_manifest",
    )
    if not record:
        record = request.app.state.scale_repository.create(
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
    record = request.app.state.scale_repository.latest(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="collaboration_manifest",
    )
    if not record:
        if payload.base_revision != 0:
            raise ApiError(409, "collaboration_revision_conflict", "Reload the shared edit.")
        record = request.app.state.scale_repository.create(
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
    return request.app.state.scale_repository.update_if_revision(
        record=record,
        expected_revision=payload.base_revision,
        payload={"manifest": payload.manifest},
        revision=record.revision + 1,
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
    record = request.app.state.scale_repository.create(
        workspace_id=actor.workspace_id,
        project_id=project_id,
        kind="model_training",
        name=payload.name,
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
async def cancel_batch(
    batch_id: UUID, actor: ActorDep, request: Request
) -> ScaleResourceResponse:
    record = request.app.state.scale_repository.get(
        resource_id=batch_id, workspace_id=actor.workspace_id
    )
    if record.kind != "batch":
        raise ApiError(409, "batch_required", "This resource is not a batch.")
    if record.status in {ScaleResourceStatus.COMPLETED, ScaleResourceStatus.PARTIAL}:
        raise ApiError(409, "batch_already_finished", "This batch has already finished.")
    job_id = f"phase5--{batch_id}"
    if job_id in request.app.state.queue_registry.jobs:
        request.app.state.queue_registry.cancel(job_id)
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
