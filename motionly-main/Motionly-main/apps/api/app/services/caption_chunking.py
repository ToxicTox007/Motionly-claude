import re

from app.schemas.captions import CaptionSegment, TranscriptWord

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


def chunk_caption_words(
    words: list[TranscriptWord],
    *,
    hide_filler_words: bool = False,
) -> list[CaptionSegment]:
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
        should_break = (
            len(current) >= MAX_WORDS_PER_SEGMENT
            or gap_ms > MAX_GAP_MS
            or PUNCTUATION_RE.search(previous.text) is not None
        )
        if should_break and _duration_ms(current) >= MIN_SEGMENT_MS:
            segments.append(_make_segment(len(segments), current))
            current = [word]
        else:
            current.append(word)

    if current:
        if segments and _duration_ms(current) < MIN_SEGMENT_MS:
            segments[-1] = _make_segment(segments[-1].index, [*segments[-1].words, *current])
        else:
            segments.append(_make_segment(len(segments), current))

    return [_enforce_two_lines(segment) for segment in segments]


def _duration_ms(words: list[TranscriptWord]) -> int:
    return words[-1].end_ms - words[0].start_ms


def _make_segment(index: int, words: list[TranscriptWord]) -> CaptionSegment:
    text = " ".join(word.text for word in words)
    return CaptionSegment(
        index=index,
        start_ms=words[0].start_ms,
        end_ms=max(words[-1].end_ms, words[0].start_ms + MIN_SEGMENT_MS),
        text=text,
        words=words,
        line_count=1 if len(words) <= 2 else 2,
    )


def _enforce_two_lines(segment: CaptionSegment) -> CaptionSegment:
    segment.line_count = 1 if len(segment.text.split()) <= 2 else 2
    return segment
