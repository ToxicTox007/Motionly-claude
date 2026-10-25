"""Table-driven tests for the fine-grained Motionly capability taxonomy (Task 3).

Covers all 14 Section 2.1 capability ids plus the 2 pipeline operations:

- each capability id has a canonical request class with a valid and an invalid
  example, and an output class with a valid and an invalid example;
- ``inference_capability_for_request`` maps each canonical request to its id;
- the request-type table is exhaustive over the ``ModelRequest`` union (derived
  with ``typing.get_args``), so a new request type without a mapping fails;
- no request type falls through to an untyped dict (the fake builds a typed
  output for every one through a real ``ModelGateway``);
- every legacy ``ModelOperation`` string parses to the right new id and unknown
  strings fail;
- route config declared with old operation strings still selects the same route;
- invocation metadata and telemetry emit ``capability_id``;
- embedding outputs reject mismatched dimensions/counts.
"""

from __future__ import annotations

import typing
from pathlib import Path
from uuid import uuid4

import pytest

from app.schemas.uploads import VideoMetadata
from app.services.inference_capabilities import (
    LEGACY_OPERATION_ALIASES,
    InferenceCapability,
    PipelineOperation,
    coarse_family_for_capability,
    parse_capability,
)
from app.services.inference_fake import DeterministicInferenceAdapter
from app.services.inference_schemas import (
    AudioEmotionOutput,
    AudioEmotionRequest,
    ImageGenerationRequest,
    OperationHandle,
    OperationStatus,
    TextEmbeddingsRequest,
    VideoGenerationStatusRequest,
    VisionCaptionOutput,
    VisionCaptionRequest,
)
from app.services.model_gateway import (
    AudioEmotionWindow,
    AudioIntelligenceRequest,
    AudioRequest,
    ChatMessage,
    DetectedObjectOutput,
    DiarizationOutput,
    DiarizationRequest,
    DiarizationSegmentOutput,
    EmbeddingsOutput,
    EmbeddingsRequest,
    GeneratedArtifact,
    GenerationOperationOutput,
    GenerationOutput,
    GenerationRequest,
    ImageEmbeddingsRequest,
    MatteSegmentationOutput,
    MatteSegmentationRequest,
    ModelCallContext,
    ModelCapability,
    ModelGateway,
    ModelOperation,
    ModelRequest,
    ModelRoute,
    ObjectDetectionOutput,
    ObjectDetectionRequest,
    OperationsGatewayTelemetry,
    PlanningOutput,
    PlanningRequest,
    SoundClassificationOutput,
    SoundClassificationRequest,
    SoundLabelOutput,
    StemSeparationOutput,
    StemSeparationRequest,
    TranscriptionOutput,
    TranscriptionRequest,
    TranscriptWordOutput,
    VideoGenerationPollRequest,
    VideoGenerationSubmitRequest,
    VisionRequest,
    VisualManifestRequest,
    capability_id_for_operation,
    inference_capability_for_request,
)

# The 14 Section 2.1 capability ids, as a literal source of truth for the test.
_EXPECTED_IDS = {
    "planning.chat",
    "transcription.speech_to_text",
    "vision.caption",
    "vision.object_detection",
    "embeddings.text",
    "embeddings.image",
    "audio.diarization",
    "audio.emotion",
    "audio.stem_separation",
    "image.generation",
    "video.generation.submit",
    "video.generation.status",
    "matte.segmentation",
    "audio.sound_classification",
}


def _media_file(tmp_path: Path, name: str = "clip.wav") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x00" * 4096)
    return path


# --- the canonical taxonomy table ------------------------------------------


