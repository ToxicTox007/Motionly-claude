"""Task 32 follow-up: seed the GENERATED 'Test track' music catalog (non-prod).

FixtureMusicProvider has been removed. Non-production environments and the CPU
stage now serve music from the real licensed catalog provider seeded with
GENERATED test tracks instead of a bespoke in-memory fixture. Each seeded track:

* is synthesised in plain Python (``synthesize_test_track``) — a deterministic
  sine-wave bed, not a real recording (D3: no real track imported);
* is uploaded to the private ``music-catalog`` bucket, the same object the render
  reads and the API mints preview links from;
* carries a COMPLETE test rights evidence record and a verified editorial-export
  grant, so the catalog provider treats it as selectable and the render rights
  gate accepts it (only when fixture media is enabled — outside production);
* is labelled ``Test track: ...`` and marked ``is_test_track=true``, so the
  importer and the render rights gate REFUSE it in production, and the fixture
  leakage gate keeps it out of production deployments.

The seed covers every mood the edit planner can emit (``planner_mood_tags``) so
every plan gets a fitting track. The seed itself is refused in production.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.services.music_audio import synthesize_test_track
from app.services.music_catalog_import import (
    AudioAnalyzer,
    CatalogTrackRepository,
    ImportResult,
    MusicImportError,
    TrackManifestEntry,
    _is_production,
    import_music_catalog,
    validate_mood_tags,
)
from app.services.music_search import planner_mood_tags

# The private bucket the render reads music audio from and the API previews.
MUSIC_CATALOG_BUCKET = "music-catalog"

# Deterministic test rights evidence. A CC0-1.0 placeholder licence page archived
# for the generated catalog; the SHA-256 is of this fixed text. This is TEST
# evidence (is_test_track=true), refused in production by the importer, the
# render rights gate, and the fixture leakage gate.
_TEST_LICENSE_TEXT = "Generated test track — CC0 1.0 Universal (synthetic, non-production)."
_TEST_LICENSE_SHA256 = hashlib.sha256(_TEST_LICENSE_TEXT.encode()).hexdigest()
_TEST_VERIFIED_AT = "2026-01-01T00:00:00+00:00"


class _StorageUploader(Protocol):
    def upload_file(
        self, *, bucket: str, path: str, source_path, content_type: str = ...
    ) -> object: ...


def _mood_tags_to_cover() -> list[str]:
    """Return the mood tags a seeded catalog must cover (every planner mood).

    Ordered deterministically so the generated ids are stable across runs.
    """
    return sorted(planner_mood_tags())


def _frequency_for(mood: str) -> int:
    """Pick a stable, audible sine frequency per mood (220–660 Hz)."""
    digest = int(hashlib.sha256(mood.encode()).hexdigest(), 16)
    return 220 + (digest % 441)


def _verification() -> dict[str, object]:
    return {
        "source_page_url": "https://music-catalog.motionly.invalid/test-tracks",
        "license_archive_sha256": _TEST_LICENSE_SHA256,
        "license_id": "CC0-1.0",
        "license_version": "1.0",
        "rights_holder": "Motionly generated test catalog",
        "composition_rights_status": "cleared",
        "recording_rights_status": "cleared",
        "verified_by": "music-catalog-seed",
        "verified_at": _TEST_VERIFIED_AT,
    }


def generate_test_track_entries() -> list[TrackManifestEntry]:
    """Build one generated test-track manifest entry per planner mood tag.

    Each entry covers one canonical mood (and any extra mood tags that resolve to
    it), so the whole set covers every mood the planner can emit. All entries are
    test tracks with a complete verification record and an audio object in the
    private bucket.
    """
    entries: list[TrackManifestEntry] = []
    for mood in _mood_tags_to_cover():
        # Keep the mood tag list to the single canonical tag (validated against
        # the fixed vocabulary) so coverage is explicit and auditable.
        mood_tags = validate_mood_tags([mood])
        track_id = f"test-track-{mood}"
        entries.append(
            TrackManifestEntry(
                provider_track_id=track_id,
                title=f"Generated {mood} bed",
                audio_path=f"{track_id}.wav",
                mood_tags=mood_tags,
                verification=_verification(),
                is_test_track=True,
                audio_bucket=MUSIC_CATALOG_BUCKET,
                audio_object_path=f"test-tracks/{track_id}.wav",
            )
        )
    return entries


def seed_test_music_catalog(
    *,
    repository: CatalogTrackRepository,
    storage: _StorageUploader,
    environment: str,
    analyzer: AudioAnalyzer,
    audio_dir: str | Path | None = None,
    synthesize: Callable[..., str] = synthesize_test_track,
    duration_s: int = 30,
) -> ImportResult:
    """Synthesise generated test tracks, upload their audio, and import them.

    Refused in production: generated test tracks must never leak. Outside
    production, this makes the licensed-catalog provider serve a full,
    mood-covering set of selectable, renderable test tracks — the replacement
    for the deleted FixtureMusicProvider.
    """
    if _is_production(environment):
        raise MusicImportError(
            "the generated test-track catalog cannot be seeded in production"
        )

    entries = generate_test_track_entries()
    work_dir = Path(audio_dir) if audio_dir is not None else Path.cwd()
    work_dir.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        assert entry.audio_bucket and entry.audio_object_path  # built above
        local_path = work_dir / Path(entry.audio_object_path).name
        synthesize(
            frequency=_frequency_for(entry.provider_track_id),
            duration_s=duration_s,
            output_path=str(local_path),
        )
        storage.upload_file(
            bucket=entry.audio_bucket,
            path=entry.audio_object_path,
            source_path=str(local_path),
            content_type="audio/x-wav",
        )

    return import_music_catalog(
        entries,
        repository=repository,
        analyzer=analyzer,
        environment=environment,
    )
