from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.schemas.captions import TranscriptWord
from app.schemas.clips import (
    ClipGoal,
    ClipRecommendation,
    ClipRecommendationGroup,
    ClipSignalAvailability,
    ClipSignalEvidence,
)
from app.schemas.visual import SceneSegment, SceneType, VideoManifest

CLIP_DURATIONS_S = (15, 30, 60)

GOAL_TERMS: dict[ClipGoal, set[str]] = {
    ClipGoal.VIRAL_HOOK: {
        "biggest",
        "break",
        "cannot",
        "dont",
        "first",
        "hook",
        "mistake",
        "must",
        "never",
        "nobody",
        "secret",
        "stop",
        "surprising",
        "truth",
        "viral",
        "watch",
        "why",
    },
    ClipGoal.EDUCATIONAL_MOMENT: {
        "because",
        "example",
        "explain",
        "first",
        "framework",
        "how",
        "learn",
        "lesson",
        "method",
        "process",
        "step",
        "steps",
        "teach",
        "tip",
        "tutorial",
    },
    ClipGoal.STORYTELLING: {
        "after",
        "before",
        "but",
        "finally",
        "happened",
        "journey",
        "moment",
        "remember",
        "realized",
        "started",
        "story",
        "then",
        "when",
        "where",
        "while",
    },
    ClipGoal.PRODUCT_MENTION: {
        "brand",
        "buy",
        "client",
        "code",
        "customer",
        "demo",
        "feature",
        "features",
        "launch",
        "offer",
        "price",
        "product",
        "shop",
        "tool",
        "use",
        "using",
    },
}

GOAL_TARGET_WORDS_PER_SECOND: dict[ClipGoal, float] = {
    ClipGoal.VIRAL_HOOK: 2.7,
    ClipGoal.EDUCATIONAL_MOMENT: 2.15,
    ClipGoal.STORYTELLING: 2.0,
    ClipGoal.PRODUCT_MENTION: 2.25,
}

POSITIVE_EMOTIONS = {"excited", "happy", "joy", "positive", "surprise", "urgent"}
STORY_EMOTIONS = {"curious", "nostalgic", "sad", "surprise", "thoughtful"}


@dataclass(frozen=True)
class _WindowScore:
    recommendation: ClipRecommendation
    start_ms: int
    end_ms: int


def find_best_clips(
    *,
    words: Sequence[TranscriptWord],
    source_duration_ms: int | None,
    goal: ClipGoal,
    audio_intelligence: Mapping[str, object] | None = None,
    visual_manifest: VideoManifest | None = None,
    limit_per_duration: int = 3,
) -> tuple[list[ClipRecommendationGroup], ClipSignalAvailability, int]:
    """Rank transcript-aligned clips without requiring another model invocation.

    Scores use only persisted transcript/audio/visual analysis and re-normalize
    available weights when visual or audio enrichment has not completed.
    """
    all_words = sorted(words, key=lambda word: (word.start_ms, word.index))
    inferred_duration = max((word.end_ms for word in all_words), default=0)
    visual_duration = visual_manifest.duration_ms if visual_manifest else None
    duration_ms = (
        source_duration_ms
        if source_duration_ms and source_duration_ms > 0
        else visual_duration or inferred_duration
    )
    ordered_words = [word for word in all_words if word.start_ms < duration_ms]
    if not ordered_words or duration_ms <= 0:
        return [], ClipSignalAvailability(transcript=bool(ordered_words)), duration_ms

    audio = _mapping(audio_intelligence)
    signal_availability = ClipSignalAvailability(
        transcript=True,
        audio_intelligence=bool(audio),
        visual_analysis=visual_manifest is not None and bool(visual_manifest.scenes),
    )
    keyword_scores = _keyword_scores(audio)
    emotion_windows = _emotion_windows(audio)
    pause_map = _pause_map(audio, ordered_words)
    pace_label = _string(_mapping(audio.get("style_signals")).get("pace"))
    groups: list[ClipRecommendationGroup] = []

    for duration_s in CLIP_DURATIONS_S:
        clip_duration_ms = duration_s * 1_000
        if duration_ms < clip_duration_ms:
            continue
        scored = [
            _score_window(
                start_ms=start_ms,
                duration_s=duration_s,
                source_duration_ms=duration_ms,
                words=ordered_words,
                goal=goal,
                keyword_scores=keyword_scores,
                emotion_windows=emotion_windows,
                pause_map=pause_map,
                pace_label=pace_label,
                visual_manifest=visual_manifest,
            )
            for start_ms in _candidate_starts(
                words=ordered_words,
                source_duration_ms=duration_ms,
                clip_duration_ms=clip_duration_ms,
            )
        ]
        ranked = _select_distinct_clips(
            scored=[item for item in scored if item is not None],
            limit=limit_per_duration,
        )
        if ranked:
            groups.append(
                ClipRecommendationGroup(
                    duration_s=duration_s,
                    clips=[
                        item.recommendation.model_copy(update={"rank": index})
                        for index, item in enumerate(ranked, start=1)
                    ],
                )
            )
    return groups, signal_availability, duration_ms


