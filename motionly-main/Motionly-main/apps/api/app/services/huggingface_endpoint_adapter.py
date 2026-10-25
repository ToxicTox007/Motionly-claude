"""Hugging Face Inference Endpoint adapter (plan Task 24, Tier 3).

A ``huggingface_endpoint`` registry adapter serves a dedicated Hugging Face
Inference Endpoint. The endpoint's *serving container* decides the wire protocol,
so this adapter has three fixed modes, each a typed mapping onto a Motionly
capability contract:

* ``tgi`` — Text Generation Inference's OpenAI-compatible chat route
  (``POST /v1/chat/completions``) for ``planning.chat``.
* ``tei`` — Text Embeddings Inference (``POST /embed``) for ``embeddings.text``.
* ``pipeline`` — the generic pipeline route (``POST /``), whose response shape
  depends on the model's ``pipeline_tag``:
    - ``image-to-text`` captions -> ``vision.caption``
    - ``automatic-speech-recognition`` with word timestamps -> transcription
    - ``audio-classification`` (emotion / sound tagging) -> sound classification
    - ``feature-extraction`` -> embeddings

Each mode's response mapping is fixed and schema-validated (plan Task 24
"each a fixed typed mapping ... typed schema validation"). Nothing here is
template-driven: the shapes are code, so they cannot drift into the frozen
Section 2.10 grammar.

Auth is bearer only, applied through :class:`app.services.inference_http.AuthApplier`
(plan Task 24 "bearer auth"; the vendor's endpoints only accept a bearer token).
The resolved token is redactor-registered by the auth strategy, never here.

"Hugging Face" and "TGI"/"TEI" may be named in this adapter module (they are not
on the Task 2 scanner's audited vendor list); the Motionly core never names them.
"""

from __future__ import annotations

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
from app.services.model_gateway import (
    AdapterResult,
    EmbeddingsOutput,
    EmbeddingsRequest,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    PlanningOutput,
    PlanningRequest,
    SoundClassificationOutput,
    SoundClassificationRequest,
    SoundLabelOutput,
    TranscriptionOutput,
    TranscriptionRequest,
    TranscriptWordOutput,
    VisionOutput,
    VisionRequest,
)

ADAPTER_TYPE = "huggingface_endpoint"

# The three serving modes (plan Task 24). ``pipeline`` dispatches by the Motionly
# request type, since one pipeline endpoint serves exactly one task.
ENDPOINT_MODES: tuple[str, ...] = ("tgi", "tei", "pipeline")

# The pipeline tasks ``pipeline`` mode supports, each with a fixed response shape.
PIPELINE_TASKS: tuple[str, ...] = (
    "image-to-text",
    "automatic-speech-recognition",
    "audio-classification",
    "feature-extraction",
)

_DEFAULT_TIMEOUT_S = 60.0
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class HuggingFaceConfigError(ValueError):
    """An endpoint adapter configuration is invalid (never carries a secret)."""


@dataclass(frozen=True)
class HuggingFaceEndpointConfig:
    """Configuration for one ``huggingface_endpoint`` adapter record.

    * ``base_url`` — the endpoint URL (an absolute HTTPS URL for a hosted
      endpoint; an ``http`` URL is allowed only for a ``network: private``
      self-hosted container, which the registry validator enforces, not here).
    * ``mode`` — one of :data:`ENDPOINT_MODES`.
    * ``pipeline_task`` — required and valid only when ``mode == "pipeline"``.
    * ``timeout_s`` — per-request timeout.
    * ``extra_headers`` — static headers merged under the adapter's own.
    """

    base_url: str
    mode: str
    pipeline_task: str | None = None
    timeout_s: float = _DEFAULT_TIMEOUT_S
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        errors = validate_huggingface_config(
            base_url=self.base_url,
            mode=self.mode,
            pipeline_task=self.pipeline_task,
            timeout_s=self.timeout_s,
        )
        if errors:
            raise HuggingFaceConfigError("; ".join(errors))


