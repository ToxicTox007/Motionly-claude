"""Task 28: starter-set tooling for the music catalog (D3).

D3 forbids importing any REAL track until the user signs off. This module is the
tooling the user needs to prepare that sign-off:

* :func:`required_manifest_fields` — the complete record every CANDIDATE track
  must carry (the same verification fields the importer enforces, plus the
  candidate-tracking fields the user fills while verifying each track against
  its own source page).
* :func:`validate_candidate_manifest` — structural validation of a candidate
  manifest BEFORE any import. It does NOT import; it checks that every entry has
  a complete record and reports which planner moods are still uncovered.
* :func:`mood_coverage` — which planner mood tags the candidate set covers and
  which are missing, so the starter set fits EVERY plan.
* :func:`build_signoff_checklist` — a per-track Markdown checklist the user ticks
  off (source page verified, licence archived, both sides cleared, ...).

Nothing here imports audio or contacts any provider. Until sign-off, running
code and tests use generated test tracks (``app.services.music_audio``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.music_catalog_import import (
    _REQUIRED_VERIFICATION_FIELDS,
    MOOD_TAGS,
    validate_mood_tags,
)
from app.services.music_search import planner_mood_tags

# The minimum number of candidate tracks the starter set must gather so the
# catalog has real breadth across moods before the user signs off.
MINIMUM_CANDIDATES = 30

# Top-level fields each candidate manifest entry must carry (mirrors
# TrackManifestEntry plus the candidate-tracking fields the user fills).
CANDIDATE_TOP_LEVEL_FIELDS: tuple[str, ...] = (
    "provider_track_id",
    "title",
    "audio_path",
    "mood_tags",
    "verification",
)


def required_manifest_fields() -> dict[str, tuple[str, ...]]:
    """Return the complete record a candidate track must carry."""
    return {
        "track": CANDIDATE_TOP_LEVEL_FIELDS,
        "verification": _REQUIRED_VERIFICATION_FIELDS,
    }


@dataclass
class ManifestValidation:
    entry_count: int = 0
    errors: list[str] = field(default_factory=list)
    covered_moods: set[str] = field(default_factory=set)
    missing_moods: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing_moods

    @property
    def meets_minimum(self) -> bool:
        return self.entry_count >= MINIMUM_CANDIDATES


def _validate_entry(index: int, entry: object) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    moods: set[str] = set()
    if not isinstance(entry, dict):
        return [f"track[{index}] is not an object"], moods
    for required in CANDIDATE_TOP_LEVEL_FIELDS:
        if required == "mood_tags":
            continue
        if not str(entry.get(required, "")).strip():
            errors.append(f"track[{index}] is missing {required!r}")
    tags = entry.get("mood_tags")
    if not isinstance(tags, list) or not tags:
        errors.append(f"track[{index}] must list at least one mood tag")
    else:
        try:
            cleaned = validate_mood_tags([str(tag) for tag in tags])
            moods.update(cleaned)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as an error
            errors.append(f"track[{index}] mood tags: {exc}")
    verification = entry.get("verification")
    if not isinstance(verification, dict):
        errors.append(f"track[{index}] is missing a verification record")
    else:
        for field_name in _REQUIRED_VERIFICATION_FIELDS:
            if not str(verification.get(field_name, "")).strip():
                errors.append(
                    f"track[{index}] verification is missing {field_name!r}"
                )
    return errors, moods


def mood_coverage(covered: set[str]) -> tuple[set[str], set[str]]:
    """Return (covered-for-planner, missing-for-planner) mood tags."""
    required = planner_mood_tags()
    return (covered & required), (required - covered)


def validate_candidate_manifest(manifest: dict) -> ManifestValidation:
    """Structurally validate a candidate manifest and report mood coverage.

    This never imports anything. It verifies each entry has a complete record
    and that the set of candidates covers every mood the planner can produce.
    """
    result = ManifestValidation()
    tracks = manifest.get("tracks")
    if not isinstance(tracks, list):
        result.errors.append("manifest must contain a 'tracks' list")
        tracks = []
    result.entry_count = len(tracks)
    covered: set[str] = set()
    for index, entry in enumerate(tracks):
        errors, moods = _validate_entry(index, entry)
        result.errors.extend(errors)
        covered.update(moods)
    result.covered_moods, result.missing_moods = mood_coverage(covered)
    return result


def _empty_verification() -> dict[str, str]:
    return {field_name: "" for field_name in _REQUIRED_VERIFICATION_FIELDS}


def empty_manifest_template(count: int = MINIMUM_CANDIDATES) -> dict:
    """Build an EMPTY candidate manifest template with ``count`` blank slots.

    Every field is present but blank; the user fills each one while verifying
    the track against its own source page. ``is_test_track`` stays false — these
    are candidate REAL tracks pending sign-off, never imported until then.
    """
    return {
        "environment": "local",
        "_note": (
            "Candidate tracks pending the user's D3 sign-off. Fill every field "
            "from the track's own source page; do NOT import until each row is "
            "signed off (see music-starter-signoff.md). Each mood_tag must be "
            f"one of: {', '.join(MOOD_TAGS)}."
        ),
        "tracks": [
            {
                "provider_track_id": "",
                "title": "",
                "audio_path": "",
                "mood_tags": [],
                "is_test_track": False,
                "verification": _empty_verification(),
            }
            for _ in range(max(1, count))
        ],
    }


def build_signoff_checklist() -> str:
    """Render the per-track sign-off checklist (Markdown) for the user.

    Covers every planner mood so the user can confirm the starter set fits every
    plan, and lists the per-track checks the importer will otherwise enforce.
    """
    required = sorted(planner_mood_tags())
    lines: list[str] = [
        "# Music starter-set sign-off checklist (D3)",
        "",
        "The import tool refuses any track without ALL of the checks below. This",
        "checklist mirrors those checks so you can verify each candidate against",
        "its own source page before signing off. **Import no real track until it",
        "is signed off here.** Until then, code and tests use generated test",
        "tracks only.",
        "",
        f"Gather at least **{MINIMUM_CANDIDATES}** candidate tracks.",
        "",
        "## Per-track checks (repeat for every candidate)",
        "",
        "- [ ] `provider_track_id` is stable and unique",
        "- [ ] `title` matches the source",
        "- [ ] `audio_path` points at the uploaded private-bucket object",
        "- [ ] at least one `mood_tag` from the fixed vocabulary",
        "- [ ] `source_page_url` is the track's own http(s) page",
        "- [ ] `license_id` + `license_version` are on the allow-list "
        "(CC0-1.0 or PD only)",
        "- [ ] archived the licence page; `license_archive_sha256` is its SHA-256",
        "- [ ] `rights_holder` recorded",
        "- [ ] `composition_rights_status` = cleared (verified independently)",
        "- [ ] `recording_rights_status` = cleared (verified independently)",
        "- [ ] `verified_by` + `verified_at` recorded",
        "",
        "## Mood coverage (every planner mood must have a fitting track)",
        "",
        "Tick each mood once at least one signed-off candidate carries it:",
        "",
    ]
    lines.extend(f"- [ ] `{tag}`" for tag in required)
    lines.append("")
    lines.append("## Final sign-off")
    lines.append("")
    lines.append("- [ ] Every row above is checked")
    lines.append("- [ ] Mood coverage is complete")
    lines.append(
        "- [ ] Every track in this set is individually signed off; only these "
        "tracks may be imported (the licence policy itself was approved at D3 on "
        "2026-10-20)"
    )
    lines.append("")
    return "\n".join(lines)