def _candidate_starts(
    *,
    words: Sequence[TranscriptWord],
    source_duration_ms: int,
    clip_duration_ms: int,
) -> list[int]:
    max_start = max(0, source_duration_ms - clip_duration_ms)
    stride_ms = max(2_500, clip_duration_ms // 3)
    starts = set(range(0, max_start + 1, stride_ms))
    starts.add(max_start)

    last_anchor = -stride_ms
    previous: TranscriptWord | None = None
    for word in words:
        is_pause_boundary = previous is not None and word.start_ms - previous.end_ms >= 700
        if word.start_ms - last_anchor >= stride_ms or is_pause_boundary:
            starts.add(_clamp(word.start_ms - 1_200, 0, max_start))
            last_anchor = word.start_ms
        previous = word
    return sorted(starts)


def _score_window(
    *,
    start_ms: int,
    duration_s: int,
    source_duration_ms: int,
    words: Sequence[TranscriptWord],
    goal: ClipGoal,
    keyword_scores: Mapping[str, float],
    emotion_windows: Sequence[Mapping[str, object]],
    pause_map: Sequence[tuple[int, int]],
    pace_label: str | None,
    visual_manifest: VideoManifest | None,
) -> _WindowScore | None:
    end_ms = min(source_duration_ms, start_ms + duration_s * 1_000)
    window_words = [word for word in words if word.start_ms < end_ms and word.end_ms > start_ms]
    if not window_words:
        return None
    minimum_word_count = max(6, round(duration_s * 0.4))
    spoken_span_ms = min(end_ms, window_words[-1].end_ms) - max(start_ms, window_words[0].start_ms)
    if len(window_words) < minimum_word_count or spoken_span_ms < duration_s * 1_000 * 0.15:
        return None

    token_pairs = [(_normalise(word.text), word.text) for word in window_words]
    normalized_tokens = [token for token, _ in token_pairs if token]
    matched_keywords = _matched_keywords(
        tokens=normalized_tokens,
        goal=goal,
        keyword_scores=keyword_scores,
    )
    lexical_score = _lexical_score(normalized_tokens, goal, keyword_scores)
    pace_score = _pace_score(window_words, duration_s, goal)
    silence_score = _silence_score(start_ms, end_ms, pause_map)
    confidence_score = _confidence_score(window_words)
    emotion, intensity, emotion_score = _emotion_score(
        start_ms=start_ms,
        end_ms=end_ms,
        goal=goal,
        emotion_windows=emotion_windows,
    )
    visual_score, visual_context = _visual_score(
        start_ms=start_ms,
        end_ms=end_ms,
        goal=goal,
        tokens=normalized_tokens,
        visual_manifest=visual_manifest,
    )

    components: list[tuple[float, float]] = [
        (0.42, lexical_score),
        (0.17, pace_score),
        (0.09, silence_score),
        (0.09, confidence_score),
    ]
    if emotion is not None:
        components.append((0.14, emotion_score))
    if visual_manifest is not None and visual_manifest.scenes:
        components.append((0.16, visual_score))
    component_weight = sum(weight for weight, _ in components)
    score = 100 * sum(weight * value for weight, value in components) / component_weight

    excerpt = _excerpt(window_words)
    if not excerpt:
        return None
    rationale = _rationale(
        goal=goal,
        matched_keywords=matched_keywords,
        emotion=emotion,
        intensity=intensity,
        silence_score=silence_score,
        visual_context=visual_context,
    )
    recommendation = ClipRecommendation(
        id=f"{goal.value}-{duration_s}-{start_ms}",
        rank=1,
        duration_s=duration_s,
        start_ms=start_ms,
        end_ms=end_ms,
        score=round(_clamp(score, 0, 100), 1),
        headline=_headline(window_words),
        transcript_excerpt=excerpt,
        rationale=rationale,
        signals=ClipSignalEvidence(
            matched_keywords=matched_keywords,
            emotion=emotion,
            emotion_intensity=round(intensity, 2) if intensity is not None else None,
            pace=pace_label,
            visual_context=visual_context,
        ),
    )
    return _WindowScore(recommendation=recommendation, start_ms=start_ms, end_ms=end_ms)


def _select_distinct_clips(
    *,
    scored: Sequence[_WindowScore],
    limit: int,
) -> list[_WindowScore]:
    ranked = sorted(
        scored,
        key=lambda item: (-item.recommendation.score, item.start_ms),
    )
    selected: list[_WindowScore] = []
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if all(_overlap_ratio(candidate, chosen) < 0.55 for chosen in selected):
            selected.append(candidate)
    return selected


def _overlap_ratio(left: _WindowScore, right: _WindowScore) -> float:
    overlap = max(0, min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms))
    shortest = min(left.end_ms - left.start_ms, right.end_ms - right.start_ms)
    return overlap / shortest if shortest else 0


