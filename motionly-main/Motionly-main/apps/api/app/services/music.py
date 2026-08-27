from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.api.errors import ApiError
from app.schemas.music import MusicProviderKey


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


SAMPLE_MUSIC: list[MusicTrackCandidate] = [
    MusicTrackCandidate(
        provider=MusicProviderKey.FIXTURE,
        provider_track_id="fixture-ambient-luxury",
        title="Ambient Luxury Bed",
        license="royalty_free",
        duration_ms=68_000,
        mood_tags=["uplifting", "ambient", "luxury"],
        bpm=80,
        energy=0.35,
        vocals=False,
        loopable=True,
        beat_map=[0.0, 750.0, 1500.0, 2250.0, 3000.0, 3750.0, 4500.0, 5250.0],
        raw_metadata={"source": "fixture"},
    ),
    MusicTrackCandidate(
        provider=MusicProviderKey.FIXTURE,
        provider_track_id="fixture-clean-confident",
        title="Clean Confident Groove",
        license="royalty_free",
        duration_ms=73_000,
        mood_tags=["clean", "confident", "minimal"],
        bpm=92,
        energy=0.55,
        vocals=False,
        loopable=True,
        beat_map=[0.0, 650.0, 1300.0, 1950.0, 2600.0, 3250.0, 3900.0, 4550.0],
        raw_metadata={"source": "fixture"},
    ),
    MusicTrackCandidate(
        provider=MusicProviderKey.FIXTURE,
        provider_track_id="fixture-subtle-conversational",
        title="Subtle Conversational Bed",
        license="royalty_free",
        duration_ms=80_000,
        mood_tags=["subtle", "conversational", "podcast"],
        bpm=72,
        energy=0.2,
        vocals=False,
        loopable=True,
        beat_map=[0.0, 1000.0, 2000.0, 3000.0, 4000.0],
        raw_metadata={"source": "fixture"},
    ),
]


@dataclass(frozen=True)
class FixtureMusicProvider:
    provider_key: MusicProviderKey = MusicProviderKey.FIXTURE

    def search(self, *, mood: str, max_results: int = 5) -> list[MusicTrackCandidate]:
        normalized = (mood or "").strip().lower()
        if not normalized:
            return SAMPLE_MUSIC[:max_results]

        terms = [term for term in normalized.replace("-", " ").split() if term]
        scored: list[tuple[int, MusicTrackCandidate]] = []
        for track in SAMPLE_MUSIC:
            searchable = " ".join([track.title.lower(), *track.mood_tags])
            exact = normalized in searchable
            score = (10 if exact else 0) + sum(1 for term in terms if term in searchable)
            if score > 0:
                scored.append((score, track))
        candidates = [
            track for _score, track in sorted(scored, key=lambda item: item[0], reverse=True)
        ]
        return candidates[:max_results] or SAMPLE_MUSIC[:max_results]

    def get_track(self, *, provider_track_id: str) -> MusicTrackCandidate | None:
        return next(
            (track for track in SAMPLE_MUSIC if track.provider_track_id == provider_track_id),
            None,
        )


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
        normalized = (mood or "").strip().lower()
        terms = [term for term in normalized.replace("-", " ").split() if term]
        scored: list[tuple[int, MusicTrackCandidate]] = []
        for track in candidates:
            searchable = " ".join([track.title.lower(), *[tag.lower() for tag in track.mood_tags]])
            exact = normalized and normalized in searchable
            score = (10 if exact else 0) + sum(1 for term in terms if term in searchable)
            if not terms:
                score = 1
            scored.append((score, track))
        return [
            track
            for score, track in sorted(scored, key=lambda item: item[0], reverse=True)
            if score > 0
        ]

    def _string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, str)]
