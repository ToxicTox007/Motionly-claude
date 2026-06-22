from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from app.schemas.plans import (
    EditPlan,
    EditPlanCut,
    TimelineAudioLayer,
    TimelineBrollLayer,
    TimelineCaptionLayer,
    TimelineDocument,
    TimelineEffectsLayer,
    TimelineSourceClip,
)

TIMELINE_SCHEMA_VERSION = 1


def compile_timeline(
    *,
    plan: EditPlan,
    source_video_id: UUID | None,
    source_duration_ms: int,
    captions: Iterable[object] | None = None,
    caption_style: dict[str, Any] | None = None,
    broll: Iterable[dict[str, Any]] | None = None,
    music: dict[str, Any] | None = None,
    source_stems: dict[str, Any] | None = None,
    effects_config: dict[str, Any] | None = None,
    preserve_layers_from: TimelineDocument | None = None,
) -> TimelineDocument:
    """Compile every editing record into the one renderer-neutral document."""
    clips = _source_clips(
        plan=plan,
        source_video_id=source_video_id,
        source_duration_ms=source_duration_ms,
    )
    duration_ms = clips[-1].output_end_ms
    preserved = preserve_layers_from
    compiled_captions = (
        _caption_layers(captions, clips=clips, style=caption_style or {})
        if captions is not None
        else list(preserved.captions if preserved else [])
    )
    compiled_broll = (
        _broll_layers(broll, clips=clips)
        if broll is not None
        else list(preserved.broll if preserved else [])
    )
    manual_layers = [item for item in plan.manual_elements if item.type != "transition"]
    transitions = [item for item in plan.manual_elements if item.type == "transition"]
    keyframes = []
    if effects_config:
        motion = effects_config.get("motion")
        if isinstance(motion, dict) and isinstance(motion.get("keyframes"), list):
            keyframes = list(motion["keyframes"])
    return TimelineDocument(
        schema_version=TIMELINE_SCHEMA_VERSION,
        duration_ms=duration_ms,
        clips=clips,
        captions=compiled_captions,
        broll=compiled_broll,
        audio=TimelineAudioLayer(
            source_stems=(
                source_stems
                if source_stems is not None
                else (preserved.audio.source_stems if preserved else None)
            ),
            music=(music if music is not None else (preserved.audio.music if preserved else None)),
            sfx=[item.model_dump(mode="json") for item in manual_layers if item.type == "sfx"],
        ),
        manual_layers=manual_layers,
        transitions=transitions,
        effects=TimelineEffectsLayer(
            color_grade=plan.color_grade,
            effects=list(plan.effects),
            zoom_moments=list(plan.zoom_moments),
            keyframes=keyframes,
            config=(
                effects_config
                if effects_config is not None
                else (preserved.effects.config if preserved else None)
            ),
        ),
        metadata={
            "authority": "project_timeline",
            "legacy_edit_plan_compatibility": True,
        },
    )


def migrate_legacy_plan_to_timeline(
    *,
    plan: EditPlan,
    source_video_id: UUID | None,
    source_duration_ms: int,
) -> EditPlan:
    if plan.timeline is not None:
        return plan
    timeline = compile_timeline(
        plan=plan,
        source_video_id=source_video_id,
        source_duration_ms=source_duration_ms,
    )
    return plan.model_copy(update={"timeline": timeline})


def project_timeline_to_legacy_plan(*, plan: EditPlan, timeline: TimelineDocument) -> EditPlan:
    """Keep old API clients operational while the timeline remains authoritative."""
    cuts = [
        EditPlanCut(
            start_ms=clip.source_start_ms,
            end_ms=clip.source_end_ms,
            reason=clip.reason,
        )
        for clip in timeline.clips
    ]
    manual_elements = [*timeline.manual_layers, *timeline.transitions]
    return plan.model_copy(
        update={
            "cuts": cuts,
            "manual_elements": manual_elements,
            "color_grade": timeline.effects.color_grade,
            "effects": timeline.effects.effects,
            "zoom_moments": timeline.effects.zoom_moments,
            "target_duration_s": max(10, round(timeline.duration_ms / 1000)),
            "timeline": timeline,
        }
    )