def _keyword_scores(audio: Mapping[str, object]) -> dict[str, float]:
    values = _sequence(audio.get("keywords"))
    scores: dict[str, float] = {}
    for value in values:
        item = _mapping(value)
        token = _normalise(_string(item.get("text")) or "")
        score = _number(item.get("score"))
        if token and score is not None:
            scores[token] = max(scores.get(token, 0), _clamp(score, 0, 1))
    return scores


def _emotion_windows(audio: Mapping[str, object]) -> list[Mapping[str, object]]:
    emotion_pacing = _mapping(audio.get("emotion_pacing"))
    return [_mapping(item) for item in _sequence(emotion_pacing.get("windows")) if _mapping(item)]


def _pause_map(
    audio: Mapping[str, object],
    words: Sequence[TranscriptWord],
) -> list[tuple[int, int]]:
    emotion_pacing = _mapping(audio.get("emotion_pacing"))
    pauses = []
    for value in _sequence(emotion_pacing.get("pause_map")):
        item = _mapping(value)
        start_ms = _integer(item.get("start_ms"))
        end_ms = _integer(item.get("end_ms"))
        if start_ms is not None and end_ms is not None and end_ms > start_ms:
            pauses.append((start_ms, end_ms))
    if pauses:
        return pauses
    return [
        (current.end_ms, following.start_ms)
        for current, following in zip(words, words[1:], strict=False)
        if following.start_ms - current.end_ms >= 250
    ]


