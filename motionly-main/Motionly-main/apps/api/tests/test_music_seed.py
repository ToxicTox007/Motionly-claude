"""Task 32 follow-up: seed the generated 'Test track' catalog for non-production.

FixtureMusicProvider is gone. Non-production and the CPU stage now serve music
from the licensed catalog seeded with GENERATED test tracks: Python-synthesised
audio in the private music-catalog bucket, carrying test rights evidence, a
verified grant, and covering every planner mood so every plan gets a fitting
track. The seed is refused in production (test tracks never leak).

Tests first.
"""

from __future__ import annotations

import pytest

from app.services.music_catalog_import import (
    TEST_TRACK_LABEL,
    AudioAnalysis,
    MusicImportError,
    build_catalog_row,
    load_license_allow_list,
)
from app.services.music_search import planner_mood_tags
from app.services.music_seed import (
    MUSIC_CATALOG_BUCKET,
    generate_test_track_entries,
    seed_test_music_catalog,
)


class _StubAnalyzer:
    def analyze(self, audio_path: str) -> AudioAnalysis:
        return AudioAnalysis(
            duration_ms=60_000,
            tempo_bpm=112.0,
            beat_positions_ms=[0.0, 500.0, 1000.0],
            energy=0.4,
        )


class _RecordingRepo:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def upsert_catalog_track(self, **values: object) -> dict[str, object]:
        self.rows.append(dict(values))
        return dict(values)


class _RecordingStorage:
    """Records uploads instead of touching real object storage."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []

    def upload_file(self, *, bucket, path, source_path, content_type="application/octet-stream"):
        self.uploads.append((bucket, path))
        return path


def _fake_synth(*, frequency, duration_s, output_path):  # matches synthesize_test_track
    # Do not synthesise audio in unit tests; pretend the file was written.
    return output_path


def test_generate_entries_cover_every_planner_mood() -> None:
    entries = generate_test_track_entries()
    covered: set[str] = set()
    for entry in entries:
        covered.update(entry.mood_tags)
        assert entry.is_test_track is True
        assert entry.audio_bucket == MUSIC_CATALOG_BUCKET
        assert entry.audio_object_path
        # Every generated candidate carries a complete verification record.
        assert set(entry.verification) >= {
            "license_id",
            "composition_rights_status",
            "recording_rights_status",
            "license_archive_sha256",
        }
    assert planner_mood_tags() <= covered


def test_generated_entries_import_into_usable_catalog_rows() -> None:
    allow_list = load_license_allow_list()
    for entry in generate_test_track_entries():
        row = build_catalog_row(entry, allow_list=allow_list, analysis=_StubAnalyzer().analyze(""))
        # A usable, selectable, renderable catalog row.
        assert row["rights_status"] == "cleared"
        assert row["is_test_track"] is True
        assert str(row["title"]).startswith(f"{TEST_TRACK_LABEL}: ")
        assert row["rights_grant_id"]
        assert row["rights_grant_version"]
        assert row["permitted_uses"] == ["editorial_export"]
        assert row["audio_bucket"] == MUSIC_CATALOG_BUCKET
        assert row["audio_path"]
        # Grant window is open now and not yet expired.
        assert row["rights_valid_from"] < row["rights_valid_until"]


def test_seed_uploads_audio_and_imports_rows(tmp_path) -> None:
    repo = _RecordingRepo()
    storage = _RecordingStorage()
    result = seed_test_music_catalog(
        repository=repo,
        storage=storage,
        environment="local",
        analyzer=_StubAnalyzer(),
        audio_dir=tmp_path,
        synthesize=_fake_synth,
    )
    assert result.imported == len(repo.rows) > 0
    # Every imported row has a matching uploaded audio object in the private bucket.
    uploaded_paths = {path for _bucket, path in storage.uploads}
    assert all(bucket == MUSIC_CATALOG_BUCKET for bucket, _ in storage.uploads)
    for row in repo.rows:
        assert row["audio_path"] in uploaded_paths
        assert row["is_test_track"] is True


def test_seed_is_refused_in_production() -> None:
    repo = _RecordingRepo()
    storage = _RecordingStorage()
    for environment in ("production", "prod"):
        with pytest.raises(MusicImportError):
            seed_test_music_catalog(
                repository=repo,
                storage=storage,
                environment=environment,
                analyzer=_StubAnalyzer(),
                synthesize=_fake_synth,
            )
    assert repo.rows == []
    assert storage.uploads == []
