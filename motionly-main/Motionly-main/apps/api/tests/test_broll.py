import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.broll import generate_broll, list_broll, swap_broll
from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.plans import create_plan
from app.repositories.billing import InMemoryBillingRepository
from app.repositories.broll import InMemoryBrollRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.billing import BillingPlanKey, SubscriptionStatus
from app.schemas.broll import BrollGenerateRequest, BrollSwapRequest
from app.schemas.captions import LanguageHint
from app.schemas.plans import BrollDensity, PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.edit_planner import FixtureEditPlannerProvider
from app.services.queue import BrollQueue, PlanQueue
from app.services.stock_video import FixtureStockVideoProvider
from app.services.transcription_provider import FixtureTranscriptionProvider


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
    asyncio.run(
        create_plan(
            project_id=project_id,
            payload=PlanCreateRequest(broll_density=BrollDensity.MEDIUM),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=plan_repository,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=PlanQueue(),
        )
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

    response = asyncio.run(
        generate_broll(
            project_id=project_id,
            payload=BrollGenerateRequest(),
            request=_request(),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=make_creator_billing_repository(workspace_id),
            broll_queue=BrollQueue(),
            providers=[FixtureStockVideoProvider()],
        )
    )

    assert response.status == "ready"
    assert response.job_id.startswith(f"broll:{project_id}:")
    assert len(response.slots) == 2
    assert all(slot.selected_asset for slot in response.slots)
    assert all(
        slot.selected_asset.orientation == "portrait"
        for slot in response.slots
        if slot.selected_asset
    )
    assert all(slot.crossfade_ms == 260 for slot in response.slots)


def test_generate_broll_reuses_cached_selection() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    first = asyncio.run(
        generate_broll(
            project_id=project_id,
            payload=BrollGenerateRequest(),
            request=_request(),
            actor=actor,
            repository=repository,
            billing_repository=make_creator_billing_repository(workspace_id),
            broll_queue=BrollQueue(),
            providers=[FixtureStockVideoProvider()],
        )
    )
    second = asyncio.run(
        generate_broll(
            project_id=project_id,
            payload=BrollGenerateRequest(),
            request=_request(),
            actor=actor,
            repository=repository,
            billing_repository=make_creator_billing_repository(workspace_id),
            broll_queue=BrollQueue(),
            providers=[FixtureStockVideoProvider()],
        )
    )

    assert second.cached is True
    assert [slot.id for slot in second.slots] == [slot.id for slot in first.slots]


def test_swap_broll_can_disable_and_reselect_candidate() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()
    actor = Actor(user_id=user_id, workspace_id=workspace_id)
    generated = asyncio.run(
        generate_broll(
            project_id=project_id,
            payload=BrollGenerateRequest(),
            request=_request(),
            actor=actor,
            repository=repository,
            billing_repository=make_creator_billing_repository(workspace_id),
            broll_queue=BrollQueue(),
            providers=[FixtureStockVideoProvider()],
        )
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


def test_generate_broll_allows_local_qa_when_billing_is_disabled() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()

    response = asyncio.run(
        generate_broll(
            project_id=project_id,
            payload=BrollGenerateRequest(),
            request=_request(billing_enabled=False),
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=repository,
            billing_repository=InMemoryBillingRepository(),
            broll_queue=BrollQueue(),
            providers=[FixtureStockVideoProvider()],
        )
    )

    assert response.status == "ready"
    assert response.slots


def test_generate_broll_requires_creator_when_billing_is_enabled() -> None:
    repository, project_id, workspace_id, user_id = make_broll_repository()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            generate_broll(
                project_id=project_id,
                payload=BrollGenerateRequest(),
                request=_request(billing_enabled=True),
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=repository,
                billing_repository=InMemoryBillingRepository(),
                broll_queue=BrollQueue(),
                providers=[FixtureStockVideoProvider()],
            )
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


def _request(*, billing_enabled: bool = True):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(billing_enabled=billing_enabled)),
        ),
    )
