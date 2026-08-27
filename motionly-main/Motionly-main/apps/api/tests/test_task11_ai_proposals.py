from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.repositories.ai_proposals import InMemoryAIProposalRepository
from app.repositories.captions import InMemoryCaptionRepository
from app.repositories.plans import InMemoryPlanRepository
from app.repositories.uploads import InMemoryUploadRepository
from app.schemas.ai_proposals import (
    ProposalDecisionRequest,
    ProposalDecisionType,
    ProposalState,
    ProposalToolName,
)
from app.schemas.captions import LanguageHint
from app.schemas.plans import PlanCreateRequest
from app.schemas.uploads import UploadStatus
from app.services.ai_proposals import (
    TOOL_REGISTRY,
    build_multimodal_context,
    canonical_hash,
    context_digest,
    derive_proposal_actions,
    request_identity,
)
from app.services.edit_planner import FixtureEditPlannerProvider
from app.services.proposal_generation import execute_proposal_generation
from app.services.queue import PlanQueue, QueueRegistry
from app.services.transcription_provider import FixtureTranscriptionProvider


def _repositories():
    upload_repository = InMemoryUploadRepository()
    project_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    upload_repository.seed_project(project_id=project_id, workspace_id=workspace_id)
    upload = upload_repository.create_upload(
        project_id=project_id,
        workspace_id=workspace_id,
        storage_bucket="raw-uploads",
        storage_path="source.mp4",
        original_filename="source.mp4",
        mime_type="video/mp4",
        file_size_bytes=2048,
    )
    upload.status = UploadStatus.READY
    upload.metadata.duration_ms = 12_000
    upload.metadata.has_audio = True
    upload_repository.attach_active_video(project_id=project_id, video_id=upload.id)
    captions = InMemoryCaptionRepository(upload_repository)
    transcript_result = FixtureTranscriptionProvider().transcribe(
        audio_path=None,
        language_hint=LanguageHint.AUTO,
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
        raw_payload={
            "audio_intelligence": {
                "keywords": [{"text": "launch", "score": 0.9}],
                "style_signals": {"predicted_style": "clean_minimal"},
            }
        },
        words=transcript_result.words,
    )
    plans = InMemoryPlanRepository(captions)
    proposals = InMemoryAIProposalRepository(plan_repository=plans)
    return plans, proposals, upload, project_id, workspace_id, user_id


def _generate(
    plans,
    proposals,
    *,
    project_id,
    workspace_id,
    user_id,
    prompt="Make a concise premium edit",
    production=False,
):
    payload = PlanCreateRequest(prompt=prompt, target_duration_s=10)
    context = build_multimodal_context(
        repository=plans,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=payload,
    )
    request_id = request_identity(
        context=context,
        idempotency_key=f"request-{canonical_hash(prompt)[:16]}",
    )
    proposal, deduplicated = proposals.create_queued(
        context=context,
        request_id=request_id,
        created_by=user_id,
        prompt_version="edit-planner-prompt-v2",
    )
    result = execute_proposal_generation(
        proposal_repository=proposals,
        plan_repository=plans,
        planner_provider=FixtureEditPlannerProvider(),
        workspace_id=workspace_id,
        project_id=project_id,
        proposal_id=proposal.id,
        settings=SimpleNamespace(app_env="production" if production else "test"),
    )
    return (
        proposals.get(
            proposal_id=proposal.id,
            project_id=project_id,
            workspace_id=workspace_id,
        ),
        result,
        deduplicated,
    )


def _approve(proposals, proposal, *, project_id, workspace_id, user_id, key="decision-key-0001"):
    return proposals.decide(
        proposal_id=proposal.id,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=ProposalDecisionRequest(
            decision=ProposalDecisionType.APPROVE,
            expected_proposal_revision=proposal.revision,
            expected_base_active_plan_id=proposal.base_active_plan_id
            if hasattr(proposal, "base_active_plan_id")
            else proposal.artifacts.base_active_plan_id,
            expected_base_active_plan_revision=proposal.artifacts.base_active_plan_revision,
            idempotency_key=key,
            accepted_action_ids=[],
        ),
    )


def test_context_hash_is_deterministic_tenant_scoped_and_safe() -> None:
    plans, _, _, project_id, workspace_id, _ = _repositories()
    payload = PlanCreateRequest(prompt="one deterministic request", target_duration_s=10)
    first = build_multimodal_context(
        repository=plans,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=payload,
    )
    second = build_multimodal_context(
        repository=plans,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=payload,
    )

    assert context_digest(first) == context_digest(second)
    assert first.artifacts.transcript_revision == second.artifacts.transcript_revision
    assert "storage_path" not in first.model_dump_json()
    other_tenant_payload = first.model_copy(update={"workspace_id": uuid4()})
    assert context_digest(first) != context_digest(other_tenant_payload)


