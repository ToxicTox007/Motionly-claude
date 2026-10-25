"""The generic ``transformers_pipeline`` local runner (plan Task 33).

Each supported pipeline task is driven against an injected fake pipeline (no
model download in CI). The runner maps every task onto its Motionly typed
output, builds the pipeline lazily on first invoke, and never imports
``transformers`` at construction time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.inference_local import (
    TRANSFORMERS_PIPELINE_TASKS,
    _transformers_pipeline_factory,
)
from app.services.model_gateway import (
    EmbeddingsOutput,
    EmbeddingsRequest,
    ModelCapability,
    ModelRoute,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    SoundClassificationOutput,
    SoundClassificationRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    VisionOutput,
    VisionRequest,
)


class _FakeRoute:
    """A minimal route stand-in carrying params + revision for the factory."""

    def __init__(self, *, pipeline_task: str, **params) -> None:
        self.id = "r-local"
        self.model_revision = "2026-01-01"
        self.params = {"pipeline_task": pipeline_task, **params}


class _FakeModel:
    def __init__(self, model_id: str, revision: str = "2026-01-01") -> None:
        self.model_id = model_id
        self.revision = revision


class _Settings:
    """Settings carrying the injected pipeline factory (plan Task 33)."""

    def __init__(self, factory) -> None:
        self.transformers_pipeline_factory = factory


def _route_for_gateway() -> ModelRoute:
    return ModelRoute(
        route_id="r-local",
        capability=ModelCapability.VISION,
        adapter="local",
        provider="local",
        model="m",
        model_revision="2026-01-01",
    )


def _build(task: str, fake_pipeline, **params):
    """Build the runner via its factory with an injected pipeline factory."""
    captured = {}

    def _factory(*, task, model, revision):  # noqa: A002 - mirrors signature
        captured["task"] = task
        captured["model"] = model
        captured["revision"] = revision
        return fake_pipeline

    adapter = _transformers_pipeline_factory(
        route=_FakeRoute(pipeline_task=task, model_name="fake/model", **params),
        adapter=None,
        model=_FakeModel("fake/model"),
        settings=_Settings(_factory),
    )
    return adapter, captured


# ---------------------------------------------------------------------------
# Task list is exactly the six the plan names.
# ---------------------------------------------------------------------------
def test_task_list_matches_plan() -> None:
    assert set(TRANSFORMERS_PIPELINE_TASKS) == {
        "image-to-text",
        "audio-classification",
        "automatic-speech-recognition",
        "object-detection",
        "zero-shot-object-detection",
        "feature-extraction",
    }


def test_unknown_task_rejected() -> None:
    with pytest.raises(ValueError, match="transformers_pipeline task"):
        _build("summarization", lambda *a, **k: None)[0]


# ---------------------------------------------------------------------------
# image-to-text -> vision.caption
# ---------------------------------------------------------------------------
def test_image_to_text_caption() -> None:
    def pipe(url):
        return [{"generated_text": "a cat on a sofa"}]

    adapter, captured = _build("image-to-text", pipe)
    result = adapter.invoke(
        route=_route_for_gateway(),
        request=VisionRequest(prompt="caption", image_urls=("https://x.invalid/a.jpg",)),
    )
    assert isinstance(result.output, VisionOutput)
    assert result.output.content == "a cat on a sofa"
    # The pipeline is built lazily from the registry task/model.
    assert captured == {"task": "image-to-text", "model": "fake/model", "revision": "2026-01-01"}


# ---------------------------------------------------------------------------
# audio-classification -> sound classification
# ---------------------------------------------------------------------------
def test_audio_classification_sound_labels(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    def pipe(path):
        return [
            {"label": "dog", "score": 0.9},
            {"label": "music", "score": 0.4},
        ]

    adapter, _ = _build("audio-classification", pipe)
    result = adapter.invoke(
        route=_route_for_gateway(),
        request=SoundClassificationRequest(audio_path=audio, max_results=1),
    )
    assert isinstance(result.output, SoundClassificationOutput)
    assert [label.label for label in result.output.labels] == ["dog"]
    assert not result.output.degraded


def test_audio_classification_empty_is_degraded(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    adapter, _ = _build("audio-classification", lambda path: [])
    result = adapter.invoke(
        route=_route_for_gateway(),
        request=SoundClassificationRequest(audio_path=audio, max_results=5),
    )
    assert result.output.degraded is True
    assert result.output.labels == ()


# ---------------------------------------------------------------------------
# automatic-speech-recognition -> transcription with word timestamps
# ---------------------------------------------------------------------------
def test_asr_word_timestamps(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    def pipe(path, return_timestamps):
        assert return_timestamps == "word"
        return {
            "text": "hello world",
            "chunks": [
                {"text": "hello", "timestamp": [0.0, 0.5]},
                {"text": "world", "timestamp": [0.5, 1.0]},
            ],
        }

    adapter, _ = _build("automatic-speech-recognition", pipe)
    result = adapter.invoke(
        route=_route_for_gateway(),
        request=TranscriptionRequest(audio_path=audio),
    )
    assert isinstance(result.output, TranscriptionOutput)
    assert result.output.text == "hello world"
    assert [w.text for w in result.output.words] == ["hello", "world"]
    assert result.output.words[0].start_ms == 0
    assert result.output.words[1].end_ms == 1000


# ---------------------------------------------------------------------------
# object-detection / zero-shot-object-detection -> ObjectDetectionOutput
# ---------------------------------------------------------------------------
def test_object_detection() -> None:
    def pipe(image):
        return [
            {
                "label": "person",
                "score": 0.95,
                "box": {"xmin": 0.1, "ymin": 0.1, "xmax": 0.4, "ymax": 0.6},
            },
        ]

    adapter, _ = _build("object-detection", pipe)
    result = adapter.invoke(
        route=_route_for_gateway(),
        request=ObjectDetectionRequest(images=(b"img",), max_results=24),
    )
    assert isinstance(result.output, ObjectDetectionOutput)
    assert len(result.output.detections) == 1
    det = result.output.detections[0][0]
    assert det.label == "person"
    assert 0 <= det.x <= 1 and 0 <= det.width <= 1


def test_zero_shot_object_detection_uses_candidate_labels() -> None:
    seen = {}

    def pipe(image, candidate_labels):
        seen["labels"] = candidate_labels
        return [
            {
                "label": "logo",
                "score": 0.8,
                "box": {"xmin": 0.0, "ymin": 0.0, "xmax": 0.2, "ymax": 0.2},
            },
        ]

    adapter, _ = _build(
        "zero-shot-object-detection", pipe, candidate_labels=["logo", "face"]
    )
    result = adapter.invoke(
        route=_route_for_gateway(),
        request=ObjectDetectionRequest(images=(b"img",), max_results=24),
    )
    assert seen["labels"] == ["logo", "face"]
    assert result.output.detections[0][0].label == "logo"


# ---------------------------------------------------------------------------
# feature-extraction -> embeddings (mean-pooled)
# ---------------------------------------------------------------------------
def test_feature_extraction_mean_pools() -> None:
    # Two inputs, each a [seq_len=2][hidden=2] token matrix.
    def pipe(inputs):
        return [
            [[1.0, 2.0], [3.0, 4.0]],
            [[0.0, 0.0], [2.0, 2.0]],
        ]

    adapter, _ = _build("feature-extraction", pipe)
    result = adapter.invoke(
        route=_route_for_gateway(),
        request=EmbeddingsRequest(inputs=("a", "b")),
    )
    assert isinstance(result.output, EmbeddingsOutput)
    assert result.output.vectors == ((2.0, 3.0), (1.0, 1.0))


def test_feature_extraction_single_vector_wrapped() -> None:
    # A single input returning one token matrix (not batched).
    def pipe(inputs):
        return [[1.0, 1.0], [3.0, 3.0]]

    adapter, _ = _build("feature-extraction", pipe)
    result = adapter.invoke(
        route=_route_for_gateway(),
        request=EmbeddingsRequest(inputs=("only",)),
    )
    assert result.output.vectors == ((2.0, 2.0),)


# ---------------------------------------------------------------------------
# Lazy build: the pipeline factory is not called until the first invoke.
# ---------------------------------------------------------------------------
def test_pipeline_built_lazily() -> None:
    calls = {"n": 0}

    def factory(*, task, model, revision):
        calls["n"] += 1
        return lambda url: [{"generated_text": "x"}]

    adapter = _transformers_pipeline_factory(
        route=_FakeRoute(pipeline_task="image-to-text", model_name="fake/model"),
        adapter=None,
        model=_FakeModel("fake/model"),
        settings=_Settings(factory),
    )
    assert calls["n"] == 0
    adapter.invoke(
        route=_route_for_gateway(),
        request=VisionRequest(prompt="c", image_urls=("https://x.invalid/a.jpg",)),
    )
    assert calls["n"] == 1
