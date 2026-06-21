import type { ManualElementType, MotionEffectKey, VisualPresetKey } from "@motionly/shared";

export type PaletteCategory =
  | "text"
  | "transitions"
  | "effects"
  | "media"
  | "audio"
  | "graphics"
  | "motion"
  | "grades"
  | "ai";

export interface PaletteElement {
  id: string;
  name: string;
  category: PaletteCategory;
  elementType: ManualElementType | "ai";
  accepts: Array<"canvas" | "timeline" | "clip" | "transition">;
  preview: string;
  durationMs?: number;
  properties?: Record<string, unknown>;
  motionEffect?: MotionEffectKey;
  grade?: VisualPresetKey;
  aiAction?: "auto_caption" | "smart_broll" | "music_match" | "reanalyze_style" | "highlight_reel";
}

const item = (value: PaletteElement) => value;

// Tiny bundled PCM cue keeps palette SFX deterministic and renderable without
// a third-party URL. Production catalogs can replace it through assetUrl.
const BUILT_IN_SFX_DATA_URL = "data:audio/wav;base64,UklGRgYEAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgATElTVBoAAABJTkZPSVNGVA4AAABMYXZmNjAuMTYuMTAwAGRhdGHAAwAAgIKEhoiKjI2Oj4+Pj4+OjYyKiIeEgoB+fHl3dnRycXBwcHBwcXJzdHZ4enx+gYOFh4mLjI6Oj4+Pj4+PjIuJh4WDgX99enh2dXNycXBwcHBwcXJ0dXd5e32AgoSGiIqMjY6Pj4+Pj46NjIqIhoSCgH17eXd1dHJxcHBwcHBxcnN1dnh6fX+Bg4WHiYuMjo+Pj4+Pjo6Mi4mHhYOBfnx6eHZ0c3JxcHBwcHBxcnR2d3l8foCChIeIioyNjo+Pj4+Pjo2MioiGhIKAfXt5d3V0cnFwcHBwcHFyc3V3eHt9f4GDhoiKi42Oj4+Pj4+OjYyLiYeFg4F+fHp4dnRzcXFwcHBwcHFzdHZ4enx+gIKFh4mKjI2Oj4+Pj4+OjYuKiIaEgn99e3l3dXNycXBwcHBwcXJzdXd5e31/goSGiIqLjY6Pj4+Pj46NjIqJh4WDgH58enh2dHNxcHBwcHBxcXN0dnh6fH6Bg4WHiYuMjY6Pj4+Pj46Ni4qIhoOBf317eHd1c3JxcHBwcHBxcnR1d3l7fYCChIaIioyNjo+Pj4+Pjo2MioiHhIKAfnx5d3Z0cnFwcHBwcHFyc3R2eHp8foGDhYeJi4yOjo+Pj4+PjoyLiYeFg4F/fXp4dnVzcnFwcHBwcHFydHV3eXt9gIKEhoiKjI2Oj4+Pj4+OjYyKiIaEgoB9e3l3dXRycXBwcHBwcXJzdXZ4en1/gYOFh4mLjI6Pj4+Pj46OjIuJh4WDgX58enh2dHNycXBwcHBwcXJ0dnd5fH6AgoSHiIqMjY6Pj4+Pj46NjIqIhoSCgH17eXd1dHJxcHBwcHBxcnN1d3h7fX+Bg4aIiouNjo+Pj4+Pjo2Mi4mHhYOBfnx6eHZ0c3FxcHBwcHBxc3R2eHp8foCChYeJioyNjo+Pj4+Pjo2LioiGhIJ/fXt5d3VzcnFwcHBwcHFyc3V3eXt9f4KEhoiKi42Oj4+Pj4+OjYyKiYeFg4B+fHp4dnRzcXBwcHBwcXFzdHZ4enx+gYOFh4mLjI2Oj4+Pj4+OjYuKiIaDgX99e3h3dXNycXBwcHBwcXJ0dXd5e32AgoSGiIqMjY6Pj4+Pj46NjIqIh4SCgH58eXd2dHJxcHBwcHBxcnN0dnh6fH6Bg4WHiYuMjo6Pj4+Pj46Mi4mHhYOBf316eHZ1c3JxcHBwcHBxcnR1d3l7fYCChIaIioyNjo+Pj4+Pjo2MioiGhIKAfXt5d3V0cnFwcHBwcHFyc3V2eHp9f4GDhYeJi4yOj4+Pj4+OjoyLiYeFg4F/fHp4";