def test_plan_queue_identity_includes_proposal_request_and_context() -> None:
    registry = QueueRegistry()
    queue = PlanQueue(registry)
    project_id, workspace_id, transcript_id = uuid4(), uuid4(), uuid4()
    proposal_id = uuid4()
    first = queue.enqueue_plan(
        project_id=project_id,
        workspace_id=workspace_id,
        transcript_id=transcript_id,
        proposal_id=proposal_id,
        request_id="a" * 64,
        context_digest="b" * 64,
    )
    replay = queue.enqueue_plan(
        project_id=project_id,
        workspace_id=workspace_id,
        transcript_id=transcript_id,
        proposal_id=proposal_id,
        request_id="a" * 64,
        context_digest="b" * 64,
    )
    distinct = queue.enqueue_plan(
        project_id=project_id,
        workspace_id=workspace_id,
        transcript_id=transcript_id,
        proposal_id=uuid4(),
        request_id="c" * 64,
        context_digest="d" * 64,
    )

    assert first.id == replay.id
    assert distinct.id != first.id


def test_generation_is_approval_gated_and_full_approval_is_idempotent() -> None:
    plans, proposals, _, project_id, workspace_id, user_id = _repositories()
    proposal, result, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )

    assert result["status"] == "awaiting_approval"
    assert proposal.state == ProposalState.AWAITING_APPROVAL
    assert proposal.actions[0].tool_name == ProposalToolName.COMPLETE_FIRST_PLAN
    assert plans.list_plans(project_id=project_id, workspace_id=workspace_id) == []

    payload = ProposalDecisionRequest(
        decision=ProposalDecisionType.APPROVE,
        expected_proposal_revision=proposal.revision,
        expected_base_active_plan_id=None,
        expected_base_active_plan_revision=None,
        idempotency_key="decision-key-0001",
        accepted_action_ids=[],
    )
    approved = proposals.decide(
        proposal_id=proposal.id,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=payload,
    )
    replay = proposals.decide(
        proposal_id=proposal.id,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=payload,
    )

    active = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    assert approved.edit_plan_id == active.id
    assert approved.proposal.state == ProposalState.APPROVED
    assert replay.idempotent_replay is True
    assert replay.decision_id == approved.decision_id
    assert len(plans.plans) == 1
    assert len(proposals.timeline_revisions) == 1
    assert proposals.timeline_revisions[0]["plan_snapshot"] == active.plan.model_dump(mode="json")


def test_partial_approval_applies_dependency_valid_subset() -> None:
    plans, proposals, _, project_id, workspace_id, user_id = _repositories()
    first, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    _approve(
        proposals,
        first,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    old_active = plans.active_plan(project_id=project_id, workspace_id=workspace_id)

    second, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt="Change pacing and look",
    )
    assert len(second.actions) == 4
    cut_action = next(
        action for action in second.actions if action.tool_name == ProposalToolName.SET_CUTS
    )
    decision = proposals.decide(
        proposal_id=second.id,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=ProposalDecisionRequest(
            decision=ProposalDecisionType.PARTIAL_APPROVE,
            expected_proposal_revision=second.revision,
            expected_base_active_plan_id=old_active.id,
            expected_base_active_plan_revision=old_active.revision,
            idempotency_key="partial-key-0001",
            accepted_action_ids=[cut_action.id],
        ),
    )

    new_active = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    assert decision.proposal.state == ProposalState.PARTIALLY_APPROVED
    assert new_active.id != old_active.id
    assert old_active.is_active is False
    assert new_active.plan.caption_highlights == old_active.plan.caption_highlights
    assert proposals.timeline_revisions[-1]["change_type"] == "partial_approval"


def test_rejection_and_stale_media_preserve_old_active_plan() -> None:
    plans, proposals, upload, project_id, workspace_id, user_id = _repositories()
    first, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    _approve(
        proposals,
        first,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    old_active = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    proposal, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt="Another direction",
    )
    rejected = proposals.decide(
        proposal_id=proposal.id,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=ProposalDecisionRequest(
            decision=ProposalDecisionType.REJECT,
            expected_proposal_revision=proposal.revision,
            expected_base_active_plan_id=old_active.id,
            expected_base_active_plan_revision=old_active.revision,
            idempotency_key="reject-key-0001",
            accepted_action_ids=[],
        ),
    )
    assert rejected.edit_plan_id is None
    assert plans.active_plan(project_id=project_id, workspace_id=workspace_id).id == old_active.id

    stale, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt="Stale media proposal",
    )
    upload.media_revision = 2
    with pytest.raises(ApiError, match="active media changed") as exc:
        proposals.decide(
            proposal_id=stale.id,
            project_id=project_id,
            workspace_id=workspace_id,
            user_id=user_id,
            payload=ProposalDecisionRequest(
                decision=ProposalDecisionType.APPROVE,
                expected_proposal_revision=stale.revision,
                expected_base_active_plan_id=old_active.id,
                expected_base_active_plan_revision=old_active.revision,
                idempotency_key="stale-key-0001",
                accepted_action_ids=[],
            ),
        )
    assert exc.value.code == "proposal_media_context_stale"
    assert plans.active_plan(project_id=project_id, workspace_id=workspace_id).id == old_active.id
    assert len(plans.plans) == 1


