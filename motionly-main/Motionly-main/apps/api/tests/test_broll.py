import asyncio
import hashlib
import tempfile
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from app.api.broll import (
    complete_user_broll_multipart_upload,
    confirm_user_broll_multipart_part,
    delete_user_broll_library_asset,
    generate_broll,
    init_user_broll_multipart_upload,
    list_broll,
    list_user_broll_library,
    search_broll_candidates,
    swap_broll,
    update_broll_region,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import (
    BROLL_SLOT_COLUMNS,
    STOCK_ASSET_COLUMNS,
    InMemoryBrollRepository,
    SupabaseBrollRepository,
)
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.schemas.broll import (
    BrollConfidenceLabel,
    BrollGenerateRequest,
    BrollRegionUpdateRequest,
    BrollSwapRequest,
    StockAssetEnrichmentStatus,
    StockOrientation,
    StockProviderKey,
    UserBrollUploadInitRequest,
)
from app.schemas.captions import LanguageHint
from app.schemas.media_uploads import MediaUploadCompleteRequest
from app.schemas.multipart_uploads import PartCompleteRequest
from app.schemas.plans import BrollDensity, BrollRequest, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.schemas.visual import (
    BoundingBox,
    FaceDetection,
    ObjectDetection,
    SceneSegment,
    SceneType,
    VideoManifest,
)
from app.services.broll_intelligence import apply_stage4_broll_rules
from app.services.generic_media_upload import GenericMediaUploadSessionService
from app.services.local_storage import LocalObjectStorage
from app.services.media_url_policy import MediaUrlPolicyError
from app.services.object_references import ObjectReferenceService
from app.services.queue import BrollQueue, QueueRegistry, StockAssetEnrichmentQueue
from app.services.stock_video import (
    EMBEDDING_DOWNLOAD_MAX_BYTES,
    FixtureStockVideoProvider,
    StockVideoCandidate,
    StockVideoProvider,
    _read_bounded_thumbnail,
    build_stock_asset_embedding_cache_key,
    build_stock_asset_raw_metadata,
    enrich_stock_asset,
    query_embedding,
    score_candidate,
    score_candidate_details,
)
from app.services.storage import StorageSigner
from app.services.storage_quota import StorageQuotaService
from app.services.transcription_provider import FixtureTranscriptionProvider
from tests.worker_job_test_helpers import (
    build_queue_request_state,
    run_enqueued_job,
    seed_active_plan,
)


def make_broll_repository() -> tuple[InMemoryBrollRepository, UUID, UUID, UUID]:
    upload_repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="video.mp4",
        original_filename="video.mp4",
        mime_type="video/mp4",
        file_size_bytes=1000,
    )
    upload.status = UploadStatus.READY
    upload.metadata.duration_ms = 12_000
    upload.metadata.has_audio = True
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    caption_repository = InMemoryCaptionRepository(upload_repository)
    caption_repository.create_transcript(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status="ready",
        language="en",
        language_hint="auto",
        provider="fixture",
        model="whisper-1",
        confidence=0.95,
        duration_ms=12_000,
        raw_payload={},
        words=FixtureTranscriptionProvider()
        .transcribe(audio_path=None, language_hint=LanguageHint.AUTO)
        .words,
    )
    plan_repository = InMemoryPlanRepository(caption_repository)
    seed_active_plan(
        plan_repository=plan_repository,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=PlanCreateRequest(broll_density=BrollDensity.MEDIUM),
    )
    return InMemoryBrollRepository(plan_repository), project_id, workspace_id, user_id


def make_creator_billing_repository(workspace_id: UUID) -> InMemoryBillingRepository:
    repository = InMemoryBillingRepository()
    repository.upsert_subscription(
        workspace_id=workspace_id,
        plan=BillingPlanKey.CREATOR,
        status=SubscriptionStatus.ACTIVE,
    )
    return repository


def attach_media_upload_services(request: SimpleNamespace) -> LocalObjectStorage:
    """Attach the production-shaped generic lifecycle to a direct API test request."""
    state = request.app.state
    signer = getattr(state, "storage_signer", StorageSigner(base_url="http://localhost:8000"))
    storage = LocalObjectStorage(root=tempfile.mkdtemp(prefix="motionly-broll-media-"))
    reference_service = ObjectReferenceService(storage_signer=signer, in_memory=True)
    quota_service = StorageQuotaService(in_memory=True)
    state.storage_signer = signer
    state.object_storage = storage
    state.object_reference_service = reference_service
    state.storage_quota_service = quota_service
    state.media_upload_session_service = GenericMediaUploadSessionService(
        storage_signer=signer,
        object_storage=storage,
        quota_service=quota_service,
        object_reference_service=reference_service,
    )
    return storage


