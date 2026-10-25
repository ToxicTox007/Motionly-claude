"""Motionly capability normalizers (plan Section 2.10, "Motionly normalizers").

These are *code*, not template functions. They encode the fixed post-processing
each capability needs so that any provider's raw values become Motionly's typed
output. They are ported exactly from the archived vendor adapters so Task 17's
differential tests pass:

* object detection — lowercase labels, collapse whitespace, cut to 80 chars,
  apply ``label_aliases``, clamp scores and boxes to ``[0, 1]``, sort by score
  descending, keep the top N. Ported from the archived object-detection adapter
  (its ``normalize_label``, ``_parse_annotations``, ``_box``, ``_clamp``),
  including its alias behaviour, rounding and tie ordering.
* box conversion — ``vertices_norm`` (axis-aligned box from normalized vertices,
  a missing coordinate counts as 0) and ``xywh_norm``.
* diarization — sort segments by ``(start, end)``.
* stems — map artifact names to ``vocals`` / ``no_vocals`` (used by Task 16B).

No I/O, no grammar: these take already-extracted Python values (produced by the
``inference_templates`` engine) and return typed gateway outputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.services.model_gateway import (
    DetectedObjectOutput,
    DiarizationSegmentOutput,
)


class NormalizationError(ValueError):
    """A raw value could not be normalized (the adapter treats this as malformed)."""


def clamp01(value: float) -> float:
    """Clamp to ``[0, 1]`` — the archived adapter's ``_clamp``."""
    return max(0.0, min(1.0, value))


def normalize_label(name: object, aliases: Mapping[str, str] | None = None) -> str:
    """Lowercase, collapse whitespace, apply aliases, cut to 80 characters.

    Ported from the archived adapter's ``normalize_label``: the alias is applied to the
    whitespace-collapsed lowercase label, and the 80-character cut happens last
    (so an aliased short name is never truncated, and a long raw label is).
    """
    label = " ".join(str(name).lower().split())
    table = aliases or {}
    return table.get(label, label)[:80]


# ---------------------------------------------------------------------------
# Box conversion
# ---------------------------------------------------------------------------
def _coord(vertex: object, axis: str) -> float:
    """One clamped coordinate; an omitted coordinate counts as 0 (proto3 default)."""
    if isinstance(vertex, Mapping):
        raw = vertex.get(axis)
    else:
        raw = None
    try:
        return clamp01(float(raw or 0.0))
    except (TypeError, ValueError) as exc:
        raise NormalizationError("vertex coordinate is not a number") from exc


def box_from_vertices_norm(vertices: object) -> tuple[float, float, float, float] | None:
    """Axis-aligned box ``(x, y, width, height)`` from normalized vertices.

    Returns ``None`` when there are no vertices. A missing coordinate counts as
    0. Ported from the archived adapter's ``_box`` including its 4-decimal rounding.
    """
    if not isinstance(vertices, Sequence) or isinstance(vertices, str | bytes | bytearray):
        return None
    if not vertices:
        return None
    xs = [_coord(vertex, "x") for vertex in vertices]
    ys = [_coord(vertex, "y") for vertex in vertices]
    x, y = min(xs), min(ys)
    return round(x, 4), round(y, 4), round(max(xs) - x, 4), round(max(ys) - y, 4)


def box_from_xywh_norm(box: object) -> tuple[float, float, float, float] | None:
    """Normalized ``[x, y, w, h]`` box, clamped to ``[0, 1]`` and rounded.

    Accepts a 4-element list/tuple or a mapping with ``x``/``y``/``width|w``/
    ``height|h`` keys. Returns ``None`` when the value is missing/empty.
    """
    if box is None:
        return None
    if isinstance(box, Mapping):
        try:
            x = float(box.get("x") or 0.0)
            y = float(box.get("y") or 0.0)
            width = float(box.get("width", box.get("w")) or 0.0)
            height = float(box.get("height", box.get("h")) or 0.0)
        except (TypeError, ValueError) as exc:
            raise NormalizationError("xywh box values are not numbers") from exc
    elif isinstance(box, Sequence) and not isinstance(box, str | bytes | bytearray):
        if len(box) != 4:
            raise NormalizationError("xywh box must have four values")
        try:
            x, y, width, height = (float(value) for value in box)
        except (TypeError, ValueError) as exc:
            raise NormalizationError("xywh box values are not numbers") from exc
    else:
        raise NormalizationError("xywh box must be a list or object")
    return (
        round(clamp01(x), 4),
        round(clamp01(y), 4),
        round(clamp01(width), 4),
        round(clamp01(height), 4),
    )