def test_cross_tenant_and_concurrent_decision_are_safe() -> None:
    plans, proposals, _, project_id, workspace_id, user_id = _repositories()
    proposal, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    with pytest.raises(ApiError) as denied:
        proposals.get(
            proposal_id=proposal.id,
            project_id=project_id,
            workspace_id=uuid4(),
        )
    assert denied.value.code == "workspace_access_denied"

    payload = ProposalDecisionRequest(
        decision=ProposalDecisionType.APPROVE,
        expected_proposal_revision=proposal.revision,
        expected_base_active_plan_id=None,
        expected_base_active_plan_revision=None,
        idempotency_key="concurrent-key-0001",
        accepted_action_ids=[],
    )

    def decide():
        return proposals.decide(
            proposal_id=proposal.id,
            project_id=project_id,
            workspace_id=workspace_id,
            user_id=user_id,
            payload=payload,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: decide(), range(2)))
    assert results[0].decision_id == results[1].decision_id
    assert sorted(result.idempotent_replay for result in results) == [False, True]
    assert len(plans.plans) == 1


def test_registry_rejects_cycle_and_invalid_partial_selection() -> None:
    plans, _, _, project_id, workspace_id, _ = _repositories()
    context = build_multimodal_context(
        repository=plans,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=PlanCreateRequest(target_duration_s=10),
    )
    transcript = plans.latest_transcript(project_id=project_id, workspace_id=workspace_id)
    result = FixtureEditPlannerProvider().create_plan(
        transcript=transcript,
        payload=PlanCreateRequest(target_duration_s=10),
        video_duration_ms=12_000,
    )
    actions = derive_proposal_actions(
        plan=result.plan,
        has_base_plan=True,
        degradation_flags=context.modality_degradation,
    )
    cycled = [
        actions[0].model_copy(update={"dependency_ids": [actions[1].id]}),
        actions[1].model_copy(update={"dependency_ids": [actions[0].id]}),
        *actions[2:],
    ]
    with pytest.raises(ApiError) as cycle:
        TOOL_REGISTRY.validate_actions(cycled)
    assert cycle.value.code == "proposal_dependency_cycle"
    with pytest.raises(ApiError) as full_subset:
        TOOL_REGISTRY.validate_selection(
            actions=actions,
            accepted_action_ids=[action.id for action in actions],
            partial=True,
        )
    assert full_subset.value.code == "proposal_partial_selection_invalid"
    assert canonical_hash(actions) == canonical_hash(actions)


def test_production_fixture_result_fails_closed_without_plan_mutation() -> None:
    plans, proposals, _, project_id, workspace_id, user_id = _repositories()
    first, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    _approve(
        proposals,
        first,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    old_active = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    proposal, result, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt="production fixture must fail closed",
        production=True,
    )

    assert result["failed"] is True
    assert proposal.state == ProposalState.FAILED
    assert proposal.failure_code == "production_deterministic_result_denied"
    assert plans.active_plan(project_id=project_id, workspace_id=workspace_id).id == old_active.id
    assert len(plans.plans) == 1


def test_plan_queue_worker_completes_proposal_without_activating_plan() -> None:
    from app.api.internal_jobs import run_queue_job

    plans, proposals, _, project_id, workspace_id, user_id = _repositories()
    context = build_multimodal_context(
        repository=plans,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=PlanCreateRequest(prompt="worker proposal", target_duration_s=10),
    )
    proposal, _ = proposals.create_queued(
        context=context,
        request_id=request_identity(context=context, idempotency_key="worker-request-0001"),
        created_by=user_id,
        prompt_version="edit-planner-prompt-v2",
    )
    registry = QueueRegistry()
    job = PlanQueue(registry).enqueue_plan(
        project_id=project_id,
        workspace_id=workspace_id,
        transcript_id=proposal.artifacts.transcript_id,
        proposal_id=proposal.id,
        request_id=proposal.request_id,
        context_digest=proposal.context_digest,
        render_engine="{}",
    )
    state = SimpleNamespace(
        settings=SimpleNamespace(app_env="test", worker_api_token="worker-secret"),
        queue_registry=registry,
        proposal_repository=proposals,
        plan_repository=plans,
        edit_planner_provider=FixtureEditPlannerProvider(),
        billing_repository=None,
        operations_repository=None,
        metrics_registry=None,
    )
    response = asyncio.run(
        run_queue_job(
            queue_job_id=job.id,
            request=SimpleNamespace(app=SimpleNamespace(state=state)),
            x_motionly_worker_token="worker-secret",
        )
    )

    assert response.status.value == "succeeded"
    assert response.result["status"] == "awaiting_approval"
    assert plans.list_plans(project_id=project_id, workspace_id=workspace_id) == []