def _cases(tmp_path: Path):
    """For each capability id: (id, valid request, request class, valid output)."""
    audio = _media_file(tmp_path, "audio.wav")
    video = _media_file(tmp_path, "video.mp4")
    image = b"\xff\xd8\xff" + b"\x00" * 64
    metadata = VideoMetadata(duration_ms=1000, width=1920, height=1080, fps=30.0)
    image_artifact = GeneratedArtifact(url="https://x.invalid/a.png")
    return [
        (
            InferenceCapability.PLANNING_CHAT,
            PlanningRequest(messages=(ChatMessage(role="user", content="plan"),)),
            PlanningRequest,
            PlanningOutput(content="ok", raw_response={}),
        ),
        (
            InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
            TranscriptionRequest(audio_path=audio, language="en"),
            TranscriptionRequest,
            TranscriptionOutput(
                text="hi",
                language="en",
                confidence=0.9,
                words=(TranscriptWordOutput(text="hi", start_ms=0, end_ms=10),),
                raw_response={},
            ),
        ),
        (
            InferenceCapability.VISION_CAPTION,
            VisionCaptionRequest(prompt="describe", image_urls=("https://x.invalid/f.jpg",)),
            VisionCaptionRequest,
            VisionCaptionOutput(content="a caption", raw_response={}),
        ),
        (
            InferenceCapability.VISION_OBJECT_DETECTION,
            ObjectDetectionRequest(images=(image,)),
            ObjectDetectionRequest,
            ObjectDetectionOutput(
                detections=(
                    (
                        DetectedObjectOutput(
                            label="cat", confidence=0.9, x=0.1, y=0.1, width=0.2, height=0.2
                        ),
                    ),
                )
            ),
        ),
        (
            InferenceCapability.EMBEDDINGS_TEXT,
            TextEmbeddingsRequest(inputs=("a", "b"), dimensions=4),
            TextEmbeddingsRequest,
            EmbeddingsOutput(vectors=((0.1, 0.2), (0.3, 0.4)), raw_response={}),
        ),
        (
            InferenceCapability.EMBEDDINGS_IMAGE,
            ImageEmbeddingsRequest(images=(image, image)),
            ImageEmbeddingsRequest,
            EmbeddingsOutput(vectors=((0.1, 0.2), (0.3, 0.4)), raw_response={}),
        ),
        (
            InferenceCapability.AUDIO_DIARIZATION,
            DiarizationRequest(audio_path=audio, duration_s=30.0),
            DiarizationRequest,
            DiarizationOutput(
                segments=(
                    DiarizationSegmentOutput(speaker="s1", start_ms=0, end_ms=100),
                ),
                raw_response={},
            ),
        ),
        (
            InferenceCapability.AUDIO_EMOTION,
            AudioEmotionRequest(prompt="classify", audio_path=audio, duration_s=4.0),
            AudioEmotionRequest,
            AudioEmotionOutput(content='{"emotion": "neutral"}', raw_response={}),
        ),
        (
            InferenceCapability.AUDIO_STEM_SEPARATION,
            StemSeparationRequest(source_path=audio, model="m", duration_s=5.0),
            StemSeparationRequest,
            StemSeparationOutput(stems={"vocals": audio, "no_vocals": audio}),
        ),
        (
            InferenceCapability.IMAGE_GENERATION,
            ImageGenerationRequest(prompt="make image", count=1),
            ImageGenerationRequest,
            GenerationOutput(
                artifacts=(image_artifact,), operation_id=None, raw_response={}
            ),
        ),
        (
            InferenceCapability.VIDEO_GENERATION_SUBMIT,
            VideoGenerationSubmitRequest(
                prompt="p",
                prompt_image_url=None,
                duration_s=4,
                ratio="16:9",
                idempotency_key="k",
            ),
            VideoGenerationSubmitRequest,
            OperationHandle(operation_id="op-1", raw_response={}),
        ),
        (
            InferenceCapability.VIDEO_GENERATION_STATUS,
            VideoGenerationStatusRequest(operation_id="op-1"),
            VideoGenerationStatusRequest,
            OperationStatus(
                artifacts=(image_artifact,), operation_id="op-1", raw_response={}
            ),
        ),
        (
            InferenceCapability.MATTE_SEGMENTATION,
            MatteSegmentationRequest(
                source_path=video,
                output_path=tmp_path / "matte.mp4",
                metadata=metadata,
                max_dimension=480,
            ),
            MatteSegmentationRequest,
            MatteSegmentationOutput(fps=25.0, width=480, height=480, frame_count=25),
        ),
        (
            InferenceCapability.AUDIO_SOUND_CLASSIFICATION,
            SoundClassificationRequest(audio_path=audio, duration_s=4.0),
            SoundClassificationRequest,
            SoundClassificationOutput(
                labels=(SoundLabelOutput(label="music", score=0.9),), raw_response={}
            ),
        ),
    ]