export const ELEMENT_CATALOG: PaletteElement[] = [
  item({ id: "word-pop", name: "Word Pop", category: "text", elementType: "caption", accepts: ["canvas", "timeline"], preview: "POP", properties: { content: "New caption", preset: "bold_influencer", animation: "pop" } }),
  item({ id: "phrase", name: "Phrase", category: "text", elementType: "caption", accepts: ["canvas", "timeline"], preview: "Phrase", properties: { content: "Your phrase", preset: "founder_clean", animation: "fade_up" } }),
  item({ id: "impact", name: "Impact", category: "text", elementType: "caption", accepts: ["canvas", "timeline"], preview: "IMPACT", properties: { content: "IMPACT", preset: "impact_stamp", animation: "impact_stamp" } }),
  item({ id: "minimal", name: "Minimal", category: "text", elementType: "caption", accepts: ["canvas", "timeline"], preview: "minimal", properties: { content: "minimal line", preset: "minimal_line", animation: "minimal_fade" } }),
  item({ id: "edu-card", name: "Edu Card", category: "text", elementType: "caption", accepts: ["canvas", "timeline"], preview: "01  Learn", properties: { content: "Step 1", preset: "educational_card", animation: "edu_step" } }),
  item({ id: "ticker", name: "Ticker", category: "text", elementType: "caption", accepts: ["canvas", "timeline"], preview: "@creator", properties: { content: "@creator", preset: "creator_ticker", animation: "ticker" } }),
  item({ id: "title-card", name: "Title Card", category: "text", elementType: "text", accepts: ["canvas", "timeline"], preview: "TITLE", properties: { content: "Title", fontSize: 42, fontWeight: 800, color: "#ffffff" } }),
  item({ id: "lower-third", name: "Lower Third", category: "text", elementType: "text", accepts: ["canvas", "timeline"], preview: "Name / Role", properties: { content: "Name · Role", fontSize: 26, color: "#ffffff", backgroundColor: "#0f172acc" } }),
  item({ id: "custom-text", name: "Custom Text", category: "text", elementType: "text", accepts: ["canvas", "timeline"], preview: "Text", properties: { content: "Text", fontSize: 32, color: "#ffffff" } }),

  ...["Hard Cut", "Dissolve", "Zoom In", "Zoom Out", "Swipe Left", "Swipe Right", "Glitch", "Whip Pan", "Fade Black", "Light Leak"].map((name) =>
    item({ id: `transition-${name.toLowerCase().replaceAll(" ", "-")}`, name, category: "transitions", elementType: "transition", accepts: ["transition"], preview: name === "Dissolve" ? "A⋯B" : "A│B", durationMs: name === "Hard Cut" ? 1 : 350, properties: { transition: name.toLowerCase().replaceAll(" ", "_") } }),
  ),

  ...["Film Grain", "Vignette", "Lens Flare", "Bokeh", "Light Leak", "Halation", "Smoke", "Sparkle", "Emoji", "Sticker"].map((name) =>
    item({ id: `fx-${name.toLowerCase().replaceAll(" ", "-")}`, name, category: "effects", elementType: "overlay", accepts: ["canvas", "timeline", "clip"], preview: name.slice(0, 3).toUpperCase(), properties: { effect: name.toLowerCase().replaceAll(" ", "_"), intensity: 0.5 } }),
  ),

  item({ id: "broll-search", name: "B-roll Search", category: "media", elementType: "broll", accepts: ["timeline"], preview: "Search", properties: { source: "stock_search" } }),
  item({ id: "my-library", name: "My Library", category: "media", elementType: "broll", accepts: ["timeline"], preview: "Upload", properties: { source: "user_library" } }),
  item({ id: "solid-color", name: "Solid Color", category: "media", elementType: "overlay", accepts: ["canvas", "timeline"], preview: "■", properties: { backgroundColor: "#111827" } }),
  item({ id: "gradient-fill", name: "Gradient Fill", category: "media", elementType: "overlay", accepts: ["canvas", "timeline"], preview: "◩", properties: { background: "linear-gradient(135deg,#0f172a,#f59e0b)" } }),
  item({ id: "screen-record", name: "Screen Record", category: "media", elementType: "broll", accepts: ["timeline"], preview: "REC", properties: { source: "screen_record" } }),

  item({ id: "music-track", name: "Music Track", category: "audio", elementType: "ai", accepts: ["timeline"], preview: "AI ♫", aiAction: "music_match" }),
  ...["Impact", "Whoosh", "Notification", "Ambient Sound", "Voice-over"].map((name) =>
    item({ id: `audio-${name.toLowerCase().replaceAll(" ", "-")}`, name, category: "audio", elementType: name === "Voice-over" ? "music" : "sfx", accepts: ["timeline"], preview: "♪", durationMs: name === "Ambient Sound" || name === "Voice-over" ? 8000 : 900, properties: { sound: name.toLowerCase().replaceAll(" ", "_"), ...(name === "Voice-over" ? {} : { assetUrl: BUILT_IN_SFX_DATA_URL, volume: 80 }) } }),
  ),

  ...["Rectangle", "Circle", "Arrow", "Line", "Progress Bar", "Countdown Timer", "Stats Card", "Quote Card", "CTA Button", "Social Handle Badge"].map((name) =>
    item({ id: `graphic-${name.toLowerCase().replaceAll(" ", "-")}`, name, category: "graphics", elementType: name === "Rectangle" || name === "Circle" || name === "Line" ? "shape" : "graphic", accepts: ["canvas", "timeline"], preview: name === "Circle" ? "●" : name === "Arrow" ? "→" : name.slice(0, 4), properties: { shape: name.toLowerCase().replaceAll(" ", "_"), content: name, color: "#14b8a6", backgroundColor: "#14b8a6" } }),
  ),

  ...([
    ["Push-In", "ken_burns"], ["Punch Zoom", "zoom_punch"], ["Ken Burns", "ken_burns"], ["Speed Ramp", "speed_ramp"], ["Shake", "beat_impact"], ["Reframe", "zoom_punch"], ["Slow Motion", "speed_ramp"], ["Reverse", "glitch"],
  ] as const).map(([name, motionEffect]) =>
    item({ id: `motion-${name.toLowerCase().replaceAll(" ", "-")}`, name, category: "motion", elementType: "motion", accepts: ["clip"], preview: "↗", motionEffect, properties: { motionEffect } }),
  ),

  ...([
    ["Vogue Luxury", "vogue_luxury", "linear-gradient(135deg,#111,#ddd)"],
    ["Champagne Gold", "champagne_gold", "linear-gradient(135deg,#5b3a12,#f8d88a)"],
    ["Dubai Influencer", "dubai_influencer", "linear-gradient(135deg,#0c4a6e,#fb923c)"],
    ["Old Money", "old_money", "linear-gradient(135deg,#344e41,#dad7cd)"],
    ["Clean Founder", "clean_founder", "linear-gradient(135deg,#f8fafc,#0f172a)"],
    ["Cinematic Hotel", "cinematic_hotel", "linear-gradient(135deg,#0f766e,#c2410c)"],
  ] as const).map(([name, grade, background]) =>
    item({ id: `grade-${grade}`, name, category: "grades", elementType: "grade", accepts: ["clip"], preview: "", grade, properties: { grade, background } }),
  ),

  item({ id: "ai-auto-caption", name: "Auto-Caption Clip", category: "ai", elementType: "ai", accepts: ["clip", "timeline"], preview: "AI CC", aiAction: "auto_caption" }),
  item({ id: "ai-broll-fill", name: "Smart B-roll Fill", category: "ai", elementType: "ai", accepts: ["clip", "timeline"], preview: "AI B", aiAction: "smart_broll" }),
  item({ id: "ai-music", name: "AI Music Match", category: "ai", elementType: "ai", accepts: ["timeline"], preview: "AI ♫", aiAction: "music_match" }),
  item({ id: "ai-style", name: "Re-analyze Style", category: "ai", elementType: "ai", accepts: ["canvas", "timeline", "clip"], preview: "AI FX", aiAction: "reanalyze_style" }),
  item({ id: "ai-highlight", name: "AI Highlight Reel", category: "ai", elementType: "ai", accepts: ["timeline"], preview: "AI ✦", aiAction: "highlight_reel" }),
];

export const CATEGORY_LABELS: Record<PaletteCategory, string> = {
  text: "Text & Captions",
  transitions: "Transitions",
  effects: "Effects & Overlays",
  media: "Media",
  audio: "Audio",
  graphics: "Shapes & Graphics",
  motion: "Motion Effects",
  grades: "Color Grades",
  ai: "AI Elements",
};
