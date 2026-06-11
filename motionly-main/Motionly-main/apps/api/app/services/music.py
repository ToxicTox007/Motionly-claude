from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

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


class MusicProvider(Protocol):
    provider_key: MusicProviderKey

    def search(self, *, mood: str, max_results: int = 5) -> list[MusicTrackCandidate]:
        ...

    def get_track(self, *, provider_track_id: str) -> MusicTrackCandidate | None:
        ...


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
