from __future__ import annotations

import base64
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.services.model_gateway import (
    AdapterResult,
    AudioOutput,
    AudioRequest,
    EmbeddingsOutput,
    EmbeddingsRequest,
    GeneratedArtifact,
    GenerationOutput,
    GenerationRequest,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    PlanningOutput,
    PlanningRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    TranscriptWordOutput,
    VisionOutput,
    VisionRequest,
)
from app.services.provider_resilience import (
    ProviderCallFailure,
    ProviderErrorInfo,
    classify_provider_exception,
    classify_provider_status,
)

_MAX_MULTIMODAL_AUDIO_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class OpenAICompatibleAdapter:
    """Typed adapter for OpenAI-compatible APIs, including private custom endpoints."""

    base_url: str
    api_key: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        _validate_base_url(
            self.base_url,
            allow_insecure_http=self.allow_insecure_http,
        )
        if self.timeout_seconds <= 0:
            raise ValueError("adapter timeout_seconds must be positive")

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        if isinstance(request, PlanningRequest):
            return self._planning(route, request)
        if isinstance(request, TranscriptionRequest):
            return self._transcription(route, request)
        if isinstance(request, VisionRequest):
            return self._vision(route, request)
        if isinstance(request, AudioRequest):
            return self._audio(route, request)
        if isinstance(request, EmbeddingsRequest):
            return self._embeddings(route, request)
        if isinstance(request, GenerationRequest):
            return self._generation(route, request)
        raise TypeError(f"unsupported request type: {type(request).__name__}")

    def _planning(self, route: ModelRoute, request: PlanningRequest) -> AdapterResult:
        body: dict[str, object] = {
            "model": route.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": min(request.max_output_units, route.max_output_units),
        }
        if request.response_format is not None:
            body["response_format"] = dict(request.response_format)
        payload, request_id = self._request_json(
            route=route,
            operation="chat/completions",
            json=body,
        )
        content = _chat_content(payload, route)
        return AdapterResult(
            output=PlanningOutput(content=content, raw_response=payload),
            usage=_usage(payload),
            usage_reported=_usage_reported(payload),
            provider_request_id=request_id,
        )

    def _transcription(
        self,
        route: ModelRoute,
        request: TranscriptionRequest,
    ) -> AdapterResult:
        data: dict[str, object] = {
            "model": route.model,
            "response_format": request.response_format,
            "timestamp_granularities[]": "word",
        }
        if request.language:
            data["language"] = request.language
        if request.prompt:
            data["prompt"] = request.prompt
        try:
            with request.audio_path.open("rb") as audio_file:
                payload, request_id = self._request_json(
                    route=route,
                    operation="audio/transcriptions",
                    data=data,
                    files={
                        "file": (
                            request.audio_path.name,
                            audio_file,
                            _audio_content_type(request.audio_path),
                        )
                    },
                )
        except OSError as exc:
            raise ProviderCallFailure(
                provider=route.provider,
                error_info=ProviderErrorInfo("provider_request_rejected", False),
            ) from exc
        words: list[TranscriptWordOutput] = []
        raw_words = payload.get("words")
        previous_start_ms = -1
        if isinstance(raw_words, list):
            for item in raw_words:
                if not isinstance(item, Mapping):
                    raise _malformed(route)
                text = str(item.get("word") or item.get("text") or "").strip()
                if not text or item.get("start") is None or item.get("end") is None:
                    raise _malformed(route)
                try:
                    start_ms = round(float(item["start"]) * 1000)
                    end_ms = round(float(item["end"]) * 1000)
                except (TypeError, ValueError) as exc:
                    raise _malformed(route) from exc
                confidence = _optional_float(item.get("confidence"))
                if (
                    start_ms < 0
                    or end_ms < start_ms
                    or start_ms < previous_start_ms
                    or (confidence is not None and not 0 <= confidence <= 1)
                ):
                    raise _malformed(route)
                previous_start_ms = start_ms
                words.append(
                    TranscriptWordOutput(
                        text=text,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        confidence=confidence,
                    )
                )
        return AdapterResult(
            output=TranscriptionOutput(
                text=str(payload.get("text") or ""),
                language=(str(payload["language"]) if payload.get("language") else None),
                confidence=_optional_float(payload.get("confidence")),
                words=tuple(words),
                raw_response=payload,
            ),
            usage=_usage(payload),
            usage_reported=_usage_reported(payload),
            provider_request_id=request_id,
        )

    def _vision(self, route: ModelRoute, request: VisionRequest) -> AdapterResult:
        content: list[dict[str, object]] = [{"type": "text", "text": request.prompt}]
        content.extend(
            {"type": "image_url", "image_url": {"url": image_url}}
            for image_url in request.image_urls
        )
        payload, request_id = self._request_json(
            route=route,
            operation="chat/completions",
            json={
                "model": route.model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": min(request.max_output_units, route.max_output_units),
            },
        )
        return AdapterResult(
            output=VisionOutput(content=_chat_content(payload, route), raw_response=payload),
            usage=_usage(payload),
            usage_reported=_usage_reported(payload),
            provider_request_id=request_id,
        )

    def _audio(self, route: ModelRoute, request: AudioRequest) -> AdapterResult:
        encoded = _encode_audio(request.audio_path)
        payload, request_id = self._request_json(
            route=route,
            operation="chat/completions",
            json={
                "model": route.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": request.prompt},
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": encoded,
                                    "format": request.audio_format,
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": min(request.max_output_units, route.max_output_units),
            },
        )
        return AdapterResult(
            output=AudioOutput(content=_chat_content(payload, route), raw_response=payload),
            usage=_usage(payload),
            usage_reported=_usage_reported(payload),
            provider_request_id=request_id,
        )

    def _embeddings(self, route: ModelRoute, request: EmbeddingsRequest) -> AdapterResult:
        body: dict[str, object] = {"model": route.model, "input": list(request.inputs)}
        if request.dimensions is not None:
            body["dimensions"] = request.dimensions
        payload, request_id = self._request_json(
            route=route,
            operation="embeddings",
            json=body,
        )
        raw_data = payload.get("data")
        if not isinstance(raw_data, list) or len(raw_data) != len(request.inputs):
            raise _malformed(route)
        vectors_by_index: list[tuple[int, tuple[float, ...]]] = []
        seen_indices: set[int] = set()
        vector_size: int | None = None
        for default_index, item in enumerate(raw_data):
            if not isinstance(item, Mapping) or not isinstance(item.get("embedding"), list):
                raise _malformed(route)
            try:
                vector = tuple(float(value) for value in item["embedding"])
                index = int(item.get("index", default_index))
            except (TypeError, ValueError) as exc:
                raise _malformed(route) from exc
            if (
                not vector
                or any(not math.isfinite(value) for value in vector)
                or index < 0
                or index >= len(request.inputs)
                or index in seen_indices
                or (request.dimensions is not None and len(vector) != request.dimensions)
                or (vector_size is not None and len(vector) != vector_size)
            ):
                raise _malformed(route)
            seen_indices.add(index)
            vector_size = len(vector)
            vectors_by_index.append((index, vector))
        if seen_indices != set(range(len(request.inputs))):
            raise _malformed(route)
        vectors = tuple(vector for _index, vector in sorted(vectors_by_index))
        return AdapterResult(
            output=EmbeddingsOutput(vectors=vectors, raw_response=payload),
            usage=_usage(payload),
            usage_reported=_usage_reported(payload),
            provider_request_id=request_id,
        )

    def _generation(self, route: ModelRoute, request: GenerationRequest) -> AdapterResult:
        body: dict[str, object] = {
            "model": route.model,
            "prompt": request.prompt,
            "size": request.size,
            "n": request.count,
            "response_format": request.response_format,
        }
        if request.image_urls:
            # Custom OpenAI-compatible media backends may accept conditioning images.
            body["input_images"] = list(request.image_urls)
        payload, request_id = self._request_json(
            route=route,
            operation="images/generations",
            json=body,
        )
        raw_data = payload.get("data")
        if not isinstance(raw_data, list) or len(raw_data) != request.count:
            raise _malformed(route)
        artifacts = tuple(
            GeneratedArtifact(
                url=str(item["url"]) if isinstance(item, Mapping) and item.get("url") else None,
                base64_data=(
                    str(item["b64_json"])
                    if isinstance(item, Mapping) and item.get("b64_json")
                    else None
                ),
                revised_prompt=(
                    str(item["revised_prompt"])
                    if isinstance(item, Mapping) and item.get("revised_prompt")
                    else None
                ),
            )
            for item in raw_data
            if isinstance(item, Mapping)
        )
        if not artifacts or any(not item.url and not item.base64_data for item in artifacts):
            raise _malformed(route)
        return AdapterResult(
            output=GenerationOutput(
                artifacts=artifacts,
                operation_id=str(payload["id"]) if payload.get("id") else None,
                raw_response=payload,
            ),
            usage=_usage(payload),
            usage_reported=_usage_reported(payload),
            provider_request_id=request_id,
        )

    def _request_json(
        self,
        *,
        route: ModelRoute,
        operation: str,
        json: Mapping[str, object] | None = None,
        data: Mapping[str, object] | None = None,
        files: Mapping[str, object] | None = None,
    ) -> tuple[dict[str, object], str | None]:
        headers = self._headers()
        url = self._operation_url(route=route, operation=operation)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    self.timeout_seconds,
                    connect=min(10.0, self.timeout_seconds),
                ),
                trust_env=False,
            ) as client:
                response = client.request(
                    "POST",
                    url,
                    headers=headers,
                    json=dict(json) if json is not None else None,
                    data=dict(data) if data is not None else None,
                    files=files,
                )
            if response.status_code >= 400:
                raise ProviderCallFailure(
                    provider=route.provider,
                    error_info=classify_provider_status(response.status_code),
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise _malformed(route) from exc
        except ProviderCallFailure:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            raise ProviderCallFailure(
                provider=route.provider,
                error_info=classify_provider_exception(exc),
            ) from None
        if not isinstance(payload, dict):
            raise _malformed(route)
        request_id = response.headers.get("x-request-id") or response.headers.get("apim-request-id")
        return payload, request_id

    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json", **dict(self.extra_headers)}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _operation_url(self, *, route: ModelRoute, operation: str) -> str:
        del route
        return f"{self.base_url.rstrip('/')}/{operation.lstrip('/')}"


@dataclass(frozen=True)
class AzureOpenAIAdapter(OpenAICompatibleAdapter):
    """Azure OpenAI adapter using deployment-scoped data-plane URLs."""

    api_version: str = "2025-01-01-preview"

    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json", **dict(self.extra_headers)}
        if self.api_key:
            headers["api-key"] = self.api_key
        return headers

    def _operation_url(self, *, route: ModelRoute, operation: str) -> str:
        deployment = quote(route.model, safe="")
        return (
            f"{self.base_url.rstrip('/')}/openai/deployments/{deployment}/{operation.lstrip('/')}"
            f"?api-version={quote(self.api_version, safe='-.')}"
        )


def _validate_base_url(value: str, *, allow_insecure_http: bool) -> None:
    parsed = urlsplit(value)
    allowed_schemes = {"http", "https"} if allow_insecure_http else {"https"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise ValueError("model adapter base_url must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("model adapter base_url cannot contain credentials, query, or fragment")


def _chat_content(payload: Mapping[str, object], route: ModelRoute) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise _malformed(route)
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise _malformed(route)
    content = message.get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, Mapping) and item.get("text")
        ]
        if parts:
            return "".join(parts)
    raise _malformed(route)


def _usage_reported(payload: Mapping[str, object]) -> bool:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return False
    recognized = (
        "input_tokens",
        "prompt_tokens",
        "input_units",
        "output_tokens",
        "completion_tokens",
        "output_units",
        "total_tokens",
        "total_units",
    )
    for key in recognized:
        if key not in usage:
            continue
        try:
            if int(usage[key]) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _usage(payload: Mapping[str, object]) -> ModelUsage:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return ModelUsage()
    input_units = _safe_int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("input_units")
        or 0
    )
    output_units = _safe_int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("output_units")
        or 0
    )
    total_units = _safe_int(usage.get("total_tokens") or usage.get("total_units") or 0)
    if total_units and not output_units:
        output_units = max(0, total_units - input_units)
    return ModelUsage(input_units=input_units, output_units=output_units)


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _audio_content_type(path: Path) -> str:
    return {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }.get(path.suffix.lower(), "audio/wav")


def _encode_audio(path: Path) -> str:
    try:
        size = path.stat().st_size
        if size > _MAX_MULTIMODAL_AUDIO_BYTES:
            raise ProviderCallFailure(
                provider="model_gateway",
                error_info=ProviderErrorInfo("provider_request_rejected", False),
            )
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except ProviderCallFailure:
        raise
    except OSError as exc:
        raise ProviderCallFailure(
            provider="model_gateway",
            error_info=ProviderErrorInfo("provider_request_rejected", False),
        ) from exc


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
