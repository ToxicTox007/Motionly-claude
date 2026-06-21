from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.request_body import JsonStringBodyMixin

EFFECTS_SCHEMA_VERSION = 3


class VisualPresetKey(StrEnum):
    VOGUE_LUXURY = "vogue_luxury"
    CHAMPAGNE_GOLD = "champagne_gold"
    DUBAI_INFLUENCER = "dubai_influencer"
    OLD_MONEY = "old_money"
    CLEAN_FOUNDER = "clean_founder"
    CINEMATIC_HOTEL = "cinematic_hotel"


class MotionEffectKey(StrEnum):
    ZOOM_PUNCH = "zoom_punch"
    KEN_BURNS = "ken_burns"
    SPEED_RAMP = "speed_ramp"
    BEAT_IMPACT = "beat_impact"
    MOTION_BLUR = "motion_blur"
    FLASH = "flash"
    GLITCH = "glitch"
    BROLL_SLIDE_IN = "broll_slide_in"
    GRADIENT_RAMP = "gradient_ramp"


class EffectsStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"


class EffectsTextureSettings(BaseModel):
    grain: float = Field(default=0.18, ge=0, le=1)
    vignette: float = Field(default=0.16, ge=0, le=1)
    light_leak: float = Field(default=0.08, ge=0, le=1)
    anamorphic_flare: bool = False


class BackgroundReplacement(StrEnum):
    TRANSPARENT = "transparent"
    GRADIENT_RAMP = "gradient_ramp"
    BLURRED_SOURCE = "blurred_source"


class BackgroundRemovalSettings(BaseModel):
    enabled: bool = False
    apply_to_broll: bool = True
    feather_px: int = Field(default=18, ge=0, le=80)
    spill_suppression: float = Field(default=0.45, ge=0, le=1)
    replacement: BackgroundReplacement = BackgroundReplacement.GRADIENT_RAMP


class GradientRampBlendMode(StrEnum):
    SCREEN = "screen"
    OVERLAY = "overlay"
    SOFT_LIGHT = "soft-light"
    NORMAL = "normal"


class GradientRampSettings(BaseModel):
    enabled: bool = False
    start_color: str = Field(default="#0f172a", pattern=r"^#[0-9a-fA-F]{6}$")
    end_color: str = Field(default="#f59e0b", pattern=r"^#[0-9a-fA-F]{6}$")
    angle_deg: int = Field(default=135, ge=0, le=360)
    opacity: float = Field(default=0.28, ge=0, le=1)
    blend_mode: GradientRampBlendMode = GradientRampBlendMode.SOFT_LIGHT


