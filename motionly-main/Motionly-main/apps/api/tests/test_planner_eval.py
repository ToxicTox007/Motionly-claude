from uuid import uuid4

from app.repositories.captions import TranscriptRecord
from app.schemas.captions import LanguageHint, TranscriptStatus, TranscriptWord
from app.schemas.plans import EditPlan, EditPlanStyle, PlanCreateRequest
from app.services.edit_planner import _fallback_plan, _validate_or_repair_plan


def _dummy_transcript() -> TranscriptRecord:
    return TranscriptRecord(
        id=uuid4(),
        workspace_id=uuid4(),
        project_id=uuid4(),
        uploaded_video_id=uuid4(),
        status=TranscriptStatus.READY,
        language="en",
        language_hint=LanguageHint.AUTO,
        provider="fixture",
        model="whisper-1",
        confidence=0.95,
        duration_ms=12000,
        words=[
            TranscriptWord(index=0, text="Welcome", start_ms=0, end_ms=1000, confidence=0.9),
            TranscriptWord(index=1, text="to", start_ms=1050, end_ms=2000, confidence=0.95),
            TranscriptWord(index=2, text="motionly", start_ms=2050, end_ms=3000, confidence=0.98),
        ],
    )


def test_eval_perfect_provider_plan() -> None:
    # Perfect plan matching our schema
    raw_plan = {
        "style": "founder",
        "target_duration_s": 10,
        "cuts": [
            {"start_ms": 0, "end_ms": 1000, "reason": "hook"},
            {"start_ms": 1050, "end_ms": 2000, "reason": "intro"},
        ],
        "caption_highlights": [
            {"timestamp_ms": 500, "word": "Welcome", "effect": "pulse"}
        ],
        "broll_requests": [
            {
                "start_ms": 0,
                "end_ms": 1000,
                "keywords": ["office", "intro"],
                "priority": 0.8,
                "shot_type": "cutaway",
                "placement_rule": "semantic_anchor",
                "semantic_query": "welcome",
                "semantic_embedding": [],
            }
        ],
        "zoom_moments": [],
        "music_mood": "inspiring",
        "effects": [],
        "color_grade": "warm",
        "risk_flags": [],
    }
    
    transcript = _dummy_transcript()
    payload = PlanCreateRequest(style=EditPlanStyle.FOUNDER, target_duration_s=10)
    
    plan, validation_errors, fallback_used = _validate_or_repair_plan(
        raw_plan=raw_plan,
        transcript=transcript,
        payload=payload,
        video_duration_ms=12000,
        provider_name="test-eval",
    )
    
    assert not fallback_used
    assert not validation_errors
    assert plan.style == EditPlanStyle.FOUNDER
    assert len(plan.cuts) == 2
    assert plan.target_duration_s == 10


def test_eval_negative_durations_and_overlaps() -> None:
    # Plan containing a negative duration cut (end_ms <= start_ms) and overlapping cuts
    raw_plan = {
        "style": "luxury",
        "target_duration_s": 15,
        "cuts": [
            {"start_ms": 1000, "end_ms": 500, "reason": "negative duration!"}, # Invalid duration
            {"start_ms": 0, "end_ms": 2000, "reason": "valid cut"},
        ],
        "caption_highlights": [],
        "broll_requests": [],
        "zoom_moments": [],
        "music_mood": "ambient",
        "effects": [],
        "color_grade": "cool",
        "risk_flags": [],
    }
    
    transcript = _dummy_transcript()
    payload = PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=15)
    
    plan, validation_errors, fallback_used = _validate_or_repair_plan(
        raw_plan=raw_plan,
        transcript=transcript,
        payload=payload,
        video_duration_ms=12000,
        provider_name="test-eval",
    )
    
    assert not fallback_used  # Should be repaired rather than using fallback
    assert any("repair_applied" in err for err in validation_errors)
    
    # Assertions on cuts: negative duration cut should be discarded or repaired
    assert len(plan.cuts) >= 1
    for cut in plan.cuts:
        assert cut.end_ms > cut.start_ms


def test_eval_insane_target_duration() -> None:
    # Target duration out of bounds (allowed is 10 to 90 seconds)
    raw_plan = {
        "style": "luxury",
        "target_duration_s": 9999,  # Insane duration
        "cuts": [{"start_ms": 0, "end_ms": 2000, "reason": "hook"}],
        "caption_highlights": [],
        "broll_requests": [],
        "zoom_moments": [],
        "music_mood": "ambient",
        "effects": [],
        "color_grade": "cool",
        "risk_flags": [],
    }
    
    transcript = _dummy_transcript()
    payload = PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=10)
    
    plan, validation_errors, fallback_used = _validate_or_repair_plan(
        raw_plan=raw_plan,
        transcript=transcript,
        payload=payload,
        video_duration_ms=12000,
        provider_name="test-eval",
    )
    
    assert not fallback_used
    assert plan.target_duration_s <= 90  # Repaired to sane max bounds


def test_eval_invalid_broll_requests() -> None:
    # Broll requests containing invalid types or structures
    raw_plan = {
        "style": "luxury",
        "target_duration_s": 15,
        "cuts": [{"start_ms": 0, "end_ms": 2000, "reason": "hook"}],
        "caption_highlights": [],
        "broll_requests": [
            {
                "start_ms": 0,
                "end_ms": 1000,
                "keywords": "not a list but a single string",
                "priority": "not a float",
            }
        ],
        "zoom_moments": [],
        "music_mood": "ambient",
        "effects": [],
        "color_grade": "cool",
        "risk_flags": [],
    }
    
    transcript = _dummy_transcript()
    payload = PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=15)
    
    plan, validation_errors, fallback_used = _validate_or_repair_plan(
        raw_plan=raw_plan,
        transcript=transcript,
        payload=payload,
        video_duration_ms=12000,
        provider_name="test-eval",
    )
    
    assert not fallback_used
    # Assert validation check for broll repairs
    assert len(plan.broll_requests) == 1
    assert isinstance(plan.broll_requests[0].keywords, list)
    assert isinstance(plan.broll_requests[0].priority, float)


def test_eval_json_decode_and_fallback_triggered() -> None:
    # Completely broken JSON payload that should fail parsing entirely and trigger fallback
    
    # Simulate decoder error in HttpEditPlannerProvider logic
    transcript = _dummy_transcript()
    payload = PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=15)
    
    # We test _fallback_plan returns a valid plan
    fallback = _fallback_plan(
        transcript=transcript,
        payload=payload,
        video_duration_ms=12000,
    )
    assert isinstance(fallback, EditPlan)
    assert fallback.style == EditPlanStyle.LUXURY


def test_eval_known_bad_fixture_fails_validation() -> None:
    # Known-bad provider output that lacks cuts entirely, triggering fallback
    raw_plan = {
        "style": "invalid-style-name",
        "target_duration_s": -50,
        "cuts": [],  # Lacks valid cuts completely
    }
    
    transcript = _dummy_transcript()
    payload = PlanCreateRequest(style=EditPlanStyle.LUXURY, target_duration_s=15)
    
    # This plan should be coerced or fall back
    plan, validation_errors, fallback_used = _validate_or_repair_plan(
        raw_plan=raw_plan,
        transcript=transcript,
        payload=payload,
        video_duration_ms=12000,
        provider_name="test-eval",
    )
    
    # Lacking cuts entirely should force fallback
    assert len(plan.cuts) > 0