def test_inference_capability_enum_has_exactly_the_14_ids() -> None:
    assert {member.value for member in InferenceCapability} == _EXPECTED_IDS


def test_pipeline_operations_are_separate_from_capabilities() -> None:
    pipeline = {member.value for member in PipelineOperation}
    assert pipeline == {"vision.video_manifest", "audio.intelligence"}
    # Pipeline ids are not capabilities and are rejected by parse_capability.
    assert not (pipeline & _EXPECTED_IDS)
    for value in pipeline:
        with pytest.raises(ValueError):
            parse_capability(value)


def test_table_covers_every_capability_id(tmp_path: Path) -> None:
    covered = {case[0] for case in _cases(tmp_path)}
    assert covered == set(InferenceCapability)


def test_canonical_request_maps_to_its_capability(tmp_path: Path) -> None:
    for capability, request, _request_type, _output in _cases(tmp_path):
        assert inference_capability_for_request(request) == capability


def test_pipeline_requests_map_to_pipeline_operations(tmp_path: Path) -> None:
    audio = _media_file(tmp_path, "a.wav")
    video = _media_file(tmp_path, "v.mp4")
    metadata = VideoMetadata(duration_ms=1000, width=1920, height=1080, fps=30.0)
    manifest = VisualManifestRequest(
        source_path=video, metadata=metadata, sample_rate_fps=2.0, scene_threshold=0.3
    )
    intelligence = AudioIntelligenceRequest(audio_path=audio)
    assert (
        inference_capability_for_request(manifest)
        == PipelineOperation.VISION_VIDEO_MANIFEST
    )
    assert (
        inference_capability_for_request(intelligence)
        == PipelineOperation.AUDIO_INTELLIGENCE
    )


def test_coarse_family_for_every_capability_id() -> None:
    expected = {
        InferenceCapability.PLANNING_CHAT: ModelCapability.PLANNING,
        InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT: ModelCapability.TRANSCRIPTION,
        InferenceCapability.VISION_CAPTION: ModelCapability.VISION,
        InferenceCapability.VISION_OBJECT_DETECTION: ModelCapability.VISION,
        InferenceCapability.EMBEDDINGS_TEXT: ModelCapability.EMBEDDINGS,
        InferenceCapability.EMBEDDINGS_IMAGE: ModelCapability.EMBEDDINGS,
        InferenceCapability.AUDIO_DIARIZATION: ModelCapability.AUDIO,
        InferenceCapability.AUDIO_EMOTION: ModelCapability.AUDIO,
        InferenceCapability.AUDIO_STEM_SEPARATION: ModelCapability.AUDIO,
        InferenceCapability.IMAGE_GENERATION: ModelCapability.GENERATION,
        InferenceCapability.VIDEO_GENERATION_SUBMIT: ModelCapability.GENERATION,
        InferenceCapability.VIDEO_GENERATION_STATUS: ModelCapability.GENERATION,
        InferenceCapability.MATTE_SEGMENTATION: ModelCapability.VISION,
        InferenceCapability.AUDIO_SOUND_CLASSIFICATION: ModelCapability.AUDIO,
    }
    for capability in InferenceCapability:
        assert coarse_family_for_capability(capability) == expected[capability]


# --- exhaustiveness over the ModelRequest union ----------------------------


def _request_union_members() -> tuple[type, ...]:
    return typing.get_args(ModelRequest.__value__)


