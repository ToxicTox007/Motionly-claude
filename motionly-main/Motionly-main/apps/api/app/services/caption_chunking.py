import re
from dataclasses import dataclass

from app.schemas.captions import CaptionMode, CaptionSegment, CaptionStyle, TranscriptWord

FILLER_WORDS = {
    "um",
    "uh",
    "like",
    "you know",
    "sort of",
    "kind of",
    "basically",
}

PUNCTUATION_RE = re.compile(r"[.!?,;:]$")
MIN_SEGMENT_MS = 450
MAX_GAP_MS = 400
MAX_WORDS_PER_SEGMENT = 4


@dataclass(frozen=True)
class CaptionChunkingRules:
    min_segment_ms: int = MIN_SEGMENT_MS
    max_gap_ms: int = MAX_GAP_MS
    max_words_per_segment: int = MAX_WORDS_PER_SEGMENT
    max_chars_per_segment: int = 42
    max_lines: int = 2
    highlight_count: int = 1


def chunk_caption_words(
    words: list[TranscriptWord],
    *,
    hide_filler_words: bool = False,
    style: CaptionStyle | None = None,
) -> list[CaptionSegment]:
    rules = _rules_for_style(style)
    visible_words = [
        word
        for word in words
        if not hide_filler_words or word.text.strip().lower() not in FILLER_WORDS
    ]
    if not visible_words:
        return []

    segments: list[CaptionSegment] = []
    current: list[TranscriptWord] = []

    for word in visible_words:
        if not current:
            current.append(word)
            continue

        previous = current[-1]
        gap_ms = word.start_ms - previous.end_ms
        next_text = " ".join(item.text for item in [*current, word])
        should_break = (
            len(current) >= rules.max_words_per_segment
            or len(next_text) > rules.max_chars_per_segment
            or gap_ms > rules.max_gap_ms
            or PUNCTUATION_RE.search(previous.text) is not None
        )
        if should_break and _duration_ms(current) >= rules.min_segment_ms:
            segments.append(_make_segment(len(segments), current, rules=rules))
            current = [word]
        else:
            current.append(word)

    if current:
        if segments and _duration_ms(current) < rules.min_segment_ms:
            segments[-1] = _make_segment(
                segments[-1].index,
                [*segments[-1].words, *current],
                rules=rules,
            )
        else:
            segments.append(_make_segment(len(segments), current, rules=rules))

    return [_enforce_line_count(segment, rules=rules) for segment in segments]


def _duration_ms(words: list[TranscriptWord]) -> int:
    return words[-1].end_ms - words[0].start_ms


def _make_segment(
    index: int,
    words: list[TranscriptWord],
    *,
    rules: CaptionChunkingRules,
) -> CaptionSegment:
    text = " ".join(word.text for word in words)
    return CaptionSegment(
        index=index,
        start_ms=words[0].start_ms,
        end_ms=max(words[-1].end_ms, words[0].start_ms + rules.min_segment_ms),
        text=text,
        words=words,
        line_count=1 if len(words) <= 2 or rules.max_lines == 1 else 2,
        highlight_words=_highlight_words(words=words, limit=rules.highlight_count),
    )


def _enforce_line_count(
    segment: CaptionSegment,
    *,
    rules: CaptionChunkingRules,
) -> CaptionSegment:
    segment.line_count = 1 if rules.max_lines == 1 or len(segment.text.split()) <= 2 else 2
    return segment


def _rules_for_style(style: CaptionStyle | None) -> CaptionChunkingRules:
    if style is None:
        return CaptionChunkingRules()
    if style.mode == CaptionMode.IMPACT_STAMP:
        return CaptionChunkingRules(
            min_segment_ms=380,
            max_gap_ms=320,
            max_words_per_segment=3,
            max_chars_per_segment=28,
            max_lines=2,
            highlight_count=2,
        )
    if style.mode == CaptionMode.MINIMAL_LINE:
        return CaptionChunkingRules(
            min_segment_ms=900,
            max_gap_ms=520,
            max_words_per_segment=6,
            max_chars_per_segment=54,
            max_lines=1,
            highlight_count=1,
        )
    if style.mode == CaptionMode.EDUCATIONAL_CARD:
        return CaptionChunkingRules(
            min_segment_ms=850,
            max_gap_ms=500,
            max_words_per_segment=6,
            max_chars_per_segment=58,
            max_lines=2,
            highlight_count=2,
        )
    if style.mode == CaptionMode.CREATOR_TICKER:
        return CaptionChunkingRules(
            min_segment_ms=520,
            max_gap_ms=360,
            max_words_per_segment=4,
            max_chars_per_segment=36,
            max_lines=1,
            highlight_count=2,
        )
    return CaptionChunkingRules(
        max_lines=style.max_lines,
        max_words_per_segment=min(MAX_WORDS_PER_SEGMENT, max(2, style.max_lines * 3)),
    )


def _highlight_words(*, words: list[TranscriptWord], limit: int) -> list[str]:
    candidates = sorted(
        {
            word.text.strip(".,!?;:").lower()
            for word in words
            if len(word.text.strip(".,!?;:")) >= 4
        },
        key=lambda text: (-len(text), text),
    )
    return candidates[:limit]
