"""Task 27: the render rights check accepts an imported, verified catalog track
and refuses a generated test track in production (fixture media disabled)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.errors import ApiError
from app.services.render_jobs import _validate_music_rights


def _verified_rights() -> dict[str, object]:
    return {
        "verified": True,
        "status": "verified",
        "valid_until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "grant_id": "grant-1",
        "grant_version": "1",
    }


def _manifest(selection: dict[str, object]) -> dict:
    return {"render_props": {"musicSelection": selection}}


def test_imported_verified_catalog_track_passes_rights_check() -> None:
    manifest = _manifest(
        {
            "music_track_id": "track-1",
            "track": {
                "provider": "licensed_catalog",
                "title": "Ambient Bed",
                "is_test_track": False,
            },
            "rights": _verified_rights(),
        }
    )
    # Fixture media disabled (production posture): a real verified grant passes.
    _validate_music_rights(manifest, allow_fixture_media=False)


def test_unverified_catalog_track_is_refused() -> None:
    manifest = _manifest(
        {
            "music_track_id": "track-1",
            "track": {"provider": "licensed_catalog", "title": "Ambient Bed"},
            "rights": {"verified": False, "status": "unverified"},
        }
    )
    with pytest.raises(ApiError) as excinfo:
        _validate_music_rights(manifest, allow_fixture_media=False)
    assert excinfo.value.code == "music_rights_unverified"


def test_generated_test_track_refused_in_production() -> None:
    manifest = _manifest(
        {
            "music_track_id": "track-1",
            "track": {
                "provider": "licensed_catalog",
                "title": "Test track: Generated 1",
                "is_test_track": True,
            },
            "rights": _verified_rights(),
        }
    )
    with pytest.raises(ApiError) as excinfo:
        _validate_music_rights(manifest, allow_fixture_media=False)
    assert excinfo.value.code == "music_rights_test_track"


def test_generated_test_track_allowed_when_fixture_media_enabled() -> None:
    manifest = _manifest(
        {
            "music_track_id": "track-1",
            "track": {
                "provider": "licensed_catalog",
                "title": "Test track: Generated 1",
                "is_test_track": True,
            },
            "rights": _verified_rights(),
        }
    )
    # Outside production ALLOW_FIXTURE_PROVIDERS may be true; test tracks render.
    _validate_music_rights(manifest, allow_fixture_media=True)