def write_broll_upload_part(
    request: SimpleNamespace,
    *,
    session_id: UUID,
    workspace_id: UUID,
    project_id: UUID,
    content: bytes,
) -> None:
    service = request.app.state.media_upload_session_service
    session = asyncio.run(
        service.get(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    request.app.state.object_storage.write_object(
        bucket=session.final_storage_bucket,
        path=session.parts[0].object_path,
        content=content,
    )


def test_generate_broll_creates_scored_vertical_slots() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    registry = QueueRegistry()
    billing_repository = make_creator_billing_repository(workspace_id)
    request = _request(
        registry=registry,
        repository=repository,
        billing_repository=billing_repository,
    )

    response = asyncio.run(
        generate_broll(
            project_id=project_id,
            payload=BrollGenerateRequest(),
            request=request,
            response=DummyResponse(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=billing_repository,
            broll_queue=BrollQueue(registry=registry),
            providers=[FixtureStockVideoProvider()],
        )
    )

    assert response.status == "queued"
    asyncio.run(run_enqueued_job(job_id=response.job_id, request=request))
    slots = repository.list_slots(project_id=project_id, workspace_id=workspace_id)
    slot_responses = [repository.response_for_slot(slot) for slot in slots]

    assert len(slot_responses) == 2
    assert all(slot.selected_asset for slot in slot_responses)
    assert all(
        slot.selected_asset.orientation == "portrait"
        for slot in slot_responses
        if slot.selected_asset
    )
    assert all(slot.crossfade_ms == 260 for slot in slot_responses)
    enrichment_jobs = [
        job for job in registry.jobs.values() if job.kind == "stock_asset_enrichment"
    ]
    assert enrichment_jobs
    assert {job.resource_id for job in enrichment_jobs} == set(repository.assets)
    assert all(job.queue_name == "stock_asset_enrichment" for job in enrichment_jobs)
    assert all(
        '"clip_model_name":"fixture-clip-model"' in job.render_engine for job in enrichment_jobs
    )


def test_user_broll_library_upload_list_and_delete() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    upload_storage_bucket="raw-uploads",
                    upload_signed_url_ttl_seconds=900,
                    media_work_dir="/tmp/motionly-tests",
                )
            )
        )
    )
    attach_media_upload_services(request)
    signer = StorageSigner(base_url="http://localhost:8000")
    content = b"b" * 4096
    initialized = asyncio.run(
        init_user_broll_multipart_upload(
            project_id=project_id,
            payload=UserBrollUploadInitRequest(
                filename="office-cutaway.mp4",
                mime_type="video/mp4",
                file_size_bytes=4096,
                sha256_hash=hashlib.sha256(content).hexdigest(),
                width=1080,
                height=1920,
                duration_ms=3200,
            ),
            request=request,
            actor=actor,
            repository=repository,
        )
    )
    assert initialized.asset.provider == "user_library"
    assert ".mp4.parts/" in initialized.session.pending_part_urls[0].upload_url

    write_broll_upload_part(
        request,
        session_id=initialized.session.session_id,
        workspace_id=workspace_id,
        project_id=project_id,
        content=content,
    )
    asyncio.run(
        confirm_user_broll_multipart_part(
            project_id=project_id,
            session_id=initialized.session.session_id,
            part_number=1,
            payload=PartCompleteRequest(),
            request=request,
            actor=actor,
            repository=repository,
        )
    )
    completed = asyncio.run(
        complete_user_broll_multipart_upload(
            project_id=project_id,
            session_id=initialized.session.session_id,
            payload=MediaUploadCompleteRequest(sha256_hash=hashlib.sha256(content).hexdigest()),
            request=request,
            actor=actor,
            repository=repository,
        )
    )
    assert completed.asset.media_state == "verified"
    assert completed.asset.object_reference_id
    assert completed.asset.verified_sha256 == hashlib.sha256(content).hexdigest()
    listed = asyncio.run(
        list_user_broll_library(
            project_id=project_id,
            request=request,
            actor=actor,
            repository=repository,
            storage_signer=signer,
        )
    )
    assert [asset.id for asset in listed.assets] == [initialized.asset.id]
    assert listed.assets[0].download_url

    deleted = asyncio.run(
        delete_user_broll_library_asset(
            project_id=project_id,
            asset_id=initialized.asset.id,
            request=request,
            actor=actor,
            repository=repository,
            storage_signer=signer,
        )
    )
    assert deleted.status == "deleted"


def test_supabase_broll_async_reads_use_bounded_projections(monkeypatch) -> None:
    upload_repository = InMemoryUploadRepository()
    caption_repository = InMemoryCaptionRepository(upload_repository)
    plan_repository = InMemoryPlanRepository(caption_repository)
    repository = SupabaseBrollRepository(
        plan_repository=plan_repository,
        supabase_url="https://supabase.example.com",
        service_role_key="service-role",
    )
    workspace_id = uuid4()
    project_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    plan_id = uuid4()
    slot_id = uuid4()
    asset_id = uuid4()
    now = "2026-07-06T00:00:00+00:00"
    slot_row = {
        "id": str(slot_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "edit_plan_id": str(plan_id),
        "version": 2,
        "schema_version": 2,
        "status": "ready",
        "start_ms": 0,
        "end_ms": 1500,
        "keywords": ["office"],
        "intent": "cutaway",
        "density": "medium",
        "priority": 0.8,
        "selected_asset_id": str(asset_id),
        "candidate_asset_ids": [str(asset_id)],
        "crossfade_ms": 260,
        "provider": "pexels",
        "score": 0.9,
        "error_code": None,
        "error_message": None,
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }
    asset_row = {
        "id": str(asset_id),
        "workspace_id": str(workspace_id),
        "project_id": str(project_id),
        "provider": "user_library",
        "provider_asset_id": "asset-1",
        "title": "Office clip",
        "thumbnail_url": None,
        "preview_video_url": "https://cdn.example.com/preview.mp4",
        "download_url": "https://cdn.example.com/download.mp4",
        "license": "user_owned",
        "width": 1080,
        "height": 1920,
        "duration_ms": 3000,
        "orientation": "portrait",
        "score": 1,
        "raw_metadata": {"status": "ready"},
        "created_at": now,
    }
    responses = [[slot_row], [asset_row], [asset_row]]
    calls = []

    async def fake_async_request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: responses.pop(0))

    monkeypatch.setattr(
        "app.repositories.broll.SupabaseRestClient.async_request",
        fake_async_request,
    )

    slots = asyncio.run(
        repository.find_cached_slots_async(
            project_id=project_id,
            workspace_id=workspace_id,
            cache_key="broll-cache-key",
        )
    )
    library = asyncio.run(
        repository.list_library_assets_async(
            project_id=project_id,
            workspace_id=workspace_id,
            limit=25,
        )
    )
    asset = asyncio.run(
        repository.get_asset_async(
            project_id=project_id,
            workspace_id=workspace_id,
            asset_id=asset_id,
        )
    )

    assert slots[0].id == slot_id
    assert library[0].id == asset_id
    assert asset.id == asset_id
    assert calls[0] == (
        "GET",
        "/broll_slots",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "cache_key": "eq.broll-cache-key",
                "select": BROLL_SLOT_COLUMNS,
                "order": "version.desc,start_ms.asc",
                "limit": "500",
            }
        },
    )
    assert calls[1] == (
        "GET",
        "/stock_assets",
        {
            "params": {
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "provider": "in.(user_library,generated)",
                "select": STOCK_ASSET_COLUMNS,
                "order": "created_at.desc",
                "limit": "25",
            }
        },
    )
    assert calls[2] == (
        "GET",
        "/stock_assets",
        {
            "params": {
                "id": f"eq.{asset_id}",
                "workspace_id": f"eq.{workspace_id}",
                "project_id": f"eq.{project_id}",
                "select": STOCK_ASSET_COLUMNS,
                "limit": "1",
            }
        },
    )


