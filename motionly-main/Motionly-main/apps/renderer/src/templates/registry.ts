import type { EditPlanStyleKey, RenderTemplateId } from "@motionly/shared";
import type { TemplateDefinition } from "../types";

export const templates: Record<RenderTemplateId, TemplateDefinition> = {
  luxury_reel_v1: {
    id: "luxury_reel_v1",
    name: "Luxury Reel",
    style: "luxury",
    caption_animation: "gold_shimmer",
    supported_effects: ["warm_grade", "soft_flash", "gold_shimmer", "ken_burns"],
    default_color_grade: "champagne_gold",
    intro: true,
    outro: true,
    accent: "#d6b35f",
    background: "#14100b",
    foreground: "#fff7e6",
    fontFamily: "Inter, Arial, sans-serif",
  },
  founder_reel_v1: {
    id: "founder_reel_v1",
    name: "Founder Reel",
    style: "founder",
    caption_animation: "slide_up",
    supported_effects: ["clean_lower_third", "emphasis_pulse", "ken_burns"],
    default_color_grade: "clean_neutral",
    intro: true,
    outro: true,
    accent: "#18a999",
    background: "#0c1110",
    foreground: "#f5fffb",
    fontFamily: "Inter, Arial, sans-serif",
  },
  travel_reel_v1: {
    id: "travel_reel_v1",
    name: "Travel Reel",
    style: "travel",
    caption_animation: "karaoke",
    supported_effects: ["warm_grade", "flash_frame", "ken_burns", "map_card"],
    default_color_grade: "sunlit",
    intro: true,
    outro: false,
    accent: "#ff6b35",
    background: "#111315",
    foreground: "#fffaf2",
    fontFamily: "Inter, Arial, sans-serif",
  },
  podcast_reel_v1: {
    id: "podcast_reel_v1",
    name: "Podcast Reel",
    style: "podcast",
    caption_animation: "pulse",
    supported_effects: ["waveform", "clean_lower_third", "emphasis_pulse"],
    default_color_grade: "studio",
    intro: false,
    outro: true,
    accent: "#7dd3fc",
    background: "#101014",
    foreground: "#f7fbff",
    fontFamily: "Inter, Arial, sans-serif",
  },
  fashion_reel_v1: {
    id: "fashion_reel_v1",
    name: "Fashion Reel",
    style: "fashion",
    caption_animation: "slide_up",
    supported_effects: ["flash_frame", "mono_grade", "emphasis_pulse", "ken_burns"],
    default_color_grade: "editorial_mono",
    intro: true,
    outro: true,
    accent: "#f5f0e6",
    background: "#050505",
    foreground: "#ffffff",
    fontFamily: "Georgia, Times New Roman, serif",
  },
};

const styleDefaults: Record<EditPlanStyleKey, RenderTemplateId> = {
  luxury: "luxury_reel_v1",
  founder: "founder_reel_v1",
  travel: "travel_reel_v1",
  podcast: "podcast_reel_v1",
  fashion: "fashion_reel_v1",
};

export function getTemplate(templateId: RenderTemplateId): TemplateDefinition {
  return templates[templateId];
}

export function templateForStyle(style: EditPlanStyleKey): TemplateDefinition {
  return getTemplate(styleDefaults[style]);
}

export function downgradeUnsupportedEffects(templateId: RenderTemplateId, effects: string[]) {
  const template = getTemplate(templateId);
  const supported = new Set(template.supported_effects);
  const applied = effects.filter((effect) => supported.has(effect));
  const unsupported = effects.filter((effect) => !supported.has(effect));
  return {
    applied,
    unsupported,
    downgraded: unsupported.map((effect) => `${effect}->${template.default_color_grade}`),
  };
}
