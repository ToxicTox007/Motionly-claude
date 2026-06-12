from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.schemas.plans import EditPlan, EditPlanStyle
from app.schemas.renders import (
    CutSegment,
    DurationPreset,
    ExportOptions,
    ExportResolution,
    OutputAspect,
    PlatformPreset,
    RenderSpecs,
    RenderTemplateId,
)
from app.schemas.uploads import VideoMetadata

RENDERER_VERSION = "remotion-premium-v1"


@dataclass(frozen=True)
class TemplateMetadata:
    id: RenderTemplateId
    name: str
    style: EditPlanStyle
    caption_animation: str
    supported_effects: tuple[str, ...]
    default_color_grade: str
    intro: bool
    outro: bool


TEMPLATES: dict[RenderTemplateId, TemplateMetadata] = {
    RenderTemplateId.LUXURY_REEL_V1: TemplateMetadata(
        id=RenderTemplateId.LUXURY_REEL_V1,
        name="Luxury Reel",
        style=EditPlanStyle.LUXURY,
        caption_animation="gold_shimmer",
        supported_effects=("warm_grade", "soft_flash", "gold_shimmer", "ken_burns"),
        default_color_grade="champagne_gold",
        intro=True,
        outro=True,
    ),
    RenderTemplateId.FOUNDER_REEL_V1: TemplateMetadata(
        id=RenderTemplateId.FOUNDER_REEL_V1,
        name="Founder Reel",
        style=EditPlanStyle.FOUNDER,
        caption_animation="slide_up",
        supported_effects=("clean_lower_third", "emphasis_pulse", "ken_burns"),
        default_color_grade="clean_neutral",
        intro=True,
        outro=True,
    ),
    RenderTemplateId.TRAVEL_REEL_V1: TemplateMetadata(
        id=RenderTemplateId.TRAVEL_REEL_V1,
        name="Travel Reel",
        style=EditPlanStyle.TRAVEL,
        caption_animation="karaoke",
        supported_effects=("warm_grade", "flash_frame", "ken_burns", "map_card"),
        default_color_grade="sunlit",
        intro=True,
        outro=False,
    ),
    RenderTemplateId.PODCAST_REEL_V1: TemplateMetadata(
        id=RenderTemplateId.PODCAST_REEL_V1,
        name="Podcast Reel",
        style=EditPlanStyle.PODCAST,
        caption_animation="pulse",
        supported_effects=("waveform", "clean_lower_third", "emphasis_pulse"),
        default_color_grade="studio",
        intro=False,
        outro=True,
    ),
    RenderTemplateId.FASHION_REEL_V1: TemplateMetadata(
        id=RenderTemplateId.FASHION_REEL_V1,
        name="Fashion Reel",
        style=EditPlanStyle.FASHION,
        caption_animation="slide_up",
        supported_effects=("flash_frame", "mono_grade", "emphasis_pulse", "ken_burns"),
        default_color_grade="editorial_mono",
        intro=True,
        outro=True,
    ),
    RenderTemplateId.TALKING_HEAD_REEL_V1: TemplateMetadata(
        id=RenderTemplateId.TALKING_HEAD_REEL_V1,
        name="Talking Head Reel",
        style=EditPlanStyle.FOUNDER,
        caption_animation="slide_up",
        supported_effects=("clean_lower_third", "emphasis_pulse", "zoom_punch", "motion_blur"),
        default_color_grade="clean_founder",
        intro=False,
        outro=True,
    ),
    RenderTemplateId.PRODUCT_PROMO_REEL_V1: TemplateMetadata(
        id=RenderTemplateId.PRODUCT_PROMO_REEL_V1,
        name="Product Promo Reel",
        style=EditPlanStyle.LUXURY,
        caption_animation="gold_shimmer",
        supported_effects=("product_card", "warm_grade", "broll_slide_in", "gradient_ramp"),
        default_color_grade="champagne_gold",
        intro=True,
        outro=True,
    ),
}

STYLE_DEFAULTS: dict[EditPlanStyle, RenderTemplateId] = {
    EditPlanStyle.LUXURY: RenderTemplateId.LUXURY_REEL_V1,
    EditPlanStyle.FOUNDER: RenderTemplateId.FOUNDER_REEL_V1,
    EditPlanStyle.TRAVEL: RenderTemplateId.TRAVEL_REEL_V1,
    EditPlanStyle.PODCAST: RenderTemplateId.PODCAST_REEL_V1,
    EditPlanStyle.FASHION: RenderTemplateId.FASHION_REEL_V1,
}


def select_template(
    *,
    plan: EditPlan | None,
    requested_template_id: RenderTemplateId | None,
) -> TemplateMetadata:
    if requested_template_id:
        return TEMPLATES[requested_template_id]
    if plan:
        return TEMPLATES[STYLE_DEFAULTS[plan.style]]
    return TEMPLATES[RenderTemplateId.LUXURY_REEL_V1]


def effect_compatibility(
    *,
    template: TemplateMetadata,
    effects: list[str],
) -> tuple[list[str], list[str], list[str]]:
    supported = set(template.supported_effects)
    applied = [effect for effect in effects if effect in supported]
    unsupported = [effect for effect in effects if effect not in supported]
    downgraded = [f"{effect}->{template.default_color_grade}" for effect in unsupported]
    return applied, unsupported, downgraded


