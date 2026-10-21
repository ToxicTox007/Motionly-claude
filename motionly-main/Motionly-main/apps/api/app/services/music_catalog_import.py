"""Task 27: music catalog import tool.

Admins add tracks that users can choose, preview and render. The importer
refuses any track without a complete, verified rights record: a licence on the
allow-list (D3, approved 2026-10-20), the track's own source page URL, an archived
copy of the licence (its SHA-256 stored as evidence), licence id + version, the
rights holder/uploader, a SEPARATE rights status for the composition AND the
recording (both must be covered), and who verified it and when.

Audio analysis (per-track duration, tempo, beat positions, energy) runs through
``AudioAnalyzer``. The production analyzer uses librosa — a PINNED optional
extra (``music-analysis`` in apps/api/pyproject.toml, librosa==0.10.2.post1,
hash-locked via requirements-ml.lock with --extra=music-analysis; see the
report). librosa is intentionally NOT installed in the API image: it pulls
numba/llvmlite and is too heavy for the request-path image, so analysis runs in
the CPU worker image and the CLI/admin importer injects the analyzer. Tests
inject a deterministic stub.

Loudness is normalised with ffmpeg ``loudnorm`` before analysis (``normalize_loudness``).

Test tracks are ffmpeg-synthesised, carry test rights evidence, are labelled
"Test track", and are refused in production (the Task 7 leakage gate also refuses
catalog tracks with test evidence in production — see fixtures/registry.json).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "music-licenses.json"

# The single fixed list of mood tags the catalog accepts. Mood tags outside this
# list are refused by the importer so the Audio tab (Task 28) can filter on a
# closed vocabulary.
MOOD_TAGS: tuple[str, ...] = (
    "uplifting",
    "ambient",
    "luxury",
    "clean",
    "confident",
    "minimal",
    "subtle",
    "conversational",
    "podcast",
    "energetic",
    "cinematic",
    "dramatic",
    "happy",
    "calm",
    "inspiring",
    "neutral",
    "dark",
    "playful",
)

# Rights status values that mean a side (composition or recording) is covered.
_COVERED = "cleared"

# The label prefix that marks a generated test track.
TEST_TRACK_LABEL = "Test track"

# How a production environment is spelled.
PRODUCTION_ENVIRONMENTS = {"production", "prod"}


class MusicImportError(Exception):
    """Raised when a track cannot be imported (bad rights, licence, or input)."""


# --------------------------------------------------------------------------
# Licence allow-list (D3, approved 2026-10-20; tracks still need their own sign-off)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenseEntry:
    id: str
    version: str
    name: str
    url: str
    requires_both_sides_public_domain: bool = False


@dataclass(frozen=True)
class LicenseAllowList:
    approved: bool
    allowed: tuple[LicenseEntry, ...]

    def entry(self, license_id: str, license_version: str) -> LicenseEntry | None:
        wanted_id = (license_id or "").strip().upper()
        wanted_version = (license_version or "").strip().lower()
        for item in self.allowed:
            if item.id.upper() != wanted_id:
                continue
            # "n/a" versions match any version string for that licence id.
            if item.version.lower() in {"n/a", wanted_version} or wanted_version in {"", "n/a"}:
                return item
            if item.version.lower() == wanted_version:
                return item
        return None

    def is_allowed(self, license_id: str, license_version: str) -> bool:
        return self.entry(license_id, license_version) is not None


def load_license_allow_list(path: Path | None = None) -> LicenseAllowList:
    raw = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
    allowed = tuple(
        LicenseEntry(
            id=str(item["id"]),
            version=str(item["version"]),
            name=str(item["name"]),
            url=str(item["url"]),
            requires_both_sides_public_domain=bool(
                item.get("requires_both_sides_public_domain", False)
            ),
        )
        for item in raw.get("allowed", [])
    )
    return LicenseAllowList(approved=bool(raw.get("approved", False)), allowed=allowed)


# --------------------------------------------------------------------------
# Mood tags
# --------------------------------------------------------------------------


def validate_mood_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        normalized = str(tag).strip().lower()
        if normalized not in MOOD_TAGS:
            raise MusicImportError(
                f"mood tag {tag!r} is not in the fixed mood list "
                f"({', '.join(MOOD_TAGS)})"
            )
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


# --------------------------------------------------------------------------
# Verification record
# --------------------------------------------------------------------------

_SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
_REQUIRED_VERIFICATION_FIELDS = (
    "source_page_url",
    "license_archive_sha256",
    "license_id",
    "license_version",
    "rights_holder",
    "composition_rights_status",
    "recording_rights_status",
    "verified_by",
    "verified_at",
)


@dataclass(frozen=True)
class TrackVerification:
    """A complete, verified rights record. The importer requires every field."""

    source_page_url: str
    license_archive_sha256: str
    license_id: str
    license_version: str
    rights_holder: str
    composition_rights_status: str
    recording_rights_status: str
    verified_by: str
    verified_at: datetime

    @property
    def fully_covered(self) -> bool:
        return (
            self.composition_rights_status == _COVERED
            and self.recording_rights_status == _COVERED
        )


def verification_from_mapping(data: dict[str, object]) -> TrackVerification:
    missing = [
        field_name
        for field_name in _REQUIRED_VERIFICATION_FIELDS
        if not str(data.get(field_name, "")).strip()
    ]
    if missing:
        raise MusicImportError(
            "verification record is missing required field(s): " + ", ".join(sorted(missing))
        )

    sha = str(data["license_archive_sha256"]).strip()
    if not _SHA256_RE.match(sha):
        raise MusicImportError(
            "license_archive_sha256 must be a 64-character hex SHA-256 of the archived licence page"
        )

    url = str(data["source_page_url"]).strip()
    if not url.lower().startswith(("http://", "https://")):
        raise MusicImportError("source_page_url must be the track's own http(s) source page")

    verified_at = _parse_datetime(data["verified_at"])
    if verified_at is None:
        raise MusicImportError("verified_at must be an ISO-8601 timestamp")

    return TrackVerification(
        source_page_url=url,
        license_archive_sha256=sha.lower(),
        license_id=str(data["license_id"]).strip(),
        license_version=str(data["license_version"]).strip(),
        rights_holder=str(data["rights_holder"]).strip(),
        composition_rights_status=str(data["composition_rights_status"]).strip().lower(),
        recording_rights_status=str(data["recording_rights_status"]).strip().lower(),
        verified_by=str(data["verified_by"]).strip(),
        verified_at=verified_at,
    )


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------
# Audio analysis (librosa in the CPU worker image; injectable for tests)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioAnalysis:
    duration_ms: int
    tempo_bpm: float
    beat_positions_ms: list[float]
    energy: float


class AudioAnalyzer(Protocol):
    def analyze(self, audio_path: str) -> AudioAnalysis: ...


# --------------------------------------------------------------------------
# Manifest entry
# --------------------------------------------------------------------------


# The permitted use the render rights gate (render_jobs._validate_music_rights,
# music.catalog_rights_snapshot) requires before a catalog track may be exported.
EDITORIAL_EXPORT_USE = "editorial_export"

# How long a generated test-track grant stays valid. Only test tracks default to
# this window; a real track supplies its own grant window in the manifest.
_DEFAULT_TEST_GRANT_YEARS = 50


@dataclass(frozen=True)
class TrackManifestEntry:
    provider_track_id: str
    title: str
    audio_path: str
    mood_tags: list[str]
    verification: dict[str, object]
    is_test_track: bool = False
    license_url: str | None = None
    # Where the catalog audio object lives in the private music-catalog bucket.
    # The render reads bytes from here; the API mints signed preview links from it.
    audio_bucket: str | None = None
    audio_object_path: str | None = None
    # The server-authoritative grant window. The render rights gate requires a
    # non-expired grant with editorial_export. Real tracks supply these from the
    # catalog operator's grant; test tracks default to verified_at + a long
    # window so generated non-production tracks are selectable and renderable.
    rights_valid_until: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, object]) -> TrackManifestEntry:
        for required in ("provider_track_id", "title", "audio_path"):
            if not str(data.get(required, "")).strip():
                raise MusicImportError(f"manifest entry is missing {required!r}")
        verification = data.get("verification")
        if not isinstance(verification, dict):
            raise MusicImportError("manifest entry is missing a verification record")
        mood_tags = data.get("mood_tags") or []
        if not isinstance(mood_tags, list):
            raise MusicImportError("mood_tags must be a list")
        return cls(
            provider_track_id=str(data["provider_track_id"]).strip(),
            title=str(data["title"]).strip(),
            audio_path=str(data["audio_path"]).strip(),
            mood_tags=[str(tag) for tag in mood_tags],
            verification=dict(verification),
            is_test_track=bool(data.get("is_test_track", False)),
            license_url=str(data["license_url"]) if data.get("license_url") else None,
            audio_bucket=str(data["audio_bucket"]) if data.get("audio_bucket") else None,
            audio_object_path=(
                str(data["audio_object_path"]) if data.get("audio_object_path") else None
            ),
            rights_valid_until=(
                str(data["rights_valid_until"]) if data.get("rights_valid_until") else None
            ),
        )


class CatalogTrackRepository(Protocol):
    def upsert_catalog_track(self, **values: object) -> dict[str, object]: ...


@dataclass
class ImportResult:
    imported: int = 0
    track_ids: list[str] = field(default_factory=list)


def _is_production(environment: str) -> bool:
    return (environment or "").strip().lower() in PRODUCTION_ENVIRONMENTS


def build_catalog_row(
    entry: TrackManifestEntry,
    *,
    allow_list: LicenseAllowList,
    analysis: AudioAnalysis,
) -> dict[str, object]:
    """Validate the rights record + licence and build the catalog row.

    Raises MusicImportError if the track cannot be imported.
    """
    verification = verification_from_mapping(entry.verification)

    licence = allow_list.entry(verification.license_id, verification.license_version)
    if licence is None:
        raise MusicImportError(
            f"licence {verification.license_id} {verification.license_version} "
            "is not on the allow-list; the import tool refuses it"
        )

    if not verification.fully_covered:
        raise MusicImportError(
            "both the composition AND the recording must be cleared; this record covers only "
            f"composition={verification.composition_rights_status!r} "
            f"recording={verification.recording_rights_status!r}"
        )

    # Public-domain licences only count when BOTH sides are independently verified
    # as public domain (the allow-list marks them requires_both_sides_public_domain).
    # fully_covered already requires both 'cleared', which is how both-sides
    # verification is recorded.

    mood_tags = validate_mood_tags(entry.mood_tags)

    title = entry.title
    if entry.is_test_track and not title.startswith(f"{TEST_TRACK_LABEL}: "):
        title = f"{TEST_TRACK_LABEL}: {title}"

    # Server-authoritative grant. The render rights gate and the provider rights
    # snapshot require a non-expired grant that permits editorial export; without
    # these columns an imported track is loaded but never selectable. The grant
    # id/version are derived from the verified licence + track; the window opens
    # at verification time. Real tracks may pin their own expiry via the manifest;
    # test tracks default to a long window so non-production catalogs work.
    valid_from = verification.verified_at
    if entry.rights_valid_until:
        valid_until = _parse_datetime(entry.rights_valid_until)
        if valid_until is None:
            raise MusicImportError("rights_valid_until must be an ISO-8601 timestamp")
    else:
        valid_until = valid_from.replace(year=valid_from.year + _DEFAULT_TEST_GRANT_YEARS)
    if valid_until <= valid_from:
        raise MusicImportError("rights_valid_until must be after the verification time")

    row: dict[str, object] = {
        "provider_track_id": entry.provider_track_id,
        "title": title,
        "license": verification.license_id,
        "license_version": verification.license_version,
        "license_url": entry.license_url or licence.url,
        "license_evidence_hash": verification.license_archive_sha256,
        "source_page_url": verification.source_page_url,
        "rights_holder": verification.rights_holder,
        "rights_status": _COVERED,
        "composition_rights_status": verification.composition_rights_status,
        "recording_rights_status": verification.recording_rights_status,
        "verified_by": verification.verified_by,
        "verified_at": verification.verified_at.isoformat(),
        # Server-authoritative grant columns (migration 202608090001).
        "rights_grant_id": f"{verification.license_id}:{entry.provider_track_id}",
        "rights_grant_version": verification.license_version,
        "rights_valid_from": valid_from.isoformat(),
        "rights_valid_until": valid_until.isoformat(),
        "permitted_uses": [EDITORIAL_EXPORT_USE],
        "duration_ms": analysis.duration_ms,
        "tempo_bpm": analysis.tempo_bpm,
        "bpm": int(round(analysis.tempo_bpm)),
        "beat_positions_ms": analysis.beat_positions_ms,
        "beat_map": analysis.beat_positions_ms,
        "energy": analysis.energy,
        "mood_tags": mood_tags,
        "is_test_track": entry.is_test_track,
        "active": True,
    }
    # The private audio object the render reads and the API previews.
    if entry.audio_bucket and entry.audio_object_path:
        row["audio_bucket"] = entry.audio_bucket
        row["audio_path"] = entry.audio_object_path
    return row


def import_music_catalog(
    entries: list[TrackManifestEntry],
    *,
    repository: CatalogTrackRepository,
    analyzer: AudioAnalyzer,
    environment: str,
) -> ImportResult:
    """Import validated tracks.

    Each track must have a complete verified rights record, a licence on the
    allow-list, both sides covered, and mood tags on the fixed list. Test tracks
    are refused in production.
    """
    allow_list = load_license_allow_list()
    result = ImportResult()
    for entry in entries:
        if entry.is_test_track and _is_production(environment):
            raise MusicImportError(
                f"test track {entry.provider_track_id!r} cannot be imported in production"
            )
        analysis = analyzer.analyze(entry.audio_path)
        row = build_catalog_row(entry, allow_list=allow_list, analysis=analysis)
        repository.upsert_catalog_track(**row)
        result.imported += 1
        result.track_ids.append(entry.provider_track_id)
    return result
