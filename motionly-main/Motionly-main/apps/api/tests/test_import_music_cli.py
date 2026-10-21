"""Task 27: the `python -m app.ops.import_music <manifest>` CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.ops import import_music
from app.services.music_catalog_import import AudioAnalysis


class _StubAnalyzer:
    def analyze(self, audio_path: str) -> AudioAnalysis:
        return AudioAnalysis(
            duration_ms=60_000,
            tempo_bpm=118.0,
            beat_positions_ms=[0.0, 500.0, 1000.0],
            energy=0.4,
        )


class _RecordingRepo:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def upsert_catalog_track(self, **values: object) -> dict[str, object]:
        self.rows.append(dict(values))
        return dict(values)


def _write_manifest(tmp_path: Path) -> Path:
    digest = hashlib.sha256(b"CC0 licence text").hexdigest()
    manifest = {
        "environment": "test",
        "tracks": [
            {
                "provider_track_id": f"test-track-{index}",
                "title": f"Generated {index}",
                "audio_path": f"/tmp/track-{index}.wav",
                "mood_tags": ["ambient", "calm"],
                "is_test_track": True,
                "verification": {
                    "source_page_url": "https://example.org/t",
                    "license_archive_sha256": digest,
                    "license_id": "CC0-1.0",
                    "license_version": "1.0",
                    "rights_holder": "Test",
                    "composition_rights_status": "cleared",
                    "recording_rights_status": "cleared",
                    "verified_by": "admin@example.org",
                    "verified_at": "2026-10-19T00:00:00+00:00",
                },
            }
            for index in range(1, 4)
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_cli_imports_tracks_from_a_manifest(tmp_path: Path) -> None:
    repo = _RecordingRepo()
    manifest = _write_manifest(tmp_path)
    exit_code = import_music.run(
        [str(manifest)],
        repository=repo,
        analyzer=_StubAnalyzer(),
    )
    assert exit_code == 0
    assert len(repo.rows) == 3


def test_cli_rejects_bad_manifest_path(tmp_path: Path) -> None:
    exit_code = import_music.run(
        [str(tmp_path / "missing.json")],
        repository=_RecordingRepo(),
        analyzer=_StubAnalyzer(),
    )
    assert exit_code == 2


def test_cli_fails_when_a_track_is_rejected(tmp_path: Path) -> None:
    repo = _RecordingRepo()
    digest = hashlib.sha256(b"x").hexdigest()
    manifest = {
        "environment": "test",
        "tracks": [
            {
                "provider_track_id": "bad",
                "title": "Bad",
                "audio_path": "/tmp/bad.wav",
                "mood_tags": ["ambient"],
                "is_test_track": True,
                "verification": {
                    "source_page_url": "https://example.org/t",
                    "license_archive_sha256": digest,
                    "license_id": "CC-BY-4.0",
                    "license_version": "4.0",
                    "rights_holder": "Test",
                    "composition_rights_status": "cleared",
                    "recording_rights_status": "cleared",
                    "verified_by": "admin@example.org",
                    "verified_at": "2026-10-19T00:00:00+00:00",
                },
            }
        ],
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    exit_code = import_music.run([str(path)], repository=repo, analyzer=_StubAnalyzer())
    assert exit_code == 1
    assert repo.rows == []
