"""Task 29: a promoted editorial memory measurably changes new plans.

Tests-first: a plan generated with an active, consented editorial memory must
differ from one generated without it in the preferred fields, the effect must
be deterministic with the fixture planner, withdrawing consent must remove the
effect, and per-field provenance must be recorded.
"""

from __future__ import annotations

from uuid import uuid4

from app.repositories.editorial_learning import InMemoryEditorialLearningRepository
from app.schemas.editorial_learning import EditorialMemoryPreferences
from app.schemas.plans import (
    BrollDensity,
    BrollRequest,
    CaptionHighlight,
    EditPlan,
    EditPlanCut,
    EditPlanStyle,
    ZoomMoment,
)
from app.services.editorial_memory import (
    apply_memory_to_plan,
    derive_memory_preferences,
)

POLICY_VERSION = "editorial-consent/v1"


def _base_plan() -> EditPlan:
    return EditPlan(
        style=EditPlanStyle.LUXURY,
        target_duration_s=30,
        cuts=[EditPlanCut(start_ms=0, end_ms=3000, reason="keep")],
        caption_highlights=[
            CaptionHighlight(timestamp_ms=100, word="brand", effect="pulse"),
        ],
        broll_requests=[
            BrollRequest(start_ms=100, end_ms=1600, keywords=["luxury moment"], priority=0.8),
        ],
        zoom_moments=[ZoomMoment(timestamp_ms=100, intensity=1.12, reason="emphasis")],
        music_mood="uplifting ambient luxury",
        effects=["warm_grade", "soft_flash"],
        color_grade="champagne_gold",
    )


def _seed_promoted_memory(
    repo: InMemoryEditorialLearningRepository,
    *,
    workspace_id,
    project_id,
    user_id,
) -> None:
    repo.set_policy_if_revision(
        workspace_id=workspace_id,
        user_id=user_id,
        memory_enabled=True,
        training_enabled=True,
        policy_version=POLICY_VERSION,
        retention_days=None,
        expected_revision=None,
    )
    for layer, after in (
        ("music", {"mood": "tense cinematic score"}),
        ("effects", {"preset": "film_grain"}),
        ("captions", {"style": "color_pop"}),
        ("broll", {"density": "high"}),
        ("cuts", {"pace": "fast"}),
    ):
        repo.capture_delta(
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            source_event_id=uuid4(),
            layer=layer,
            delta={"before": {}, "after": after},
            normalizer_version="editorial-delta/v1",
            memory_eligible=True,
            training_eligible=True,
        )
    dataset = repo.freeze_dataset(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        minimum_examples=1,
    )
    repo.promote(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        dataset_id=dataset.id,
        expected_revision=0,
    )


def test_derive_memory_preferences_is_typed_and_from_consented_examples() -> None:
    workspace_id, project_id, user_id = uuid4(), uuid4(), uuid4()
    repo = InMemoryEditorialLearningRepository()
    _seed_promoted_memory(
        repo, workspace_id=workspace_id, project_id=project_id, user_id=user_id
    )
    preferences = repo.resolve_active_preferences(
        workspace_id=workspace_id, project_id=project_id, user_id=user_id
    )
    assert isinstance(preferences, EditorialMemoryPreferences)
    assert preferences.music_mood == "tense cinematic score"
    assert preferences.broll_density == BrollDensity.HIGH
    assert preferences.caption_style == "color_pop"
    assert preferences.cut_style == "fast"
    assert "film_grain" in preferences.effects
    assert preferences.example_count == 5
    assert preferences.memory_version  # manifest hash


def test_plan_with_memory_differs_from_plan_without_it() -> None:
    preferences = EditorialMemoryPreferences(
        memory_id=str(uuid4()),
        memory_version="abc123",
        policy_revision=1,
        example_count=5,
        music_mood="tense cinematic score",
        effects=["film_grain"],
        caption_style="color_pop",
        broll_density=BrollDensity.HIGH,
        cut_style="fast",
    )
    baseline = _base_plan()
    personalized, provenance = apply_memory_to_plan(plan=baseline, preferences=preferences)

    # Preferred fields change deterministically.
    assert personalized.music_mood != baseline.music_mood
    assert personalized.music_mood == "tense cinematic score"
    assert "film_grain" in personalized.effects
    assert personalized.caption_highlights[0].effect == "color_pop"

    # Provenance records where each field came from.
    assert provenance["music_mood"]["source"] == "memory"
    assert provenance["music_mood"]["memory_version"] == "abc123"
    assert provenance["effects"]["source"] == "memory"
    assert provenance["caption_style"]["source"] == "memory"

    # Deterministic: applying twice yields the same plan.
    again, _ = apply_memory_to_plan(plan=baseline, preferences=preferences)
    assert again.model_dump() == personalized.model_dump()


def test_without_memory_fields_default_provenance() -> None:
    preferences = EditorialMemoryPreferences(
        memory_id=str(uuid4()),
        memory_version="abc123",
        policy_revision=1,
        example_count=2,
        music_mood=None,
        effects=[],
        caption_style=None,
        broll_density=None,
        cut_style=None,
    )
    baseline = _base_plan()
    personalized, provenance = apply_memory_to_plan(plan=baseline, preferences=preferences)
    assert personalized.music_mood == baseline.music_mood
    assert provenance["music_mood"]["source"] == "default"
    assert provenance["effects"]["source"] == "default"


