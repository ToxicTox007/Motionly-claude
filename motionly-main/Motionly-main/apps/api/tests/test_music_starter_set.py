"""Task 28: tests for the music starter-set tooling (D3, no real import)."""

from __future__ import annotations

from app.services.music_search import planner_mood_tags
from app.services.music_starter_set import (
    MINIMUM_CANDIDATES,
    build_signoff_checklist,
    empty_manifest_template,
    validate_candidate_manifest,
)


def _complete_entry(track_id: str, mood_tags: list[str]) -> dict:
    return {
        "provider_track_id": track_id,
        "title": f"Candidate {track_id}",
        "audio_path": f"candidates/{track_id}.wav",
        "mood_tags": mood_tags,
        "is_test_track": False,
        "verification": {
            "source_page_url": "https://example.com/track",
            "license_archive_sha256": "a" * 64,
            "license_id": "CC0-1.0",
            "license_version": "1.0",
            "rights_holder": "Example Artist",
            "composition_rights_status": "cleared",
            "recording_rights_status": "cleared",
            "verified_by": "reviewer@example.com",
            "verified_at": "2026-10-20T00:00:00+00:00",
        },
    }


def test_empty_template_has_minimum_blank_slots() -> None:
    template = empty_manifest_template()
    assert len(template["tracks"]) == MINIMUM_CANDIDATES
    first = template["tracks"][0]
    assert first["provider_track_id"] == ""
    assert first["is_test_track"] is False
    # Every verification field is present but blank.
    assert set(first["verification"]) and all(
        value == "" for value in first["verification"].values()
    )


def test_empty_template_fails_validation() -> None:
    # The template is a starting point; it must not validate until filled.
    result = validate_candidate_manifest(empty_manifest_template())
    assert not result.ok
    assert result.errors
    assert result.missing_moods == planner_mood_tags()


def test_filled_manifest_covering_every_planner_mood_validates() -> None:
    required = sorted(planner_mood_tags())
    tracks = []
    # Spread the required moods across enough candidates to meet the minimum.
    for index in range(MINIMUM_CANDIDATES):
        mood = required[index % len(required)]
        tracks.append(_complete_entry(f"cand-{index}", [mood]))
    result = validate_candidate_manifest({"tracks": tracks})
    assert result.errors == []
    assert result.missing_moods == set()
    assert result.meets_minimum
    assert result.ok


def test_incomplete_entry_is_reported() -> None:
    entry = _complete_entry("cand-x", ["luxury"])
    del entry["verification"]["verified_by"]
    result = validate_candidate_manifest({"tracks": [entry]})
    assert any("verified_by" in error for error in result.errors)


def test_bad_mood_tag_is_reported() -> None:
    entry = _complete_entry("cand-y", ["definitely-not-a-mood"])
    result = validate_candidate_manifest({"tracks": [entry]})
    assert any("mood" in error.lower() for error in result.errors)


def test_checklist_covers_every_planner_mood() -> None:
    checklist = build_signoff_checklist()
    for tag in planner_mood_tags():
        assert f"`{tag}`" in checklist
    assert str(MINIMUM_CANDIDATES) in checklist
    assert "D3" in checklist
    # The licence policy is already approved; importing still needs each
    # track's own sign-off, so the checklist must not equate the two.
    assert "set `approved: true`" not in checklist
    assert "every track in this set is individually signed off" in checklist.lower()
