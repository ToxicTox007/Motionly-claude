"""Task 28: mood synonym resolution + energy/tempo ranking for music search.

The edit planner emits FREE-TEXT moods ("uplifting anthemic momentum", "soft
intimate piano bed", "cinematic atmospheric build", ...). The catalog, however,
tags every track with a FIXED closed vocabulary (``MOOD_TAGS`` in
``music_catalog_import``). N9 bridges the two: each free-text word maps, via a
synonym table, to one or more canonical mood tags. The same mapping drives both

* the database filter (the synonym array lives on each catalog row + a GIN
  index — see migration 2026101914xx), and
* the in-process fallback ranking used by the licensed-catalog provider and the
  tests, so free-text moods always resolve to real mood tags.

Ranking combines three signals, highest first:

1. mood-tag overlap (how many of the resolved target tags the track carries),
2. energy fit (how close the track's energy is to the style's target energy),
3. tempo fit (how close the track's bpm is to the style's target tempo).

A non-empty catalog ALWAYS returns something: when nothing matches the mood we
fall back to ranking the whole catalog by energy/tempo fit alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.music_catalog_import import MOOD_TAGS

# --------------------------------------------------------------------------
# Synonym table: free-text word -> canonical mood tag(s).
# --------------------------------------------------------------------------
# Every canonical MOOD_TAG maps to itself implicitly (added below). The extra
# entries here are the free-text vocabulary the planner and users actually type.
# Keys are single lowercase words; the resolver tokenises the phrase first.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    # uplifting / motivational family
    "anthemic": ("uplifting", "inspiring"),
    "momentum": ("uplifting", "energetic"),
    "motivational": ("uplifting", "inspiring"),
    "inspirational": ("inspiring", "uplifting"),
    "triumphant": ("uplifting", "inspiring"),
    "hopeful": ("inspiring", "uplifting"),
    "positive": ("happy", "uplifting"),
    "bright": ("happy", "uplifting"),
    "feelgood": ("happy", "uplifting"),
    "pop": ("happy", "playful"),
    "creator": ("playful", "happy"),
    # emotional / calm family
    "soft": ("calm", "subtle"),
    "intimate": ("calm", "subtle"),
    "piano": ("calm", "cinematic"),
    "bed": ("ambient", "subtle"),
    "emotional": ("calm", "cinematic"),
    "gentle": ("calm", "subtle"),
    "warm": ("calm", "inspiring"),
    "narrative": ("cinematic", "calm"),
    "arc": ("cinematic", "inspiring"),
    "story": ("cinematic", "calm"),
    "storytelling": ("cinematic", "calm"),
    "reflective": ("calm", "ambient"),
    # cinematic / dramatic family
    "cinematic": ("cinematic",),
    "atmospheric": ("ambient", "cinematic"),
    "build": ("cinematic", "dramatic"),
    "epic": ("cinematic", "dramatic"),
    "score": ("cinematic",),
    "tense": ("dark", "dramatic"),
    "impact": ("dramatic", "energetic"),
    "dramatic": ("dramatic",),
    "dark": ("dark",),
    "moody": ("dark", "cinematic"),
    "suspense": ("dark", "dramatic"),
    "editorial": ("cinematic", "dramatic"),
    "contrast": ("dramatic", "cinematic"),
    "fashion": ("cinematic", "confident"),
    # energetic / fast family
    "energetic": ("energetic",),
    "beat": ("energetic", "playful"),
    "sync": ("energetic",),
    "fast": ("energetic",),
    "upbeat": ("energetic", "happy"),
    "driving": ("energetic", "confident"),
    "punchy": ("energetic", "confident"),
    "travel": ("energetic", "uplifting"),
    "adventure": ("energetic", "inspiring"),
    # clean / minimal / corporate family
    "clean": ("clean", "minimal"),
    "minimal": ("minimal", "clean"),
    "corporate": ("clean", "confident"),
    "confident": ("confident",),
    "crisp": ("clean", "confident"),
    "light": ("clean", "subtle"),
    "educational": ("clean", "neutral"),
    "teaching": ("clean", "neutral"),
    "neutral": ("neutral",),
    "modern": ("clean", "minimal"),
    "sleek": ("minimal", "luxury"),
    # luxury family
    "luxury": ("luxury",),
    "luxurious": ("luxury",),
    "premium": ("luxury", "confident"),
    "elegant": ("luxury", "calm"),
    "sophisticated": ("luxury", "confident"),
    # ambient / subtle / podcast family
    "ambient": ("ambient",),
    "subtle": ("subtle",),
    "background": ("ambient", "subtle"),
    "underscore": ("subtle", "ambient"),
    "conversational": ("conversational", "podcast"),
    "podcast": ("podcast", "conversational"),
    "talk": ("podcast", "conversational"),
    "calm": ("calm",),
    "chill": ("calm", "ambient"),
    "chilled": ("calm", "ambient"),
    "relaxed": ("calm", "ambient"),
    "mellow": ("calm", "subtle"),
    "focused": ("minimal", "clean"),
    # playful / happy family
    "happy": ("happy",),
    "playful": ("playful",),
    "fun": ("playful", "happy"),
    "quirky": ("playful",),
    "inspiring": ("inspiring",),
    "uplifting": ("uplifting",),
}


def _build_synonym_index() -> dict[str, tuple[str, ...]]:
    index: dict[str, set[str]] = {}
    # Every canonical tag resolves to itself.
    for tag in MOOD_TAGS:
        index.setdefault(tag, set()).add(tag)
    for word, tags in _SYNONYMS.items():
        bucket = index.setdefault(word, set())
        for tag in tags:
            if tag not in MOOD_TAGS:
                raise ValueError(f"synonym {word!r} maps to unknown mood tag {tag!r}")
            bucket.add(tag)
    return {word: tuple(sorted(tags)) for word, tags in index.items()}


SYNONYM_INDEX: dict[str, tuple[str, ...]] = _build_synonym_index()


def _tokenise(mood: str) -> list[str]:
    normalized = (mood or "").strip().lower().replace("-", " ").replace("/", " ")
    return [token for token in normalized.split() if token]


def resolve_mood_tags(mood: str) -> list[str]:
    """Resolve a free-text mood phrase to canonical mood tags via synonyms.

    Preserves first-seen order so the most relevant tags rank first. Returns an
    empty list only when nothing in the phrase maps to a known tag.
    """
    resolved: list[str] = []
    for token in _tokenise(mood):
        for tag in SYNONYM_INDEX.get(token, ()):  # unknown words contribute nothing
            if tag not in resolved:
                resolved.append(tag)
    return resolved


# --------------------------------------------------------------------------
# Style energy/tempo targets. The planner mood phrase implies an intensity; we
# derive target energy (0..1) and tempo (bpm) from the resolved tags so ranking
# favours a track whose feel fits the edit.
# --------------------------------------------------------------------------
# Per-tag (target_energy, target_bpm). Averaged over the resolved tags.
_TAG_PROFILE: dict[str, tuple[float, float]] = {
    "uplifting": (0.7, 120.0),
    "ambient": (0.3, 80.0),
    "luxury": (0.45, 90.0),
    "clean": (0.5, 100.0),
    "confident": (0.6, 100.0),
    "minimal": (0.4, 95.0),
    "subtle": (0.25, 80.0),
    "conversational": (0.2, 75.0),
    "podcast": (0.2, 72.0),
    "energetic": (0.85, 128.0),
    "cinematic": (0.55, 90.0),
    "dramatic": (0.75, 110.0),
    "happy": (0.7, 118.0),
    "calm": (0.25, 78.0),
    "inspiring": (0.65, 115.0),
    "neutral": (0.45, 95.0),
    "dark": (0.5, 90.0),
    "playful": (0.7, 122.0),
}

# A reasonable midpoint when a mood resolves to no known tags.
_DEFAULT_PROFILE: tuple[float, float] = (0.5, 100.0)


@dataclass(frozen=True)
class StyleTarget:
    energy: float
    tempo_bpm: float


def style_target(mood: str) -> StyleTarget:
    tags = resolve_mood_tags(mood)
    if not tags:
        return StyleTarget(energy=_DEFAULT_PROFILE[0], tempo_bpm=_DEFAULT_PROFILE[1])
    energies = [_TAG_PROFILE.get(tag, _DEFAULT_PROFILE)[0] for tag in tags]
    tempos = [_TAG_PROFILE.get(tag, _DEFAULT_PROFILE)[1] for tag in tags]
    return StyleTarget(
        energy=sum(energies) / len(energies),
        tempo_bpm=sum(tempos) / len(tempos),
    )


def _energy_fit(track_energy: float | None, target: float) -> float:
    if track_energy is None:
        return 0.5  # unknown energy: neutral fit, never disqualifying
    return 1.0 - min(1.0, abs(track_energy - target))


def _tempo_fit(track_bpm: int | float | None, target: float) -> float:
    if not track_bpm:
        return 0.5
    # 40 bpm away => fit 0; same bpm => fit 1.
    return 1.0 - min(1.0, abs(float(track_bpm) - target) / 40.0)


@dataclass(frozen=True)
class RankedScore:
    mood_overlap: int
    energy_fit: float
    tempo_fit: float

    @property
    def total(self) -> float:
        # Mood overlap dominates; energy and tempo break ties and order the
        # fallback pool. Weights keep overlap strictly above the 0..2 fit sum.
        return (self.mood_overlap * 10.0) + self.energy_fit + self.tempo_fit


def score_track(
    *,
    track_mood_tags: list[str],
    track_energy: float | None,
    track_bpm: int | float | None,
    target_tags: list[str],
    target: StyleTarget,
) -> RankedScore:
    track_tags = {tag.lower() for tag in track_mood_tags}
    overlap = sum(1 for tag in target_tags if tag in track_tags)
    return RankedScore(
        mood_overlap=overlap,
        energy_fit=_energy_fit(track_energy, target.energy),
        tempo_fit=_tempo_fit(track_bpm, target.tempo_bpm),
    )


def planner_mood_tags() -> set[str]:
    """Return every canonical mood tag the edit planner can produce.

    The planner emits a fixed set of free-text moods (one per style, plus the
    direction-text variants). Resolving each through the synonym table gives the
    closed set of mood tags the starter catalog must cover so every plan gets a
    fitting track.
    """
    # Imported lazily to avoid a heavy import from this low-level module.
    from app.schemas.plans import EditPlanStyle  # noqa: PLC0415
    from app.services.edit_planner import _music_mood, _style_config  # noqa: PLC0415

    moods: set[str] = set()
    for style in EditPlanStyle:
        moods.add(str(_style_config(style)["music_mood"]))
        moods.add(_music_mood(style, "sharper punchier founder"))
        moods.add(_music_mood(style, "preserve educational teaching"))
    tags: set[str] = set()
    for mood in moods:
        tags.update(resolve_mood_tags(mood))
    return tags


def rank_tracks(tracks: list, *, mood: str) -> list:
    """Rank catalog candidates by mood overlap then energy/tempo fit.

    ``tracks`` is any sequence whose items expose ``mood_tags``, ``energy`` and
    ``bpm`` attributes (e.g. ``MusicTrackCandidate``). A non-empty input always
    yields a non-empty output: when no track shares a mood tag, the whole pool
    is ordered by energy/tempo fit so the best-feeling track still wins.
    """
    target_tags = resolve_mood_tags(mood)
    target = style_target(mood)
    scored = [
        (
            score_track(
                track_mood_tags=list(getattr(track, "mood_tags", []) or []),
                track_energy=getattr(track, "energy", None),
                track_bpm=getattr(track, "bpm", None),
                target_tags=target_tags,
                target=target,
            ),
            index,
            track,
        )
        for index, track in enumerate(tracks)
    ]
    scored.sort(key=lambda item: (-item[0].total, item[1]))
    return [track for _score, _index, track in scored]
