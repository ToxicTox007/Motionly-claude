"""The fine-grained Motionly capability taxonomy (plan Section 2.1).

Capability ids are stable, provider-neutral Motionly API concepts. Adapter
details never leak into them. ``InferenceCapability`` carries the 14 ids of the
Section 2.1 table; the two orchestration concepts ``vision.video_manifest`` and
``audio.intelligence`` are kept separate in ``PipelineOperation`` because they
call the fine-grained capabilities rather than becoming provider routes.

The coarse ``ModelCapability`` families (planning, transcription, vision, audio,
embeddings, generation) are still used by the gateway route filter, so this
module provides ``coarse_family_for_capability`` to map every fine-grained id
back onto its family.

Old ``ModelOperation`` values remain readable through ``LEGACY_OPERATION_ALIASES``
and ``parse_capability`` so historical serialized operation strings keep parsing
while callers migrate (Task 3).
"""

from __future__ import annotations

from enum import StrEnum

from app.services.model_gateway import ModelCapability, ModelOperation


class InferenceCapability(StrEnum):
    """The 14 fine-grained capability ids of plan Section 2.1."""

    PLANNING_CHAT = "planning.chat"
    TRANSCRIPTION_SPEECH_TO_TEXT = "transcription.speech_to_text"
    VISION_CAPTION = "vision.caption"
    VISION_OBJECT_DETECTION = "vision.object_detection"
    EMBEDDINGS_TEXT = "embeddings.text"
    EMBEDDINGS_IMAGE = "embeddings.image"
    AUDIO_DIARIZATION = "audio.diarization"
    AUDIO_EMOTION = "audio.emotion"
    AUDIO_STEM_SEPARATION = "audio.stem_separation"
    IMAGE_GENERATION = "image.generation"
    VIDEO_GENERATION_SUBMIT = "video.generation.submit"
    VIDEO_GENERATION_STATUS = "video.generation.status"
    MATTE_SEGMENTATION = "matte.segmentation"
    AUDIO_SOUND_CLASSIFICATION = "audio.sound_classification"


class PipelineOperation(StrEnum):
    """Orchestration concepts that call capabilities but are not provider routes.

    Section 2.1: ``vision.video_manifest`` and ``audio.intelligence`` remain
    pipeline concepts. They are kept separate from ``InferenceCapability`` so a
    route can never be configured for them.
    """

    VISION_VIDEO_MANIFEST = "vision.video_manifest"
    AUDIO_INTELLIGENCE = "audio.intelligence"


# Coarse family (used by the gateway route filter) for every fine-grained id.
_COARSE_FAMILY_BY_CAPABILITY: dict[InferenceCapability, ModelCapability] = {
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

# Old ``ModelOperation`` value -> new fine-grained id. Unchanged ids map to
# themselves (added below). Historical serialized operation names stay readable.
LEGACY_OPERATION_ALIASES: dict[str, InferenceCapability] = {
    # Operations whose string changed between the coarse and fine taxonomy.
    "vision.prompt": InferenceCapability.VISION_CAPTION,
    "audio.prompt": InferenceCapability.AUDIO_EMOTION,
    "vision.matte_segmentation": InferenceCapability.MATTE_SEGMENTATION,
    "generation.image": InferenceCapability.IMAGE_GENERATION,
    "generation.video_submit": InferenceCapability.VIDEO_GENERATION_SUBMIT,
    "generation.video_poll": InferenceCapability.VIDEO_GENERATION_STATUS,
    # Operations whose string is unchanged keep mapping to themselves.
    "planning.chat": InferenceCapability.PLANNING_CHAT,
    "transcription.speech_to_text": InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
    "vision.object_detection": InferenceCapability.VISION_OBJECT_DETECTION,
    "embeddings.text": InferenceCapability.EMBEDDINGS_TEXT,
    "embeddings.image": InferenceCapability.EMBEDDINGS_IMAGE,
    "audio.diarization": InferenceCapability.AUDIO_DIARIZATION,
    "audio.stem_separation": InferenceCapability.AUDIO_STEM_SEPARATION,
}


def coarse_family_for_capability(capability: InferenceCapability) -> ModelCapability:
    """The coarse ``ModelCapability`` family the route filter uses for an id."""
    return _COARSE_FAMILY_BY_CAPABILITY[capability]


# Legacy ``ModelOperation`` value for every fine-grained capability id. The
# gateway still filters routes by ``ModelOperation``; a registry route carries
# exactly one operation, the one for its capability. Only the ids whose string
# changed between the coarse and fine taxonomy are remapped; the rest reuse the
# same string (plan Task 4, ``to_model_route``).
_OPERATION_BY_CAPABILITY: dict[InferenceCapability, ModelOperation] = {
    InferenceCapability.VISION_CAPTION: ModelOperation.VISION_PROMPT,
    InferenceCapability.AUDIO_EMOTION: ModelOperation.AUDIO_PROMPT,
    InferenceCapability.MATTE_SEGMENTATION: ModelOperation.VISION_MATTE_SEGMENTATION,
    InferenceCapability.IMAGE_GENERATION: ModelOperation.GENERATION_IMAGE,
    InferenceCapability.VIDEO_GENERATION_SUBMIT: ModelOperation.GENERATION_VIDEO_SUBMIT,
    InferenceCapability.VIDEO_GENERATION_STATUS: ModelOperation.GENERATION_VIDEO_POLL,
    InferenceCapability.AUDIO_SOUND_CLASSIFICATION: (
        ModelOperation.AUDIO_SOUND_CLASSIFICATION
    ),
    # Ids whose operation string is unchanged map to the same-named operation.
    InferenceCapability.PLANNING_CHAT: ModelOperation.PLANNING_CHAT,
    InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT: (
        ModelOperation.TRANSCRIPTION_SPEECH_TO_TEXT
    ),
    InferenceCapability.VISION_OBJECT_DETECTION: ModelOperation.VISION_OBJECT_DETECTION,
    InferenceCapability.EMBEDDINGS_TEXT: ModelOperation.EMBEDDINGS_TEXT,
    InferenceCapability.EMBEDDINGS_IMAGE: ModelOperation.EMBEDDINGS_IMAGE,
    InferenceCapability.AUDIO_DIARIZATION: ModelOperation.AUDIO_DIARIZATION,
    InferenceCapability.AUDIO_STEM_SEPARATION: ModelOperation.AUDIO_STEM_SEPARATION,
}


def model_operation_for_capability(capability: InferenceCapability) -> ModelOperation:
    """The single legacy ``ModelOperation`` a registry route carries for an id."""
    return _OPERATION_BY_CAPABILITY[capability]


def parse_capability(value: str) -> InferenceCapability:
    """Parse a new id or a legacy ``ModelOperation`` string into a capability.

    Accepts every id in ``InferenceCapability`` and every old operation string in
    ``LEGACY_OPERATION_ALIASES``. Unknown strings raise ``ValueError``. The two
    pipeline operations (``vision.video_manifest`` and ``audio.intelligence``)
    are not capabilities and are rejected.
    """
    text = str(value).strip()
    try:
        return InferenceCapability(text)
    except ValueError:
        pass
    alias = LEGACY_OPERATION_ALIASES.get(text)
    if alias is not None:
        return alias
    raise ValueError(f"unknown inference capability id: {value!r}")