# ---------------------------------------------------------------------------
# Object detection
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RawDetection:
    """One provider detection after template extraction, before normalization.

    ``box`` is the pre-conversion value (vertices list for ``vertices_norm`` or a
    4-tuple/mapping for ``xywh_norm``). ``label`` and ``score`` are the raw
    extracted values.
    """

    label: object
    score: object
    box: object


def normalize_detections(
    detections: Sequence[RawDetection],
    *,
    box_format: str,
    max_results: int,
    label_aliases: Mapping[str, str] | None = None,
    image_size: tuple[int, int] | None = None,
) -> tuple[DetectedObjectOutput, ...]:
    """Normalize one image's detections — ported exactly from the archived object-detection adapter.

    Labels are lowercased/whitespace-collapsed, aliased and cut to 80 chars; an
    empty label is dropped. Scores are clamped and rounded to 3 decimals. Boxes
    are converted per ``box_format`` and clamped; a detection with no box is
    dropped (matching the archived adapter, where ``_box`` returning ``None``
    skips the annotation). The result is sorted by confidence descending (stable,
    so ties keep input order) and cut to ``max_results``.

    ``image_size`` is ``(width, height)`` in pixels, required only for the Tier 3
    ``xyxy_px`` format so pixel corners are normalised to ``[0, 1]``.
    """
    objects: list[DetectedObjectOutput] = []
    for detection in detections:
        label = normalize_label(detection.label or "", label_aliases)
        if not label:
            continue
        try:
            score = float(detection.score or 0.0)
        except (TypeError, ValueError) as exc:
            raise NormalizationError("detection score is not a number") from exc
        box = _convert_box(detection.box, box_format, image_size)
        if box is None:
            continue
        x, y, width, height = box
        objects.append(
            DetectedObjectOutput(
                label=label,
                confidence=round(clamp01(score), 3),
                x=x,
                y=y,
                width=width,
                height=height,
            )
        )
    objects.sort(key=lambda item: item.confidence, reverse=True)
    return tuple(objects[:max_results])


def _convert_box(
    box: object, box_format: str, image_size: tuple[int, int] | None = None
) -> tuple[float, float, float, float] | None:
    if box_format == "vertices_norm":
        return box_from_vertices_norm(box)
    if box_format == "xywh_norm":
        return box_from_xywh_norm(box)
    if box_format == "xyxy_norm":
        return box_from_xyxy_norm(box)
    if box_format == "xyxy_px":
        return box_from_xyxy_px(box, image_size)
    raise NormalizationError(f"unsupported box_format: {box_format!r}")


# ---------------------------------------------------------------------------
# Tier 3 box formats (plan Section 2.10 Tier 3 extensions, Task 28)
# ---------------------------------------------------------------------------
def _xyxy_corners(box: object) -> tuple[float, float, float, float]:
    """Extract ``(x1, y1, x2, y2)`` corners from a 4-list or a mapping."""
    if isinstance(box, Mapping):
        try:
            x1 = float(box.get("x1", box.get("xmin")) or 0.0)
            y1 = float(box.get("y1", box.get("ymin")) or 0.0)
            x2 = float(box.get("x2", box.get("xmax")) or 0.0)
            y2 = float(box.get("y2", box.get("ymax")) or 0.0)
        except (TypeError, ValueError) as exc:
            raise NormalizationError("xyxy box values are not numbers") from exc
        return x1, y1, x2, y2
    if isinstance(box, Sequence) and not isinstance(box, str | bytes | bytearray):
        if len(box) != 4:
            raise NormalizationError("xyxy box must have four values")
        try:
            x1, y1, x2, y2 = (float(value) for value in box)
        except (TypeError, ValueError) as exc:
            raise NormalizationError("xyxy box values are not numbers") from exc
        return x1, y1, x2, y2
    raise NormalizationError("xyxy box must be a list or object")


def box_from_xyxy_norm(box: object) -> tuple[float, float, float, float] | None:
    """Normalized ``[x1, y1, x2, y2]`` corners -> ``(x, y, w, h)`` clamped box."""
    if box is None:
        return None
    x1, y1, x2, y2 = _xyxy_corners(box)
    x_min, x_max = (x1, x2) if x1 <= x2 else (x2, x1)
    y_min, y_max = (y1, y2) if y1 <= y2 else (y2, y1)
    x, y = clamp01(x_min), clamp01(y_min)
    return (
        round(x, 4),
        round(y, 4),
        round(clamp01(x_max) - x, 4),
        round(clamp01(y_max) - y, 4),
    )