def test_broll_api_prefers_async_repository_methods() -> None:
    base_repository, project_id, workspace_id, user_id = make_broll_repository()

    class AsyncBrollRepository(InMemoryBrollRepository):
        def __init__(self, plan_repository):
            super().__init__(plan_repository)
            self.async_calls: list[str] = []

        async def upsert_asset_async(self, **kwargs):
            self.async_calls.append("upsert-asset")
            return self.upsert_asset(**kwargs)

        async def get_asset_async(self, **kwargs):
            self.async_calls.append("get-asset")
            return self.get_asset(**kwargs)

        async def list_library_assets_async(self, **kwargs):
            self.async_calls.append("list-library")
            return self.list_library_assets(**kwargs)

        async def set_library_media_session_async(self, **kwargs):
            self.async_calls.append("set-library-media-session")
            return self.set_library_media_session(**kwargs)

        async def mark_library_asset_verified_async(self, **kwargs):
            self.async_calls.append("mark-library-verified")
            return self.mark_library_asset_verified(**kwargs)

        async def mark_library_asset_ready_async(self, **kwargs):
            self.async_calls.append("mark-library-ready")
            return self.mark_library_asset_ready(**kwargs)

        async def find_cached_slots_async(self, **kwargs):
            self.async_calls.append("find-cache")
            return self.find_cached_slots(**kwargs)

        async def list_slots_async(self, **kwargs):
            self.async_calls.append("list-slots")
            return self.list_slots(**kwargs)

        async def get_slot_async(self, **kwargs):
            self.async_calls.append("get-slot")
            return self.get_slot(**kwargs)

        async def update_slot_selection_async(self, **kwargs):
            self.async_calls.append("update-selection")
            return self.update_slot_selection(**kwargs)

        async def update_slot_region_async(self, **kwargs):
            self.async_calls.append("update-region")
            return self.update_slot_region(**kwargs)

        async def response_for_slot_async(self, slot):
            self.async_calls.append("response-for-slot")
            return self.response_for_slot(slot)

    repository = AsyncBrollRepository(base_repository.plan_repository)
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    billing_repository = make_creator_billing_repository(workspace_id)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    upload_storage_bucket="raw-uploads",
                    upload_signed_url_ttl_seconds=900,
                    media_work_dir="/tmp/motionly-tests",
                    billing_enabled=False,
                    visual_clip_model="fixture-clip-model",
                ),
                storage_signer=StorageSigner(base_url="http://localhost:8000"),
            )
        )
    )
    attach_media_upload_services(request)
    signer = StorageSigner(base_url="http://localhost:8000")
    content = b"b" * 4096
    initialized = asyncio.run(
        init_user_broll_multipart_upload(
            project_id=project_id,
            payload=UserBrollUploadInitRequest(
                filename="office-cutaway.mp4",
                mime_type="video/mp4",
                file_size_bytes=4096,
                sha256_hash=hashlib.sha256(content).hexdigest(),
                width=1080,
                height=1920,
                duration_ms=3200,
            ),
            request=request,
            actor=actor,
            repository=repository,
        )
    )
    write_broll_upload_part(
        request,
        session_id=initialized.session.session_id,
        workspace_id=workspace_id,
        project_id=project_id,
        content=content,
    )
    asyncio.run(
        confirm_user_broll_multipart_part(
            project_id=project_id,
            session_id=initialized.session.session_id,
            part_number=1,
            payload=PartCompleteRequest(),
            request=request,
            actor=actor,
            repository=repository,
        )
    )
    asyncio.run(
        complete_user_broll_multipart_upload(
            project_id=project_id,
            session_id=initialized.session.session_id,
            payload=MediaUploadCompleteRequest(sha256_hash=hashlib.sha256(content).hexdigest()),
            request=request,
            actor=actor,
            repository=repository,
        )
    )
    asyncio.run(
        list_user_broll_library(
            project_id=project_id,
            request=request,
            actor=actor,
            repository=repository,
            storage_signer=signer,
        )
    )
    first = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=billing_repository,
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )
    cached = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=billing_repository,
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )
    asyncio.run(list_broll(project_id=project_id, actor=actor, repository=repository))
    slot = first.slots[0]
    alternate = slot.candidates[1]
    asyncio.run(
        swap_broll(
            project_id=project_id,
            slot_id=slot.id,
            payload=BrollSwapRequest(asset_id=alternate.id),
            actor=actor,
            repository=repository,
        )
    )
    asyncio.run(
        update_broll_region(
            project_id=project_id,
            slot_id=slot.id,
            payload=BrollRegionUpdateRequest(start_ms=750, end_ms=2250),
            actor=actor,
            repository=repository,
        )
    )

    assert cached.cached is True
    assert "upsert-asset" in repository.async_calls
    assert "get-asset" in repository.async_calls
    assert "list-library" in repository.async_calls
    assert "set-library-media-session" in repository.async_calls
    assert "mark-library-verified" in repository.async_calls
    assert repository.async_calls.count("find-cache") >= 2
    assert "list-slots" in repository.async_calls
    assert "get-slot" in repository.async_calls
    assert "update-selection" in repository.async_calls
    assert "update-region" in repository.async_calls
    assert "response-for-slot" in repository.async_calls


