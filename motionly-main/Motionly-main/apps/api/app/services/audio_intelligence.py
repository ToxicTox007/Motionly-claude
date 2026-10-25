import math
import re
from collections import Counter
from typing import Any

from app.schemas.captions import TranscriptWord

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "then",
    "this",
    "to",
    "we",
    "with",
    "you",
    "your",
}

STYLE_TERMS = {
    "educational": {
        "learn",
        "step",
        "steps",
        "how",
        "teach",
        "tutorial",
        "explain",
        "secret",
        "framework",
    },
    "motivational": {
        "win",
        "dream",
        "build",
        "believe",
        "success",
        "prove",
        "result",
        "growth",
        "future",
    },
    "creator": {
        "hook",
        "viral",
        "content",
        "creator",
        "audience",
        "follow",
        "watch",
        "show",
    },
    "luxury": {
        "luxury",
        "premium",
        "brand",
        "elegant",
        "exclusive",
        "crafted",
        "design",
    },
    "podcast": {
        "listen",
        "conversation",
        "episode",
        "guest",
        "interview",
        "talk",
        "question",
    },
}

POSITIVE_TERMS = {
    "build",
    "growth",
    "prove",
    "result",
    "secret",
    "show",
    "success",
    "win",
}
INTENSE_TERMS = {"urgent", "now", "never", "must", "stop", "break", "risk", "fail"}


def build_audio_intelligence_payload(
    *,
    words: list[TranscriptWord],
    duration_ms: int | None,
    has_audio: bool | None = True,
) -> dict[str, Any]:
    tokens = [_normalise_token(word.text) for word in words]
    tokens = [token for token in tokens if token]
    keyword_scores = _keyword_scores(tokens)
    average_confidence = _average_confidence(words)
    low_confidence_word_count = sum(
        1 for word in words if word.confidence is not None and word.confidence < 0.7
    )
    words_per_minute = _words_per_minute(words=words, duration_ms=duration_ms)
    pause_count = _pause_count(words)
    style_scores = _style_scores(tokens)
    predicted_style = max(style_scores, key=style_scores.get) if style_scores else "creator"

    return {
        "schema_version": 1,
        "keywords": [{"text": text, "score": score} for text, score in keyword_scores[:10]],
        "style_signals": {
            "predicted_style": predicted_style,
            "tone": _tone(tokens),
            "pace": _pace_label(words_per_minute),
            "style_scores": style_scores,
        },
        "speech_metrics": {
            "word_count": len(words),
            "duration_ms": duration_ms,
            "words_per_minute": words_per_minute,
        },
        "confidence": {
            "average_word_confidence": average_confidence,
            "low_confidence_word_count": low_confidence_word_count,
            "word_confidence_available": any(word.confidence is not None for word in words),
        },
        "speaker_diarization": _speaker_diarization(words),
        "emotion_pacing": {
            "aggregate_emotion": _aggregate_emotion(tokens),
            "pause_count": pause_count,
            "pause_map": _pause_map(words),
            "speech_rhythm": _speech_rhythm(words=words, duration_ms=duration_ms),
            "windows": _emotion_windows(words=words, duration_ms=duration_ms),
        },
        "sound_classification": _sound_classification(
            has_audio=has_audio,
            has_words=bool(words),
        ),
    }


def merge_audio_intelligence(
    *,
    raw_payload: dict[str, object],
    words: list[TranscriptWord],
    duration_ms: int | None,
    has_audio: bool | None = True,
    ml_insights: dict[str, object] | None = None,
) -> dict[str, object]:
    audio_intelligence = build_audio_intelligence_payload(
        words=words,
        duration_ms=duration_ms,
        has_audio=has_audio,
    )
    if ml_insights:
        audio_intelligence = _merge_ml_insights(
            audio_intelligence=audio_intelligence,
            ml_insights=ml_insights,
        )
    return {
        **raw_payload,
        "audio_intelligence": audio_intelligence,
    }


