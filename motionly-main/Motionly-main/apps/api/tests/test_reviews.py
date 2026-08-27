import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.dependencies import Actor
from app.api.errors import ApiError
from app.api.public_reviews import _public_response
from app.api.reviews import create_feedback_suggestion
from app.repositories.ai_proposals import InMemoryAIProposalRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.reviews import InMemoryReviewRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.ai_proposals import ProposalDecisionRequest, ProposalDecisionType
from app.schemas.captions import LanguageHint
from app.schemas.plans import PlanCreateRequest
from app.schemas.reviews import ReviewFeedbackSuggestionRequest, ReviewStatus
from app.schemas.uploads import UploadStatus
from app.services.ai_proposals import build_multimodal_context, request_identity
from app.services.edit_planner import FixtureEditPlannerProvider
from app.services.object_references import ObjectReferenceService
from app.services.proposal_generation import execute_proposal_generation
from app.services.queue import PlanQueue, QueueRegistry
from app.services.transcription_provider import FixtureTranscriptionProvider


def _review() -> tuple[InMemoryReviewRepository, object, object, object]:
    repository = InMemoryReviewRepository()
    workspace_id, project_id, user_id = uuid4(), uuid4(), uuid4()
    review = repository.create_review(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=uuid4(),
        edit_plan_revision=4,
        uploaded_video_id=uuid4(),
        source_media_revision=2,
        source_object_reference_id=uuid4(),
        source_sha256="a" * 64,
        plan_snapshot={"timeline": {"clips": [{"id": "clip-1"}]}},
        created_by=user_id,
    )
    return repository, review, workspace_id, user_id


def test_review_comments_are_timestamped_and_assignments_stay_scoped() -> None:
    repository, review, workspace_id, user_id = _review()
    assignee = uuid4()
    repository.set_active_member(workspace_id=workspace_id, user_id=assignee)
    comment = repository.create_comment(
        review_id=review.id,
        workspace_id=workspace_id,
        project_id=review.project_id,
        body="Tighten this opening beat.",
        timecode_ms=1_250,
        parent_comment_id=None,
        assignee_user_ids=[assignee],
        author_user_id=user_id,
        author_name=None,
    )

    detail = repository.get_detail(
        review_id=review.id,
        workspace_id=workspace_id,
        project_id=review.project_id,
    )
    assert detail.comments[0].id == comment.id
    assert detail.comments[0].timecode_ms == 1_250
    assert detail.comments[0].assignee_user_ids == [assignee]

    with pytest.raises(ApiError) as exc:
        repository.get_review(
            review_id=review.id,
            workspace_id=uuid4(),
            project_id=review.project_id,
        )
    assert exc.value.code == "review_not_found"


def test_review_comment_assignments_require_active_workspace_members() -> None:
    repository, review, workspace_id, user_id = _review()

    def create_comment(assignee: UUID) -> None:
        repository.create_comment(
            review_id=review.id,
            workspace_id=workspace_id,
            project_id=review.project_id,
            body="Please revise this beat.",
            timecode_ms=500,
            parent_comment_id=None,
            assignee_user_ids=[assignee],
            author_user_id=user_id,
            author_name=None,
        )

    with pytest.raises(ApiError) as non_member:
        create_comment(uuid4())
    assert non_member.value.code == "review_assignee_invalid"

    inactive_member = uuid4()
    repository.set_active_member(workspace_id=workspace_id, user_id=inactive_member)
    repository.set_member_status(
        workspace_id=workspace_id,
        user_id=inactive_member,
        status="inactive",
    )
    with pytest.raises(ApiError) as inactive:
        create_comment(inactive_member)
    assert inactive.value.code == "review_assignee_invalid"


def test_expiring_share_link_stores_only_a_digest_and_is_non_enumerable() -> None:
    repository, review, workspace_id, user_id = _review()
    token = "share-link-secret-with-at-least-thirty-two-bytes"
    link = repository.create_share_link(
        review_id=review.id,
        workspace_id=workspace_id,
        project_id=review.project_id,
        token=token,
        expires_at=review.created_at.replace(year=review.created_at.year + 1),
        can_comment=True,
        can_decide=False,
        created_by=user_id,
    )

    assert link.token_digest != token
    assert len(link.token_digest) == 64
    resolved = repository.resolve_share_link(share_link_id=link.id, token=token)
    assert resolved.review.id == review.id
    with pytest.raises(ApiError) as exc:
        repository.resolve_share_link(share_link_id=link.id, token="wrong-token" * 4)
    assert exc.value.status_code == 404
    assert exc.value.code == "review_link_not_found"