def test_generate_broll_stores_stage_4_semantic_metadata() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()

    response = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(force=True),
        request=_request(),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=make_creator_billing_repository(workspace_id),
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )

    slot = response.slots[0]
    assert slot.schema_version == 2
    assert slot.metadata["stage"] == 4
    assert slot.metadata["semantic_query"]
    assert slot.metadata["shot_type"]
    assert slot.metadata["placement_rule"]
    assert "clip_embedding_similarity" in slot.metadata["selection_rules"]
    assert slot.selected_asset
    assert slot.selected_asset.raw_metadata["semantic_query"] == slot.metadata["semantic_query"]


def test_generate_broll_reuses_cached_selection() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    first = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=make_creator_billing_repository(workspace_id),
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )
    second = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=make_creator_billing_repository(workspace_id),
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )

    assert second.cached is True
    assert [slot.id for slot in second.slots] == [slot.id for slot in first.slots]


def test_swap_broll_can_disable_and_reselect_candidate() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    generated = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=make_creator_billing_repository(workspace_id),
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )
    slot = generated.slots[0]
    alternate = slot.candidates[1]

    disabled = asyncio.run(
        swap_broll(
            project_id=project_id,
            slot_id=slot.id,
            payload=BrollSwapRequest(disabled=True),
            actor=actor,
            repository=repository,
        )
    )
    swapped = asyncio.run(
        swap_broll(
            project_id=project_id,
            slot_id=slot.id,
            payload=BrollSwapRequest(asset_id=alternate.id),
            actor=actor,
            repository=repository,
        )
    )

    assert disabled.slot.status == "disabled"
    assert swapped.slot.status == "ready"
    assert swapped.slot.selected_asset_id == alternate.id


def test_update_broll_region_persists_timing_and_disabled_state() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    generated = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=make_creator_billing_repository(workspace_id),
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )
    slot = generated.slots[0]

    updated = asyncio.run(
        update_broll_region(
            project_id=project_id,
            slot_id=slot.id,
            payload=BrollRegionUpdateRequest(start_ms=750, end_ms=2250, disabled=True),
            actor=actor,
            repository=repository,
        )
    )

    saved = repository.get_slot(
        project_id=project_id,
        workspace_id=workspace_id,
        slot_id=slot.id,
    )
    assert updated.slot.start_ms == 750
    assert updated.slot.end_ms == 2250
    assert updated.slot.status == "disabled"
    assert saved.start_ms == 750


def test_update_broll_region_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    generated = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=_request(),
        actor=actor,
        repository=repository,
        billing_repository=make_creator_billing_repository(workspace_id),
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            update_broll_region(
                project_id=project_id,
                slot_id=generated.slots[0].id,
                payload=BrollRegionUpdateRequest(start_ms=750, end_ms=2250),
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
            )
        )

    assert exc_info.value.status_code in {403, 404}


def test_update_broll_region_validates_ranges() -> None:
    with pytest.raises(ValueError):
        BrollRegionUpdateRequest(start_ms=3000, end_ms=3000)


def test_search_broll_candidates_adds_provider_results_to_slot() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    request = _request()
    generated = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=request,
        actor=actor,
        repository=repository,
        billing_repository=make_creator_billing_repository(workspace_id),
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )
    slot = generated.slots[0]

    response = asyncio.run(
        search_broll_candidates(
            project_id=project_id,
            slot_id=slot.id,
            request=request,
            actor=actor,
            repository=repository,
            providers=[FixtureStockVideoProvider()],
            stock_asset_enrichment_queue=StockAssetEnrichmentQueue(
                request.app.state.stock_asset_enrichment_queue.registry
            ),
            query="modern office",
        )
    )

    assert response.query == "modern office"
    assert response.candidates
    assert len(response.slot.candidates) >= len(slot.candidates)
    assert any(
        job.kind == "stock_asset_enrichment"
        for job in request.app.state.queue_registry.jobs.values()
    )


def test_stage_4_rules_avoid_critical_face_overlap_and_limit_consecutive_broll() -> None:
    requests = [
        BrollRequest(
            start_ms=500 + index * 500,
            end_ms=1300 + index * 500,
            keywords=["watch detail"],
            priority=0.9,
        )
        for index in range(4)
    ]

    placed = apply_stage4_broll_rules(
        requests=requests,
        visual_manifest=_face_and_product_manifest(),
        duration_ms=8_000,
    )

    assert placed
    assert all(not (request.start_ms < 2_500 and request.end_ms > 0) for request in placed)
    assert all(request.semantic_query for request in placed)
    assert all(request.semantic_embedding for request in placed)
    assert all(request.shot_type == "product_detail" for request in placed)
    assert _longest_consecutive_broll_run(placed) <= 3


