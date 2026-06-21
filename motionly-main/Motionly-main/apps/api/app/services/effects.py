from uuid import UUID

from app.schemas.effects import (
    AfterEffectsPreset,
    AutoReframeSettings,
    BackgroundRemovalSettings,
    BeatImpactSettings,
    EffectsTextureSettings,
    GradientRampSettings,
    KenBurnsSettings,
    MotionEffectKey,
    MotionKeyframe,
    MotionSettings,
    SpeedRampSettings,
    VisualPresetKey,
)
from app.schemas.plans import EditPlanStyle

PRESET_DEFAULTS: dict[VisualPresetKey, dict[str, object]] = {
    VisualPresetKey.VOGUE_LUXURY: {
        "style": EditPlanStyle.DRAMATIC,
        "lut": "luts/vogue_luxury.cube",
        "lut_intensity": 0.82,
        "texture": EffectsTextureSettings(grain=0.22, vignette=0.2, light_leak=0.08),
        "motion_effects": [
            MotionEffectKey.ZOOM_PUNCH,
            MotionEffectKey.BEAT_IMPACT,
            MotionEffectKey.FLASH,
        ],
        "motion": MotionSettings(
            beat_impact=BeatImpactSettings(enabled=True, intensity=0.28, shake_px=8, zoom=1.04),
            keyframes=[
                MotionKeyframe(at_ms=0, scale=1.04, x_pct=50, y_pct=48),
                MotionKeyframe(at_ms=1800, scale=1.1, x_pct=52, y_pct=48),
            ],
        ),
        "gradient": GradientRampSettings(
            enabled=True,
            start_color="#111827",
            end_color="#f8fafc",
            angle_deg=118,
            opacity=0.24,
        ),
    },
    VisualPresetKey.CHAMPAGNE_GOLD: {
        "style": EditPlanStyle.LUXURY,
        "lut": "luts/champagne_gold.cube",
        "lut_intensity": 0.76,
        "texture": EffectsTextureSettings(grain=0.16, vignette=0.14, light_leak=0.12),
        "motion_effects": [
            MotionEffectKey.ZOOM_PUNCH,
            MotionEffectKey.KEN_BURNS,
            MotionEffectKey.BROLL_SLIDE_IN,
        ],
        "motion": MotionSettings(
            ken_burns=KenBurnsSettings(enabled=True, intensity=0.07, direction="push_in"),
            keyframes=[
                MotionKeyframe(at_ms=0, scale=1, x_pct=50, y_pct=50),
                MotionKeyframe(at_ms=2400, scale=1.07, x_pct=49, y_pct=48),
            ],
        ),
        "gradient": GradientRampSettings(
            enabled=False,
            start_color="#20120b",
            end_color="#f5c76b",
            angle_deg=135,
            opacity=0.28,
        ),
    },
    VisualPresetKey.DUBAI_INFLUENCER: {
        "style": EditPlanStyle.FAST_PACED,
        "lut": "luts/dubai_influencer.cube",
        "lut_intensity": 0.8,
        "texture": EffectsTextureSettings(
            grain=0.12,
            vignette=0.1,
            light_leak=0.18,
            anamorphic_flare=True,
        ),
        "motion_effects": [
            MotionEffectKey.SPEED_RAMP,
            MotionEffectKey.BEAT_IMPACT,
            MotionEffectKey.FLASH,
            MotionEffectKey.BROLL_SLIDE_IN,
        ],
        "motion": MotionSettings(
            speed_ramp=SpeedRampSettings(
                enabled=True,
                ramp_in_ms=220,
                ramp_out_ms=340,
                peak_speed=1.55,
            ),
            beat_impact=BeatImpactSettings(enabled=True, intensity=0.32, shake_px=14, zoom=1.05),
            auto_reframe=AutoReframeSettings(enabled=True, target_aspect="9:16", smoothing=0.72),
        ),
        "gradient": GradientRampSettings(
            enabled=False,
            start_color="#0284c7",
            end_color="#f97316",
            angle_deg=122,
            opacity=0.22,
            blend_mode="screen",
        ),
    },
    VisualPresetKey.OLD_MONEY: {
        "style": EditPlanStyle.LUXURY,
        "lut": "luts/old_money.cube",
        "lut_intensity": 0.72,
        "texture": EffectsTextureSettings(grain=0.2, vignette=0.18, light_leak=0.05),
        "motion_effects": [MotionEffectKey.MOTION_BLUR],
        "motion": MotionSettings(),
        "gradient": GradientRampSettings(
            enabled=True,
            start_color="#1f2933",
            end_color="#bfa46a",
            angle_deg=146,
            opacity=0.18,
        ),
    },
    VisualPresetKey.CLEAN_FOUNDER: {
        "style": EditPlanStyle.CLEAN_MINIMAL,
        "lut": "luts/clean_founder.cube",
        "lut_intensity": 0.62,
        "texture": EffectsTextureSettings(grain=0.06, vignette=0.06, light_leak=0),
        "motion_effects": [MotionEffectKey.ZOOM_PUNCH],
        "motion": MotionSettings(
            auto_reframe=AutoReframeSettings(enabled=True, target_aspect="9:16", smoothing=0.8),
        ),
        "gradient": GradientRampSettings(
            enabled=False,
            start_color="#0f172a",
            end_color="#38bdf8",
            angle_deg=90,
            opacity=0.12,
            blend_mode="normal",
        ),
    },
    VisualPresetKey.CINEMATIC_HOTEL: {
        "style": EditPlanStyle.CINEMATIC,
        "lut": "luts/cinematic_hotel.cube",
        "lut_intensity": 0.84,
        "texture": EffectsTextureSettings(
            grain=0.24,
            vignette=0.24,
            light_leak=0.1,
            anamorphic_flare=True,
        ),
        "motion_effects": [
            MotionEffectKey.KEN_BURNS,
            MotionEffectKey.MOTION_BLUR,
            MotionEffectKey.FLASH,
        ],
        "motion": MotionSettings(
            ken_burns=KenBurnsSettings(enabled=True, intensity=0.11, direction="push_in"),
            keyframes=[
                MotionKeyframe(at_ms=0, scale=1.02, x_pct=50, y_pct=49),
                MotionKeyframe(at_ms=3200, scale=1.13, x_pct=51, y_pct=47),
            ],
        ),
        "gradient": GradientRampSettings(
            enabled=True,
            start_color="#111827",
            end_color="#c08457",
            angle_deg=152,
            opacity=0.26,
        ),
    },
}

