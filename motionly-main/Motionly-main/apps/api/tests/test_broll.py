import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.broll import (
    complete_user_broll_upload,
    delete_user_broll_library_asset,
    generate_broll,
    init_user_broll_upload,
    list_broll,
    list_user_broll_library,
    search_broll_candidates,
    swap_broll,
    update_broll_region,
)
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.schemas.broll import (
    BrollGenerateRequest,
    BrollRegionUpdateRequest,
    BrollSwapRequest,
    UserBrollUploadInitRequest,
)
from app.schemas.captions import LanguageHint
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
from app.services.queue import BrollQueue, QueueRegistry
from app.services.stock_video import FixtureStockVideoProvider, score_candidate, StockVideoProvider
from app.services.storage import StorageSigner
from app.services.transcription_provider import FixtureTranscriptionProvider
from tests.worker_job_test_helpers import build_queue_request_state, run_enqueued_job, seed_active_plan


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
    signer = StorageSigner(base_url="http://localhost:8000")
    initialized = asyncio.run(
        init_user_broll_upload(
            project_id=project_id,
            payload=UserBrollUploadInitRequest(
                filename="office-cutaway.mp4",
                mime_type="video/mp4",
                file_size_bytes=4096,
                width=1080,
                height=1920,
                duration_ms=3200,
            ),
            request=request,
            actor=actor,
            repository=repository,
            storage_signer=signer,
        )
    )
    assert initialized.asset.provider == "user_library"
    assert initialized.signed_upload_url.endswith(".mp4")

    completed = asyncio.run(
        complete_user_broll_upload(
            project_id=project_id,
            asset_id=initialized.asset.id,
            request=request,
            actor=actor,
            repository=repository,
            storage_signer=signer,
        )
    )
    assert completed.asset.download_url
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

    response = asyncio.run(
        search_broll_candidates(
            project_id=project_id,
            slot_id=slot.id,
            actor=actor,
            repository=repository,
            providers=[FixtureStockVideoProvider()],
            query="modern office",
        )
    )

    assert response.query == "modern office"
    assert response.candidates
    assert len(response.slot.candidates) >= len(slot.candidates)


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


def _request(*, billing_enabled: bool = True, registry: QueueRegistry | None = None, repository=None, billing_repository=None):
    registry = registry or QueueRegistry()
    return build_queue_request_state(
        registry=registry,
        settings=SimpleNamespace(
            billing_enabled=billing_enabled,
            app_env="test",
            redis_url=None,
            admin_api_token="worker-secret",
        ),
        broll_repository=repository,
        billing_repository=billing_repository,
        stock_video_providers=[FixtureStockVideoProvider()],
        storage_signer=StorageSigner(base_url="http://localhost:8000"),
    )


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
    from tests.worker_job_test_helpers import run_enqueued_job
    from app.schemas.broll import BrollGenerateResponse
    from app.services.queue import QueueRegistry

    registry = broll_queue.registry or QueueRegistry()
    broll_queue = BrollQueue(registry=registry)
    request.app.state.queue_registry = registry
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
        asyncio.run(
            run_enqueued_job(job_id=broll_response.job_id, request=request)
        )
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
