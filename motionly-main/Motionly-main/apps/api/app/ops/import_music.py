"""Import music catalog tracks from a manifest (Task 27).

    python -m app.ops.import_music <manifest.json>

The manifest is a JSON object with an optional ``environment`` (defaults to the
API's APP_ENV) and a ``tracks`` list. Each track carries its audio path, mood
tags, a test-track flag, and a complete verification record (source page URL,
archived-licence SHA-256, licence id + version, rights holder, SEPARATE
composition and recording rights status, verifier + timestamp).

The importer refuses any track without a complete verified rights record, a
licence on the allow-list, both sides covered, and mood tags on the fixed list.
Generated test tracks are refused in production.

Audio analysis uses librosa (``LibrosaAudioAnalyzer``), which lives in the CPU
worker image — run this command there, or in a context where librosa is
installed. Loudness is normalised with ffmpeg ``loudnorm`` beforehand when a
manifest marks a track ``normalize_loudness`` (handled by the caller/worker).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.repositories.music_catalog import SupabaseCatalogTrackRepository
from app.services.music_audio import LibrosaAudioAnalyzer
from app.services.music_catalog_import import (
    AudioAnalyzer,
    CatalogTrackRepository,
    MusicImportError,
    TrackManifestEntry,
    import_music_catalog,
)


def run(
    argv: list[str],
    *,
    repository: CatalogTrackRepository | None = None,
    analyzer: AudioAnalyzer | None = None,
) -> int:
    if len(argv) != 1:
        print("usage: python -m app.ops.import_music <manifest.json>", file=sys.stderr)
        return 2

    manifest_path = Path(argv[0])
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read manifest {manifest_path}: {exc}", file=sys.stderr)
        return 2

    settings = get_settings()
    environment = str(manifest.get("environment") or settings.app_env)

    try:
        entries = [
            TrackManifestEntry.from_mapping(item) for item in manifest.get("tracks", [])
        ]
    except MusicImportError as exc:
        print(f"Invalid manifest: {exc}", file=sys.stderr)
        return 1

    if repository is None:
        if not (settings.supabase_url and settings.supabase_service_role_key):
            print(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured.",
                file=sys.stderr,
            )
            return 2
        repository = SupabaseCatalogTrackRepository(
            supabase_url=str(settings.supabase_url),
            service_role_key=settings.supabase_service_role_key,
        )
    analyzer = analyzer or LibrosaAudioAnalyzer()

    try:
        result = import_music_catalog(
            entries,
            repository=repository,
            analyzer=analyzer,
            environment=environment,
        )
    except MusicImportError as exc:
        print(f"Import rejected: {exc}", file=sys.stderr)
        return 1

    print(f"Imported {result.imported} track(s): {', '.join(result.track_ids)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