def _matched_keywords(
    *,
    tokens: Sequence[str],
    goal: ClipGoal,
    keyword_scores: Mapping[str, float],
) -> list[str]:
    token_set = set(tokens)
    goal_hits = sorted(token_set & GOAL_TERMS[goal])
    scored_hits = sorted(
        (token for token in token_set if token in keyword_scores),
        key=lambda token: (-keyword_scores[token], token),
    )
    return list(dict.fromkeys([*goal_hits, *scored_hits]))[:12]


def _lexical_score(
    tokens: Sequence[str],
    goal: ClipGoal,
    keyword_scores: Mapping[str, float],
) -> float:
    if not tokens:
        return 0
    goal_hits = sum(1 for token in tokens if token in GOAL_TERMS[goal])
    keyword_strength = sum(keyword_scores.get(token, 0) for token in set(tokens))
    story_pronoun_bonus = (
        0.15
        if goal == ClipGoal.STORYTELLING
        and any(token in {"i", "we", "my", "our"} for token in tokens)
        else 0
    )
    return _clamp(
        min(0.82, goal_hits * 0.22) + min(0.3, keyword_strength * 0.08) + story_pronoun_bonus,
        0,
        1,
    )


def _pace_score(words: Sequence[TranscriptWord], duration_s: int, goal: ClipGoal) -> float:
    spoken_rate = len(words) / max(duration_s, 1)
    target = GOAL_TARGET_WORDS_PER_SECOND[goal]
    return _clamp(1 - abs(spoken_rate - target) / max(target, 0.1), 0, 1)


def _silence_score(start_ms: int, end_ms: int, pause_map: Sequence[tuple[int, int]]) -> float:
    if not pause_map:
        return 0.5
    edge_pause = any(
        (start_ms - 1_000 <= pause_end <= start_ms + 1_000)
        or (end_ms - 1_000 <= pause_start <= end_ms + 1_000)
        for pause_start, pause_end in pause_map
    )
    internal_pause_ms = sum(
        min(end_ms, pause_end) - max(start_ms, pause_start)
        for pause_start, pause_end in pause_map
        if pause_start >= start_ms + 1_000 and pause_end <= end_ms - 1_000
    )
    internal_penalty = _clamp(internal_pause_ms / max(end_ms - start_ms, 1), 0, 1)
    return _clamp((0.65 if edge_pause else 0.35) + 0.35 * (1 - internal_penalty), 0, 1)


def _confidence_score(words: Sequence[TranscriptWord]) -> float:
    confidences = [word.confidence for word in words if word.confidence is not None]
    return sum(confidences) / len(confidences) if confidences else 0.65


def _emotion_score(
    *,
    start_ms: int,
    end_ms: int,
    goal: ClipGoal,
    emotion_windows: Sequence[Mapping[str, object]],
) -> tuple[str | None, float | None, float]:
    overlaps: list[tuple[str, float, int]] = []
    for item in emotion_windows:
        window_start = _integer(item.get("start_ms"))
        window_end = _integer(item.get("end_ms"))
        intensity = _number(item.get("intensity"))
        if window_start is None or window_end is None or intensity is None:
            continue
        overlap = max(0, min(end_ms, window_end) - max(start_ms, window_start))
        if overlap:
            emotion = _string(item.get("emotion")) or "expressive"
            overlaps.append((emotion.lower(), _clamp(intensity, 0, 1), overlap))
    if not overlaps:
        return None, None, 0
    total_overlap = sum(overlap for _, _, overlap in overlaps)
    intensity = sum(value * overlap for _, value, overlap in overlaps) / total_overlap
    emotion = max(overlaps, key=lambda item: item[1] * item[2])[0]
    alignment = 0.6
    if goal == ClipGoal.VIRAL_HOOK:
        alignment = 1 if emotion in POSITIVE_EMOTIONS else 0.7
    elif goal == ClipGoal.STORYTELLING:
        alignment = 1 if emotion in STORY_EMOTIONS else 0.72
    elif goal == ClipGoal.EDUCATIONAL_MOMENT:
        alignment = 0.85 if emotion in {"calm", "confident", "positive"} else 0.65
    elif goal == ClipGoal.PRODUCT_MENTION:
        alignment = 0.85 if emotion in {"positive", "confident", "excited"} else 0.65
    return emotion, intensity, _clamp(0.45 * intensity + 0.55 * alignment, 0, 1)