def validate_huggingface_config(
    *,
    base_url: str,
    mode: str,
    pipeline_task: str | None,
    timeout_s: float,
) -> list[str]:
    """Validate a ``huggingface_endpoint`` config; return human-readable errors.

    The registry/runtime wave calls this before activating a route (plan Task 24
    "a validation function"). It never resolves a secret and never echoes one.
    """
    errors: list[str] = []
    if not base_url or "://" not in base_url:
        errors.append("base_url must be an absolute URL")
    if mode not in ENDPOINT_MODES:
        errors.append(f"mode must be one of {list(ENDPOINT_MODES)}")
    if mode == "pipeline":
        if pipeline_task is None:
            errors.append("pipeline mode requires pipeline_task")
        elif pipeline_task not in PIPELINE_TASKS:
            errors.append(f"pipeline_task must be one of {list(PIPELINE_TASKS)}")
    elif pipeline_task is not None:
        errors.append("pipeline_task is only valid for pipeline mode")
    if not (
        isinstance(timeout_s, (int, float))
        and not isinstance(timeout_s, bool)
        and timeout_s > 0
    ):
        errors.append("timeout_s must be a positive number")
    return errors


class HuggingFaceEndpointAdapter:
    """A typed, config-driven Hugging Face Inference Endpoint adapter."""

    def __init__(
        self,
        *,
        config: HuggingFaceEndpointConfig,
        auth: AuthApplier,
        provider: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._auth = auth
        self._provider = provider
        self._transport = transport

    # -- dispatch -----------------------------------------------------------
    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        provider = self._provider or route.provider
        mode = self._config.mode
        if mode == "tgi":
            if isinstance(request, PlanningRequest):
                return self._invoke_tgi_chat(provider, request)
            raise TypeError("tgi mode serves PlanningRequest (planning.chat)")
        if mode == "tei":
            if isinstance(request, EmbeddingsRequest):
                return self._invoke_tei(provider, request)
            raise TypeError("tei mode serves EmbeddingsRequest (embeddings.text)")
        # pipeline mode: the configured task fixes the shape.
        return self._invoke_pipeline(provider, request)

    # -- tgi (chat) ---------------------------------------------------------
    def _invoke_tgi_chat(self, provider: str, request: PlanningRequest) -> AdapterResult:
        body: dict[str, object] = {
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": request.max_output_units,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.response_format is not None:
            body["response_format"] = dict(request.response_format)
        payload = self._post("/v1/chat/completions", body, provider)
        content = _dig(payload, "choices", 0, "message", "content")
        if not isinstance(content, str) or not content:
            raise malformed_failure(provider)
        usage = _openai_usage(payload)
        return AdapterResult(
            output=PlanningOutput(content=content, raw_response={"mode": "tgi"}),
            usage=usage if usage is not None else ModelUsage(),
            usage_reported=usage is not None,
        )

    # -- tei (text embeddings) ---------------------------------------------
    def _invoke_tei(self, provider: str, request: EmbeddingsRequest) -> AdapterResult:
        # TEI accepts a string or a list of strings and returns a list of vectors.
        payload = self._post("/embed", {"inputs": list(request.inputs)}, provider)
        vectors = _vectors_from(payload, provider)
        if len(vectors) != len(request.inputs):
            raise malformed_failure(provider)
        return self._embeddings_result(provider, vectors)

    # -- pipeline -----------------------------------------------------------
    def _invoke_pipeline(self, provider: str, request: ModelRequest) -> AdapterResult:
        task = self._config.pipeline_task
        if task == "image-to-text" and isinstance(request, VisionRequest):
            return self._pipeline_caption(provider, request)
        if task == "automatic-speech-recognition" and isinstance(request, TranscriptionRequest):
            return self._pipeline_asr(provider, request)
        if task == "audio-classification" and isinstance(request, SoundClassificationRequest):
            return self._pipeline_audio_classification(provider, request)
        if task == "feature-extraction" and isinstance(request, EmbeddingsRequest):
            return self._pipeline_feature_extraction(provider, request)
        raise TypeError(
            f"pipeline task {task!r} does not serve {type(request).__name__}"
        )

    def _pipeline_caption(self, provider: str, request: VisionRequest) -> AdapterResult:
        # image-to-text returns ``[{"generated_text": "..."}]``.
        body = {"inputs": request.image_urls[0]} if request.image_urls else {"inputs": ""}
        payload = self._post("/", body, provider)
        caption = _generated_text(payload)
        if not caption:
            raise malformed_failure(provider)
        return AdapterResult(
            output=VisionOutput(content=caption, raw_response={"task": "image-to-text"}),
            usage=ModelUsage(input_units=1),
            usage_reported=True,
        )

    def _pipeline_asr(self, provider: str, request: TranscriptionRequest) -> AdapterResult:
        audio = request.audio_path.read_bytes()
        payload = self._post_bytes("/", audio, provider)
        if not isinstance(payload, Mapping):
            raise malformed_failure(provider)
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            raise malformed_failure(provider)
        words = _asr_words(payload.get("chunks"), provider)
        return AdapterResult(
            output=TranscriptionOutput(
                text=text,
                language=None,
                confidence=None,
                words=words,
                raw_response={"task": "automatic-speech-recognition"},
            ),
            usage=ModelUsage(input_units=len(words)),
            usage_reported=True,
        )

    def _pipeline_audio_classification(
        self, provider: str, request: SoundClassificationRequest
    ) -> AdapterResult:
        # audio-classification (emotion or sound tagging) returns
        # ``[{"label": "...", "score": 0.0}]`` sorted by score.
        audio = request.audio_path.read_bytes()
        payload = self._post_bytes("/", audio, provider)
        if not isinstance(payload, Sequence) or isinstance(payload, str | bytes | bytearray):
            raise malformed_failure(provider)
        labels: list[SoundLabelOutput] = []
        for entry in payload:
            if not isinstance(entry, Mapping):
                raise malformed_failure(provider)
            label = entry.get("label")
            score = entry.get("score")
            if not isinstance(label, str) or not label.strip():
                continue
            try:
                labels.append(SoundLabelOutput(label=label, score=max(0.0, min(1.0, float(score)))))
            except (TypeError, ValueError) as exc:
                raise malformed_failure(provider) from exc
        labels.sort(key=lambda item: item.score, reverse=True)
        labels = labels[: request.max_results]
        return AdapterResult(
            output=SoundClassificationOutput(
                labels=tuple(labels),
                degraded=not labels,
                raw_response={"task": "audio-classification"},
            ),
            usage=ModelUsage(input_units=1),
            usage_reported=True,
        )

    def _pipeline_feature_extraction(
        self, provider: str, request: EmbeddingsRequest
    ) -> AdapterResult:
        payload = self._post("/", {"inputs": list(request.inputs)}, provider)
        vectors = _vectors_from(payload, provider)
        if len(vectors) != len(request.inputs):
            raise malformed_failure(provider)
        return self._embeddings_result(provider, vectors)

    def _embeddings_result(
        self, provider: str, vectors: tuple[tuple[float, ...], ...]
    ) -> AdapterResult:
        try:
            output = EmbeddingsOutput(vectors=vectors, raw_response={"count": len(vectors)})
        except ValueError as exc:
            raise malformed_failure(provider) from exc
        return AdapterResult(
            output=output,
            usage=ModelUsage(input_units=len(vectors)),
            usage_reported=True,
        )

    # -- transport ----------------------------------------------------------
    def _post(self, path: str, body: Mapping[str, object], provider: str) -> object:
        return self._send(path, provider, json_body=body)

    def _post_bytes(self, path: str, data: bytes, provider: str) -> object:
        return self._send(path, provider, content=data, content_type="application/octet-stream")

    def _send(
        self,
        path: str,
        provider: str,
        *,
        json_body: Mapping[str, object] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> object:
        headers = base_headers(self._config.extra_headers)
        self._auth.apply(headers)
        if content_type is not None:
            headers["content-type"] = content_type
        url = f"{self._base_url}{path}"
        try:
            with build_client(
                timeout_s=self._config.timeout_s, transport=self._transport
            ) as client:
                response = client.request(
                    "POST",
                    url,
                    headers=headers,
                    json=json_body if json_body is not None else None,
                    content=content,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise exception_failure(provider, exc) from None
        payload = read_json_response(
            response,
            provider=provider,
            max_bytes=self._config.max_response_bytes,
        )
        return payload


def huggingface_endpoint_factory(
    *,
    config: HuggingFaceEndpointConfig,
    auth: AuthApplier,
    provider: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> HuggingFaceEndpointAdapter:
    """Build a :class:`HuggingFaceEndpointAdapter` from a validated config.

    The registry/runtime wave calls this factory after it resolves the bearer
    secret and builds the :class:`AuthApplier`. The config is validated in
    :meth:`HuggingFaceEndpointConfig.__post_init__`, so an invalid record fails
    before any route is activated.
    """
    return HuggingFaceEndpointAdapter(
        config=config, auth=auth, provider=provider, transport=transport
    )


# ---------------------------------------------------------------------------
# Typed response helpers (code, not grammar).
# ---------------------------------------------------------------------------
def _dig(payload: object, *path: object) -> object:
    current = payload
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, Sequence) or isinstance(current, str | bytes | bytearray):
                return None
            if not 0 <= key < len(current):
                return None
            current = current[key]
        elif isinstance(current, Mapping) and key in current:
            current = current[key]
        else:
            return None
    return current


def _openai_usage(payload: object) -> ModelUsage | None:
    usage = _dig(payload, "usage")
    if not isinstance(usage, Mapping):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    try:
        return ModelUsage(
            input_units=int(prompt) if prompt is not None else 0,
            output_units=int(completion) if completion is not None else 0,
        )
    except (TypeError, ValueError):
        return None


def _generated_text(payload: object) -> str | None:
    # image-to-text returns either a list of {"generated_text": ...} or one object.
    entry = payload
    if isinstance(payload, Sequence) and not isinstance(payload, str | bytes | bytearray):
        entry = payload[0] if payload else None
    if isinstance(entry, Mapping):
        text = entry.get("generated_text")
        if isinstance(text, str) and text.strip():
            return text
    return None


def _asr_words(chunks: object, provider: str) -> tuple[TranscriptWordOutput, ...]:
    if chunks is None:
        return ()
    if not isinstance(chunks, Sequence) or isinstance(chunks, str | bytes | bytearray):
        raise malformed_failure(provider)
    words: list[TranscriptWordOutput] = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise malformed_failure(provider)
        text = chunk.get("text")
        timestamp = chunk.get("timestamp")
        if not isinstance(text, str) or not text.strip():
            continue
        if (
            not isinstance(timestamp, Sequence)
            or isinstance(timestamp, str | bytes | bytearray)
            or len(timestamp) != 2
            or timestamp[0] is None
            or timestamp[1] is None
        ):
            continue
        try:
            start_ms = round(float(timestamp[0]) * 1000)
            end_ms = round(float(timestamp[1]) * 1000)
        except (TypeError, ValueError) as exc:
            raise malformed_failure(provider) from exc
        if end_ms < start_ms:
            end_ms = start_ms
        try:
            words.append(
                TranscriptWordOutput(text=text.strip(), start_ms=start_ms, end_ms=end_ms)
            )
        except ValueError as exc:
            raise malformed_failure(provider) from exc
    return tuple(words)


def _vectors_from(payload: object, provider: str) -> tuple[tuple[float, ...], ...]:
    # TEI /embed and feature-extraction both return a list of vectors (a list of
    # lists of floats). A single-vector response (one list of floats) is wrapped.
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes | bytearray):
        raise malformed_failure(provider)
    is_single_vector = bool(payload) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in payload
    )
    rows: Sequence[object] = [payload] if is_single_vector else payload
    out: list[tuple[float, ...]] = []
    for row in rows:
        if not isinstance(row, Sequence) or isinstance(row, str | bytes | bytearray):
            raise malformed_failure(provider)
        try:
            out.append(tuple(float(value) for value in row))
        except (TypeError, ValueError) as exc:
            raise malformed_failure(provider) from exc
    if not out:
        raise malformed_failure(provider)
    return tuple(out)