DEFAULT_AFTER_EFFECTS_PRESETS = [
    AfterEffectsPreset(
        name="Motionly Correction Controls",
        intensity=0.5,
        expression_controls={
            "lutIntensity": 0.75,
            "captionSafeArea": True,
        },
    ),
]


def defaults_for_preset(preset: VisualPresetKey) -> dict[str, object]:
    defaults = PRESET_DEFAULTS[preset]
    return {
        "lut_intensity": defaults["lut_intensity"],
        "texture": defaults["texture"],
        "motion_effects": list(defaults["motion_effects"]),  # type: ignore[arg-type]
        "motion": defaults["motion"],
        "background_removal": BackgroundRemovalSettings(),
        "gradient_ramp": defaults["gradient"],
        "after_effects_presets": DEFAULT_AFTER_EFFECTS_PRESETS,
    }


def normalize_motion_and_gradient(
    *,
    motion_effects: list[MotionEffectKey],
    motion: MotionSettings | None = None,
    gradient_ramp: GradientRampSettings,
) -> tuple[list[MotionEffectKey], MotionSettings, GradientRampSettings]:
    motion = motion or MotionSettings()
    canonical_motion_effects: list[MotionEffectKey] = []
    for effect in motion_effects:
        if effect == MotionEffectKey.GRADIENT_RAMP:
            continue
        if effect not in canonical_motion_effects:
            canonical_motion_effects.append(effect)
    if MotionEffectKey.GRADIENT_RAMP in motion_effects:
        gradient_ramp = gradient_ramp.model_copy(update={"enabled": True})

    ken_burns = motion.ken_burns
    speed_ramp = motion.speed_ramp
    beat_impact = motion.beat_impact
    if MotionEffectKey.KEN_BURNS in canonical_motion_effects and not ken_burns.enabled:
        ken_burns = ken_burns.model_copy(update={"enabled": True})
    if MotionEffectKey.SPEED_RAMP in canonical_motion_effects and not speed_ramp.enabled:
        speed_ramp = speed_ramp.model_copy(update={"enabled": True})
    if MotionEffectKey.BEAT_IMPACT in canonical_motion_effects and not beat_impact.enabled:
        beat_impact = beat_impact.model_copy(update={"enabled": True})

    normalized_motion = motion.model_copy(
        update={
            "ken_burns": ken_burns,
            "speed_ramp": speed_ramp,
            "beat_impact": beat_impact,
        }
    )
    for effect, enabled in (
        (MotionEffectKey.KEN_BURNS, normalized_motion.ken_burns.enabled),
        (MotionEffectKey.SPEED_RAMP, normalized_motion.speed_ramp.enabled),
        (MotionEffectKey.BEAT_IMPACT, normalized_motion.beat_impact.enabled),
    ):
        if enabled and effect not in canonical_motion_effects:
            canonical_motion_effects.append(effect)

    return canonical_motion_effects, normalized_motion, gradient_ramp


