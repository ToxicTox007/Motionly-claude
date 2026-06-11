from app.schemas.captions import CaptionAnimation, CaptionPreset, CaptionPresetKey, CaptionStyle

CAPTION_SCHEMA_VERSION = 1


CAPTION_PRESETS: dict[CaptionPresetKey, CaptionPreset] = {
    CaptionPresetKey.LUXURY_MINIMAL: CaptionPreset(
        key=CaptionPresetKey.LUXURY_MINIMAL,
        name="Minimal captions",
        style=CaptionStyle(
            font_family="Inter",
            font_size_px=42,
            font_weight=600,
            text_case="normal",
            text_color="#f8fafc",
            accent_color="#14b8a6",
            stroke_color="#0f172a",
            stroke_width_px=1,
            shadow="0 8px 24px rgba(15, 23, 42, 0.45)",
            safe_area_bottom_pct=18,
            placement="lower",
            animation=CaptionAnimation.FADE_UP,
            contrast_guardrail=(
                "Use dark overlay behind bright footage and keep 4.5:1 text contrast."
            ),
        ),
    ),
    CaptionPresetKey.BOLD_INFLUENCER: CaptionPreset(
        key=CaptionPresetKey.BOLD_INFLUENCER,
        name="Bold Influencer",
        style=CaptionStyle(
            font_family="Inter",
            font_size_px=50,
            font_weight=900,
            text_case="uppercase",
            text_color="#ffffff",
            accent_color="#ef4444",
            stroke_color="#111827",
            stroke_width_px=3,
            shadow="0 5px 0 rgba(17, 24, 39, 0.9)",
            safe_area_bottom_pct=15,
            placement="lower",
            animation=CaptionAnimation.POP,
            contrast_guardrail=(
                "Always stroke text and cap chunks at three words for motion-heavy clips."
            ),
        ),
    ),
    CaptionPresetKey.VOGUE_WHITE: CaptionPreset(
        key=CaptionPresetKey.VOGUE_WHITE,
        name="Editorial white",
        style=CaptionStyle(
            font_family="Georgia",
            font_size_px=44,
            font_weight=700,
            text_case="title",
            text_color="#ffffff",
            accent_color="#d1d5db",
            stroke_color="#020617",
            stroke_width_px=1,
            shadow="0 12px 28px rgba(2, 6, 23, 0.4)",
            safe_area_bottom_pct=20,
            placement="middle",
            animation=CaptionAnimation.TYPE_ON,
            contrast_guardrail="Prefer center placement only when subject face-safe area is clear.",
        ),
    ),
    CaptionPresetKey.GOLD_PREMIUM: CaptionPreset(
        key=CaptionPresetKey.GOLD_PREMIUM,
        name="Gold accent",
        style=CaptionStyle(
            font_family="Inter",
            font_size_px=46,
            font_weight=800,
            text_case="uppercase",
            text_color="#fff7ed",
            accent_color="#d6a13d",
            stroke_color="#21180b",
            stroke_width_px=2,
            shadow="0 0 20px rgba(214, 161, 61, 0.42)",
            safe_area_bottom_pct=16,
            placement="lower",
            animation=CaptionAnimation.GLOW,
            contrast_guardrail="Keep gold as accent, not full text, when footage is warm or beige.",
        ),
    ),
    CaptionPresetKey.FOUNDER_CLEAN: CaptionPreset(
        key=CaptionPresetKey.FOUNDER_CLEAN,
        name="Clean captions",
        style=CaptionStyle(
            font_family="Inter",
            font_size_px=40,
            font_weight=700,
            text_case="normal",
            text_color="#e5f3ff",
            accent_color="#2563eb",
            stroke_color="#0f172a",
            stroke_width_px=1,
            shadow="0 10px 26px rgba(15, 23, 42, 0.35)",
            safe_area_bottom_pct=17,
            placement="lower",
            animation=CaptionAnimation.SLIDE_UP,
            contrast_guardrail=(
                "Use sentence case and avoid more than two lines for trust-building clips."
            ),
        ),
    ),
}


def get_caption_preset(key: CaptionPresetKey) -> CaptionPreset:
    return CAPTION_PRESETS[key]


def list_caption_presets() -> list[CaptionPreset]:
    return list(CAPTION_PRESETS.values())