def _visual_score(
    *,
    start_ms: int,
    end_ms: int,
    goal: ClipGoal,
    tokens: Sequence[str],
    visual_manifest: VideoManifest | None,
) -> tuple[float, list[str]]:
    if visual_manifest is None:
        return 0, []
    scenes = [
        scene
        for scene in visual_manifest.scenes
        if scene.start_ms < end_ms and scene.end_ms > start_ms
    ]
    if not scenes:
        return 0, []
    importance = sum(scene.visual_importance_score for scene in scenes) / len(scenes)
    scene_types = {scene.scene_type for scene in scenes}
    has_motion = any(scene.camera_motion.value not in {"static", "unknown"} for scene in scenes)
    labels = _visual_labels(scenes)
    context = list(dict.fromkeys([*(scene_type.value for scene_type in scene_types), *labels]))[:8]

    goal_fit = 0.5
    if goal == ClipGoal.VIRAL_HOOK:
        goal_fit = 0.85 if has_motion or len(scene_types) > 1 else 0.55
    elif goal == ClipGoal.EDUCATIONAL_MOMENT:
        goal_fit = 0.9 if scene_types & {SceneType.SCREEN_RECORDING, SceneType.TEXT_HEAVY} else 0.6
    elif goal == ClipGoal.STORYTELLING:
        goal_fit = (
            0.85
            if SceneType.TALKING_HEAD in scene_types or any(scene.faces for scene in scenes)
            else 0.6
        )
    elif goal == ClipGoal.PRODUCT_MENTION:
        token_set = set(tokens)
        object_match = any(_normalise(label) in token_set for label in labels)
        goal_fit = 0.95 if SceneType.PRODUCT in scene_types or object_match else 0.45
    return _clamp(0.55 * importance + 0.45 * goal_fit, 0, 1), context


def _visual_labels(scenes: Sequence[SceneSegment]) -> list[str]:
    labels: list[str] = []
    for scene in scenes:
        labels.extend(object_detection.label for object_detection in scene.objects)
    return list(dict.fromkeys(label for label in labels if label))[:5]


def _excerpt(words: Sequence[TranscriptWord]) -> str:
    text = " ".join(word.text.strip() for word in words if word.text.strip())
    if len(text) <= 480:
        return text
    return f"{text[:477].rsplit(' ', 1)[0]}…"


def _headline(words: Sequence[TranscriptWord]) -> str:
    text = " ".join(word.text.strip() for word in words[:10] if word.text.strip())
    if len(text) <= 180:
        return text or "Recommended moment"
    return f"{text[:177].rsplit(' ', 1)[0]}…"


def _rationale(
    *,
    goal: ClipGoal,
    matched_keywords: Sequence[str],
    emotion: str | None,
    intensity: float | None,
    silence_score: float,
    visual_context: Sequence[str],
) -> list[str]:
    rationale: list[str] = []
    if matched_keywords:
        terms = ", ".join(matched_keywords[:3])
        rationale.append(f"Matches {goal.value.replace('_', ' ')} language: {terms}.")
    if emotion and intensity is not None and intensity >= 0.6:
        rationale.append(f"{emotion.title()} delivery has strong intensity.")
    if silence_score >= 0.75:
        rationale.append("Natural pause points make this moment easy to cut.")
    if visual_context:
        rationale.append(f"Visual context supports it: {', '.join(visual_context[:3])}.")
    return rationale[:4] or ["Strong transcript continuity for a self-contained highlight."]


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower()).replace("'", "")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else []


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, float | int) else None


def _integer(value: object) -> int | None:
    return int(value) if isinstance(value, float | int) else None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
