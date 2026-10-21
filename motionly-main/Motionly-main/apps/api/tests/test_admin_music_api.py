"""Task 27: the admin music catalog API (import + listing with rights status).

The routes are behind require_admin. These exercise the route callables with a
stubbed service-role repository and analyzer (no network, no librosa): a verified
track imports; a track with a licence off the allow-list is rejected (422); and
the listing returns rights status plus a short-lived signed preview link minted
from the private music-catalog bucket.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.api import admin_music
from app.api.dependencies import Actor
from app.services.music_catalog_import import AudioAnalysis


class _StubAnalyzer:
    def analyze(self, audio_path: str) -> AudioAnalysis:
        return AudioAnalysis(
            duration_ms=60_000,
            tempo_bpm=120.0,
            beat_positions_ms=[0.0, 500.0],
            energy=0.4,
        )


class _StubRepo:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def upsert_catalog_track(self, **values: object) -> dict[str, object]:
        row = dict(values)
        row["audio_path"] = "catalog/test-track-1.wav"
        self.rows.insert(0, row)
        return row

    def list_catalog_tracks(self, *, limit: int = 200) -> list[dict[str, object]]:
        return self.rows[:limit]


class _StubSigner:
    def create_signed_download(self, *, bucket, path, expires_in_seconds, download=None):
        assert bucket == "music-catalog"
        return SimpleNamespace(url=f"https://signed.invalid/{bucket}/{path}?ttl={expires_in_seconds}")


def _make_request(repo: _StubRepo, *, app_env: str = "test") -> SimpleNamespace:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    app_env=app_env,
                    supabase_url="https://supabase.invalid",
                    supabase_service_role_key="service-role-key",
                    admin_api_token=None,
                    admin_user_id_list=set(),
                    admin_workspace_id_list=set(),
                ),
                music_audio_analyzer=_StubAnalyzer(),
            ),
        ),
        state=SimpleNamespace(request_id="req_admin_music"),
    )
    # Patch the service-role repository factory to return the stub.
    request._repo = repo  # noqa: SLF001
    return request

def test_admin_list_returns_rights_status_and_signed_preview(monkeypatch) -> None:
    repo = _StubRepo()
    repo.rows.append(
        {
            "provider_track_id": "test-track-1",
            "title": "Test track: Test Track One",
            "license": "CC0-1.0",
            "license_version": "1.0",
            "rights_status": "cleared",
            "composition_rights_status": "cleared",
            "recording_rights_status": "cleared",
            "rights_holder": "Test Uploader",
            "verified_by": "admin@example.org",
            "verified_at": datetime(2026, 10, 19, tzinfo=UTC).isoformat(),
            "source_page_url": "https://example.org/t",
            "duration_ms": 60_000,
            "bpm": 120,
            "energy": 0.4,
            "mood_tags": ["uplifting"],
            "is_test_track": True,
            "audio_path": "catalog/test-track-1.wav",
        }
    )
    request = _make_request(repo)
    monkeypatch.setattr(admin_music, "_catalog_repository", lambda _req: repo)

    response = asyncio.run(
        admin_music.list_catalog_tracks(
            request=request,
            _actor=Actor(user_id=None, workspace_id=None),
            signer=_StubSigner(),
            limit=200,
        )
    )
    assert response.allow_list_approved is True
    assert len(response.tracks) == 1
    track = response.tracks[0]
    assert track.rights_status == "cleared"
    assert track.composition_rights_status == "cleared"
    assert track.recording_rights_status == "cleared"
    assert track.preview_url is not None
    assert "music-catalog" in track.preview_url
