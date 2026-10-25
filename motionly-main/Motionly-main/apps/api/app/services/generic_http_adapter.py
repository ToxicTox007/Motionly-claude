"""The ``generic_http`` model-gateway adapter — Tier 1 slice (plan Task 15).

One :class:`GenericHttpAdapter` instance stands for one registry adapter record
(a base URL, auth and shared headers). Each route served by that adapter supplies
a :class:`GenericHttpRouteConfig` — the Section 2.10 ``mapping`` plus the route
options the normalizer reads (``label_aliases``) and the batch/size limits.

Tier 1 has exactly one consumer, ``vision.object_detection`` via
:class:`~app.services.model_gateway.ObjectDetectionRequest`. Section 2.10 also
allows synchronous diarization APIs; a ``DiarizationRequest`` is accepted only if
its output mapping falls out naturally — otherwise other request types are
rejected clearly with ``TypeError``.

Behaviour (Section 2.10 and the Task 17 "Objects" parity rows):

* one JSON request per call: ``request.method`` / ``request.path`` / ``request.body``;
* ``$map`` over ``input.images`` is bounded by the route ``max_batch`` and
  exceeding it is rejected before any request is sent;
* ``items`` locates the per-item result list; ``aligned_to_input`` requires one
  result per input image and a length mismatch is a malformed response;
* ``fields`` (``detections``/``label``/``score``/``box``) are read relative to
  each item; ``box_format`` is ``vertices_norm`` or ``xywh_norm``;
* ``item_error`` + ``item_error_codes`` give the per-item error class (an
  unlisted code is ``unavailable``); if every item fails the call raises that
  class, if some fail those items are empty and a secret-free warning is logged;
* ``error_map`` classifies the HTTP status; ``usage`` is ``count_items`` or
  ``count_ok_items`` and becomes ``ModelUsage.input_units``;
* the response is size-capped; request bodies, media and headers are never logged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import httpx

from app.services.inference_http import (
    AuthApplier,
    base_headers,
    build_client,
    exception_failure,
    malformed_failure,
    read_json_response,
)
from app.services.inference_normalization import (
    ImageDimensionError,
    NormalizationError,
    RawDetection,
    image_dimensions,
    normalize_detections,
)
from app.services.inference_templates import (
    MultipartBody,
    TemplateContext,
    TemplateError,
    TemplateMissing,
    evaluate_value,
    render_path,
    resolve_ref,
    resolve_relative_ref,
    validate_mapping,
)
from app.services.model_gateway import (
    AdapterResult,
    DetectedObjectOutput,
    EmbeddingsOutput,
    EmbeddingsRequest,
    ImageEmbeddingsRequest,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    TranscriptWordOutput,
    VisionOutput,
    VisionRequest,
)
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
)

logger = logging.getLogger(__name__)

ADAPTER_TYPE = "generic_http"


@dataclass(frozen=True)
class GenericHttpRouteConfig:
    """Per-route configuration for one ``generic_http`` route.

    ``mapping`` is the Section 2.10 ``generic_http`` mapping. It is validated at
    construction (before activation), so an invalid mapping fails loudly here
    rather than at call time.
    """

    mapping: Mapping[str, object]
    params: Mapping[str, object] = field(default_factory=dict)
    label_aliases: Mapping[str, str] = field(default_factory=dict)
    max_batch: int = 16
    max_request_bytes: int = 9 * 1024 * 1024
    max_results_default: int = 24

    def __post_init__(self) -> None:
        if self.max_batch < 1:
            raise ValueError("generic_http route max_batch must be positive")
        if self.max_request_bytes < 1:
            raise ValueError("generic_http route max_request_bytes must be positive")
        if self.max_results_default < 1:
            raise ValueError("generic_http route max_results_default must be positive")
        errors = validate_mapping(ADAPTER_TYPE, self.mapping)
        if errors:
            raise ValueError(
                "generic_http route mapping is invalid: " + "; ".join(errors)
            )


class GenericHttpAdapter:
    """A declarative HTTP/JSON adapter driven entirely by registry config."""

    def __init__(
        self,
        *,
        base_url: str,
        auth: AuthApplier,
        timeout_s: float,
        provider: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        routes: Mapping[str, GenericHttpRouteConfig] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("generic_http adapter base_url is required")
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._timeout_s = timeout_s
        self._provider = provider
        self._extra_headers = dict(extra_headers or {})
        self._transport = transport
        self._routes: dict[str, GenericHttpRouteConfig] = dict(routes or {})

    def register_route(self, route_id: str, config: GenericHttpRouteConfig) -> None:
        if not route_id.strip():
            raise ValueError("route_id cannot be empty")
        if route_id in self._routes:
            raise ValueError(f"generic_http route already configured: {route_id}")
        self._routes[route_id] = config

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        config = self._routes.get(route.route_id)
        if config is None:
            raise ValueError(
                f"generic_http adapter has no mapping for route {route.route_id!r}"
            )
        if isinstance(request, ObjectDetectionRequest):
            return self._invoke_object_detection(route=route, request=request, config=config)
        # Tier 3 typed mappings for non-OpenAI-compatible APIs (Task 28). The
        # ``output`` discriminator in the mapping selects the response shape.
        if isinstance(request, TranscriptionRequest):
            return self._invoke_transcription(route=route, request=request, config=config)
        if isinstance(request, VisionRequest):
            return self._invoke_caption(route=route, request=request, config=config)
        if isinstance(request, (EmbeddingsRequest, ImageEmbeddingsRequest)):
            return self._invoke_embeddings(route=route, request=request, config=config)
        raise TypeError(
            "generic_http adapter supports ObjectDetectionRequest, TranscriptionRequest, "
            f"VisionRequest and embeddings requests; got {type(request).__name__}"
        )

    # -- object detection ---------------------------------------------------
    def _invoke_object_detection(
        self,
        *,
        route: ModelRoute,
        request: ObjectDetectionRequest,
        config: GenericHttpRouteConfig,
    ) -> AdapterResult:
        mapping = config.mapping
        provider = self._provider or route.provider
        max_results = request.max_results or config.max_results_default

        # Reject an oversized batch before any request is sent (Section 2.10:
        # "$map ... bounded by max_batch; exceeding = rejected before send").
        if len(request.images) > config.max_batch:
            raise _rejected(
                provider,
                f"At most {config.max_batch} images per request.",
            )

        ctx = TemplateContext(
            input={
                "images": tuple(request.images),
                "max_results": max_results,
            },
            params=dict(config.params),
            vars={},
            max_batch=config.max_batch,
        )

        request_spec = mapping["request"]
        assert isinstance(request_spec, Mapping)  # validated at construction
        method = str(request_spec["method"]).upper()
        try:
            path = render_path(str(request_spec["path"]), ctx)
            body = (
                evaluate_value(request_spec.get("body"), ctx)
                if request_spec.get("body") is not None
                else None
            )
        except TemplateMissing as exc:
            raise malformed_failure(provider) from exc
        except TemplateError as exc:
            raise _rejected(provider, str(exc)) from exc

        payload = self._send(
            provider=provider,
            method=method,
            path=path,
            body=body,
            error_map=_str_map(mapping.get("error_map")),
            max_bytes=config.max_request_bytes,
        )

        items = self._extract_items(mapping, payload, provider)
        if _as_bool(mapping.get("aligned_to_input")) and len(items) != len(request.images):
            raise malformed_failure(provider)

        box_format = str(mapping.get("box_format") or "vertices_norm")
        # ``xyxy_px`` needs the source image dimensions (stdlib header parsing).
        # Boxes are aligned to input, so item i corresponds to image i.
        image_sizes: list[tuple[int, int] | None] = [None] * len(items)
        if box_format == "xyxy_px":
            if not _as_bool(mapping.get("aligned_to_input")):
                raise _rejected(provider, "xyxy_px requires aligned_to_input results.")
            try:
                sizes = [image_dimensions(image) for image in request.images]
            except ImageDimensionError as exc:
                raise _rejected(provider, str(exc)) from exc
            image_sizes = [
                sizes[index] if index < len(sizes) else None
                for index in range(len(items))
            ]

        detections, failures = self._parse_items(
            items=items,
            mapping=mapping,
            config=config,
            provider=provider,
            max_results=max_results,
            box_format=box_format,
            image_sizes=image_sizes,
        )

        if failures and len(failures) == len(items):
            # Every item failed: raise the (first) item's class.
            raise ProviderCallFailure(provider=provider, error_info=failures[0])
        if failures:
            logger.warning(
                "generic_http_item_errors",
                extra={
                    "route_id": route.route_id,
                    "failed": len(failures),
                    "items": len(items),
                },
            )

        usage_units = self._usage_units(
            mapping=mapping,
            payload=payload,
            provider=provider,
            total=len(items),
            ok=len(items) - len(failures),
        )
        return AdapterResult(
            output=ObjectDetectionOutput(
                detections=tuple(detections),
                raw_response={"items": len(items), "item_errors": len(failures)},
            ),
            usage=ModelUsage(input_units=usage_units),
            usage_reported=True,
        )

    # -- Tier 3 typed outputs (Task 28) ------------------------------------
    def _build_request(
        self,
        *,
        mapping: Mapping[str, object],
        ctx: TemplateContext,
        provider: str,
    ) -> tuple[str, str, object]:
        """Render the one request (method, path, body) from the mapping."""
        request_spec = mapping["request"]
        assert isinstance(request_spec, Mapping)
        method = str(request_spec["method"]).upper()
        try:
            path = render_path(str(request_spec["path"]), ctx)
            body = (
                evaluate_value(request_spec.get("body"), ctx)
                if request_spec.get("body") is not None
                else None
            )
        except TemplateMissing as exc:
            raise malformed_failure(provider) from exc
        except TemplateError as exc:
            raise _rejected(provider, str(exc)) from exc
        return method, path, body

    def _invoke_transcription(
        self,
        *,
        route: ModelRoute,
        request: TranscriptionRequest,
        config: GenericHttpRouteConfig,
    ) -> AdapterResult:
        provider = self._provider or route.provider
        mapping = config.mapping
        audio_bytes = request.audio_path.read_bytes()
        ctx = TemplateContext(
            input={
                "file": audio_bytes,
                "language": request.language,
                "prompt": request.prompt,
                "response_format": request.response_format,
            },
            params=dict(config.params),
            vars={},
            max_batch=config.max_batch,
        )
        method, path, body = self._build_request(mapping=mapping, ctx=ctx, provider=provider)
        payload = self._send(
            provider=provider,
            method=method,
            path=path,
            body=body,
            error_map=_str_map(mapping.get("error_map")),
            max_bytes=config.max_request_bytes,
        )
        fields = mapping.get("fields")
        assert isinstance(fields, Mapping)
        response_ctx = TemplateContext(response=payload)
        text = self._field(response_ctx, fields.get("text"))
        if not isinstance(text, str) or not text:
            raise malformed_failure(provider)
        language = self._field(response_ctx, fields.get("language"))
        time_unit = str(mapping.get("time_unit") or "seconds")
        words = self._parse_transcript_words(
            mapping=mapping, fields=fields, payload=payload, provider=provider, time_unit=time_unit
        )
        usage_units = self._usage_units(
            mapping=mapping, payload=payload, provider=provider, total=len(words), ok=len(words)
        )
        return AdapterResult(
            output=TranscriptionOutput(
                text=text,
                language=language if isinstance(language, str) else None,
                confidence=None,
                words=words,
                raw_response={"word_count": len(words)},
            ),
            usage=ModelUsage(input_units=usage_units),
            usage_reported=True,
        )

    def _parse_transcript_words(
        self,
        *,
        mapping: Mapping[str, object],
        fields: Mapping[str, object],
        payload: object,
        provider: str,
        time_unit: str,
    ) -> tuple[TranscriptWordOutput, ...]:
        words_path = fields.get("words")
        if words_path is None:
            return ()
        try:
            raw_words = resolve_ref(str(words_path), TemplateContext(response=payload))
        except TemplateMissing:
            return ()
        if raw_words is None:
            return ()
        if not isinstance(raw_words, Sequence) or isinstance(raw_words, str | bytes | bytearray):
            raise malformed_failure(provider)
        out: list[TranscriptWordOutput] = []
        for entry in raw_words:
            word_ctx = TemplateContext(response=None).with_item(entry)
            text = self._field(word_ctx, fields.get("word"))
            start = self._field(word_ctx, fields.get("start"))
            end = self._field(word_ctx, fields.get("end"))
            confidence = self._field(word_ctx, fields.get("confidence"))
            if not isinstance(text, str) or not text or start is None or end is None:
                continue
            try:
                start_ms = _to_ms(start, time_unit)
                end_ms = _to_ms(end, time_unit)
                conf = float(confidence) if confidence is not None else None
            except (TypeError, ValueError) as exc:
                raise malformed_failure(provider) from exc
            if end_ms < start_ms:
                end_ms = start_ms
            try:
                out.append(
                    TranscriptWordOutput(
                        text=text, start_ms=start_ms, end_ms=end_ms, confidence=conf
                    )
                )
            except ValueError as exc:
                raise malformed_failure(provider) from exc
        return tuple(out)

    def _invoke_caption(
        self,
        *,
        route: ModelRoute,
        request: VisionRequest,
        config: GenericHttpRouteConfig,
    ) -> AdapterResult:
        provider = self._provider or route.provider
        mapping = config.mapping
        ctx = TemplateContext(
            input={
                "prompt": request.prompt,
                "image_urls": tuple(request.image_urls),
                "max_output_units": request.max_output_units,
            },
            params=dict(config.params),
            vars={},
            max_batch=config.max_batch,
        )
        method, path, body = self._build_request(mapping=mapping, ctx=ctx, provider=provider)
        payload = self._send(
            provider=provider,
            method=method,
            path=path,
            body=body,
            error_map=_str_map(mapping.get("error_map")),
            max_bytes=config.max_request_bytes,
        )
        fields = mapping.get("fields")
        assert isinstance(fields, Mapping)
        caption = self._field(TemplateContext(response=payload), fields.get("caption"))
        if not isinstance(caption, str) or not caption.strip():
            raise malformed_failure(provider)
        usage_units = self._usage_units(
            mapping=mapping, payload=payload, provider=provider, total=1, ok=1
        )
        return AdapterResult(
            output=VisionOutput(content=caption, raw_response={}),
            usage=ModelUsage(input_units=usage_units),
            usage_reported=True,
        )

    def _invoke_embeddings(
        self,
        *,
        route: ModelRoute,
        request: EmbeddingsRequest | ImageEmbeddingsRequest,
        config: GenericHttpRouteConfig,
    ) -> AdapterResult:
        provider = self._provider or route.provider
        mapping = config.mapping
        if isinstance(request, EmbeddingsRequest):
            inputs: tuple[object, ...] = tuple(request.inputs)
            input_payload: dict[str, object] = {"inputs": inputs, "texts": inputs}
            count = len(request.inputs)
        else:
            images = tuple(request.images)
            input_payload = {"images": images, "inputs": images}
            count = len(request.images)
        if count > config.max_batch:
            raise _rejected(provider, f"At most {config.max_batch} inputs per request.")
        ctx = TemplateContext(
            input=input_payload,
            params=dict(config.params),
            vars={},
            max_batch=config.max_batch,
        )
        method, path, body = self._build_request(mapping=mapping, ctx=ctx, provider=provider)
        payload = self._send(
            provider=provider,
            method=method,
            path=path,
            body=body,
            error_map=_str_map(mapping.get("error_map")),
            max_bytes=config.max_request_bytes,
        )
        fields = mapping.get("fields")
        assert isinstance(fields, Mapping)
        vectors = self._parse_vectors(
            mapping=mapping, fields=fields, payload=payload, provider=provider
        )
        if _as_bool(mapping.get("aligned_to_input")) and len(vectors) != count:
            raise malformed_failure(provider)
        try:
            output = EmbeddingsOutput(vectors=vectors, raw_response={"count": len(vectors)})
        except ValueError as exc:
            raise malformed_failure(provider) from exc
        usage_units = self._usage_units(
            mapping=mapping, payload=payload, provider=provider, total=len(vectors), ok=len(vectors)
        )
        return AdapterResult(
            output=output,
            usage=ModelUsage(input_units=usage_units),
            usage_reported=True,
        )

    def _parse_vectors(
        self,
        *,
        mapping: Mapping[str, object],
        fields: Mapping[str, object],
        payload: object,
        provider: str,
    ) -> tuple[tuple[float, ...], ...]:
        items_path = mapping.get("items")
        if items_path is None:
            raise malformed_failure(provider)
        try:
            items = resolve_ref(str(items_path), TemplateContext(response=payload))
        except TemplateMissing as exc:
            raise malformed_failure(provider) from exc
        if not isinstance(items, Sequence) or isinstance(items, str | bytes | bytearray):
            raise malformed_failure(provider)
        vector_path = fields.get("vector")
        out: list[tuple[float, ...]] = []
        for entry in items:
            item_ctx = TemplateContext(response=None).with_item(entry)
            raw_vector = (
                self._field(item_ctx, vector_path) if vector_path is not None else entry
            )
            if not isinstance(raw_vector, Sequence) or isinstance(
                raw_vector, str | bytes | bytearray
            ):
                raise malformed_failure(provider)
            try:
                out.append(tuple(float(value) for value in raw_vector))
            except (TypeError, ValueError) as exc:
                raise malformed_failure(provider) from exc
        return tuple(out)

    def _extract_items(
        self, mapping: Mapping[str, object], payload: object, provider: str
    ) -> list[object]:
        items_path = str(mapping["items"])
        ctx = TemplateContext(response=payload)
        try:
            items = resolve_ref(items_path, ctx)
        except TemplateMissing as exc:
            raise malformed_failure(provider) from exc
        if not isinstance(items, Sequence) or isinstance(items, str | bytes | bytearray):
            raise malformed_failure(provider)
        return list(items)

    def _parse_items(
        self,
        *,
        items: Sequence[object],
        mapping: Mapping[str, object],
        config: GenericHttpRouteConfig,
        provider: str,
        max_results: int,
        box_format: str,
        image_sizes: Sequence[tuple[int, int] | None],
    ) -> tuple[list[tuple[DetectedObjectOutput, ...]], list[ProviderErrorInfo]]:
        fields = mapping.get("fields")
        assert isinstance(fields, Mapping)  # validated at construction
        item_error_path = mapping.get("item_error")
        item_error_codes = _str_map(mapping.get("item_error_codes")) or {}

        detections: list[tuple[DetectedObjectOutput, ...]] = []
        failures: list[ProviderErrorInfo] = []
        for index, item in enumerate(items):
            item_ctx = TemplateContext(response=None).with_item(item)
            error_info = self._item_error(
                item_ctx, item_error_path, item_error_codes, provider
            )
            if error_info is not None:
                failures.append(error_info)
                detections.append(())
                continue
            image_size = image_sizes[index] if index < len(image_sizes) else None
            detections.append(
                self._item_detections(
                    item_ctx=item_ctx,
                    fields=fields,
                    box_format=box_format,
                    config=config,
                    provider=provider,
                    max_results=max_results,
                    image_size=image_size,
                )
            )
        return detections, failures

    def _item_error(
        self,
        item_ctx: TemplateContext,
        item_error_path: object,
        item_error_codes: Mapping[str, str],
        provider: str,
    ) -> ProviderErrorInfo | None:
        if not item_error_path:
            return None
        try:
            code = resolve_relative_ref(str(item_error_path), item_ctx)
        except TemplateMissing:
            return None
        if code is None or code == "" or code == {}:
            return None
        klass = item_error_codes.get(str(code), "unavailable")
        if klass == "rejected":
            return ProviderErrorInfo(
                "provider_request_rejected",
                False,
                fallback_eligible=False,
                circuit_worthy=False,
                billable=False,
            )
        return ProviderErrorInfo("provider_unavailable", True, billable=False)

    def _item_detections(
        self,
        *,
        item_ctx: TemplateContext,
        fields: Mapping[str, object],
        box_format: str,
        config: GenericHttpRouteConfig,
        provider: str,
        max_results: int,
        image_size: tuple[int, int] | None = None,
    ) -> tuple[DetectedObjectOutput, ...]:
        detections_path = fields.get("detections")
        raw_list: Sequence[object]
        if detections_path is None:
            # The item itself is a single detection (flat-list shape).
            raw_list = [item_ctx.item]
        else:
            try:
                resolved = resolve_relative_ref(str(detections_path), item_ctx)
            except TemplateMissing:
                resolved = []
            if resolved is None:
                resolved = []
            if not isinstance(resolved, Sequence) or isinstance(
                resolved, str | bytes | bytearray
            ):
                raise malformed_failure(provider)
            raw_list = list(resolved)

        raw_detections: list[RawDetection] = []
        for entry in raw_list:
            detection_ctx = item_ctx.with_item(entry)
            raw_detections.append(
                RawDetection(
                    label=self._field(detection_ctx, fields.get("label")),
                    score=self._field(detection_ctx, fields.get("score")),
                    box=self._field(detection_ctx, fields.get("box")),
                )
            )
        try:
            return normalize_detections(
                raw_detections,
                box_format=box_format,
                max_results=max_results,
                label_aliases=config.label_aliases,
                image_size=image_size,
            )
        except NormalizationError as exc:
            raise malformed_failure(provider) from exc

    @staticmethod
    def _field(ctx: TemplateContext, path: object) -> object:
        if path is None:
            return None
        try:
            return resolve_relative_ref(str(path), ctx)
        except TemplateMissing:
            return None

    def _usage_units(
        self,
        *,
        mapping: Mapping[str, object],
        payload: object,
        provider: str,
        total: int,
        ok: int,
    ) -> int:
        mode = mapping.get("usage")
        if mode == "response_path":
            # Tier 3: usage taken from a response path (Task 28).
            usage_path = mapping.get("usage_path")
            if usage_path is None:
                raise malformed_failure(provider)
            try:
                raw = resolve_ref(str(usage_path), TemplateContext(response=payload))
            except TemplateMissing as exc:
                raise malformed_failure(provider) from exc
            try:
                units = int(raw)
            except (TypeError, ValueError) as exc:
                raise malformed_failure(provider) from exc
            if units < 0:
                raise malformed_failure(provider)
            return units
        if mode == "count_ok_items":
            return ok
        # Default and "count_items".
        return total

    # -- transport ----------------------------------------------------------
    def _send(
        self,
        *,
        provider: str,
        method: str,
        path: str,
        body: object,
        error_map: Mapping[str, str] | None,
        max_bytes: int,
    ) -> object:
        headers = base_headers(self._extra_headers)
        self._auth.apply(headers)
        url = f"{self._base_url}{path}"
        json_body: object | None = None
        multipart_data: Mapping[str, object] | None = None
        multipart_files: list[tuple[str, tuple[str | None, bytes, str | None]]] | None = None
        if isinstance(body, MultipartBody):
            # Multipart request body (Tier 3, Task 28). httpx builds the wire
            # body and the boundary; the engine never assembles it as a string.
            multipart_data = {key: _form_value(val) for key, val in body.data.items()}
            multipart_files = [
                (part.field, (part.filename, part.content, part.content_type))
                for part in body.files
            ]
        elif body is not None:
            json_body = body
        try:
            with build_client(
                timeout_s=self._timeout_s, transport=self._transport
            ) as client:
                response = client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    data=multipart_data,
                    files=multipart_files,
                )
        except ProviderCallFailure:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise exception_failure(provider, exc) from None
        payload = read_json_response(
            response,
            provider=provider,
            max_bytes=max_bytes,
            error_map=error_map,
        )
        if not isinstance(payload, Mapping | list):
            raise malformed_failure(provider)
        return payload


def _form_value(value: object) -> str:
    """Render a non-file multipart part value to a form string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return value if isinstance(value, str) else json.dumps(value)


def _to_ms(value: object, time_unit: str) -> int:
    """Convert a transcription timestamp to integer milliseconds."""
    number = float(value)  # type: ignore[arg-type]
    if time_unit == "milliseconds":
        return round(number)
    return round(number * 1000)


def _as_bool(value: object) -> bool:
    return value is True


def _str_map(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    return {str(key): str(val) for key, val in value.items()}


def _rejected(provider: str, message: str) -> ProviderCallFailure:
    return ProviderCallFailure(
        provider=provider,
        error_info=ProviderErrorInfo(
            "provider_request_rejected",
            False,
            fallback_eligible=False,
            circuit_worthy=False,
            billable=False,
        ),
        message=message,
    )