def build_props_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


RESOLUTION_SPECS: dict[ExportResolution, tuple[int, int]] = {
    ExportResolution.FREE_720P: (720, 1280),
    ExportResolution.PAID_1080P: (1080, 1920),
    ExportResolution.PRO_4K: (2160, 3840),
}

ASPECT_RATIOS: dict[OutputAspect, tuple[int, int]] = {
    OutputAspect.VERTICAL_9_16: (9, 16),
    OutputAspect.SQUARE_1_1: (1, 1),
    OutputAspect.PORTRAIT_4_5: (4, 5),
    OutputAspect.LANDSCAPE_16_9: (16, 9),
}

RESOLUTION_RANK: dict[str, int] = {"720p": 0, "1080p": 1, "4k": 2}

DURATION_TARGET_MS: dict[DurationPreset, int] = {
    DurationPreset.SHORT: 15_000,
    DurationPreset.MEDIUM: 30_000,
    DurationPreset.LONG: 60_000,
}

PLATFORM_METADATA: dict[PlatformPreset, dict[str, object]] = {
    PlatformPreset.INSTAGRAM: {
        "aspect_ratio": "9:16",
        "delivery": "reels",
        "safe_area_top_pct": 0.12,
        "safe_area_bottom_pct": 0.16,
    },
    PlatformPreset.TIKTOK: {
        "aspect_ratio": "9:16",
        "delivery": "for_you_feed",
        "safe_area_top_pct": 0.14,
        "safe_area_bottom_pct": 0.18,
    },
    PlatformPreset.SHORTS: {
        "aspect_ratio": "9:16",
        "delivery": "youtube_shorts",
        "safe_area_top_pct": 0.11,
        "safe_area_bottom_pct": 0.17,
    },
}


def render_specs_for_options(
    *,
    options: ExportOptions,
    max_resolution: str,
    source_metadata: VideoMetadata | None = None,
) -> tuple[RenderSpecs, ExportResolution]:
    requested_rank = RESOLUTION_RANK[options.resolution.value]
    max_rank = RESOLUTION_RANK.get(max_resolution, RESOLUTION_RANK["720p"])
    applied_resolution = options.resolution
    if requested_rank > max_rank:
        applied_resolution = (
            ExportResolution.PAID_1080P
            if max_rank >= RESOLUTION_RANK["1080p"]
            else ExportResolution.FREE_720P
        )
    width, height = _dimensions_for_aspect(
        applied_resolution=applied_resolution,
        output_aspect=options.framing.output_aspect,
        source_metadata=source_metadata,
    )
    return RenderSpecs(width=width, height=height), applied_resolution


def cap_keep_segments_to_duration(
    *,
    segments: list[CutSegment],
    duration_preset: DurationPreset,
) -> list[CutSegment]:
    target_ms = DURATION_TARGET_MS[duration_preset]
    capped: list[CutSegment] = []
    elapsed_ms = 0
    for segment in segments:
        segment_duration = segment.end_ms - segment.start_ms
        remaining_ms = target_ms - elapsed_ms
        if remaining_ms <= 0:
            break
        if segment_duration <= remaining_ms:
            capped.append(segment)
            elapsed_ms += segment_duration
            continue
        capped.append(
            CutSegment(
                start_ms=segment.start_ms,
                end_ms=segment.start_ms + remaining_ms,
                reason=f"{segment.reason}; capped for {duration_preset.value} export",
            )
        )
        break
    return capped or segments[:1]


def export_settings(
    *,
    specs: RenderSpecs,
    options: ExportOptions,
    applied_resolution: ExportResolution,
    duration_preset: DurationPreset,
) -> dict[str, object]:
    return {
        "platform": options.platform.value,
        "platform_metadata": PLATFORM_METADATA[options.platform],
        "requested_resolution": options.resolution.value,
        "resolution": applied_resolution.value,
        "duration_preset": duration_preset.value,
        "fps": specs.fps,
        "format": specs.format.value,
        "framing": options.framing.model_dump(mode="json"),
        "watermark": False,
    }


def _dimensions_for_aspect(
    *,
    applied_resolution: ExportResolution,
    output_aspect: OutputAspect,
    source_metadata: VideoMetadata | None,
) -> tuple[int, int]:
    base_width, base_height = RESOLUTION_SPECS[applied_resolution]
    max_long_edge = max(base_width, base_height)
    if output_aspect == OutputAspect.ORIGINAL:
        source_width = source_metadata.width if source_metadata else None
        source_height = source_metadata.height if source_metadata else None
        ratio_width, ratio_height = (
            (source_width, source_height)
            if source_width and source_height
            else ASPECT_RATIOS[OutputAspect.VERTICAL_9_16]
        )
    else:
        ratio_width, ratio_height = ASPECT_RATIOS[output_aspect]

    if ratio_width >= ratio_height:
        width = max_long_edge
        height = round(width * ratio_height / ratio_width)
    else:
        height = max_long_edge
        width = round(height * ratio_width / ratio_height)
    return _even(width), _even(height)


def _even(value: int) -> int:
    return max(2, int(round(value / 2) * 2))