def test_withdrawing_consent_removes_memory_from_new_plans() -> None:
    workspace_id, project_id, user_id = uuid4(), uuid4(), uuid4()
    repo = InMemoryEditorialLearningRepository()
    _seed_promoted_memory(
        repo, workspace_id=workspace_id, project_id=project_id, user_id=user_id
    )
    assert (
        repo.resolve_active_preferences(
            workspace_id=workspace_id, project_id=project_id, user_id=user_id
        )
        is not None
    )
    repo.revoke(
        workspace_id=workspace_id,
        user_id=user_id,
        memory=True,
        training=True,
        reason="user_request",
    )
    assert (
        repo.resolve_active_preferences(
            workspace_id=workspace_id, project_id=project_id, user_id=user_id
        )
        is None
    )


def test_memory_only_from_consented_examples_not_arbitrary_deltas() -> None:
    workspace_id, project_id, user_id = uuid4(), uuid4(), uuid4()
    repo = InMemoryEditorialLearningRepository()
    # No consent -> capture_delta returns None -> nothing to promote.
    captured = repo.capture_delta(
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        source_event_id=uuid4(),
        layer="music",
        delta={"before": {}, "after": {"mood": "sneaky"}},
        normalizer_version="editorial-delta/v1",
        memory_eligible=True,
        training_eligible=True,
    )
    assert captured is None
    assert (
        repo.resolve_active_preferences(
            workspace_id=workspace_id, project_id=project_id, user_id=user_id
        )
        is None
    )


def test_derive_memory_preferences_pure_helper() -> None:
    class _Delta:
        def __init__(self, layer, after):
            self.layer = layer
            self.delta = {"after": after}

    deltas = [
        _Delta("music", {"mood": "warm"}),
        _Delta("music", {"mood": "bright"}),  # newer wins (reversed order)
    ]
    prefs = derive_memory_preferences(
        deltas=deltas,
        memory_id="d1",
        memory_version="hash",
        policy_revision=2,
    )
    # Latest non-empty per layer wins; deltas iterated newest-first by caller.
    assert prefs.music_mood in {"warm", "bright"}
    assert prefs.example_count == 2


def _proposal_harness():
    from app.repositories.ai_proposals import InMemoryAIProposalRepository
    from app.repositories.captions import InMemoryCaptionRepository
    from app.repositories.plans import InMemoryPlanRepository
    from app.repositories.uploads import InMemoryUploadRepository
    from app.schemas.captions import LanguageHint
    from app.schemas.uploads import UploadStatus
    from app.services.transcription_provider import FixtureTranscriptionProvider

    upload_repository = InMemoryUploadRepository()
    project_id, workspace_id, user_id = uuid4(), uuid4(), uuid4()
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
        words=transcript_result.words,
    )
    plans = InMemoryPlanRepository(captions)
    proposals = InMemoryAIProposalRepository(plan_repository=plans)
    return plans, proposals, project_id, workspace_id, user_id


def _generate_proposal(
    plans, proposals, *, project_id, workspace_id, user_id, learning=None
):
    from types import SimpleNamespace

    from app.schemas.plans import PlanCreateRequest
    from app.services.ai_proposals import (
        build_multimodal_context,
        canonical_hash,
        request_identity,
    )
    from app.services.edit_planner import FixtureEditPlannerProvider
    from app.services.proposal_generation import execute_proposal_generation

    payload = PlanCreateRequest(prompt=f"edit {uuid4()}", target_duration_s=10)
    context = build_multimodal_context(
        repository=plans,
        workspace_id=workspace_id,
        project_id=project_id,
        payload=payload,
    )
    proposal, _ = proposals.create_queued(
        context=context,
        request_id=request_identity(
            context=context, idempotency_key=f"req-{canonical_hash(str(payload))[:16]}"
        ),
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
        settings=SimpleNamespace(
            app_env="test", editorial_learning_enabled=learning is not None
        ),
        editorial_learning_repository=learning,
    )
    return proposals.get(
        proposal_id=proposal.id, project_id=project_id, workspace_id=workspace_id
    )


def test_generated_proposal_plan_changes_with_memory_and_records_provenance() -> None:
    plans, proposals, project_id, workspace_id, user_id = _proposal_harness()
    baseline = _generate_proposal(
        plans, proposals, project_id=project_id, workspace_id=workspace_id, user_id=user_id
    )

    learning = InMemoryEditorialLearningRepository()
    _seed_promoted_memory(
        learning, workspace_id=workspace_id, project_id=project_id, user_id=user_id
    )
    personalized = _generate_proposal(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        learning=learning,
    )

    assert baseline.generated_plan.music_mood != personalized.generated_plan.music_mood
    assert personalized.generated_plan.music_mood == "tense cinematic score"
    assert "film_grain" in personalized.generated_plan.effects

    memory = personalized.provenance.editorial_memory
    assert memory is not None
    assert memory["example_count"] == 5
    assert memory["fields"]["music_mood"]["source"] == "memory"
    assert memory["fields"]["caption_style"]["source"] == "memory"

    # Baseline proposal records no memory provenance.
    assert baseline.provenance.editorial_memory is None


def test_withdrawn_consent_leaves_generated_plan_unchanged() -> None:
    plans, proposals, project_id, workspace_id, user_id = _proposal_harness()
    baseline = _generate_proposal(
        plans, proposals, project_id=project_id, workspace_id=workspace_id, user_id=user_id
    )
    learning = InMemoryEditorialLearningRepository()
    _seed_promoted_memory(
        learning, workspace_id=workspace_id, project_id=project_id, user_id=user_id
    )
    learning.revoke(
        workspace_id=workspace_id,
        user_id=user_id,
        memory=True,
        training=True,
        reason="user_request",
    )
    after_withdrawal = _generate_proposal(
        plans,
        proposals,
        project_id=project_id,
        workspace_id=workspace_id,
        user_id=user_id,
        learning=learning,
    )
    assert after_withdrawal.generated_plan.music_mood == baseline.generated_plan.music_mood
    assert after_withdrawal.provenance.editorial_memory is None
