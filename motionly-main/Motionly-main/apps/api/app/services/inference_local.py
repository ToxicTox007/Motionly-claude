"""The local inference runner registry (plan Task 14, adapter type ``local``).

A ``local`` registry route names a neutral *runner* in ``params.runner`` (for
example ``mediapipe_selfie_matte``, ``demucs_stems``, ``yamnet_sound``,
``clip_embeddings``). :func:`local_runner_factories` returns the mapping from a
runner name to a factory that builds the route's :class:`ModelAdapter`, wrapping
the EXISTING local implementations (``cpu_vision``, ``scale``,
``audio_ml_providers``, ``stock_video``, ``visual_intelligence``) without
rewriting any model code.

``build_inference_runtime`` wires this mapping into its ``local_adapter_factories``
hook by default. Each factory has the signature
``(route, adapter, model, settings) -> ModelAdapter`` and must not import a heavy
ML package (``torch``, ``tensorflow``, ``transformers``) at construction time:
the actual model is loaded lazily on the first ``invoke`` call. This keeps a
no-GPU deployment that only enables the CPU runners (matte, stems, sound) free of
the GPU-only packages, which is the Task 14 acceptance criterion.

The factories read the model identity (weights path, SHA-256, revision, repo id)
from the route's referenced :class:`ModelRecord`, never from a code default: the
registry file is the only source (plan Task 14, Section 2.9). The weights path
and SHA-256 for the CPU file models live in ``params`` (``model_path`` /
``weights_sha256``) because a ``ModelRecord`` carries the SHA but not the local
file path, which is operator/deployment specific.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.services.model_domain_adapters import (
    LocalClipEmbeddingsAdapter,
    LocalMatteSegmentationAdapter,
    LocalStemSeparationAdapter,
)
from app.services.model_gateway import (
    AdapterResult,
    DetectedObjectOutput,
    EmbeddingsOutput,
    EmbeddingsRequest,
    MatteSegmentationRequest,
    ModelRequest,
    ModelRoute,
    ModelUsage,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    SoundClassificationOutput,
    SoundClassificationRequest,
    SoundLabelOutput,
    TranscriptionOutput,
    TranscriptionRequest,
    TranscriptWordOutput,
    VisionOutput,
    VisionRequest,
)

# The pipeline tasks the ``transformers_pipeline`` runner supports (plan Task
# 33). Each maps a Hugging Face pipeline ``task`` onto one Motionly capability
# contract. ``zero-shot-object-detection`` shares the object-detection mapping
# (it additionally needs candidate labels from the route params).
TRANSFORMERS_PIPELINE_TASKS: tuple[str, ...] = (
    "image-to-text",
    "audio-classification",
    "automatic-speech-recognition",
    "object-detection",
    "zero-shot-object-detection",
    "feature-extraction",
)

# A factory builds one local route's adapter. ``model`` is the referenced
# ModelRecord (or None when the route carries no model_ref), ``settings`` is the
# app settings object. Keyword-only to match ``_build_local_adapter``.
LocalRunnerFactory = Callable[..., object]


def _require_param(route: object, key: str) -> str:
    params = getattr(route, "params", {}) or {}
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"local route {getattr(route, 'id', '?')!r} requires params.{key}")
    return value


def _model_field(model: object | None, attr: str) -> str | None:
    value = getattr(model, attr, None)
    return value if isinstance(value, str) and value.strip() else None


# ---------------------------------------------------------------------------
# Lazy adapter wrappers. These defer the heavy model load to the first invoke so
# that importing/constructing the factory never imports a heavy ML package.
# ---------------------------------------------------------------------------
class _LazyMatteAdapter:
    """CPU MediaPipe selfie segmentation; loads the TFLite model on first call."""

    def __init__(self, *, model_path: str, model_sha256: str) -> None:
        self._model_path = model_path
        self._model_sha256 = model_sha256
        self._delegate: LocalMatteSegmentationAdapter | None = None

    def _ensure(self) -> LocalMatteSegmentationAdapter:
        if self._delegate is None:
            from app.services.cpu_vision import load_cpu_matte_models

            provider_models = load_cpu_matte_models(
                model_path=self._model_path, model_sha256=self._model_sha256
            )
            self._delegate = LocalMatteSegmentationAdapter(provider_models)
        return self._delegate

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        if not isinstance(request, MatteSegmentationRequest):
            raise TypeError("matte runner requires MatteSegmentationRequest")
        return self._ensure().invoke(route=route, request=request)


class _LazyStemAdapter:
    """CPU Demucs stem separation; builds the subprocess separator on first call."""

    def __init__(self, *, model: str) -> None:
        self._model = model
        self._delegate: LocalStemSeparationAdapter | None = None

    def _ensure(self) -> LocalStemSeparationAdapter:
        if self._delegate is None:
            from app.services.scale import DemucsStemSeparator

            self._delegate = LocalStemSeparationAdapter(DemucsStemSeparator())
        return self._delegate

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        return self._ensure().invoke(route=route, request=request)


class _LazyAudioIntelligenceAdapter:
    """Audio-intelligence runner (sound / diarization / emotion) built on demand.

    ``builder`` returns the provider mapping; it is called once on first invoke
    so constructing the factory never loads TensorFlow/torch/transformers.
    """

    def __init__(self, *, builder: Callable[[], Mapping[str, object]]) -> None:
        self._builder = builder
        self._delegate: Any | None = None

    def _ensure(self) -> Any:
        if self._delegate is None:
            from app.services.model_domain_adapters import LocalAudioIntelligenceAdapter

            self._delegate = LocalAudioIntelligenceAdapter(self._builder())
        return self._delegate

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        return self._ensure().invoke(route=route, request=request)


class _LazyClipAdapter:
    """Local CLIP text/image embeddings; loads transformers on first call."""

    def __init__(self, *, model_name: str, revision: str) -> None:
        self._model_name = model_name
        self._revision = revision
        self._delegate: LocalClipEmbeddingsAdapter | None = None

    def _ensure(self) -> LocalClipEmbeddingsAdapter:
        if self._delegate is None:
            from app.services.stock_video import CompatibleClipEmbeddingProvider

            provider = CompatibleClipEmbeddingProvider(
                model_name=self._model_name,
                allow_fallback=False,
                revision=self._revision,
            )
            self._delegate = LocalClipEmbeddingsAdapter(provider)
        return self._delegate

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        return self._ensure().invoke(route=route, request=request)


class _LazyVisualManifestAdapter:
    """Local GPU visual manifest (CLIP+BLIP+YOLO); builds the provider on demand."""

    def __init__(self, *, builder: Callable[[], object]) -> None:
        self._builder = builder
        self._delegate: Any | None = None

    def _ensure(self) -> Any:
        if self._delegate is None:
            from app.services.model_domain_adapters import LocalVisualManifestAdapter

            self._delegate = LocalVisualManifestAdapter(self._builder())
        return self._delegate

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        return self._ensure().invoke(route=route, request=request)


# A pipeline factory builds a callable Hugging Face ``transformers`` pipeline for
# one task+model. The default lazily imports ``transformers`` (GPU/ML image
# only); tests inject a fake so no model is ever downloaded in CI.
PipelineFactory = Callable[..., Any]


def _default_pipeline_factory(*, task: str, model: str, revision: str) -> Any:
    """Build a real ``transformers`` pipeline (plan Task 33).

    Imported lazily so a CPU image that never enables a ``transformers_pipeline``
    route does not import ``transformers`` at all. The task and model come from
    the registry route/model record, never a code default.
    """
    from transformers import pipeline  # noqa: PLC0415 - lazy GPU/ML-only import

    return pipeline(task=task, model=model, revision=revision or None)


class _TransformersPipelineAdapter:
    """A generic local runner over a Hugging Face ``transformers`` pipeline.

    One route = one pipeline task + model. The pipeline is built lazily on the
    first ``invoke`` via the injected ``pipeline_factory`` (plan Task 33: "an
    injectable pipeline factory for tests"), so constructing the adapter never
    imports ``transformers``. The task fixes the request/output mapping:

    * ``image-to-text`` -> ``vision.caption`` (:class:`VisionOutput`);
    * ``audio-classification`` -> sound tags
      (:class:`SoundClassificationOutput`); also serves ``audio.emotion`` as the
      same label/score shape;
    * ``automatic-speech-recognition`` -> transcription with word timestamps
      (:class:`TranscriptionOutput`);
    * ``object-detection`` / ``zero-shot-object-detection`` ->
      :class:`ObjectDetectionOutput`;
    * ``feature-extraction`` -> :class:`EmbeddingsOutput`.
    """

    def __init__(
        self,
        *,
        task: str,
        model: str,
        revision: str,
        pipeline_factory: PipelineFactory,
        candidate_labels: tuple[str, ...] = (),
    ) -> None:
        if task not in TRANSFORMERS_PIPELINE_TASKS:
            raise ValueError(
                f"transformers_pipeline task {task!r} must be one of "
                f"{list(TRANSFORMERS_PIPELINE_TASKS)}"
            )
        self._task = task
        self._model = model
        self._revision = revision
        self._pipeline_factory = pipeline_factory
        self._candidate_labels = candidate_labels
        self._pipeline: Any | None = None

    def _ensure(self) -> Any:
        if self._pipeline is None:
            self._pipeline = self._pipeline_factory(
                task=self._task, model=self._model, revision=self._revision
            )
        return self._pipeline

    def invoke(self, *, route: ModelRoute, request: ModelRequest) -> AdapterResult:
        del route
        task = self._task
        if task == "image-to-text":
            return self._caption(request)
        if task == "audio-classification":
            return self._audio_classification(request)
        if task == "automatic-speech-recognition":
            return self._asr(request)
        if task in ("object-detection", "zero-shot-object-detection"):
            return self._object_detection(request)
        if task == "feature-extraction":
            return self._feature_extraction(request)
        raise ValueError(f"unsupported transformers_pipeline task {task!r}")

    # -- image-to-text ------------------------------------------------------
    def _caption(self, request: ModelRequest) -> AdapterResult:
        if not isinstance(request, VisionRequest):
            raise TypeError("image-to-text pipeline requires VisionRequest")
        pipe = self._ensure()
        result = pipe(request.image_urls[0])
        caption = _pipeline_generated_text(result)
        if not caption:
            raise ValueError("image-to-text pipeline returned no caption")
        return AdapterResult(
            output=VisionOutput(content=caption, raw_response={"task": self._task}),
            usage=ModelUsage(input_units=1),
            usage_reported=True,
        )

    # -- audio-classification ----------------------------------------------
    def _audio_classification(self, request: ModelRequest) -> AdapterResult:
        if not isinstance(request, SoundClassificationRequest):
            raise TypeError("audio-classification pipeline requires SoundClassificationRequest")
        pipe = self._ensure()
        rows = pipe(str(request.audio_path))
        labels = _pipeline_sound_labels(rows, request.max_results)
        return AdapterResult(
            output=SoundClassificationOutput(
                labels=tuple(labels),
                degraded=not labels,
                raw_response={"task": self._task},
            ),
            usage=ModelUsage(input_units=1),
            usage_reported=True,
        )

    # -- automatic-speech-recognition --------------------------------------
    def _asr(self, request: ModelRequest) -> AdapterResult:
        if not isinstance(request, TranscriptionRequest):
            raise TypeError("ASR pipeline requires TranscriptionRequest")
        pipe = self._ensure()
        # Word timestamps: ``return_timestamps="word"`` yields ``chunks`` with a
        # ``(start, end)`` timestamp per word.
        result = pipe(str(request.audio_path), return_timestamps="word")
        text = result.get("text") if isinstance(result, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            raise ValueError("ASR pipeline returned no text")
        words = _pipeline_asr_words(result.get("chunks") if isinstance(result, Mapping) else None)
        return AdapterResult(
            output=TranscriptionOutput(
                text=text.strip(),
                language=None,
                confidence=None,
                words=words,
                raw_response={"task": self._task},
            ),
            usage=ModelUsage(input_units=len(words)),
            usage_reported=True,
        )

    # -- object-detection / zero-shot-object-detection ---------------------
    def _object_detection(self, request: ModelRequest) -> AdapterResult:
        if not isinstance(request, ObjectDetectionRequest):
            raise TypeError("object-detection pipeline requires ObjectDetectionRequest")
        pipe = self._ensure()
        detections: list[tuple[DetectedObjectOutput, ...]] = []
        for image in request.images:
            if self._task == "zero-shot-object-detection":
                rows = pipe(image, candidate_labels=list(self._candidate_labels))
            else:
                rows = pipe(image)
            detections.append(_pipeline_detections(rows, request.max_results))
        return AdapterResult(
            output=ObjectDetectionOutput(
                detections=tuple(detections),
                raw_response={"task": self._task},
            ),
            usage=ModelUsage(input_units=len(request.images)),
            usage_reported=True,
        )

    # -- feature-extraction -------------------------------------------------
    def _feature_extraction(self, request: ModelRequest) -> AdapterResult:
        if not isinstance(request, EmbeddingsRequest):
            raise TypeError("feature-extraction pipeline requires EmbeddingsRequest")
        pipe = self._ensure()
        vectors = _pipeline_feature_vectors(pipe(list(request.inputs)))
        if len(vectors) != len(request.inputs):
            raise ValueError("feature-extraction pipeline returned the wrong vector count")
        try:
            output = EmbeddingsOutput(vectors=vectors, raw_response={"task": self._task})
        except ValueError as exc:
            raise ValueError(f"feature-extraction output invalid: {exc}") from exc
        return AdapterResult(
            output=output,
            usage=ModelUsage(input_units=len(vectors)),
            usage_reported=True,
        )


# ---------------------------------------------------------------------------
# transformers_pipeline response helpers (code, not grammar). They mean-pool
# feature-extraction, clamp sound scores, and round detection boxes onto [0, 1].
# ---------------------------------------------------------------------------
def _pipeline_generated_text(result: object) -> str | None:
    entry = result
    if isinstance(result, list):
        entry = result[0] if result else None
    if isinstance(entry, Mapping):
        text = entry.get("generated_text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    if isinstance(entry, str) and entry.strip():
        return entry.strip()
    return None


def _pipeline_sound_labels(rows: object, max_results: int) -> list[SoundLabelOutput]:
    if not isinstance(rows, list):
        return []
    labels: list[SoundLabelOutput] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        label = row.get("label")
        score = row.get("score")
        if not isinstance(label, str) or not label.strip():
            continue
        try:
            labels.append(
                SoundLabelOutput(label=label, score=max(0.0, min(1.0, float(score))))
            )
        except (TypeError, ValueError):
            continue
    labels.sort(key=lambda item: item.score, reverse=True)
    return labels[:max_results]


def _pipeline_asr_words(chunks: object) -> tuple[TranscriptWordOutput, ...]:
    if not isinstance(chunks, list):
        return ()
    words: list[TranscriptWordOutput] = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        text = chunk.get("text")
        stamp = chunk.get("timestamp")
        if not isinstance(text, str) or not text.strip():
            continue
        if (
            not isinstance(stamp, (list, tuple))
            or len(stamp) != 2
            or stamp[0] is None
            or stamp[1] is None
        ):
            continue
        try:
            start_ms = round(float(stamp[0]) * 1000)
            end_ms = round(float(stamp[1]) * 1000)
        except (TypeError, ValueError):
            continue
        if end_ms < start_ms:
            end_ms = start_ms
        try:
            words.append(
                TranscriptWordOutput(text=text.strip(), start_ms=start_ms, end_ms=end_ms)
            )
        except ValueError:
            continue
    return tuple(words)


def _pipeline_detections(rows: object, max_results: int) -> tuple[DetectedObjectOutput, ...]:
    if not isinstance(rows, list):
        return ()
    detections: list[DetectedObjectOutput] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        label = row.get("label")
        score = row.get("score")
        box = row.get("box")
        if not isinstance(label, str) or not label.strip() or not isinstance(box, Mapping):
            continue
        try:
            xmin = float(box["xmin"])
            ymin = float(box["ymin"])
            xmax = float(box["xmax"])
            ymax = float(box["ymax"])
            confidence = max(0.0, min(1.0, float(score)))
        except (KeyError, TypeError, ValueError):
            continue
        # The object-detection pipeline reports pixel boxes; the route carries
        # normalized boxes already in [0, 1] when ``width``/``height`` are in the
        # params, but the common case normalizes against the reported extent.
        width = max(0.0, min(1.0, xmax - xmin))
        height = max(0.0, min(1.0, ymax - ymin))
        x = max(0.0, min(1.0, xmin))
        y = max(0.0, min(1.0, ymin))
        try:
            detections.append(
                DetectedObjectOutput(
                    label=label.strip(),
                    confidence=confidence,
                    x=x,
                    y=y,
                    width=min(width, 1.0 - x),
                    height=min(height, 1.0 - y),
                )
            )
        except ValueError:
            continue
    detections.sort(key=lambda item: item.confidence, reverse=True)
    return tuple(detections[:max_results])


def _pipeline_feature_vectors(result: object) -> tuple[tuple[float, ...], ...]:
    # feature-extraction returns, per input, a list of token embeddings
    # (``[seq_len][hidden]``); mean-pool over tokens to one vector per input. A
    # single-input call may return one nested list, which is wrapped.
    if not isinstance(result, list) or not result:
        return ()
    rows = result
    if _is_token_matrix(result):
        rows = [result]
    vectors: list[tuple[float, ...]] = []
    for row in rows:
        vectors.append(_mean_pool(row))
    return tuple(vectors)


def _is_token_matrix(value: object) -> bool:
    # True when ``value`` is a ``[seq_len][hidden]`` matrix of numbers (one
    # input's token embeddings), False when it is a batch of such matrices.
    return (
        isinstance(value, list)
        and bool(value)
        and isinstance(value[0], list)
        and bool(value[0])
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value[0])
    )


def _mean_pool(row: object) -> tuple[float, ...]:
    if not isinstance(row, list) or not row:
        return ()
    # A ``[hidden]`` vector (already pooled) is returned as-is.
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in row):
        return tuple(float(v) for v in row)
    # A ``[seq_len][hidden]`` matrix: mean over the sequence dimension.
    columns = len(row[0]) if isinstance(row[0], list) else 0
    if columns == 0:
        return ()
    sums = [0.0] * columns
    count = 0
    for token in row:
        if not isinstance(token, list) or len(token) != columns:
            continue
        for index, value in enumerate(token):
            sums[index] += float(value)
        count += 1
    if count == 0:
        return ()
    return tuple(total / count for total in sums)


# ---------------------------------------------------------------------------
# Factories. Each resolves its model identity from the registry (model record +
# route params), never a code default.
# ---------------------------------------------------------------------------
def _matte_factory(*, route, adapter, model, settings) -> object:  # noqa: ANN001
    del adapter, model, settings
    return _LazyMatteAdapter(
        model_path=_require_param(route, "model_path"),
        model_sha256=_require_param(route, "weights_sha256"),
    )


def _demucs_factory(*, route, adapter, model, settings) -> object:  # noqa: ANN001
    del adapter, settings
    model_id = _model_field(model, "model_id") or _require_param(route, "model_name")
    return _LazyStemAdapter(model=model_id)


def _yamnet_factory(*, route, adapter, model, settings) -> object:  # noqa: ANN001
    del adapter, model, settings

    def _builder() -> Mapping[str, object]:
        from app.services.audio_ml_providers import YamnetTFLiteSoundClassifier

        return {
            "sound": YamnetTFLiteSoundClassifier(
                model_path=_require_param(route, "model_path"),
                model_sha256=_require_param(route, "weights_sha256"),
            )
        }

    return _LazyAudioIntelligenceAdapter(builder=_builder)


def _clip_factory(*, route, adapter, model, settings) -> object:  # noqa: ANN001
    del adapter, settings
    model_id = _model_field(model, "model_id") or _require_param(route, "model_name")
    revision = _model_field(model, "revision") or getattr(route, "model_revision", "")
    return _LazyClipAdapter(model_name=model_id, revision=revision)


def _visual_manifest_factory(*, route, adapter, model, settings) -> object:  # noqa: ANN001
    del adapter, model

    def _builder() -> object:
        from app.services.visual_intelligence import LocalMLVisualIntelligenceProvider

        return LocalMLVisualIntelligenceProvider(
            clip_model=_require_param(route, "clip_model"),
            blip_model=_require_param(route, "blip_model"),
            yolo_model=_require_param(route, "yolo_model"),
            clip_revision=_require_param(route, "clip_revision"),
            blip_revision=_require_param(route, "blip_revision"),
            yolo_revision=_require_param(route, "yolo_revision"),
        )

    return _LazyVisualManifestAdapter(builder=_builder)


def _diarization_factory(*, route, adapter, model, settings) -> object:  # noqa: ANN001
    del adapter, model

    def _builder() -> Mapping[str, object]:
        from app.services.audio_ml_providers import PyannoteSpeakerDiarizationProvider

        return {
            "speaker_diarization": PyannoteSpeakerDiarizationProvider(
                model=_require_param(route, "model_name"),
                auth_token=getattr(settings, "pyannote_auth_token", None),
                revision=getattr(route, "model_revision", None),
            )
        }

    return _LazyAudioIntelligenceAdapter(builder=_builder)


def _emotion_factory(*, route, adapter, model, settings) -> object:  # noqa: ANN001
    del adapter, model, settings

    def _builder() -> Mapping[str, object]:
        from app.services.audio_ml_providers import Wav2VecEmotionProvider

        return {
            "emotion": Wav2VecEmotionProvider(
                model=_require_param(route, "model_name"),
                revision=getattr(route, "model_revision", None),
            )
        }

    return _LazyAudioIntelligenceAdapter(builder=_builder)


def _transformers_pipeline_factory(*, route, adapter, model, settings) -> object:  # noqa: ANN001
    """Build a generic ``transformers_pipeline`` local adapter (plan Task 33).

    The pipeline ``task`` comes from ``route.params["pipeline_task"]`` and the
    model/revision from the referenced model record (falling back to the route's
    ``model_name`` param / ``model_revision`` so a model record is not strictly
    required). An injectable pipeline factory is read from
    ``settings.transformers_pipeline_factory`` when present, so tests pass a fake
    and CI never downloads a model; otherwise the real ``transformers`` pipeline
    is built lazily on first invoke.
    """
    del adapter
    task = _require_param(route, "pipeline_task")
    model_id = _model_field(model, "model_id") or _require_param(route, "model_name")
    revision = _model_field(model, "revision") or getattr(route, "model_revision", "")
    params = getattr(route, "params", {}) or {}
    raw_labels = params.get("candidate_labels", [])
    candidate_labels = (
        tuple(str(label) for label in raw_labels)
        if isinstance(raw_labels, (list, tuple))
        else ()
    )
    pipeline_factory = getattr(
        settings, "transformers_pipeline_factory", None
    ) or _default_pipeline_factory
    return _TransformersPipelineAdapter(
        task=task,
        model=model_id,
        revision=revision,
        pipeline_factory=pipeline_factory,
        candidate_labels=candidate_labels,
    )


def local_runner_factories() -> dict[str, LocalRunnerFactory]:
    """The neutral runner name -> adapter factory registry (plan Task 14/33).

    CPU runners (``mediapipe_selfie_matte``, ``demucs_stems``, ``yamnet_sound``)
    never import torch/tensorflow/transformers until a request arrives, and even
    then only the package their own implementation needs. The GPU runners
    (``clip_embeddings``, ``blip_caption``, ``yolo_objects``,
    ``pyannote_diarization``, ``wav2vec2_emotion``, the ``visual_manifest``
    pipeline and the generic ``transformers_pipeline`` runner) are only ever
    enabled in the GPU/ML registry and import ``transformers`` lazily.
    """
    return {
        # CPU runners.
        "mediapipe_selfie_matte": _matte_factory,
        "demucs_stems": _demucs_factory,
        "yamnet_sound": _yamnet_factory,
        # GPU runners.
        "clip_embeddings": _clip_factory,
        "blip_caption": _clip_factory,
        "yolo_objects": _visual_manifest_factory,
        "visual_manifest": _visual_manifest_factory,
        "pyannote_diarization": _diarization_factory,
        "wav2vec2_emotion": _emotion_factory,
        # Generic Hugging Face pipeline runner (plan Task 33).
        "transformers_pipeline": _transformers_pipeline_factory,
    }
