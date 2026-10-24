"""Object localisation for visual keyframes through Google Cloud Vision.

Uses the synchronous ``images:annotate`` method with the ``OBJECT_LOCALIZATION``
feature. Per Google's data-usage FAQ, images in these online requests are
processed in memory and not persisted, and are not used for training. The API key
is sent in the ``x-goog-api-key`` header (never in the URL) and should be
restricted to the Vision API in the Google Cloud console.

Labels are lower-cased, and a few Google names are mapped to the COCO names the
GPU path (YOLO) emits, so downstream rules keep working (for example
``Mobile phone`` -> ``cell phone``, ``Television`` -> ``tv``).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from app.services.model_gateway import (
    AdapterResult,
    DetectedObjectOutput,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
)
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
    classify_provider_status,
)

logger = logging.getLogger(__name__)

GOOGLE_VISION_BASE_URL = "https://vision.googleapis.com/v1"
GOOGLE_VISION_ENDPOINT_HOST = "vision.googleapis.com"
# System limits for images:annotate (https://cloud.google.com/vision/quotas).
GOOGLE_VISION_MAX_IMAGES_PER_REQUEST = 16
GOOGLE_VISION_MAX_REQUEST_BYTES = 10 * 1024 * 1024
GOOGLE_VISION_MODELS = ("builtin/stable", "builtin/latest")

# Google object names -> the COCO names the local YOLO path produces.
LABEL_ALIASES = {
    "mobile phone": "cell phone",
    "television": "tv",
    "computer monitor": "monitor",
    "computer keyboard": "keyboard",
    "man": "person",
    "woman": "person",
    "boy": "person",
    "girl": "person",
    "human": "person",
    "human face": "face",
    "human head": "head",
}
# google.rpc.Code values that mean "this image is bad", not "the service is down".
_PER_IMAGE_REJECTION_CODES = {3, 9, 11}  # INVALID_ARGUMENT, FAILED_PRECONDITION, OUT_OF_RANGE


def normalize_label(name: str) -> str:
    label = " ".join(str(name).lower().split())
    return LABEL_ALIASES.get(label, label)[:80]


class GoogleVisionObjectsAdapter:
    """Model-gateway adapter for ``vision.object_detection`` on Cloud Vision."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "builtin/stable",
        base_url: str = GOOGLE_VISION_BASE_URL,
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Google Cloud Vision API key is required")
        if model not in GOOGLE_VISION_MODELS:
            raise ValueError(f"Google Cloud Vision model must be one of {GOOGLE_VISION_MODELS}")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Google Cloud Vision base_url must be an absolute HTTPS URL")
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        if not isinstance(request, ObjectDetectionRequest):
            raise TypeError("Google Cloud Vision adapter requires ObjectDetectionRequest")
        if len(request.images) > GOOGLE_VISION_MAX_IMAGES_PER_REQUEST:
            raise _rejected(
                f"At most {GOOGLE_VISION_MAX_IMAGES_PER_REQUEST} images per request."
            )
        features = [
            {
                "type": "OBJECT_LOCALIZATION",
                "maxResults": request.max_results,
                "model": self.model,
            }
        ]
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(image).decode("ascii")},
                    "features": features,
                }
                for image in request.images
            ]
        }
        payload = self._post(route, body)
        responses = payload.get("responses")
        if not isinstance(responses, list) or len(responses) != len(request.images):
            raise _malformed(route)
        detections: list[tuple[DetectedObjectOutput, ...]] = []
        image_errors: list[Mapping[str, object]] = []
        for response in responses:
            if not isinstance(response, Mapping):
                raise _malformed(route)
            error = response.get("error")
            if isinstance(error, Mapping) and error:
                image_errors.append(error)
                detections.append(())
                continue
            detections.append(
                _parse_annotations(
                    response.get("localizedObjectAnnotations") or [],
                    route=route,
                    max_results=request.max_results,
                )
            )
        if image_errors and len(image_errors) == len(request.images):
            raise _image_error(route, image_errors[0])
        if image_errors:
            logger.warning(
                "google_vision_image_errors",
                extra={"failed": len(image_errors), "images": len(request.images)},
            )
        succeeded = len(request.images) - len(image_errors)
        return AdapterResult(
            output=ObjectDetectionOutput(
                detections=tuple(detections),
                raw_response={"model": self.model, "image_errors": len(image_errors)},
            ),
            # Only images that were annotated are billable.
            usage=ModelUsage(input_units=succeeded),
        )

    def _post(self, route: ModelRoute, body: Mapping[str, object]) -> dict[str, object]:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._timeout, connect=min(10.0, self._timeout)),
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = client.post(
                    f"{self._base_url}/images:annotate",
                    json=dict(body),
                    headers={
                        "accept": "application/json",
                        "x-goog-api-key": self._api_key,
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderCallFailure(
                provider=route.provider,
                error_info=classify_provider_exception(exc),
            ) from None
        if response.status_code >= 400:
            raise ProviderCallFailure(
                provider=route.provider,
                error_info=classify_provider_status(response.status_code),
            )
        try:
            payload = response.json()
        except ValueError:
            raise _malformed(route) from None
        if not isinstance(payload, dict):
            raise _malformed(route)
        return payload


def _parse_annotations(
    annotations: object, *, route: ModelRoute, max_results: int
) -> tuple[DetectedObjectOutput, ...]:
    if not isinstance(annotations, list):
        raise _malformed(route)
    objects: list[DetectedObjectOutput] = []
    for annotation in annotations:
        if not isinstance(annotation, Mapping):
            raise _malformed(route)
        label = normalize_label(str(annotation.get("name") or ""))
        if not label:
            continue
        try:
            score = float(annotation.get("score") or 0.0)
            box = _box(annotation.get("boundingPoly"))
        except (TypeError, ValueError) as exc:
            raise _malformed(route) from exc
        if box is None:
            continue
        x, y, width, height = box
        objects.append(
            DetectedObjectOutput(
                label=label,
                confidence=round(_clamp(score), 3),
                x=x,
                y=y,
                width=width,
                height=height,
            )
        )
    objects.sort(key=lambda item: item.confidence, reverse=True)
    return tuple(objects[:max_results])


def _box(polygon: object) -> tuple[float, float, float, float] | None:
    """Axis-aligned box from normalised vertices (omitted coordinates are 0)."""
    vertices = polygon.get("normalizedVertices") if isinstance(polygon, Mapping) else None
    if not isinstance(vertices, Sequence) or not vertices:
        return None
    xs = [_clamp(float(vertex.get("x") or 0.0)) for vertex in vertices]
    ys = [_clamp(float(vertex.get("y") or 0.0)) for vertex in vertices]
    x, y = min(xs), min(ys)
    return round(x, 4), round(y, 4), round(max(xs) - x, 4), round(max(ys) - y, 4)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _image_error(route: ModelRoute, error: Mapping[str, object]) -> ProviderCallFailure:
    try:
        code = int(error.get("code") or 0)
    except (TypeError, ValueError):
        code = 0
    if code in _PER_IMAGE_REJECTION_CODES:
        return _rejected("Google Cloud Vision rejected every image in the request.")
    return ProviderCallFailure(
        provider=route.provider,
        error_info=ProviderErrorInfo("provider_unavailable", True, billable=False),
    )


def _malformed(route: ModelRoute) -> ProviderCallFailure:
    return ProviderCallFailure(
        provider=route.provider,
        error_info=ProviderErrorInfo(
            "provider_malformed_response",
            False,
            fallback_eligible=True,
            circuit_worthy=True,
        ),
    )


def _rejected(message: str) -> ProviderCallFailure:
    return ProviderCallFailure(
        provider="google_vision",
        error_info=ProviderErrorInfo(
            "provider_request_rejected",
            False,
            fallback_eligible=False,
            circuit_worthy=False,
            billable=False,
        ),
        message=message,
    )