def effect_count_limit_per_30s(
    effects: list[MotionEffectKey],
    motion: MotionSettings | None = None,
) -> int:
    motion_cost = _motion_complexity(motion)
    clip_effect_count = sum(len(v) for v in (motion.clip_effects if motion else {}).values())
    return min(12, max(2, len(effects) + 2 + motion_cost + clip_effect_count // 3))


def render_time_impact(
    *,
    texture: EffectsTextureSettings,
    motion_effects: list[MotionEffectKey],
    motion: MotionSettings | None = None,
    background_removal: BackgroundRemovalSettings | None = None,
    gradient_ramp: GradientRampSettings | None = None,
    after_effects_presets: list[AfterEffectsPreset] | None = None,
) -> str:
    clip_effect_count = sum(len(v) for v in (motion.clip_effects if motion else {}).values())
    score = (
        texture.grain
        + texture.vignette
        + texture.light_leak
        + len(motion_effects) * 0.18
        + _motion_complexity(motion) * 0.08
        + clip_effect_count * 0.05
    )
    if texture.anamorphic_flare:
        score += 0.22
    if background_removal and background_removal.enabled:
        score += 0.4
    if gradient_ramp and gradient_ramp.enabled:
        score += 0.18
    if after_effects_presets:
        score += len([preset for preset in after_effects_presets if preset.enabled]) * 0.08
    if score >= 1.1:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def build_effects_cache_key(
    *,
    edit_plan_id: UUID | None,
    preset: VisualPresetKey,
    lut_intensity: float,
    texture: EffectsTextureSettings,
    motion_effects: list[MotionEffectKey],
    motion: MotionSettings | None = None,
    background_removal: BackgroundRemovalSettings | None = None,
    gradient_ramp: GradientRampSettings | None = None,
    after_effects_presets: list[AfterEffectsPreset] | None = None,
) -> str:
    motion_key = ",".join(sorted(effect.value for effect in motion_effects)) or "none"
    motion_settings_key = (motion or MotionSettings()).model_dump_json()
    texture_key = (
        f"g:{texture.grain:.2f}:v:{texture.vignette:.2f}:"
        f"l:{texture.light_leak:.2f}:a:{int(texture.anamorphic_flare)}"
    )
    background_key = (
        background_removal.model_dump_json()
        if background_removal
        else BackgroundRemovalSettings().model_dump_json()
    )
    gradient_key = (
        gradient_ramp.model_dump_json()
        if gradient_ramp
        else GradientRampSettings().model_dump_json()
    )
    ae_key = ",".join(
        sorted(
            preset.model_dump_json()
            for preset in (after_effects_presets or DEFAULT_AFTER_EFFECTS_PRESETS)
        )
    )
    return (
        f"plan:{edit_plan_id or 'none'}:preset:{preset.value}:"
        f"lut:{lut_intensity:.2f}:texture:{texture_key}:motion:{motion_key}:"
        f"motion_settings:{motion_settings_key}:background:{background_key}:"
        f"gradient:{gradient_key}:ae:{ae_key}"
    )


def preview_urls(_project_id: UUID, _preset: VisualPresetKey) -> tuple[None, None]:
    return None, None


def after_effects_handoff_urls(_project_id: UUID, _version: int) -> tuple[None, None]:
    return None, None


def _motion_complexity(motion: MotionSettings | None) -> int:
    if not motion:
        return 0
    enabled_features = sum(
        [
            motion.ken_burns.enabled,
            motion.speed_ramp.enabled,
            motion.beat_impact.enabled,
            motion.auto_reframe.enabled,
        ]
    )
    keyframe_cost = min(4, len(motion.keyframes) // 4)
    clip_effect_cost = min(3, sum(len(v) for v in motion.clip_effects.values()) // 2)
    return enabled_features + keyframe_cost + clip_effect_cost