def test_review_decision_is_terminal_and_idempotent() -> None:
    repository, review, workspace_id, user_id = _review()
    decided = repository.decide(
        review_id=review.id,
        workspace_id=workspace_id,
        project_id=review.project_id,
        decision=ReviewStatus.APPROVED,
        expected_status_revision=1,
        reason="Looks good.",
        idempotency_key="review-decision-0001",
        decided_by=user_id,
    )
    replay = repository.decide(
        review_id=review.id,
        workspace_id=workspace_id,
        project_id=review.project_id,
        decision=ReviewStatus.APPROVED,
        expected_status_revision=1,
        reason="Looks good.",
        idempotency_key="review-decision-0001",
        decided_by=user_id,
    )
    assert decided.status == ReviewStatus.APPROVED
    assert decided.status_revision == 2
    assert replay.status_revision == 2
    with pytest.raises(ApiError) as exc:
        repository.decide(
            review_id=review.id,
            workspace_id=workspace_id,
            project_id=review.project_id,
            decision=ReviewStatus.REJECTED,
            expected_status_revision=2,
            reason=None,
            idempotency_key="review-decision-0002",
            decided_by=user_id,
        )
    assert exc.value.code == "review_decision_conflict"


def test_review_link_media_signing_is_bounded_and_audited() -> None:
    class Signer:
        def create_signed_download(self, *, bucket, path, expires_in_seconds):
            return SimpleNamespace(
                url=f"https://signed.example/{bucket}/{path}/{expires_in_seconds}"
            )

    workspace_id, project_id, share_link_id = uuid4(), uuid4(), uuid4()
    service = ObjectReferenceService(storage_signer=Signer(), in_memory=True)
    reference = asyncio.run(
        service.create_reference(
            workspace_id=workspace_id,
            project_id=project_id,
            storage_bucket="raw-uploads",
            storage_path=(f"workspaces/{workspace_id}/projects/{project_id}/raw/review-source.mp4"),
            owner_id=uuid4(),
            object_type="source_video",
        )
    )
    signed = asyncio.run(
        service.get_share_link_signed_url(
            workspace_id=workspace_id,
            project_id=project_id,
            reference_id=reference.id,
            share_link_id=share_link_id,
            link_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
    )
    assert signed.ttl_seconds <= 30
    assert service.grants[-1]["principal_type"] == "share_link"
    assert service.grants[-1]["principal_id"] == str(share_link_id)


def test_public_review_redacts_internal_comment_ids_and_signs_the_pinned_reference() -> None:
    class Signer:
        def create_signed_download(self, *, bucket, path, expires_in_seconds):
            return SimpleNamespace(url=f"https://signed.example/{bucket}/{path}")

    workspace_id, project_id, user_id, video_id = uuid4(), uuid4(), uuid4(), uuid4()
    source_sha256 = "c" * 64
    service = ObjectReferenceService(storage_signer=Signer(), in_memory=True)
    reference = asyncio.run(
        service.create_reference(
            workspace_id=workspace_id,
            project_id=project_id,
            storage_bucket="raw-uploads",
            storage_path=(f"workspaces/{workspace_id}/projects/{project_id}/raw/pinned.mp4"),
            owner_id=video_id,
            object_type="source_video",
            sha256=source_sha256,
        )
    )
    repository = InMemoryReviewRepository()
    review = repository.create_review(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=uuid4(),
        edit_plan_revision=1,
        uploaded_video_id=video_id,
        source_media_revision=1,
        source_object_reference_id=reference.id,
        source_sha256=source_sha256,
        plan_snapshot={"timeline": {"clips": []}},
        created_by=user_id,
    )
    assignee = uuid4()
    repository.set_active_member(workspace_id=workspace_id, user_id=assignee)
    repository.create_comment(
        review_id=review.id,
        workspace_id=workspace_id,
        project_id=project_id,
        body="Shift this title earlier.",
        timecode_ms=2_000,
        parent_comment_id=None,
        assignee_user_ids=[assignee],
        author_user_id=user_id,
        author_name=None,
    )
    token = "public-review-token-with-at-least-thirty-two-bytes"
    link = repository.create_share_link(
        review_id=review.id,
        workspace_id=workspace_id,
        project_id=project_id,
        token=token,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        can_comment=True,
        can_decide=False,
        created_by=user_id,
    )
    response = asyncio.run(
        _public_response(
            resolved=repository.resolve_share_link(share_link_id=link.id, token=token),
            repository=repository,
            request=SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(object_reference_service=service))
            ),
        )
    )

    assert response.media_url is not None
    assert response.media_url.endswith("/raw/pinned.mp4")
    assert set(response.comments[0].model_dump()) == {
        "id",
        "parent_comment_id",
        "timecode_ms",
        "body",
        "author_name",
        "created_at",
        "updated_at",
    }
    assert response.comments[0].author_name == "Reviewer"
    assert service.grants[-1]["object_reference_id"] == str(reference.id)