def box_from_xyxy_px(
    box: object, image_size: tuple[int, int] | None
) -> tuple[float, float, float, float] | None:
    """Pixel ``[x1, y1, x2, y2]`` corners -> normalized ``(x, y, w, h)`` box.

    ``image_size`` is ``(width, height)`` in pixels (read from the request image
    headers). A missing or non-positive size is a malformed response.
    """
    if box is None:
        return None
    if image_size is None or min(image_size) <= 0:
        raise NormalizationError("xyxy_px box requires positive image dimensions")
    width_px, height_px = image_size
    x1, y1, x2, y2 = _xyxy_corners(box)
    return box_from_xyxy_norm(
        [x1 / width_px, y1 / height_px, x2 / width_px, y2 / height_px]
    )


class ImageDimensionError(NormalizationError):
    """Image header bytes could not be parsed for width/height (malformed input)."""


def image_dimensions(data: bytes) -> tuple[int, int]:
    """Read ``(width, height)`` in pixels from PNG/JPEG/WebP/GIF header bytes.

    Standard-library header parsing only (no Pillow, no network). Used by the
    ``xyxy_px`` box format, which needs the source image size to normalise pixel
    corners. Raises :class:`ImageDimensionError` for an unsupported or truncated
    image.
    """
    if not isinstance(data, bytes | bytearray):
        raise ImageDimensionError("image data must be bytes")
    data = bytes(data)
    # PNG: IHDR width/height are big-endian uint32 at bytes 16..24.
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise ImageDimensionError("truncated PNG header")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return width, height
    # GIF: logical screen width/height are little-endian uint16 at bytes 6..10.
    if data[:6] in (b"GIF87a", b"GIF89a"):
        if len(data) < 10:
            raise ImageDimensionError("truncated GIF header")
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        return width, height
    # WebP (VP8/VP8L/VP8X) inside a RIFF container.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_dimensions(data)
    # JPEG: scan the marker segments for a Start-Of-Frame (SOFn).
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    raise ImageDimensionError("unsupported image format for dimension parsing")


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 30:
        raise ImageDimensionError("truncated WebP header")
    fourcc = data[12:16]
    if fourcc == b"VP8 ":
        # Lossy: 16-bit width/height (14 bits used) at offset 26.
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    if fourcc == b"VP8L":
        # Lossless: 14-bit width/height packed after a 0x2f signature byte.
        if len(data) < 25 or data[20] != 0x2F:
            raise ImageDimensionError("truncated WebP VP8L header")
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    if fourcc == b"VP8X":
        # Extended: 24-bit width-1/height-1 at offset 24.
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    raise ImageDimensionError("unsupported WebP variant")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    length = len(data)
    while index + 9 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        # Standalone markers (RSTn, SOI, EOI, TEM) carry no length.
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            index += 2
            continue
        segment_len = int.from_bytes(data[index + 2 : index + 4], "big")
        # SOF0..SOF15 except DHT(0xC4)/DAC(0xCC)/RSTn carry height/width.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + segment_len
    raise ImageDimensionError("no JPEG start-of-frame marker found")


# ---------------------------------------------------------------------------
# Diarization
# ---------------------------------------------------------------------------
def sort_diarization_segments(
    segments: Sequence[DiarizationSegmentOutput],
) -> tuple[DiarizationSegmentOutput, ...]:
    """Sort segments by ``(start_ms, end_ms)`` — parity with the archived adapter."""
    return tuple(sorted(segments, key=lambda segment: (segment.start_ms, segment.end_ms)))


# ---------------------------------------------------------------------------
# Stems (used by Task 16B)
# ---------------------------------------------------------------------------
def map_stem_name(raw_name: object, name_map: Mapping[str, str]) -> str | None:
    """Map a provider artifact name to ``vocals`` / ``no_vocals``.

    Returns ``None`` when the raw name is not in ``name_map`` (the adapter skips
    unmapped artifacts, per the Section 2.10 fixed rule). Used by Task 16B's
    ``async_job`` stem port.
    """
    return name_map.get(str(raw_name))