def test_semantic_candidate_scoring_enforces_shot_type_diversity() -> None:
    candidates = FixtureStockVideoProvider().search(
        query="watch macro detail",
        orientation="portrait",
        min_width=720,
        min_height=1280,
    )

    first_score = score_candidate(
        candidate=candidates[0],
        slot_duration_ms=1800,
        duplicate_provider_asset_ids=set(),
        semantic_query="watch macro detail",
        target_shot_type="product_detail",
        used_shot_types={"product_detail"},
    )
    alternate_score = score_candidate(
        candidate=candidates[2],
        slot_duration_ms=1800,
        duplicate_provider_asset_ids=set(),
        semantic_query="watch macro detail",
        target_shot_type="product_detail",
        used_shot_types={"product_detail"},
    )

    assert alternate_score > first_score


def test_generate_broll_allows_local_qa_when_billing_is_disabled() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()

    response = run_generate_broll(
        project_id=project_id,
        payload=BrollGenerateRequest(),
        request=_request(billing_enabled=False),
        actor=Actor(user_id=user_id, workspace_id=workspace_id),
        repository=repository,
        billing_repository=InMemoryBillingRepository(),
        broll_queue=BrollQueue(),
        providers=[FixtureStockVideoProvider()],
    )

    assert response.status == "ready"
    assert response.slots
    assert response.slots[0].selected_asset
    assert response.slots[0].selected_asset.confidence_label is not None


def test_generate_broll_requires_creator_when_billing_is_enabled() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()

    with pytest.raises(ApiError) as exc_info:
        run_generate_broll(
            project_id=project_id,
            payload=BrollGenerateRequest(),
            request=_request(billing_enabled=True),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            broll_queue=BrollQueue(),
            providers=[FixtureStockVideoProvider()],
        )

    assert exc_info.value.status_code == 402
    assert exc_info.value.code == "broll_plan_required"


def test_list_broll_rejects_cross_workspace_access() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            list_broll(
                project_id=project_id,
                actor=Actor(user_id=user_id, workspace_id=uuid4()),
                repository=repository,
            )
        )

    assert exc_info.value.status_code in {403, 404}


def test_embedding_similarity_outranks_lexical_only_match() -> None:
    semantic_embedding = query_embedding(semantic_query="founder using laptop in modern office")
    semantic_candidate = StockVideoCandidate(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="semantic-match",
        title="workspace team scene",
        thumbnail_url="https://example.com/semantic.jpg",
        preview_video_url=None,
        download_url=None,
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=3200,
        relevance_rank=2,
        raw_metadata={"embedding": semantic_embedding, "embedding_model_version": "fixture"},
    )
    lexical_only_candidate = StockVideoCandidate(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="lexical-match",
        title="founder using laptop in modern office",
        thumbnail_url="https://example.com/lexical.jpg",
        preview_video_url=None,
        download_url=None,
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=3200,
        relevance_rank=1,
        raw_metadata={},
    )

    semantic_score = score_candidate_details(
        candidate=semantic_candidate,
        slot_duration_ms=3000,
        duplicate_provider_asset_ids=set(),
        semantic_query="founder using laptop in modern office",
        semantic_embedding=semantic_embedding,
    )
    lexical_score = score_candidate_details(
        candidate=lexical_only_candidate,
        slot_duration_ms=3000,
        duplicate_provider_asset_ids=set(),
        semantic_query="founder using laptop in modern office",
        semantic_embedding=semantic_embedding,
    )

    assert semantic_score.total > lexical_score.total
    assert lexical_score.confidence_label == BrollConfidenceLabel.LEXICAL_FALLBACK


def test_missing_embeddings_route_falls_back_to_lexical_ranking() -> None:
    # Task 19: with no embeddings.text route configured, B-roll ranking must use
    # its lexical fallback (an empty query embedding) instead of failing.
    from app.services.model_domain_adapters import GatewayClipEmbeddingProvider
    from app.services.model_gateway import ModelGateway

    gateway = ModelGateway(adapters={}, routes=[])
    provider = GatewayClipEmbeddingProvider(gateway=gateway)

    # No embeddings route -> no vector, no exception.
    embedding = query_embedding(
        semantic_query="founder using laptop in modern office",
        embedding_provider=provider,
    )
    assert embedding == []

    # Ranking still completes and marks the result as the lexical fallback.
    candidate = StockVideoCandidate(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="lexical-match",
        title="founder using laptop in modern office",
        thumbnail_url="https://example.com/lexical.jpg",
        preview_video_url=None,
        download_url=None,
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=3200,
        relevance_rank=1,
        raw_metadata={},
    )
    score = score_candidate_details(
        candidate=candidate,
        slot_duration_ms=3000,
        duplicate_provider_asset_ids=set(),
        semantic_query="founder using laptop in modern office",
        semantic_embedding=embedding,
    )
    assert score.total > 0
    assert score.confidence_label == BrollConfidenceLabel.LEXICAL_FALLBACK


