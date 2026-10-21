from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.api.errors import ApiError
from app.schemas.music import MusicProviderKey
from app.services.music_search import rank_tracks, resolve_mood_tags


@dataclass(frozen=True)
class MusicTrackCandidate:
    provider: MusicProviderKey
    provider_track_id: str
    title: str
    license: str
    duration_ms: int
    mood_tags: list[str]
    bpm: int | None = None
    energy: float | None = None
    vocals: bool = False
    loopable: bool = False
    beat_map: list[float] = field(default_factory=list)
    raw_metadata: dict[str, object] = field(default_factory=dict)


def catalog_rights_snapshot(track: object, *, acknowledged: bool) -> dict[str, object]:
    """Return immutable selection evidence derived from current catalog metadata.

    A request boolean can only acknowledge the displayed terms. It cannot turn a
    track into a usable catalog asset: the grant, version, scope, validity, and
    evidence fingerprint must all be supplied by the catalog.
    """
    provider = getattr(track, "provider", None)
    metadata = getattr(track, "raw_metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    rights = metadata.get("rights") if isinstance(metadata.get("rights"), dict) else {}
    provider_track_id = str(getattr(track, "provider_track_id", "") or "")
    now = datetime.now(UTC)
    valid_until = _parse_datetime(rights.get("valid_until"))
    valid_from = _parse_datetime(rights.get("valid_from"))
    permitted_uses = [
        str(item) for item in rights.get("permitted_uses", []) if isinstance(item, str)
    ]
    evidence = str(rights.get("evidence_fingerprint") or "")
    grant_id = str(rights.get("grant_id") or "")
    grant_version = str(rights.get("grant_version") or "")
    status = str(rights.get("status") or "unverified")
    verified = (
        str(provider) == MusicProviderKey.LICENSED_CATALOG.value
        and acknowledged
        and status == "cleared"
        and bool(provider_track_id and grant_id and grant_version and evidence)
        and "editorial_export" in permitted_uses
        and valid_from is not None
        and valid_from <= now
        and valid_until is not None
        and valid_until > now
        and not rights.get("revoked_at")
    )
    resolved_status = (
        "verified"
        if verified
        else (
            "revoked"
            if rights.get("revoked_at") or status == "revoked"
            else "expired"
            if valid_until is not None and valid_until <= now
            else "unverified"
        )
    )
    return {
        "status": resolved_status,
        "verified": verified,
        "provider_track_id": provider_track_id or None,
        "grant_id": grant_id or None,
        "grant_version": grant_version or None,
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "permitted_uses": permitted_uses,
        "evidence_fingerprint": evidence or None,
        "verified_at": now.isoformat() if verified else None,
        "catalog_revision": rights.get("catalog_revision"),
    }


def rights_fingerprint(snapshot: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:32]


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class MusicProvider(Protocol):
    provider_key: MusicProviderKey

    def search(self, *, mood: str, max_results: int = 5) -> list[MusicTrackCandidate]: ...

    def get_track(self, *, provider_track_id: str) -> MusicTrackCandidate | None: ...


@dataclass(frozen=True)
class LicensedCatalogMusicProvider:
    supabase_url: str
    service_role_key: str
    table_name: str = "licensed_music_tracks"
    provider_key: MusicProviderKey = MusicProviderKey.LICENSED_CATALOG

    def __post_init__(self) -> None:
        if not self.table_name.replace("_", "").isalnum():
            raise ValueError("licensed music catalog table name must be alphanumeric/underscore")

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1/{self.table_name}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "authorization": f"Bearer {self.service_role_key}",
            "content-type": "application/json",
        }

    def search(self, *, mood: str, max_results: int = 5) -> list[MusicTrackCandidate]:
        # N9: resolve the free-text mood to canonical mood tags via synonyms and
        # filter in the database on the mood_synonyms array (GIN-indexed overlap,
        # migration 2026101914xx). Fall back to the whole active catalog so a
        # non-empty catalog ALWAYS returns something.
        target_tags = resolve_mood_tags(mood)
        rows: list[dict[str, object]] = []
        if target_tags:
            quoted = ",".join(f'"{tag}"' for tag in target_tags)
            rows = self._fetch_rows(
                params={
                    "active": "eq.true",
                    "mood_synonyms": f"ov.{{{quoted}}}",
                    "limit": "100",
                },
            )
        if not rows:
            rows = self._fetch_rows(params={"active": "eq.true", "limit": "100"})
        candidates = [candidate for row in rows if (candidate := self._candidate_from_row(row))]
        return self._rank(candidates=candidates, mood=mood)[:max_results]

    def get_track(self, *, provider_track_id: str) -> MusicTrackCandidate | None:
        rows = self._fetch_rows(
            params={
                "provider_track_id": f"eq.{provider_track_id}",
                "active": "eq.true",
                "limit": "1",
            },
        )
        return self._candidate_from_row(rows[0]) if rows else None

    def _fetch_rows(self, *, params: dict[str, str]) -> list[dict[str, object]]:
        response = httpx.get(
            self.rest_url,
            headers=self.headers,
            params={"select": "*", **params},
            timeout=10,
        )
        if not response.is_success:
            raise ApiError(
                502,
                "music_catalog_request_failed",
                "Licensed music catalog could not be loaded.",
            )
        rows = response.json()
        return rows if isinstance(rows, list) else []

    def _candidate_from_row(self, row: dict[str, object]) -> MusicTrackCandidate | None:
        rights = self._rights_from_row(row)
        if rights is None:
            return None
        raw_metadata = row.get("raw_metadata") if isinstance(row.get("raw_metadata"), dict) else {}
        audio_url = row.get("audio_url")
        audio_bucket = row.get("audio_bucket") or row.get("storage_bucket")
        audio_path = row.get("audio_path") or row.get("storage_path")
        metadata = dict(raw_metadata)
        metadata["rights"] = rights
        if isinstance(audio_url, str) and audio_url:
            metadata.setdefault("audio_url", audio_url)
        if isinstance(audio_bucket, str) and isinstance(audio_path, str):
            metadata.setdefault("audio_bucket", audio_bucket)
            metadata.setdefault("audio_path", audio_path)
        return MusicTrackCandidate(
            provider=MusicProviderKey.LICENSED_CATALOG,
            provider_track_id=str(row["provider_track_id"]),
            title=str(row["title"]),
            license=str(row["license"]),
            duration_ms=int(row["duration_ms"]),
            mood_tags=self._string_list(row.get("mood_tags")),
            bpm=int(row["bpm"]) if row.get("bpm") is not None else None,
            energy=float(row["energy"]) if row.get("energy") is not None else None,
            vocals=bool(row.get("vocals") or False),
            loopable=bool(row.get("loopable") or False),
            beat_map=[
                float(item)
                for item in (row.get("beat_map") if isinstance(row.get("beat_map"), list) else [])
                if isinstance(item, int | float)
            ],
            raw_metadata=metadata,
        )

    def _rights_from_row(self, row: dict[str, object]) -> dict[str, object] | None:
        """Fail closed: free-form license text never proves export rights."""
        status = str(row.get("rights_status") or "")
        grant_id = str(row.get("rights_grant_id") or "")
        grant_version = str(row.get("rights_grant_version") or "")
        evidence = str(row.get("license_evidence_hash") or "")
        valid_from = _parse_datetime(row.get("rights_valid_from"))
        valid_until = _parse_datetime(row.get("rights_valid_until"))
        permitted_uses = self._string_list(row.get("permitted_uses"))
        if (
            status != "cleared"
            or not grant_id
            or not grant_version
            or not evidence
            or valid_from is None
            or valid_until is None
            or valid_until <= datetime.now(UTC)
            or "editorial_export" not in permitted_uses
            or row.get("rights_revoked_at") is not None
        ):
            return None
        return {
            "status": status,
            "grant_id": grant_id,
            "grant_version": grant_version,
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat(),
            "permitted_uses": permitted_uses,
            "evidence_fingerprint": evidence,
            "catalog_revision": row.get("rights_revision"),
        }

    def _rank(
        self,
        *,
        candidates: list[MusicTrackCandidate],
        mood: str,
    ) -> list[MusicTrackCandidate]:
        # N9: rank by mood-tag overlap (via synonyms) then energy/tempo fit.
        # rank_tracks never drops a candidate, so a non-empty catalog always
        # returns something.
        return rank_tracks(candidates, mood=mood)

    def _string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, str)]
