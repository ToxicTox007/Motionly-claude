"""Task 27: catalog write repository for the music importer.

Writes verified tracks into ``public.licensed_music_tracks`` with the service
role. The rights evidence columns are persisted so the render rights check and
the admin catalog page can show a track's verified status. Browser roles cannot
write this table (RLS + grants from migration 202608090001 and 2026101913xx).
"""

from __future__ import annotations

from app.api.errors import ApiError
from app.services.supabase import SupabaseRestClient

# Columns the importer sets that map directly onto licensed_music_tracks.
CATALOG_WRITE_COLUMNS = (
    "provider_track_id",
    "title",
    "license",
    "license_version",
    "license_url",
    "license_evidence_hash",
    "source_page_url",
    "rights_holder",
    "rights_status",
    "composition_rights_status",
    "recording_rights_status",
    "verified_by",
    "verified_at",
    "rights_grant_id",
    "rights_grant_version",
    "rights_valid_from",
    "rights_valid_until",
    "permitted_uses",
    "audio_bucket",
    "audio_path",
    "duration_ms",
    "bpm",
    "energy",
    "beat_map",
    "mood_tags",
    "tempo_bpm",
    "beat_positions_ms",
    "is_test_track",
    "active",
)


class SupabaseCatalogTrackRepository:
    def __init__(self, *, supabase_url: str, service_role_key: str) -> None:
        self.client = SupabaseRestClient(
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            repository="music_catalog_import",
        )

    def upsert_catalog_track(self, **values: object) -> dict[str, object]:
        payload = {key: values[key] for key in CATALOG_WRITE_COLUMNS if key in values}
        response = self.client.request(
            "POST",
            "/licensed_music_tracks",
            headers={"prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "provider_track_id", "select": "*"},
            json=payload,
        )
        if not response.is_success:
            raise ApiError(502, "music_catalog_write_failed", "Catalog write failed.")
        rows = response.json()
        return rows[0] if isinstance(rows, list) and rows else payload

    def list_catalog_tracks(self, *, limit: int = 200) -> list[dict[str, object]]:
        """List catalog tracks (service role) for the admin catalog page."""
        response = self.client.request(
            "GET",
            "/licensed_music_tracks",
            params={
                "select": "*",
                "order": "created_at.desc",
                "limit": str(max(1, min(limit, 500))),
            },
        )
        if not response.is_success:
            raise ApiError(502, "music_catalog_read_failed", "Catalog read failed.")
        rows = response.json()
        return rows if isinstance(rows, list) else []
