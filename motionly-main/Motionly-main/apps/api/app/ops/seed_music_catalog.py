"""Seed the generated 'Test track' music catalog for non-production (Task 32).

    python -m app.ops.seed_music_catalog

Replaces the deleted FixtureMusicProvider: synthesises generated test tracks
(plain-Python sine beds), uploads them to the private ``music-catalog`` bucket, and
imports a verified, mood-covering catalog so the licensed-catalog provider has
real selectable, renderable tracks in local development and the CPU stage.

Refused in production — the generated test tracks must never leak. The CPU stage
/ local startup runs this once after migrations (never in production).
"""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.repositories.music_catalog import SupabaseCatalogTrackRepository
from app.services.local_storage import create_object_storage
from app.services.music_audio import GeneratedTrackAnalyzer
from app.services.music_catalog_import import MusicImportError, _is_production
from app.services.music_seed import seed_test_music_catalog


def run(argv: list[str]) -> int:
    if argv:
        print("usage: python -m app.ops.seed_music_catalog", file=sys.stderr)
        return 2

    settings = get_settings()
    environment = str(settings.app_env)

    if _is_production(environment):
        print(
            "Refusing to seed generated test tracks in production.",
            file=sys.stderr,
        )
        return 1

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
    storage = create_object_storage(
        root=settings.media_work_dir,
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key,
    )

    try:
        result = seed_test_music_catalog(
            repository=repository,
            storage=storage,
            environment=environment,
            analyzer=GeneratedTrackAnalyzer(),
        )
    except MusicImportError as exc:
        print(f"Seed rejected: {exc}", file=sys.stderr)
        return 1

    print(
        f"Seeded {result.imported} generated test track(s): "
        f"{', '.join(result.track_ids)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