def test_orientation_and_duration_affect_ranking_after_embedding_similarity() -> None:
    semantic_embedding = query_embedding(semantic_query="watch macro detail")
    portrait_good = StockVideoCandidate(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="portrait-good",
        title="watch detail",
        thumbnail_url=None,
        preview_video_url=None,
        download_url=None,
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=2800,
        relevance_rank=1,
        raw_metadata={"embedding": semantic_embedding},
    )
    landscape_good = StockVideoCandidate(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="landscape-good",
        title="watch detail",
        thumbnail_url=None,
        preview_video_url=None,
        download_url=None,
        license="pexels_license",
        width=1920,
        height=1080,
        duration_ms=2800,
        relevance_rank=1,
        raw_metadata={"embedding": semantic_embedding},
    )
    portrait_bad_duration = StockVideoCandidate(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="portrait-bad-duration",
        title="watch detail",
        thumbnail_url=None,
        preview_video_url=None,
        download_url=None,
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=1200,
        relevance_rank=1,
        raw_metadata={"embedding": semantic_embedding},
    )

    portrait_score = score_candidate_details(
        candidate=portrait_good,
        slot_duration_ms=3000,
        duplicate_provider_asset_ids=set(),
        semantic_query="watch macro detail",
        semantic_embedding=semantic_embedding,
    )
    landscape_score = score_candidate_details(
        candidate=landscape_good,
        slot_duration_ms=3000,
        duplicate_provider_asset_ids=set(),
        semantic_query="watch macro detail",
        semantic_embedding=semantic_embedding,
    )
    bad_duration_score = score_candidate_details(
        candidate=portrait_bad_duration,
        slot_duration_ms=3000,
        duplicate_provider_asset_ids=set(),
        semantic_query="watch macro detail",
        semantic_embedding=semantic_embedding,
    )

    assert portrait_score.total > landscape_score.total
    assert portrait_score.total > bad_duration_score.total


def test_bad_embedding_dimensions_do_not_crash_ranking() -> None:
    score = score_candidate_details(
        candidate=StockVideoCandidate(
            provider=StockProviderKey.PEXELS,
            provider_asset_id="bad-dims",
            title="office collaboration",
            thumbnail_url=None,
            preview_video_url=None,
            download_url=None,
            license="pexels_license",
            width=1080,
            height=1920,
            duration_ms=2800,
            relevance_rank=1,
            raw_metadata={"embedding": [0.1, 0.2]},
        ),
        slot_duration_ms=3000,
        duplicate_provider_asset_ids=set(),
        semantic_query="office collaboration",
        semantic_embedding=[0.1, 0.2, 0.3],
    )

    assert score.total >= 0
    assert "embedding_dimension_mismatch" in score.reasons


def test_stock_thumbnail_reader_rejects_oversized_stream() -> None:
    class _OversizedResponse:
        headers = {"content-type": "image/jpeg"}

        def iter_bytes(self, **_kwargs):
            yield b"\xff\xd8\xff"
            yield b"x" * EMBEDDING_DOWNLOAD_MAX_BYTES

    with pytest.raises(MediaUrlPolicyError, match="allowed size"):
        _read_bounded_thumbnail(_OversizedResponse())


def test_enrich_stock_asset_downloads_thumbnail_and_caches_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, project_id, workspace_id, _user_id = make_broll_repository()
    candidate = StockVideoCandidate(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="pexels-asset-1",
        title="office teamwork",
        thumbnail_url="https://example.com/thumb.jpg",
        preview_video_url="https://example.com/video.mp4",
        download_url="https://example.com/video.mp4",
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=3200,
        relevance_rank=1,
        provider_url="https://pexels.com/video/1",
        source_url="https://example.com/video.mp4",
        license_metadata={"license": "pexels_license"},
        attribution_metadata={"creator": "Pexels Author"},
        raw_metadata={},
    )
    asset = repository.upsert_asset(
        workspace_id=workspace_id,
        project_id=project_id,
        provider=candidate.provider,
        provider_asset_id=candidate.provider_asset_id,
        title=candidate.title,
        thumbnail_url=candidate.thumbnail_url,
        preview_video_url=candidate.preview_video_url,
        download_url=candidate.download_url,
        license=candidate.license,
        width=candidate.width,
        height=candidate.height,
        duration_ms=candidate.duration_ms,
        orientation=StockOrientation.PORTRAIT,
        score=0.5,
        raw_metadata=build_stock_asset_raw_metadata(
            candidate=candidate,
            query="office teamwork",
            semantic_query="office teamwork",
            target_shot_type="context_cutaway",
        ),
    )

    class _Response:
        headers = {"content-length": "13", "content-type": "image/jpeg"}

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, **_kwargs):
            yield b"\xff\xd8\xfffake-image"

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "app.services.stock_video.safe_http_stream", lambda *_args, **_kwargs: _Stream()
    )

    enriched = enrich_stock_asset(
        asset=asset,
        repository=repository,
        clip_model_name="fixture-clip-model",
    )

    assert enriched.raw_metadata["license_metadata"] == {"license": "pexels_license"}
    assert enriched.raw_metadata["attribution_metadata"] == {"creator": "Pexels Author"}
    assert isinstance(enriched.raw_metadata.get("embedding"), list)
    assert enriched.raw_metadata["enrichment_status"] == StockAssetEnrichmentStatus.COMPLETED
    assert enriched.raw_metadata["embedding_cache_key"] == build_stock_asset_embedding_cache_key(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="pexels-asset-1",
        embedding_model_version="fixture-clip-model",
    )

    cached = enrich_stock_asset(
        asset=enriched,
        repository=repository,
        clip_model_name="fixture-clip-model",
    )
    assert cached.raw_metadata["enrichment_cache_hit"] is True
    assert len(repository.assets) == 1