def test_stale_base_revision_fails_without_lost_update() -> None:
    plans, proposals, _, project_id, workspace_id, user_id = _repositories()
    first, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    _approve(
        proposals,
        first,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    active = plans.active_plan(project_id=project_id, workspace_id=workspace_id)
    proposal, _, _ = _generate(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        prompt="proposal against revision one",
    )
    active.revision += 1
    with pytest.raises(ApiError) as stale:
        proposals.decide(
            proposal_id=proposal.id,
            project_id=project_id,
            workspace_id=workspace_id,
            user_id=user_id,
            payload=ProposalDecisionRequest(
                decision=ProposalDecisionType.APPROVE,
                expected_proposal_revision=proposal.revision,
                expected_base_active_plan_id=proposal.artifacts.base_active_plan_id,
                expected_base_active_plan_revision=proposal.artifacts.base_active_plan_revision,
                idempotency_key="stale-base-key-0001",
                accepted_action_ids=[],
            ),
        )
    assert stale.value.code == "proposal_base_plan_stale"
    assert plans.active_plan(project_id=project_id, workspace_id=workspace_id).id == active.id
    assert len(plans.plans) == 1


def test_proposal_http_flow_queues_reads_and_decides() -> None:
    from fastapi.testclient import TestClient

    from app.api.dependencies import Actor, get_actor
    from app.core.middleware import InMemoryRateLimiter
    from app.main import create_app

    plans, proposals, _, project_id, workspace_id, user_id = _repositories()
    app = create_app()
    registry = QueueRegistry()
    app.state.plan_repository = plans
    app.state.proposal_repository = proposals
    app.state.queue_registry = registry
    app.state.plan_queue = PlanQueue(registry)
    app.state.edit_planner_provider = FixtureEditPlannerProvider()
    app.state.scale_repository = None
    app.state.settings = SimpleNamespace(
        app_env="test",
        redis_url=None,
        rate_limit_per_minute=1_000,
        supabase_jwt_secret=None,
        supabase_url=None,
        supabase_service_role_key=None,
    )
    app.state.rate_limiter = InMemoryRateLimiter()
    app.dependency_overrides[get_actor] = lambda: Actor(
        user_id=user_id,
        workspace_id=workspace_id,
    )

    with TestClient(app) as client:
        created = client.post(
            f"/projects/{project_id}/plans",
            json={
                "prompt": "HTTP proposal",
                "target_duration_s": 10,
                "idempotency_key": "http-request-0001",
            },
        )
        assert created.status_code == 202
        created_body = created.json()
        proposal_id = created_body["proposal_id"]
        assert created_body["plan"] is None
        assert plans.list_plans(project_id=project_id, workspace_id=workspace_id) == []

        queued = client.get(f"/projects/{project_id}/proposals/{proposal_id}")
        assert queued.status_code == 200
        assert queued.json()["state"] == "queued"

        result = execute_proposal_generation(
            proposal_repository=proposals,
            plan_repository=plans,
            planner_provider=FixtureEditPlannerProvider(),
            workspace_id=workspace_id,
            project_id=project_id,
            proposal_id=uuid4_from_text(proposal_id),
            settings=SimpleNamespace(app_env="test"),
        )
        assert result["status"] == "awaiting_approval"
        ready = client.get(f"/projects/{project_id}/proposals/{proposal_id}/status")
        ready_body = ready.json()
        revision = ready_body["revision"]
        action_id = ready_body["actions"][0]["id"]
        invalid = client.post(
            f"/projects/{project_id}/proposals/{proposal_id}/decisions",
            json={
                "decision": "partial_approve",
                "expected_proposal_revision": revision,
                "expected_base_active_plan_id": None,
                "expected_base_active_plan_revision": None,
                "idempotency_key": "http-invalid-0001",
                "accepted_action_ids": [action_id, action_id],
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "proposal_duplicate_selection"
        decided = client.post(
            f"/projects/{project_id}/proposals/{proposal_id}/decisions",
            json={
                "decision": "approve",
                "expected_proposal_revision": revision,
                "expected_base_active_plan_id": None,
                "expected_base_active_plan_revision": None,
                "idempotency_key": "http-decision-0001",
                "accepted_action_ids": [],
            },
        )
        assert decided.status_code == 200
        assert decided.json()["proposal"]["state"] == "approved"
        assert plans.active_plan(project_id=project_id, workspace_id=workspace_id).is_active


def uuid4_from_text(value: str):
    from uuid import UUID

    return UUID(value)