def _source_clips(
    *, plan: EditPlan, source_video_id: UUID | None, source_duration_ms: int
) -> list[TimelineSourceClip]:
    source_cuts = plan.cuts
    output_cursor = 0
    clips: list[TimelineSourceClip] = []
    for index, cut in enumerate(source_cuts):
        start_ms = max(0, min(source_duration_ms, cut.start_ms))
        end_ms = max(start_ms + 1, min(source_duration_ms, cut.end_ms))
        duration_ms = end_ms - start_ms
        clips.append(
            TimelineSourceClip(
                id=f"cut-{index}",
                source_video_id=source_video_id,
                source_start_ms=start_ms,
                source_end_ms=end_ms,
                output_start_ms=output_cursor,
                output_end_ms=output_cursor + duration_ms,
                reason=cut.reason,
            )
        )
        output_cursor += duration_ms
    if clips:
        return clips
    fallback_duration = max(1, source_duration_ms)
    return [
        TimelineSourceClip(
            id="cut-0",
            source_video_id=source_video_id,
            source_start_ms=0,
            source_end_ms=fallback_duration,
            output_start_ms=0,
            output_end_ms=fallback_duration,
            reason="full source fallback",
        )
    ]


def _caption_layers(
    captions: Iterable[object],
    *,
    clips: list[TimelineSourceClip],
    style: dict[str, Any],
) -> list[TimelineCaptionLayer]:
    layers: list[TimelineCaptionLayer] = []
    for raw in captions:
        item = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict(raw)
        for clip in clips:
            overlap_start = max(int(item.get("start_ms", 0)), clip.source_start_ms)
            overlap_end = min(int(item.get("end_ms", 0)), clip.source_end_ms)
            if overlap_end <= overlap_start:
                continue
            layers.append(
                TimelineCaptionLayer(
                    id=f"caption-{item.get('id', item.get('index', len(layers)))}-{clip.id}",
                    index=int(item.get("index") or 0),
                    start_ms=clip.output_start_ms + overlap_start - clip.source_start_ms,
                    end_ms=clip.output_start_ms + overlap_end - clip.source_start_ms,
                    text=str(item.get("text") or ""),
                    words=_retimed_words(
                        list(item.get("words") or []),
                        clip=clip,
                    ),
                    line_count=int(item.get("line_count") or 1),
                    highlight_words=list(item.get("highlight_words") or []),
                    style=style,
                )
            )
    return layers


def _retimed_words(
    words: list[dict[str, Any]], *, clip: TimelineSourceClip
) -> list[dict[str, Any]]:
    retimed: list[dict[str, Any]] = []
    for word in words:
        overlap_start = max(int(word.get("start_ms") or 0), clip.source_start_ms)
        overlap_end = min(int(word.get("end_ms") or 0), clip.source_end_ms)
        if overlap_end <= overlap_start:
            continue
        retimed.append(
            {
                **word,
                "start_ms": clip.output_start_ms + overlap_start - clip.source_start_ms,
                "end_ms": clip.output_start_ms + overlap_end - clip.source_start_ms,
            }
        )
    return retimed


def _broll_layers(
    broll: Iterable[dict[str, Any]], *, clips: list[TimelineSourceClip]
) -> list[TimelineBrollLayer]:
    layers: list[TimelineBrollLayer] = []
    for index, item in enumerate(broll):
        start_ms = int(item.get("start_ms") or 0)
        end_ms = int(item.get("end_ms") or 0)
        if end_ms <= start_ms:
            continue
        ranges = [(start_ms, end_ms)]
        if item.get("source_start_ms") is None:
            ranges = [
                (
                    clip.output_start_ms + overlap_start - clip.source_start_ms,
                    clip.output_start_ms + overlap_end - clip.source_start_ms,
                )
                for clip in clips
                for overlap_start, overlap_end in [
                    (max(start_ms, clip.source_start_ms), min(end_ms, clip.source_end_ms))
                ]
                if overlap_end > overlap_start
            ]
        for range_index, (output_start, output_end) in enumerate(ranges):
            layer_id = str(item.get("id") or f"broll-{index}")
            if len(ranges) > 1:
                layer_id = f"{layer_id}-cut-{range_index}"
            layers.append(
                TimelineBrollLayer(
                    id=layer_id,
                    start_ms=output_start,
                    end_ms=output_end,
                    src=str(item.get("src") or ""),
                    source_start_ms=_optional_int(
                        item.get("source_start_ms")
                        if item.get("source_start_ms") is not None
                        else start_ms
                    ),
                    source_end_ms=_optional_int(
                        item.get("source_end_ms")
                        if item.get("source_end_ms") is not None
                        else end_ms
                    ),
                    metadata={
                        key: value
                        for key, value in item.items()
                        if key
                        not in {
                            "id",
                            "start_ms",
                            "end_ms",
                            "src",
                            "source_start_ms",
                            "source_end_ms",
                        }
                    },
                )
            )
    return layers


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None
