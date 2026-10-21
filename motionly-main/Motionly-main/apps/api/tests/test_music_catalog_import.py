"""Task 27: music catalog import tool and verified-rights enforcement.

Tests first. The importer refuses any track without a complete, verified rights
record, a licence on the allow-list, and both the composition AND the recording
covered. Test tracks are labelled and refused in production.
"""

from __future__ import annotations

import hashlib

import pytest

from app.api.errors import ApiError
from app.services.music_catalog_import import (
    MOOD_TAGS,
    AudioAnalysis,
    MusicImportError,
    TrackManifestEntry,
    import_music_catalog,
    load_license_allow_list,
    validate_mood_tags,
    verification_from_mapping,
)


def _licence_archive(text: str = "CC0 1.0 Universal licence text") -> tuple[str, str]:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return text, digest


def _full_verification(**overrides: object) -> dict[str, object]:
    archive_text, digest = _licence_archive()
    record: dict[str, object] = {
        "source_page_url": "https://example.org/tracks/test-track-1",
        "license_archive_sha256": digest,
        "license_id": "CC0-1.0",
        "license_version": "1.0",
        "rights_holder": "Test Uploader",
        "composition_rights_status": "cleared",
        "recording_rights_status": "cleared",
        "verified_by": "admin@example.org",
        "verified_at": "2026-10-19T00:00:00+00:00",
    }
    record.update(overrides)
    return record


class _StubAnalyzer:
    """Deterministic stand-in for librosa analysis (no audio decode in tests)."""

    def analyze(self, audio_path: str) -> AudioAnalysis:
        return AudioAnalysis(
            duration_ms=60_000,
            tempo_bpm=120.0,
            beat_positions_ms=[0.0, 500.0, 1000.0, 1500.0],
            energy=0.42,
        )


class _RecordingRepo:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def upsert_catalog_track(self, **values: object) -> dict[str, object]:
        self.rows.append(dict(values))
        return dict(values)


# ---- allow-list ----------------------------------------------------------


def test_allow_list_is_the_policy_approved_at_d3() -> None:
    allow_list = load_license_allow_list()
    assert allow_list.approved is True
    # Exactly the approved policy: CC0 1.0, or public domain verified for both sides.
    assert {(entry.id, entry.version) for entry in allow_list.allowed} == {
        ("CC0-1.0", "1.0"),
        ("PD", "n/a"),
    }
    assert allow_list.is_allowed("CC0-1.0", "1.0")
    # Attribution / NC / ND / SA licences are excluded.
    assert not allow_list.is_allowed("CC-BY-4.0", "4.0")
    assert not allow_list.is_allowed("CC-BY-SA-4.0", "4.0")


def test_public_domain_requires_both_sides_verified() -> None:
    allow_list = load_license_allow_list()
    entry = allow_list.entry("PD", "n/a")
    assert entry is not None
    assert entry.requires_both_sides_public_domain is True


# ---- mood tags -----------------------------------------------------------


def test_mood_tags_checked_against_the_fixed_list() -> None:
    assert validate_mood_tags(["uplifting", "ambient"]) == ["uplifting", "ambient"]
    with pytest.raises(MusicImportError):
        validate_mood_tags(["definitely-not-a-mood"])
    assert "uplifting" in MOOD_TAGS


# ---- verification record -------------------------------------------------


def test_complete_verification_record_is_accepted() -> None:
    record = verification_from_mapping(_full_verification())
    assert record.license_id == "CC0-1.0"
    assert record.composition_rights_status == "cleared"
    assert record.recording_rights_status == "cleared"
    assert record.fully_covered is True


@pytest.mark.parametrize(
    "missing_field",
    [
        "source_page_url",
        "license_archive_sha256",
        "license_id",
        "license_version",
        "rights_holder",
        "composition_rights_status",
        "recording_rights_status",
        "verified_by",
        "verified_at",
    ],
)
def test_verification_record_rejects_a_missing_field(missing_field: str) -> None:
    data = _full_verification()
    data.pop(missing_field)
    with pytest.raises(MusicImportError):
        verification_from_mapping(data)


def test_verification_record_rejects_composition_only_cover() -> None:
    record = verification_from_mapping(
        _full_verification(recording_rights_status="unknown")
    )
    assert record.fully_covered is False


# ---- importer ------------------------------------------------------------


def _manifest_entry(**overrides: object) -> TrackManifestEntry:
    base: dict[str, object] = {
        "provider_track_id": "test-track-1",
        "title": "Test Track One",
        "audio_path": "/tmp/test-track-1.wav",
        "mood_tags": ["uplifting", "ambient"],
        "is_test_track": True,
        "verification": _full_verification(),
    }
    base.update(overrides)
    return TrackManifestEntry.from_mapping(base)


def test_import_three_generated_test_tracks() -> None:
    repo = _RecordingRepo()
    entries = [
        _manifest_entry(provider_track_id=f"test-track-{index}", title=f"Test Track {index}")
        for index in range(1, 4)
    ]
    result = import_music_catalog(
        entries,
        repository=repo,
        analyzer=_StubAnalyzer(),
        environment="test",
    )
    assert result.imported == 3
    assert len(repo.rows) == 3
    for row in repo.rows:
        assert row["title"].startswith("Test track: ")
        assert row["rights_status"] == "cleared"
        assert row["tempo_bpm"] == 120.0
        assert row["energy"] == 0.42
        assert row["license_evidence_hash"]
        # Test rights evidence is explicit on the row.
        assert row["is_test_track"] is True


def test_import_rejects_a_record_missing_a_field() -> None:
    repo = _RecordingRepo()
    bad = _full_verification()
    bad.pop("verified_by")
    with pytest.raises(MusicImportError):
        import_music_catalog(
            [_manifest_entry(verification=bad)],
            repository=repo,
            analyzer=_StubAnalyzer(),
            environment="test",
        )
    assert repo.rows == []


def test_import_rejects_composition_only_track() -> None:
    repo = _RecordingRepo()
    with pytest.raises(MusicImportError):
        import_music_catalog(
            [_manifest_entry(verification=_full_verification(recording_rights_status="unknown"))],
            repository=repo,
            analyzer=_StubAnalyzer(),
            environment="test",
        )
    assert repo.rows == []


def test_import_rejects_licence_not_on_allow_list() -> None:
    repo = _RecordingRepo()
    with pytest.raises(MusicImportError):
        import_music_catalog(
            [
                _manifest_entry(
                    verification=_full_verification(license_id="CC-BY-4.0", license_version="4.0")
                )
            ],
            repository=repo,
            analyzer=_StubAnalyzer(),
            environment="test",
        )
    assert repo.rows == []


def test_test_tracks_refused_in_production() -> None:
    repo = _RecordingRepo()
    with pytest.raises((MusicImportError, ApiError)):
        import_music_catalog(
            [_manifest_entry(is_test_track=True)],
            repository=repo,
            analyzer=_StubAnalyzer(),
            environment="production",
        )
    assert repo.rows == []