class MotionEasing(StrEnum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    SPRING = "spring"


class MotionTarget(StrEnum):
    SOURCE = "source"
    BROLL = "broll"
    ALL = "all"


class MotionKeyframe(BaseModel):
    target: MotionTarget = MotionTarget.SOURCE
    clip_id: str | None = Field(default=None, max_length=120)
    at_ms: int = Field(ge=0)
    scale: float = Field(default=1, ge=0.5, le=3)
    x_pct: float = Field(default=50, ge=0, le=100)
    y_pct: float = Field(default=50, ge=0, le=100)
    rotation_deg: float = Field(default=0, ge=-45, le=45)
    opacity: float = Field(default=1, ge=0, le=1)
    easing: MotionEasing = MotionEasing.EASE_IN_OUT


class KenBurnsSettings(BaseModel):
    enabled: bool = False
    intensity: float = Field(default=0.08, ge=0, le=0.35)
    direction: Literal[
        "push_in",
        "pull_out",
        "pan_left",
        "pan_right",
        "pan_up",
        "pan_down",
    ] = "push_in"
    easing: MotionEasing = MotionEasing.EASE_IN_OUT


class SpeedRampSettings(BaseModel):
    enabled: bool = False
    ramp_in_ms: int = Field(default=280, ge=80, le=1200)
    ramp_out_ms: int = Field(default=320, ge=80, le=1600)
    peak_speed: float = Field(default=1.35, ge=1, le=3)
    easing: MotionEasing = MotionEasing.EASE_IN_OUT


class BeatImpactSettings(BaseModel):
    enabled: bool = False
    intensity: float = Field(default=0.22, ge=0, le=1)
    shake_px: int = Field(default=10, ge=0, le=48)
    zoom: float = Field(default=1.035, ge=1, le=1.2)
    max_impacts: int = Field(default=12, ge=0, le=48)


class AutoReframeSettings(BaseModel):
    enabled: bool = False
    target_aspect: Literal["9:16", "1:1", "4:5", "16:9"] = "9:16"
    subject_x_pct: float = Field(default=50, ge=0, le=100)
    subject_y_pct: float = Field(default=50, ge=0, le=100)
    smoothing: float = Field(default=0.65, ge=0, le=1)


class MotionSettings(BaseModel):
    keyframes: list[MotionKeyframe] = Field(default_factory=list, max_length=48)
    ken_burns: KenBurnsSettings = Field(default_factory=KenBurnsSettings)
    speed_ramp: SpeedRampSettings = Field(default_factory=SpeedRampSettings)
    beat_impact: BeatImpactSettings = Field(default_factory=BeatImpactSettings)
    auto_reframe: AutoReframeSettings = Field(default_factory=AutoReframeSettings)
    clip_effects: dict[str, list[MotionEffectKey]] = Field(default_factory=dict)


class AfterEffectsPreset(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    intensity: float = Field(default=0.65, ge=0, le=1)
    expression_controls: dict[str, float | str | bool] = Field(default_factory=dict)


class EffectsConfigRequest(JsonStringBodyMixin, BaseModel):
    preset: VisualPresetKey
    lut_intensity: float = Field(default=0.75, ge=0.5, le=1)
    texture: EffectsTextureSettings = Field(default_factory=EffectsTextureSettings)
    motion_effects: list[MotionEffectKey] = Field(default_factory=list, max_length=10)
    motion: MotionSettings = Field(default_factory=MotionSettings)
    background_removal: BackgroundRemovalSettings = Field(default_factory=BackgroundRemovalSettings)
    gradient_ramp: GradientRampSettings = Field(default_factory=GradientRampSettings)
    after_effects_presets: list[AfterEffectsPreset] = Field(
        default_factory=list,
        max_length=6,
    )
    reset_to_defaults: bool = False
    force: bool = False

    @model_validator(mode="before")
    @classmethod
    def coerce_json_string_body(cls, data: Any) -> Any:
        return cls._coerce_json_string_body(data)

    @model_validator(mode="after")
    def cap_readability_risk(self) -> "EffectsConfigRequest":
        if self.texture.grain + self.texture.vignette + self.texture.light_leak > 1.55:
            raise ValueError("Texture intensity is too high for reliable caption readability.")
        if self.gradient_ramp.enabled and self.gradient_ramp.opacity > 0.72:
            raise ValueError("Gradient Ramp opacity is too high for reliable caption readability.")
        return self


class EffectsConfigResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    edit_plan_id: UUID | None = None
    version: int
    schema_version: int
    status: EffectsStatus
    preset: VisualPresetKey
    lut_intensity: float = Field(ge=0.5, le=1)
    texture: EffectsTextureSettings
    motion_effects: list[MotionEffectKey]
    motion: MotionSettings
    background_removal: BackgroundRemovalSettings
    gradient_ramp: GradientRampSettings
    after_effects_presets: list[AfterEffectsPreset]
    after_effects_project_url: str | None = None
    correction_manifest_url: str | None = None
    effect_count_limit_per_30s: int
    render_time_impact: Literal["low", "medium", "high"]
    preview_before_url: str | None = None
    preview_after_url: str | None = None
    cache_key: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class EffectsUpdateResponse(BaseModel):
    config: EffectsConfigResponse
    job_id: str
    status: Literal["ready"] = "ready"
    cached: bool = False
