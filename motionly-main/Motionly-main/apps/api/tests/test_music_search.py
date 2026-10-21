"""Task 28: tests for mood synonym resolution + energy/tempo ranking (N9)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.schemas.plans import EditPlanStyle
from app.services.edit_planner import _style_config
from app.services.music_search import (
    SYNONYM_INDEX,
    rank_tracks,
    resolve_mood_tags,
    score_track,
    style_target,
)


@dataclass
class FakeTrack:
    mood_tags: list[str]
    energy: float | None = None
    bpm: int | None = None
    provider_track_id: str = "t"


def test_free_text_mood_resolves_via_synonyms() -> None:
    # "anthemic momentum" contains no literal catalog tag, yet must resolve to
    # real mood tags through the synonym table.
    tags = resolve_mood_tags("uplifting anthemic momentum")
    assert "uplifting" in tags
    assert "inspiring" in tags or "energetic" in tags


def test_piano_bed_resolves_to_calm_tags() -> None:
    tags = resolve_mood_tags("soft intimate piano bed")
    assert "calm" in tags
    assert "ambient" in tags or "subtle" in tags


def test_unknown_words_contribute_no_tags() -> None:
    assert resolve_mood_tags("zxqw florbington") == []


@pytest.mark.parametrize("style", list(EditPlanStyle))
def test_every_planner_mood_resolves_to_at_least_one_tag(style: EditPlanStyle) -> None:
    mood = str(_style_config(style)["music_mood"])
    assert resolve_mood_tags(mood), f"planner mood {mood!r} resolved to no mood tags"


def test_synonym_index_only_maps_to_known_tags() -> None:
    from app.services.music_catalog_import import MOOD_TAGS

    for word, tags in SYNONYM_INDEX.items():
        for tag in tags:
            assert tag in MOOD_TAGS, f"{word!r} maps to unknown tag {tag!r}"


def test_energy_ranking_prefers_matching_intensity() -> None:
    # Same mood overlap; the track whose energy fits the (energetic) target wins.
    high = FakeTrack(mood_tags=["energetic"], energy=0.85, bpm=128, provider_track_id="high")
    low = FakeTrack(mood_tags=["energetic"], energy=0.2, bpm=70, provider_track_id="low")
    ranked = rank_tracks([low, high], mood="energetic beat sync")
    assert ranked[0].provider_track_id == "high"


def test_tempo_ranking_breaks_ties() -> None:
    target = style_target("energetic beat sync")
    near = score_track(
        track_mood_tags=["energetic"],
        track_energy=target.energy,
        track_bpm=int(round(target.tempo_bpm)),
        target_tags=["energetic"],
        target=target,
    )
    far = score_track(
        track_mood_tags=["energetic"],
        track_energy=target.energy,
        track_bpm=int(round(target.tempo_bpm)) + 60,
        target_tags=["energetic"],
        target=target,
    )
    assert near.total > far.total


def test_mood_overlap_dominates_energy_and_tempo() -> None:
    # A track that shares a mood tag beats a non-matching track even if the
    # latter has a perfect energy/tempo fit.
    on_mood = FakeTrack(mood_tags=["luxury"], energy=0.1, bpm=60, provider_track_id="on")
    off_mood = FakeTrack(mood_tags=["energetic"], energy=0.45, bpm=90, provider_track_id="off")
    ranked = rank_tracks([off_mood, on_mood], mood="uplifting ambient luxury")
    assert ranked[0].provider_track_id == "on"


def test_non_empty_catalog_always_returns_something() -> None:
    # No track shares a tag with the (dark) mood, but ranking must still return
    # the whole pool ordered by energy/tempo fit — never empty.
    pool = [
        FakeTrack(mood_tags=["happy"], energy=0.7, bpm=118, provider_track_id="a"),
        FakeTrack(mood_tags=["clean"], energy=0.5, bpm=100, provider_track_id="b"),
    ]
    ranked = rank_tracks(pool, mood="dark moody suspense")
    assert len(ranked) == len(pool)
    assert {t.provider_track_id for t in ranked} == {"a", "b"}


def test_empty_catalog_returns_empty() -> None:
    assert rank_tracks([], mood="luxury") == []
