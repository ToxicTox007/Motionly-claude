from dataclasses import dataclass

from app.schemas.captions import LanguageHint, TranscriptQualityStatus, TranscriptWord


@dataclass(frozen=True)
class TranscriptQualityResult:
    status: TranscriptQualityStatus
    reasons: list[str]


def assess_transcript_quality(
    *,
    words: list[TranscriptWord],
    duration_ms: int | None,
    confidence: float | None,
    language: str,
    language_hint: LanguageHint,
    raw_payload: dict[str, object] | None = None,
) -> TranscriptQualityResult:
    reasons: list[str] = []
    raw_payload = raw_payload or {}
    if raw_payload.get("audio_extraction_failed") is True:
        return TranscriptQualityResult(
            TranscriptQualityStatus.AUDIO_FAILED,
            ["audio_extraction_failed"],
        )
    if raw_payload.get("partial_audio") is True or raw_payload.get("partial") is True:
        reasons.append("partial_audio")
    if not words:
        return TranscriptQualityResult(TranscriptQualityStatus.EMPTY, ["empty_transcript"])
    duration_s = (duration_ms or max((word.end_ms for word in words), default=0)) / 1000
    if duration_s >= 10 and len(words) < 3:
        reasons.append("suspiciously_low_word_count")
    normalized = [word.text.strip().lower() for word in words if word.text.strip()]
    if normalized:
        most_common = max(normalized.count(token) for token in set(normalized))
        if len(normalized) >= 6 and most_common / len(normalized) >= 0.6:
            reasons.append("repeated_text")
    sorted_words = sorted(words, key=lambda item: item.start_ms)
    for previous, current in zip(sorted_words, sorted_words[1:], strict=False):
        if current.start_ms - previous.end_ms >= 5000:
            reasons.append("long_silent_gaps")
            break
    provider_confidence = confidence
    if provider_confidence is None:
        confidences = [word.confidence for word in words if word.confidence is not None]
        provider_confidence = sum(confidences) / len(confidences) if confidences else None
    if provider_confidence is not None and provider_confidence < 0.55:
        reasons.append("provider_low_confidence")
    if language_hint != LanguageHint.AUTO and language != language_hint.value:
        reasons.append("language_mismatch")
    if "partial_audio" in reasons:
        return TranscriptQualityResult(TranscriptQualityStatus.PARTIAL, sorted(set(reasons)))
    if reasons:
        return TranscriptQualityResult(TranscriptQualityStatus.LOW_CONFIDENCE, sorted(set(reasons)))
    return TranscriptQualityResult(TranscriptQualityStatus.OK, [])


def is_transcript_usable_for_planning(status: TranscriptQualityStatus) -> bool:
    return status in {TranscriptQualityStatus.OK, TranscriptQualityStatus.LOW_CONFIDENCE}
