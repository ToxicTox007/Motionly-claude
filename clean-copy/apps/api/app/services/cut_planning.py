from dataclasses import dataclass

from app.schemas.captions import CaptionSegment, TranscriptWord
from app.schemas.renders import CutSegment
from app.schemas.uploads import VideoMetadata

PLANNER_VERSION = "basic-auto-editor-v1"


@dataclass(frozen=True)
class SilenceRange:
    start_ms: int
    end_ms: int


def parse_ffmpeg_silencedetect(output: str) -> list[SilenceRange]:
    starts: list[int] = []
    ranges: list[SilenceRange] = []
    for line in output.splitlines():
        if "silence_start:" in line:
            value = line.rsplit("silence_start:", 1)[-1].strip().split(" ", 1)[0]
            starts.append(_seconds_to_ms(value))
        elif "silence_end:" in line and starts:
            value = line.rsplit("silence_end:", 1)[-1].strip().split(" ", 1)[0]
            ranges.append(SilenceRange(start_ms=starts.pop(0), end_ms=_seconds_to_ms(value)))
    return ranges


def plan_keep_segments(
    *,
    words: list[TranscriptWord],
    caption_segments: list[CaptionSegment],
    metadata: VideoMetadata,
    silence_ranges: list[SilenceRange] | None = None,
    awkward_pause_ms: int = 800,
    micro_pause_ms: int = 400,
) -> list[CutSegment]:
    if not words:
        duration_ms = metadata.duration_ms or 0
        return [CutSegment(start_ms=0, end_ms=duration_ms, reason="no transcript timings")]

    removal_ranges = [
        silence
        for silence in (silence_ranges or [])
        if silence.end_ms - silence.start_ms >= awkward_pause_ms
    ]
    for previous, current in zip(words, words[1:], strict=False):
        gap = current.start_ms - previous.end_ms
        if gap >= awkward_pause_ms:
            removal_ranges.append(SilenceRange(start_ms=previous.end_ms, end_ms=current.start_ms))

    start_ms = max(0, words[0].start_ms - 150)
    end_ms = metadata.duration_ms or max(word.end_ms for word in words)
    keep: list[CutSegment] = []
    cursor = start_ms
    for removal in sorted(removal_ranges, key=lambda item: item.start_ms):
        if removal.end_ms <= cursor or removal.end_ms - removal.start_ms < micro_pause_ms:
            continue
        trim_start = max(cursor, removal.start_ms)
        if trim_start - cursor >= 250:
            keep.append(CutSegment(start_ms=cursor, end_ms=trim_start, reason="speech"))
        cursor = max(cursor, removal.end_ms)

    if end_ms - cursor >= 250:
        keep.append(CutSegment(start_ms=cursor, end_ms=end_ms, reason="speech"))

    if not keep and caption_segments:
        return [
            CutSegment(
                start_ms=max(0, segment.start_ms - 100),
                end_ms=segment.end_ms + 100,
                reason="caption segment",
            )
            for segment in caption_segments
        ]
    return keep or [CutSegment(start_ms=0, end_ms=end_ms, reason="fallback full source")]


def build_crop_warnings(metadata: VideoMetadata) -> list[str]:
    warnings = []
    if metadata.orientation == "landscape":
        warnings.append(
            "Landscape source will be fit into 9:16 with padding until face-aware crop ships."
        )
    if metadata.width and metadata.height and metadata.width > metadata.height:
        warnings.append("Horizontal footage may show side padding in this MVP export.")
    return warnings


def _seconds_to_ms(value: str) -> int:
    return int(round(float(value) * 1000))