def _approved_plan_fixture():
    uploads = InMemoryUploadRepository()
    workspace_id, project_id, user_id = uuid4(), uuid4(), uuid4()
    uploads.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = uploads.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="source.mp4",
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=2048,
    )
    upload.status = UploadStatus.READY
    upload.source_object_reference_id = uuid4()
    upload.verified_sha256 = "b" * 64
    upload.metadata.duration_ms = 12_000
    upload.metadata.has_audio = True
    uploads.attach_active_video(project_id=project_id, video_id=upload.id)
    captions = InMemoryCaptionRepository(uploads)
    transcript = FixtureTranscriptionProvider().transcribe(
        audio_path=None, language_hint=LanguageHint.AUTO
    )
    captions.create_transcript(
        workspace_id=workspace_id,
        project_id=project_id,
        uploaded_video_id=upload.id,
        status="ready",
        language="en",
        language_hint="auto",
        provider="fixture",
        model="fixture-whisper",
        confidence=0.95,
        duration_ms=12_000,
        raw_payload={},
        words=transcript.words,
    )
    plans = InMemoryPlanRepository(captions)
    proposals = InMemoryAIProposalRepository(plan_repository=plans)
    payload = PlanCreateRequest(prompt="Create a reviewable first edit", target_duration_s=10)
    context = build_multimodal_context(
        repository=plans,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=payload,
    )
    proposal, _ = proposals.create_queued(
        context=context,
        request_id=request_identity(context=context, idempotency_key="first-review-plan"),
        created_by=user_id,
        prompt_version="edit-planner-prompt-v2",
    )
    execute_proposal_generation(
        proposal_repository=proposals,
        plan_repository=plans,
        planner_provider=FixtureEditPlannerProvider(),
        workspace_id=workspace_id,
        project_id=project_id,
        proposal_id=proposal.id,
        settings=SimpleNamespace(app_env="test"),
    )
    proposal = proposals.get(
        proposal_id=proposal.id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    proposals.decide(
        proposal_id=proposal.id,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=ProposalDecisionRequest(
            decision=ProposalDecisionType.APPROVE,
            expected_proposal_revision=proposal.revision,
            expected_base_active_plan_id=None,
            expected_base_active_plan_revision=None,
            idempotency_key="approve-first-review-plan",
            accepted_action_ids=[],
        ),
    )
    return plans, proposals, upload, workspace_id, project_id, user_id


def test_review_feedback_queues_an_existing_approval_gated_ai_proposal() -> None:
    plans, proposals, upload, workspace_id, project_id, user_id = _approved_plan_fixture()
    active_plan = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    reviews = InMemoryReviewRepository()
    review = reviews.create_review(
        workspace_id=workspace_id,
        project_id=project_id,
        edit_plan_id=active_plan.id,
        edit_plan_revision=active_plan.revision,
        uploaded_video_id=upload.id,
        source_media_revision=upload.media_revision,
        source_object_reference_id=upload.source_object_reference_id,
        source_sha256=upload.verified_sha256,
        plan_snapshot=active_plan.plan.model_dump(mode="json"),
        created_by=user_id,
    )
    reviews.create_comment(
        review_id=review.id,
        workspace_id=workspace_id,
        project_id=project_id,
        body="Make the hook more direct at this moment.",
        timecode_ms=1_000,
        parent_comment_id=None,
        assignee_user_ids=[],
        author_user_id=user_id,
        author_name=None,
    )
    queue = PlanQueue(QueueRegistry())
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(editorial_learning_enabled=False, app_env="test"),
                queue_registry=queue.registry,
            )
        )
    )

    response = asyncio.run(
        create_feedback_suggestion(
            project_id=project_id,
            review_id=review.id,
            payload=ReviewFeedbackSuggestionRequest(),
            request=request,
            actor=Actor(user_id=user_id, workspace_id=workspace_id),
            repository=reviews,
            plan_repository=plans,
            planner_provider=FixtureEditPlannerProvider(),
            plan_queue=queue,
            proposal_repository=proposals,
        )
    )

    queued = proposals.get(
        proposal_id=response.proposal.proposal_id,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    assert queued.context.artifacts.base_active_plan_id == active_plan.id
    assert queued.context.artifacts.base_active_plan_revision == active_plan.revision
    assert "Make the hook more direct" in (queued.context.controls["feedback"] or "")
    assert active_plan.revision == 1

    upload.id = uuid4()
    upload.media_revision += 1
    upload.source_object_reference_id = uuid4()
    upload.verified_sha256 = "d" * 64
    with pytest.raises(ApiError) as stale:
        asyncio.run(
            create_feedback_suggestion(
                project_id=project_id,
                review_id=review.id,
                payload=ReviewFeedbackSuggestionRequest(),
                request=request,
                actor=Actor(user_id=user_id, workspace_id=workspace_id),
                repository=reviews,
                plan_repository=plans,
                planner_provider=FixtureEditPlannerProvider(),
                plan_queue=queue,
                proposal_repository=proposals,
            )
        )
    assert stale.value.code == "review_feedback_stale"