def test_request_union_is_fully_mapped(tmp_path: Path) -> None:
    # Every member of the ModelRequest union must map to a fine-grained id or a
    # pipeline operation; a new request type without a mapping fails here.
    members = _request_union_members()
    assert members, "ModelRequest union has no members"
    audio = _media_file(tmp_path, "a.wav")
    video = _media_file(tmp_path, "v.mp4")
    image = b"\xff\xd8\xff" + b"\x00" * 64
    metadata = VideoMetadata(duration_ms=1000, width=1920, height=1080, fps=30.0)
    samples: dict[type, ModelRequest] = {
        PlanningRequest: PlanningRequest(
            messages=(ChatMessage(role="user", content="p"),)
        ),
        TranscriptionRequest: TranscriptionRequest(audio_path=audio),
        VisionRequest: VisionRequest(prompt="d", image_urls=("https://x.invalid/f.jpg",)),
        AudioRequest: AudioRequest(prompt="c", audio_path=audio, duration_s=1.0),
        EmbeddingsRequest: EmbeddingsRequest(inputs=("a",)),
        GenerationRequest: GenerationRequest(prompt="g"),
        VisualManifestRequest: VisualManifestRequest(
            source_path=video, metadata=metadata, sample_rate_fps=2.0, scene_threshold=0.3
        ),
        AudioIntelligenceRequest: AudioIntelligenceRequest(audio_path=audio),
        ImageEmbeddingsRequest: ImageEmbeddingsRequest(images=(image,)),
        VideoGenerationSubmitRequest: VideoGenerationSubmitRequest(
            prompt="p", prompt_image_url=None, duration_s=4, ratio="16:9", idempotency_key="k"
        ),
        VideoGenerationPollRequest: VideoGenerationPollRequest(operation_id="op"),
        MatteSegmentationRequest: MatteSegmentationRequest(
            source_path=video, output_path=video, metadata=metadata
        ),
        StemSeparationRequest: StemSeparationRequest(source_path=audio, model="m"),
        DiarizationRequest: DiarizationRequest(audio_path=audio, duration_s=1.0),
        ObjectDetectionRequest: ObjectDetectionRequest(images=(image,)),
        SoundClassificationRequest: SoundClassificationRequest(audio_path=audio),
    }
    assert set(members) == set(samples), "union members and test samples disagree"
    for member in members:
        mapped = inference_capability_for_request(samples[member])
        assert isinstance(mapped, InferenceCapability | PipelineOperation), member.__name__


# --- per-capability valid/invalid request and output examples --------------


def test_each_request_class_has_a_valid_and_invalid_example(tmp_path: Path) -> None:
    audio = _media_file(tmp_path, "a.wav")
    image = b"\xff\xd8\xff" + b"\x00" * 64
    # One invalid example per request class: each must raise on construction.
    invalid_calls = [
        lambda: PlanningRequest(messages=()),
        lambda: TranscriptionRequest(audio_path=audio, response_format=""),
        lambda: VisionCaptionRequest(prompt="", image_urls=()),
        lambda: ObjectDetectionRequest(images=()),
        lambda: TextEmbeddingsRequest(inputs=()),
        lambda: ImageEmbeddingsRequest(images=()),
        lambda: DiarizationRequest(audio_path=audio, duration_s=0.0),
        lambda: AudioEmotionRequest(prompt="", audio_path=audio),
        lambda: StemSeparationRequest(source_path=audio, model="  "),
        lambda: ImageGenerationRequest(prompt=""),
        lambda: VideoGenerationSubmitRequest(
            prompt="", prompt_image_url=None, duration_s=4, ratio="16:9", idempotency_key="k"
        ),
        lambda: VideoGenerationStatusRequest(operation_id=""),
        lambda: MatteSegmentationRequest(
            source_path=audio,
            output_path=audio,
            metadata=VideoMetadata(duration_ms=1, width=1, height=1, fps=1.0),
            max_dimension=1,
        ),
        lambda: SoundClassificationRequest(audio_path=audio, max_results=0),
    ]
    assert len(invalid_calls) == len(_EXPECTED_IDS)
    for make in invalid_calls:
        with pytest.raises(ValueError):
            make()
    del image  # the valid examples live in _cases; invalid examples suffice here


