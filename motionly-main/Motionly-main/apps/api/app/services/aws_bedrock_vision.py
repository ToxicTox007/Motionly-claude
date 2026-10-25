"""Converse object-detection helpers for the AWS Bedrock adapter (spec item B).

These are *pure* helpers — no I/O, no network, no transport. They turn encoded
image bytes into an Amazon Bedrock Converse request body and turn the model's
text answer back into Motionly's typed :class:`DetectedObjectOutput` detections.

Amazon Nova Lite / Nova Pro are trained to emit bounding boxes on a ``[0, 1000)``
scale (see the Amazon Nova "Image understanding" user guide, *Bounding box
detection*). :data:`DETECTION_PROMPT` asks the model for that convention and
:func:`parse_detection_text` rescales the corners to the ``[0, 1]`` normalized
box format Motionly's :func:`normalize_detections` expects (``box_format``
``"xyxy_norm"``), so every provider converges on the same typed output.

The integrate stage (``aws_bedrock_adapter``) calls these; this module never
imports the adapter, so it stays a leaf with no transport dependency.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping, Sequence
from urllib.parse import unquote_to_bytes

from app.services.inference_normalization import (
    NormalizationError,
    RawDetection,
    normalize_detections,
)
from app.services.model_gateway import DetectedObjectOutput

# Amazon Nova emits bounding boxes on a [0, 1000) scale; we divide by this to
# reach the normalized [0, 1] corners that ``normalize_detections`` expects.
NOVA_BOX_SCALE = 1000.0

# Motionly-owned detection instruction. It asks the model for ONLY a JSON array
# so the answer parses deterministically, and pins the Amazon Nova box
# convention (0-1000 scale) so :func:`parse_detection_text` can rescale it.
DETECTION_PROMPT = (
    "Detect the distinct, visible objects in this image. "
    "Respond with ONLY a JSON array and nothing else — no prose, no code "
    "fences, no explanation. Each array element must be an object with exactly "
    'these keys: "label" (a short lowercase noun for the object), "box" (an '
    "array of four numbers [x1, y1, x2, y2] giving the top-left and "
    "bottom-right corners on a 0-1000 scale, where 0 is the left/top edge and "
    '1000 is the right/bottom edge of the image), and "score" (your confidence '
    "from 0 to 1). Return an empty array [] if you see no objects."
)


class DetectionParseError(ValueError):
    """The model's detection text could not be parsed into any detections.

    The adapter treats this as a per-image parse failure (empty tuple + warning)
    and, when every image fails, as a malformed provider response.
    """


# ---------------------------------------------------------------------------
# Image format sniffing and Converse image blocks
# ---------------------------------------------------------------------------
def sniff_image_format(data: object) -> str | None:
    """Return the Bedrock image ``format`` for ``data`` or ``None`` if unknown.

    Recognizes the four formats Bedrock Converse accepts via magic bytes:
    ``"jpeg"``, ``"png"``, ``"gif"`` and ``"webp"``. Pure header inspection — no
    decoding, no Pillow.
    """
    if not isinstance(data, bytes | bytearray):
        return None
    head = bytes(data)
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def image_block_from_bytes(data: bytes) -> Mapping[str, object]:
    """Build a Converse ``image`` content block from raw encoded image bytes.

    The block carries the sniffed format and the base64-encoded source bytes, as
    Bedrock Converse requires (``{"image": {"format": fmt, "source": {"bytes":
    "<base64>"}}}``). Raises :class:`ValueError` when the format is unknown.
    """
    fmt = sniff_image_format(data)
    if fmt is None:
        raise ValueError("unsupported or unrecognized image format")
    encoded = base64.b64encode(bytes(data)).decode("ascii")
    return {"image": {"format": fmt, "source": {"bytes": encoded}}}


def image_block_from_data_url(url: object) -> Mapping[str, object] | None:
    """Build an image block from a ``data:`` URL, or ``None`` for other URLs.

    Only ``data:`` URLs with a base64 or percent-encoded payload are decoded; any
    other URL (``https://`` ...) returns ``None`` so the caller can keep its
    existing by-reference behavior. A malformed ``data:`` URL or an unknown image
    format raises :class:`ValueError`.
    """
    if not isinstance(url, str) or not url.lower().startswith("data:"):
        return None
    header, _, payload = url[len("data:") :].partition(",")
    if not _:
        raise ValueError("data URL is missing a payload separator")
    if header.rsplit(";", 1)[-1].lower() == "base64":
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("data URL has an invalid base64 payload") from exc
    else:
        data = unquote_to_bytes(payload)
    return image_block_from_bytes(data)


def detection_converse_body(
    image: bytes,
    *,
    max_results: int,
    prompt: str = DETECTION_PROMPT,
) -> Mapping[str, object]:
    """Build the Converse request body for one image's object detection.

    The single user message carries the image block followed by the detection
    instruction text. ``inferenceConfig`` uses ``temperature`` 0 for a stable,
    deterministic answer and a token budget scaled to ``max_results`` so the
    model has room to list every requested object.
    """
    image_block = image_block_from_bytes(image)
    text = f"{prompt}\nReturn at most {max_results} objects."
    max_tokens = max(256, min(4096, 48 * max_results + 128))
    return {
        "messages": [
            {"role": "user", "content": [image_block, {"text": text}]},
        ],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0},
    }


# ---------------------------------------------------------------------------
# Parsing the model's detection text
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_array(text: str) -> list[object]:
    """Pull the JSON array out of ``text`` tolerating fences and surrounding prose."""
    candidates: list[str] = []
    for match in _FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())
    stripped = text.strip()
    candidates.append(stripped)
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, Mapping):
            for key in ("objects", "detections", "results"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return value
    raise DetectionParseError("no JSON array found in detection text")


def _raw_detection(item: object, default_score: float) -> RawDetection | None:
    """Turn one parsed element into a :class:`RawDetection`, or ``None`` to skip it."""
    if not isinstance(item, Mapping):
        return None
    box = item.get("box", item.get("bbox", item.get("bounding_box")))
    if not isinstance(box, Sequence) or isinstance(box, str | bytes | bytearray):
        return None
    if len(box) != 4:
        return None
    try:
        scaled = [float(coord) / NOVA_BOX_SCALE for coord in box]
    except (TypeError, ValueError):
        return None
    label = item.get("label", item.get("name", item.get("object")))
    score = item.get("score", item.get("confidence"))
    if score is None:
        score = default_score
    return RawDetection(label=label, score=score, box=scaled)


def parse_detection_text(
    text: object,
    *,
    max_results: int,
    label_aliases: Mapping[str, str] | None = None,
    default_score: float = 0.5,
) -> tuple[DetectedObjectOutput, ...]:
    """Parse a model detection answer into normalized :class:`DetectedObjectOutput`s.

    Tolerates ```` ```json ```` fences and surrounding prose, accepts a bare JSON
    array or an object wrapping one under ``objects``/``detections``/``results``,
    scales the Amazon Nova ``0-1000`` corners down to ``[0, 1]`` and hands the
    result to :func:`normalize_detections` with ``box_format="xyxy_norm"`` (which
    lowercases/aliases labels, clamps, sorts by confidence and cuts to
    ``max_results``). A detection missing a usable box or score is skipped; an
    answer with no parsable JSON array raises :class:`DetectionParseError`.
    """
    if not isinstance(text, str) or not text.strip():
        raise DetectionParseError("detection text is empty")
    elements = _extract_json_array(text)
    raw: list[RawDetection] = []
    for element in elements:
        detection = _raw_detection(element, default_score)
        if detection is not None:
            raw.append(detection)
    try:
        return normalize_detections(
            raw,
            box_format="xyxy_norm",
            max_results=max_results,
            label_aliases=label_aliases,
        )
    except NormalizationError as exc:
        raise DetectionParseError(str(exc)) from exc
