"""Canonical typed Motionly request/response contracts (plan Section 2.1).

This module is the public, provider-neutral surface for the fine-grained
capability schemas. The classes are defined in ``model_gateway`` (the engine and
the ``ModelRequest``/``ModelOutput`` unions live there, and the fake adapter
imports them from there), so importing them here would create a cycle if they
were redefined. Instead this module re-exports them under their Section 2.1
canonical names and keeps the old names as aliases, so every current caller
keeps working while callers migrate within Tier 1.

Re-export approach (least churn): the Section 2.1 name is canonical; the legacy
name is an alias bound to the same class, with unchanged fields. Where Section
2.1's field list differs from today's, today's fields are kept and the gap is
noted for the task that will add the fields:

- ``VisionCaptionRequest`` keeps ``VisionRequest``'s single prompt + image_urls
  instead of Section 2.1's ``images``/``captions[]``; the multi-image caption
  contract is Task 11.
- ``AudioEmotionOutput`` keeps ``AudioOutput``'s ``content`` instead of Section
  2.1's ``windows[]``; ``AudioEmotionWindow`` is the typed window Task 11 wires in.
- ``OperationHandle`` (= ``GenerationOperationOutput``) carries ``operation_id``
  only; the ``route_id``/``model_revision`` fields of Section 2.1 are supplied by
  the gateway metadata / Task 16A operation record.
- ``OperationStatus`` (= ``GenerationOutput``) carries ``artifacts`` +
  ``operation_id``; the explicit ``state``/``error``/``usage`` fields of Section
  2.1 are Task 18 / Task 16A.
- ``TextEmbeddingsRequest`` (= ``EmbeddingsRequest``) and ``ImageGenerationRequest``
  (= ``GenerationRequest``) match Section 2.1's fields.
"""

from __future__ import annotations

from app.services.model_gateway import (
    AudioEmotionWindow,
    AudioOutput,
    AudioRequest,
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
    ObjectDetectionOutput,
    ObjectDetectionRequest,
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
    VisionOutput,
    VisionRequest,
)

# --- canonical Section 2.1 names (aliases onto the engine classes) ----------

# planning.chat
# (PlanningRequest / PlanningOutput are already canonical.)

# transcription.speech_to_text
# (TranscriptionRequest / TranscriptionOutput are already canonical.)

# vision.caption
VisionCaptionRequest = VisionRequest
VisionCaptionOutput = VisionOutput

# vision.object_detection
# (ObjectDetectionRequest / ObjectDetectionOutput are already canonical.)

# embeddings.text
TextEmbeddingsRequest = EmbeddingsRequest

# embeddings.image
# (ImageEmbeddingsRequest is already canonical; EmbeddingsOutput is shared.)

# audio.diarization
# (DiarizationRequest / DiarizationOutput are already canonical.)

# audio.emotion
AudioEmotionRequest = AudioRequest
AudioEmotionOutput = AudioOutput

# audio.stem_separation
# (StemSeparationRequest / StemSeparationOutput are already canonical.)

# image.generation
ImageGenerationRequest = GenerationRequest

# video.generation.submit
OperationHandle = GenerationOperationOutput

# video.generation.status
VideoGenerationStatusRequest = VideoGenerationPollRequest
OperationStatus = GenerationOutput

# matte.segmentation
# (MatteSegmentationRequest / MatteSegmentationOutput are already canonical.)

# audio.sound_classification
# (SoundClassificationRequest / SoundClassificationOutput are already canonical.)


__all__ = [
    # planning.chat
    "PlanningRequest",
    "PlanningOutput",
    # transcription.speech_to_text
    "TranscriptionRequest",
    "TranscriptionOutput",
    "TranscriptWordOutput",
    # vision.caption
    "VisionCaptionRequest",
    "VisionCaptionOutput",
    "VisionRequest",
    "VisionOutput",
    # vision.object_detection
    "ObjectDetectionRequest",
    "ObjectDetectionOutput",
    "DetectedObjectOutput",
    # embeddings.text
    "TextEmbeddingsRequest",
    "EmbeddingsRequest",
    "EmbeddingsOutput",
    # embeddings.image
    "ImageEmbeddingsRequest",
    # audio.diarization
    "DiarizationRequest",
    "DiarizationOutput",
    "DiarizationSegmentOutput",
    # audio.emotion
    "AudioEmotionRequest",
    "AudioEmotionOutput",
    "AudioEmotionWindow",
    "AudioRequest",
    "AudioOutput",
    # audio.stem_separation
    "StemSeparationRequest",
    "StemSeparationOutput",
    # image.generation
    "ImageGenerationRequest",
    "GenerationRequest",
    "GenerationOutput",
    "GeneratedArtifact",
    # video.generation.submit
    "VideoGenerationSubmitRequest",
    "OperationHandle",
    "GenerationOperationOutput",
    # video.generation.status
    "VideoGenerationStatusRequest",
    "VideoGenerationPollRequest",
    "OperationStatus",
    # matte.segmentation
    "MatteSegmentationRequest",
    "MatteSegmentationOutput",
    # audio.sound_classification
    "SoundClassificationRequest",
    "SoundClassificationOutput",
    "SoundLabelOutput",
]