def test_enrich_stock_asset_failed_download_falls_back_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, project_id, workspace_id, _user_id = make_broll_repository()
    candidate = StockVideoCandidate(
        provider=StockProviderKey.PIXABAY,
        provider_asset_id="pixabay-asset-2",
        title="city skyline",
        thumbnail_url="https://example.com/fail.jpg",
        preview_video_url=None,
        download_url=None,
        license="pixabay_license",
        width=1080,
        height=1920,
        duration_ms=3400,
        relevance_rank=1,
        raw_metadata={},
    )
    asset = repository.upsert_asset(
        workspace_id=workspace_id,
        project_id=project_id,
        provider=candidate.provider,
        provider_asset_id=candidate.provider_asset_id,
        title=candidate.title,
        thumbnail_url=candidate.thumbnail_url,
        preview_video_url=candidate.preview_video_url,
        download_url=candidate.download_url,
        license=candidate.license,
        width=candidate.width,
        height=candidate.height,
        duration_ms=candidate.duration_ms,
        orientation=StockOrientation.PORTRAIT,
        score=0.4,
        raw_metadata=build_stock_asset_raw_metadata(
            candidate=candidate,
            query="city skyline",
            semantic_query="city skyline",
            target_shot_type="establishing",
        ),
    )

    def _raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("app.services.stock_video.safe_http_stream", _raise_timeout)

    failed = enrich_stock_asset(
        asset=asset,
        repository=repository,
        clip_model_name="fixture-clip-model",
    )

    assert failed.raw_metadata["enrichment_status"] == StockAssetEnrichmentStatus.FAILED
    assert failed.raw_metadata["enrichment_failure_reason"] == "thumbnail_download_timeout"


def test_stock_asset_enrichment_queue_executes_without_duplicate_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, project_id, workspace_id, _user_id = make_broll_repository()
    registry = QueueRegistry()
    request = _request(registry=registry, repository=repository)
    candidate = StockVideoCandidate(
        provider=StockProviderKey.PEXELS,
        provider_asset_id="queued-asset-1",
        title="product detail",
        thumbnail_url="https://example.com/thumb.jpg",
        preview_video_url=None,
        download_url=None,
        license="pexels_license",
        width=1080,
        height=1920,
        duration_ms=3600,
        relevance_rank=1,
        raw_metadata={},
    )
    asset = repository.upsert_asset(
        workspace_id=workspace_id,
        project_id=project_id,
        provider=candidate.provider,
        provider_asset_id=candidate.provider_asset_id,
        title=candidate.title,
        thumbnail_url=candidate.thumbnail_url,
        preview_video_url=candidate.preview_video_url,
        download_url=candidate.download_url,
        license=candidate.license,
        width=candidate.width,
        height=candidate.height,
        duration_ms=candidate.duration_ms,
        orientation=StockOrientation.PORTRAIT,
        score=0.4,
        raw_metadata=build_stock_asset_raw_metadata(
            candidate=candidate,
            query="product detail",
            semantic_query="product detail",
            target_shot_type="product_detail",
        ),
    )

    class _Response:
        headers = {"content-length": "15", "content-type": "image/jpeg"}

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, **_kwargs):
            yield b"\xff\xd8\xffqueued-image"

    class _Stream:
        def __enter__(self):
            return _Response()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "app.services.stock_video.safe_http_stream", lambda *_args, **_kwargs: _Stream()
    )
    queue = StockAssetEnrichmentQueue(registry)
    job = queue.enqueue_asset_enrichment(
        project_id=project_id,
        workspace_id=workspace_id,
        asset_id=asset.id,
        refresh_key="provider-metadata-v1",
        job_payload='{"clip_model_name":"fixture-clip-model"}',
    )
    duplicate = queue.enqueue_asset_enrichment(
        project_id=project_id,
        workspace_id=workspace_id,
        asset_id=asset.id,
        refresh_key="provider-metadata-v1",
        job_payload='{"clip_model_name":"fixture-clip-model"}',
    )
    assert duplicate.id == job.id
    assert len(registry.jobs) == 1

    asyncio.run(run_enqueued_job(job_id=job.id, request=request))

    refreshed = queue.enqueue_asset_enrichment(
        project_id=project_id,
        workspace_id=workspace_id,
        asset_id=asset.id,
        refresh_key="provider-metadata-v2",
        job_payload='{"clip_model_name":"fixture-clip-model"}',
    )
    assert refreshed.id != job.id
    assert refreshed.resource_id == asset.id

    enriched = repository.get_asset(
        project_id=project_id,
        workspace_id=workspace_id,
        asset_id=asset.id,
    )
    assert enriched.raw_metadata["enrichment_status"] == StockAssetEnrichmentStatus.COMPLETED
    assert len(repository.assets) == 1


def _request(
    *,
    billing_enabled: bool = True,
    registry: QueueRegistry | None = None,
    repository=None,
    billing_repository=None,
):
    registry = registry or QueueRegistry()
    request = build_queue_request_state(
        registry=registry,
        settings=SimpleNamespace(
            billing_enabled=billing_enabled,
            app_env="test",
            redis_url=None,
            worker_api_token="worker-secret",
            visual_clip_model="fixture-clip-model",
        ),
        broll_repository=repository,
        billing_repository=billing_repository,
        stock_video_providers=[FixtureStockVideoProvider()],
        storage_signer=StorageSigner(base_url="http://localhost:8000"),
    )
    request.app.state.stock_asset_enrichment_queue = StockAssetEnrichmentQueue(registry)
    return request