def _merge_ml_insights(
    *,
    audio_intelligence: dict[str, Any],
    ml_insights: dict[str, object],
) -> dict[str, Any]:
    merged = dict(audio_intelligence)
    diarization = ml_insights.get("speaker_diarization")
    if isinstance(diarization, dict):
        if diarization.get("degraded") and diarization.get("heuristic_fallback"):
            # A remote provider failed: keep the transcript-based estimate and
            # record why the ML result is missing.
            merged["speaker_diarization"] = {
                **dict(merged.get("speaker_diarization") or {}),
                "ml_provider": diarization.get("provider"),
                "ml_status": diarization.get("status"),
            }
        else:
            merged["speaker_diarization"] = diarization
    sound = ml_insights.get("sound_classification")
    if isinstance(sound, dict):
        if sound.get("degraded") and sound.get("heuristic_fallback"):
            merged["sound_classification"] = {
                **dict(merged.get("sound_classification") or {}),
                "ml_provider": sound.get("provider"),
                "ml_status": sound.get("status"),
            }
        else:
            merged["sound_classification"] = sound
    emotion_ml = ml_insights.get("emotion_ml")
    emotion_ml = emotion_ml if isinstance(emotion_ml, dict) else {}
    emotion_windows = ml_insights.get("emotion_windows")
    if isinstance(emotion_windows, list):
        emotion_pacing = dict(merged.get("emotion_pacing") or {})
        emotion_pacing["provider"] = str(emotion_ml.get("provider") or "wav2vec2_emotion")
        emotion_pacing["windows"] = emotion_windows
        if emotion_windows:
            emotion_pacing["aggregate_emotion"] = _dominant_window_emotion(emotion_windows)
        merged["emotion_pacing"] = emotion_pacing
    elif emotion_ml.get("degraded"):
        emotion_pacing = dict(merged.get("emotion_pacing") or {})
        emotion_pacing["ml_provider"] = emotion_ml.get("provider")
        emotion_pacing["ml_status"] = emotion_ml.get("status")
        merged["emotion_pacing"] = emotion_pacing
    return merged


def _normalise_token(text: str) -> str:
    token = re.sub(r"[^a-z0-9'-]+", "", text.lower().strip())
    if token in STOPWORDS or len(token) < 3:
        return ""
    return token


def _keyword_scores(tokens: list[str]) -> list[tuple[str, float]]:
    counts = Counter(tokens)
    if not counts:
        return []
    max_count = max(counts.values())
    scored = []
    for token, count in counts.items():
        length_bonus = min(len(token) / 12, 1)
        score = round((0.75 * (count / max_count)) + (0.25 * length_bonus), 3)
        scored.append((token, score))
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def _style_scores(tokens: list[str]) -> dict[str, float]:
    if not tokens:
        return {"creator": 0.1}
    token_counts = Counter(tokens)
    scores: dict[str, float] = {}
    for style, terms in STYLE_TERMS.items():
        hits = sum(token_counts[term] for term in terms)
        scores[style] = round(hits / max(len(tokens), 1), 3)
    if all(score == 0 for score in scores.values()):
        scores["creator"] = 0.1
    return scores