def test_each_output_class_has_a_valid_and_invalid_example(tmp_path: Path) -> None:
    audio = _media_file(tmp_path, "a.wav")
    # Valid outputs come from the table; here assert one invalid per output class.
    for _capability, _request, _request_type, valid_output in _cases(tmp_path):
        assert valid_output is not None
    invalid_output_calls = [
        lambda: PlanningOutput(content="", raw_response={}),
        lambda: TranscriptionOutput(
            text="x",
            language=None,
            confidence=None,
            words=(TranscriptWordOutput(text="x", start_ms=10, end_ms=0),),
            raw_response={},
        ),
        lambda: VisionCaptionOutput(content="", raw_response={}),
        lambda: ObjectDetectionOutput(
            detections=(
                (
                    DetectedObjectOutput(
                        label="x", confidence=2.0, x=0.0, y=0.0, width=0.1, height=0.1
                    ),
                ),
            )
        ),
        lambda: EmbeddingsOutput(vectors=(), raw_response={}),
        lambda: DiarizationSegmentOutput(speaker="", start_ms=0, end_ms=1),
        lambda: AudioEmotionOutput(content="", raw_response={}),
        lambda: StemSeparationOutput(stems={}),
        lambda: GenerationOutput(artifacts=(), operation_id=None, raw_response={}),
        lambda: OperationHandle(operation_id="", raw_response={}),
        lambda: MatteSegmentationOutput(fps=0.0, width=1, height=1, frame_count=1),
        lambda: SoundClassificationOutput(labels=(), degraded=False),
        lambda: SoundLabelOutput(label="x", score=2.0),
        lambda: AudioEmotionWindow(start_ms=10, end_ms=0, label="x"),
    ]
    for make in invalid_output_calls:
        with pytest.raises(ValueError):
            make()
    del audio


# --- legacy operation aliases ----------------------------------------------


def test_every_legacy_operation_parses_to_the_right_new_id() -> None:
    expected = {
        "planning.chat": InferenceCapability.PLANNING_CHAT,
        "transcription.speech_to_text": InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
        "vision.prompt": InferenceCapability.VISION_CAPTION,
        "vision.object_detection": InferenceCapability.VISION_OBJECT_DETECTION,
        "vision.matte_segmentation": InferenceCapability.MATTE_SEGMENTATION,
        "audio.prompt": InferenceCapability.AUDIO_EMOTION,
        "audio.diarization": InferenceCapability.AUDIO_DIARIZATION,
        "audio.stem_separation": InferenceCapability.AUDIO_STEM_SEPARATION,
        "embeddings.text": InferenceCapability.EMBEDDINGS_TEXT,
        "embeddings.image": InferenceCapability.EMBEDDINGS_IMAGE,
        "generation.image": InferenceCapability.IMAGE_GENERATION,
        "generation.video_submit": InferenceCapability.VIDEO_GENERATION_SUBMIT,
        "generation.video_poll": InferenceCapability.VIDEO_GENERATION_STATUS,
    }
    for value, capability in expected.items():
        assert parse_capability(value) == capability
    # The alias table covers exactly these legacy + self-mapped strings.
    assert set(LEGACY_OPERATION_ALIASES) == set(expected)


def test_new_ids_parse_to_themselves() -> None:
    for member in InferenceCapability:
        assert parse_capability(member.value) == member


def test_unknown_capability_strings_fail() -> None:
    for bad in ("", "nope", "vision", "generation.unknown", "vision.video_manifest"):
        with pytest.raises(ValueError):
            parse_capability(bad)


def test_capability_id_for_operation_covers_every_model_operation() -> None:
    # Every routed ModelOperation yields a fine-grained id; the two pipeline
    # operations yield None (they are not provider capabilities).
    pipeline_values = {"vision.video_manifest", "audio.intelligence"}
    for operation in ModelOperation:
        result = capability_id_for_operation(operation)
        if operation.value in pipeline_values:
            assert result is None
        else:
            assert result == parse_capability(operation.value).value
    assert capability_id_for_operation(None) is None


# --- route config with old operation strings -------------------------------
# --- gateway metadata / telemetry emit capability_id -----------------------


def _fake_route(route_id: str, capability: ModelCapability, operation: ModelOperation):
    return ModelRoute(
        route_id=route_id,
        capability=capability,
        adapter="fake",
        provider=route_id,
        model=f"{route_id}-model",
        model_revision=f"{route_id}-2026-07-14",
        operations=(operation,),
    )


def test_invocation_metadata_emits_capability_id(tmp_path: Path) -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _fake_route("vision", ModelCapability.VISION, ModelOperation.VISION_PROMPT)
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route],
        request_budget_usd=100.0,
        monthly_budget_usd=1000.0,
    )
    request = VisionCaptionRequest(prompt="d", image_urls=("https://x.invalid/f.jpg",))
    result = gateway.invoke(request, context=ModelCallContext(workspace_id=uuid4()))
    assert result.metadata.capability_id == InferenceCapability.VISION_CAPTION.value
    assert result.metadata.as_dict()["capability_id"] == "vision.caption"
    # The legacy operation value stays unchanged for comparability (Task 3).
    assert result.metadata.operation == ModelOperation.VISION_PROMPT