def run_generate_broll(
    *,
    project_id: UUID,
    payload: BrollGenerateRequest,
    request: object,
    actor: Actor,
    repository: InMemoryBrollRepository,
    billing_repository: InMemoryBillingRepository,
    broll_queue: BrollQueue,
    providers: list[StockVideoProvider],
) -> object:
    from app.schemas.broll import BrollGenerateResponse
    from app.services.queue import QueueRegistry
    from tests.worker_job_test_helpers import run_enqueued_job

    registry = broll_queue.registry or QueueRegistry()
    broll_queue = BrollQueue(registry=registry)
    request.app.state.queue_registry = registry
    request.app.state.stock_asset_enrichment_queue = StockAssetEnrichmentQueue(registry)
    request.app.state.broll_repository = repository
    request.app.state.billing_repository = billing_repository
    request.app.state.stock_video_providers = providers
    request.app.state.storage_signer = StorageSigner(base_url="http://localhost:8000")

    response = DummyResponse()
    broll_response = asyncio.run(
        generate_broll(
            project_id=project_id,
            payload=payload,
            request=request,
            response=response,
            actor=actor,
            repository=repository,
            billing_repository=billing_repository,
            broll_queue=broll_queue,
            providers=providers,
        )
    )
    if getattr(broll_response, "status", None) == "queued":
        asyncio.run(run_enqueued_job(job_id=broll_response.job_id, request=request))
        slots = repository.list_slots(project_id=project_id, workspace_id=actor.workspace_id)
        broll_response = BrollGenerateResponse(
            slots=[repository.response_for_slot(slot) for slot in slots],
            job_id=broll_response.job_id,
            status="ready",
        )
    return broll_response


class DummyResponse:
    status_code = 200


def _face_and_product_manifest() -> VideoManifest:
    return VideoManifest(
        duration_ms=8_000,
        sample_rate_fps=2,
        frame_count=4,
        provider="fixture_visual",
        model_versions={"clip": "fixture"},
        scenes=[
            SceneSegment(
                index=0,
                start_ms=0,
                end_ms=2_500,
                representative_timestamp_ms=1_000,
                caption="speaker explaining the key point",
                embedding=[0.1, 0.2, 0.3],
                faces=[
                    FaceDetection(
                        confidence=0.97,
                        box=BoundingBox(x=0.25, y=0.1, width=0.5, height=0.5),
                    )
                ],
                scene_type=SceneType.TALKING_HEAD,
                visual_importance_score=0.92,
            ),
            SceneSegment(
                index=1,
                start_ms=2_500,
                end_ms=8_000,
                representative_timestamp_ms=3_200,
                caption="handmade watch macro detail",
                embedding=[0.4, 0.5, 0.6],
                objects=[
                    ObjectDetection(
                        label="watch",
                        confidence=0.91,
                        box=BoundingBox(x=0.2, y=0.2, width=0.4, height=0.3),
                    )
                ],
                scene_type=SceneType.PRODUCT,
                visual_importance_score=0.9,
            ),
        ],
        summary={"scene_count": 2},
    )


def _longest_consecutive_broll_run(requests: list[BrollRequest]) -> int:
    longest = 0
    current = 0
    previous_end_ms: int | None = None
    for request in sorted(requests, key=lambda item: item.start_ms):
        if previous_end_ms is None or request.start_ms - previous_end_ms > 350:
            current = 1
        else:
            current += 1
        longest = max(longest, current)
        previous_end_ms = request.end_ms
    return longest


def test_broll_multipart_mutations_reject_foreign_sessions_before_side_effects() -> None:
    from app.api.broll import (
        confirm_user_broll_multipart_part,
    )
    from app.schemas.media_uploads import PartCompleteRequest

    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    upload_storage_bucket="raw-uploads",
                    upload_signed_url_ttl_seconds=900,
                    media_work_dir="/tmp/motionly-tests",
                )
            )
        )
    )
    attach_media_upload_services(request)
    service = request.app.state.media_upload_session_service
    style_session = asyncio.run(
        service.initiate(
            workspace_id=workspace_id,
            project_id=project_id,
            owner_type="style_profile",
            owner_id=uuid4(),
            object_type="style_reference",
            final_storage_bucket="raw-uploads",
            final_storage_path=(
                f"workspaces/{workspace_id}/projects/{project_id}/style-references/{uuid4()}/sample.mp4"
            ),
            filename="sample.mp4",
            mime_type="video/mp4",
            file_size_bytes=4,
        )
    )

    with pytest.raises(ApiError) as error:
        asyncio.run(
            confirm_user_broll_multipart_part(
                project_id=project_id,
                session_id=style_session.id,
                part_number=1,
                payload=PartCompleteRequest(),
                request=request,
                actor=actor,
                repository=repository,
            )
        )
    assert error.value.code == "media_upload_session_not_found"
    untouched = asyncio.run(
        service.inspect(
            session_id=style_session.id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    )
    assert untouched.status.value == "active"
    assert untouched.parts[0].status.value == "pending"


def test_broll_init_compensates_when_owner_session_link_fails() -> None:
    class FailingLinkRepository(InMemoryBrollRepository):
        def set_library_media_session(self, **_kwargs):
            raise RuntimeError("asset link write failed")

    base_repository, project_id, workspace_id, user_id = make_broll_repository()
    repository = FailingLinkRepository(base_repository.plan_repository)
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    upload_storage_bucket="raw-uploads",
                    upload_signed_url_ttl_seconds=900,
                    media_work_dir="/tmp/motionly-tests",
                )
            )
        )
    )
    attach_media_upload_services(request)

    with pytest.raises(RuntimeError, match="asset link write failed"):
        asyncio.run(
            init_user_broll_multipart_upload(
                project_id=project_id,
                payload=UserBrollUploadInitRequest(
                    filename="failed-link.mp4",
                    mime_type="video/mp4",
                    file_size_bytes=4,
                    width=1920,
                    height=1080,
                    duration_ms=1000,
                ),
                request=request,
                actor=actor,
                repository=repository,
            )
        )

    service = request.app.state.media_upload_session_service
    session = next(iter(service._sessions.values()))
    assert session.status.value == "aborted"
    assert (
        asyncio.run(
            request.app.state.storage_quota_service.get_reservation_state(
                session.quota_reservation_id
            )
        )
        == "released"
    )
    assert next(iter(repository.assets.values())).media_state == "failed"