def _average_confidence(words: list[TranscriptWord]) -> float | None:
    values = [word.confidence for word in words if word.confidence is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _words_per_minute(*, words: list[TranscriptWord], duration_ms: int | None) -> int | None:
    if not words:
        return None
    duration = duration_ms
    if not duration:
        duration = max(word.end_ms for word in words) - min(word.start_ms for word in words)
    if duration <= 0:
        return None
    return math.ceil(len(words) / (duration / 60_000))


def _pause_count(words: list[TranscriptWord]) -> int:
    if len(words) < 2:
        return 0
    return sum(
        1
        for current, next_word in zip(words, words[1:], strict=False)
        if next_word.start_ms - current.end_ms >= 700
    )


def _pause_map(words: list[TranscriptWord]) -> list[dict[str, int]]:
    return [
        {
            "start_ms": current.end_ms,
            "end_ms": next_word.start_ms,
            "duration_ms": next_word.start_ms - current.end_ms,
        }
        for current, next_word in zip(words, words[1:], strict=False)
        if next_word.start_ms - current.end_ms >= 250
    ]


def _speech_rhythm(*, words: list[TranscriptWord], duration_ms: int | None) -> dict[str, Any]:
    if not words:
        return {
            "bpm_equivalent": None,
            "onset_count": 0,
            "mean_interval_ms": None,
            "interval_variability": None,
            "energy_curve": [],
        }
    intervals = [
        next_word.start_ms - current.start_ms
        for current, next_word in zip(words, words[1:], strict=False)
        if next_word.start_ms > current.start_ms
    ]
    mean_interval = sum(intervals) / len(intervals) if intervals else None
    variance = (
        sum((value - mean_interval) ** 2 for value in intervals) / len(intervals)
        if intervals and mean_interval is not None
        else None
    )
    end_ms = duration_ms or max(word.end_ms for word in words)
    energy_curve = []
    for start_ms in range(0, max(end_ms, 1), 500):
        active = [
            word for word in words if word.start_ms < start_ms + 500 and word.end_ms >= start_ms
        ]
        voiced_ms = sum(
            max(0, min(word.end_ms, start_ms + 500) - max(word.start_ms, start_ms))
            for word in active
        )
        energy_curve.append(
            {
                "start_ms": start_ms,
                "end_ms": min(start_ms + 500, end_ms),
                "speech_density": round(min(1, voiced_ms / 500), 3),
                "word_onsets": len([word for word in active if word.start_ms >= start_ms]),
            }
        )
    return {
        "bpm_equivalent": round(60_000 / mean_interval) if mean_interval else None,
        "onset_count": len(words),
        "mean_interval_ms": round(mean_interval) if mean_interval else None,
        "interval_variability": round(math.sqrt(variance) / mean_interval, 3)
        if variance is not None and mean_interval
        else None,
        "energy_curve": energy_curve,
    }


def _pace_label(words_per_minute: int | None) -> str:
    if words_per_minute is None:
        return "unknown"
    if words_per_minute >= 185:
        return "fast"
    if words_per_minute <= 105:
        return "slow"
    return "medium"


def _tone(tokens: list[str]) -> str:
    token_set = set(tokens)
    if token_set & INTENSE_TERMS:
        return "urgent"
    if token_set & POSITIVE_TERMS:
        return "confident"
    return "neutral"


def _speaker_diarization(words: list[TranscriptWord]) -> dict[str, Any]:
    if not words:
        return {
            "provider": "heuristic_single_speaker_v1",
            "speaker_count": 0,
            "segments": [],
            "confidence": 0.0,
        }
    segments = []
    segment_start = words[0].start_ms
    previous = words[0]
    segment_index = 0
    for word in words[1:]:
        if word.start_ms - previous.end_ms >= 1600:
            segments.append(
                {
                    "speaker": "speaker_1",
                    "start_ms": segment_start,
                    "end_ms": previous.end_ms,
                    "segment_index": segment_index,
                }
            )
            segment_index += 1
            segment_start = word.start_ms
        previous = word
    segments.append(
        {
            "speaker": "speaker_1",
            "start_ms": segment_start,
            "end_ms": previous.end_ms,
            "segment_index": segment_index,
        }
    )
    return {
        "provider": "heuristic_single_speaker_v1",
        "speaker_count": 1,
        "segments": segments,
        "confidence": 0.5,
    }


def _aggregate_emotion(tokens: list[str]) -> str:
    token_set = set(tokens)
    if token_set & INTENSE_TERMS:
        return "urgent"
    if token_set & POSITIVE_TERMS:
        return "positive"
    return "neutral"


def _emotion_windows(
    *,
    words: list[TranscriptWord],
    duration_ms: int | None,
) -> list[dict[str, Any]]:
    if not words:
        return []
    end_ms = duration_ms or max(word.end_ms for word in words)
    windows = []
    for start_ms in range(0, max(end_ms, 1), 500):
        window_words = [
            word for word in words if word.start_ms < start_ms + 500 and word.end_ms >= start_ms
        ]
        if not window_words:
            continue
        tokens = [_normalise_token(word.text) for word in window_words]
        tokens = [token for token in tokens if token]
        intensity = min(1.0, round((len(window_words) / 4) + _emotion_bonus(tokens), 3))
        emotion = _aggregate_emotion(tokens)
        joy = 0.65 if emotion == "positive" else 0.08
        sadness = (
            0.55 if any(token in {"sad", "loss", "hurt", "miss"} for token in tokens) else 0.08
        )
        urgency = 0.7 if emotion == "urgent" else 0.08
        windows.append(
            {
                "start_ms": start_ms,
                "end_ms": min(start_ms + 500, end_ms),
                "emotion": emotion,
                "joy": joy,
                "sadness": sadness,
                "urgency": urgency,
                "intensity": intensity,
                "word_count": len(window_words),
            }
        )
    return windows


def _dominant_window_emotion(windows: list[object]) -> str:
    scores = Counter()
    labels = Counter()
    for window in windows:
        if not isinstance(window, dict):
            continue
        if window.get("emotion"):
            labels[str(window["emotion"])] += 1
        for key in ("joy", "sadness", "urgency"):
            scores[key] += float(window.get(key) or 0)
    if scores and sum(scores.values()) > 0:
        return scores.most_common(1)[0][0]
    return labels.most_common(1)[0][0] if labels else "unknown"


def _emotion_bonus(tokens: list[str]) -> float:
    token_set = set(tokens)
    if token_set & INTENSE_TERMS:
        return 0.35
    if token_set & POSITIVE_TERMS:
        return 0.2
    return 0.0


def _sound_classification(*, has_audio: bool | None, has_words: bool) -> dict[str, Any]:
    if has_audio is False:
        return {
            "provider": "heuristic_audio_presence_v1",
            "labels": [{"label": "no_audio", "confidence": 1.0}],
            "background_sound": "none",
        }
    if not has_words:
        return {
            "provider": "heuristic_audio_presence_v1",
            "labels": [{"label": "no_detected_speech", "confidence": 0.7}],
            "background_sound": "unknown",
        }
    return {
        "provider": "heuristic_audio_presence_v1",
        "labels": [{"label": "speech", "confidence": 0.8}],
        "background_sound": "unknown",
    }