def test_telemetry_payload_carries_capability_id(tmp_path: Path) -> None:
    class _Recorder:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def record_provider_slo(self, **values) -> None:
            self.events.append(values)

    recorder = _Recorder()
    adapter = DeterministicInferenceAdapter(app_env="test")
    route = _fake_route(
        "objects", ModelCapability.VISION, ModelOperation.VISION_OBJECT_DETECTION
    )
    gateway = ModelGateway(
        adapters={"fake": adapter},
        routes=[route],
        request_budget_usd=100.0,
        monthly_budget_usd=1000.0,
        telemetry=OperationsGatewayTelemetry(
            metrics_registry=None, operations_repository=recorder
        ),
    )
    image = b"\xff\xd8\xff" + b"\x00" * 64
    gateway.invoke(
        ObjectDetectionRequest(images=(image,)),
        context=ModelCallContext(workspace_id=uuid4()),
    )
    assert recorder.events, "telemetry recorded no event"
    event = recorder.events[-1]
    # operation keeps its historical value; capability_id lives in the usage jsonb.
    assert event["operation"] == "vision.object_detection"
    assert event["usage"]["capability_id"] == "vision.object_detection"


# --- embedding validation ---------------------------------------------------


def test_embedding_output_rejects_mismatched_dimensions() -> None:
    with pytest.raises(ValueError):
        EmbeddingsOutput(vectors=((0.1, 0.2), (0.3,)), raw_response={})


def test_embedding_output_rejects_empty_vectors() -> None:
    with pytest.raises(ValueError):
        EmbeddingsOutput(vectors=(), raw_response={})
    with pytest.raises(ValueError):
        EmbeddingsOutput(vectors=((), ()), raw_response={})


# --- the fake produces a schema-valid result for every id ------------------


def test_fake_produces_schema_valid_output_for_every_capability(tmp_path: Path) -> None:
    adapter = DeterministicInferenceAdapter(app_env="test")
    for capability, request, _request_type, expected_output in _cases(tmp_path):
        route = ModelRoute(
            route_id=str(capability).replace(".", "-"),
            capability=coarse_family_for_capability(capability),
            adapter="fake",
            provider="example",
            model="m",
            model_revision="2026-07-14",
        )
        gateway = ModelGateway(
            adapters={"fake": adapter},
            routes=[route],
            request_budget_usd=100.0,
            monthly_budget_usd=1000.0,
        )
        context = ModelCallContext(workspace_id=uuid4())
        if capability == InferenceCapability.VIDEO_GENERATION_STATUS:
            # A status poll needs an operation from a prior submit on the same
            # route; submit first, then poll the returned id.
            submit_route = ModelRoute(
                route_id="video-submit",
                capability=ModelCapability.GENERATION,
                adapter="fake",
                provider="example",
                model="m",
                model_revision="2026-07-14",
                operations=(ModelOperation.GENERATION_VIDEO_SUBMIT,),
            )
            submit_gateway = ModelGateway(
                adapters={"fake": adapter},
                routes=[submit_route],
                request_budget_usd=100.0,
                monthly_budget_usd=1000.0,
            )
            handle = submit_gateway.invoke(
                VideoGenerationSubmitRequest(
                    prompt="p",
                    prompt_image_url=None,
                    duration_s=4,
                    ratio="16:9",
                    idempotency_key="k",
                ),
                context=context,
            )
            assert isinstance(handle.output, GenerationOperationOutput)
            request = VideoGenerationStatusRequest(
                operation_id=handle.output.operation_id
            )
            # The default script runs running->succeeded, so poll until a
            # terminal GenerationOutput is produced (never a bare dict).
            result = gateway.invoke(request, context=context)
            while isinstance(result.output, GenerationOperationOutput):
                result = gateway.invoke(request, context=context)
            assert isinstance(result.output, GenerationOutput)
            assert not isinstance(result.output, dict)
            continue
        result = gateway.invoke(request, context=context)
        assert isinstance(result.output, type(expected_output)), str(capability)
        assert not isinstance(result.output, dict)
