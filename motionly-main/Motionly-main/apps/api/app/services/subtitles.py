from collections.abc import Iterable

from app.schemas.captions import CaptionSegment
from app.schemas.localized_publishing import SubtitleFormat


def subtitle_content(*, segments: Iterable[CaptionSegment], format: SubtitleFormat) -> str:
    ordered = sorted(segments, key=lambda segment: (segment.start_ms, segment.index))
    if format == SubtitleFormat.VTT:
        lines = ["WEBVTT", ""]
        for segment in ordered:
            lines.extend(
                [
                    f"{_timestamp(segment.start_ms, separator='.')} --> "
                    f"{_timestamp(segment.end_ms, separator='.')}",
                    segment.text.strip(),
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    lines: list[str] = []
    for index, segment in enumerate(ordered, start=1):
        lines.extend(
            [
                str(index),
                f"{_timestamp(segment.start_ms, separator=',')} --> "
                f"{_timestamp(segment.end_ms, separator=',')}",
                segment.text.strip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _timestamp(value_ms: int, *, separator: str) -> str:
    hours, remainder = divmod(max(0, value_ms), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"
