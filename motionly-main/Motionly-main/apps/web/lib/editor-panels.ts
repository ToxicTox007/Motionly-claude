import type { InspectorTab } from "@/components/editor/layout/InspectorTabs";

// The editor's independently loaded panels (B27). The server loader returns
// one outcome per panel; a failed panel is reported here instead of being
// handed to the editor as empty data, so the UI can say "couldn't load" and
// offer Retry rather than an empty state ("Generate an AI edit first").

export type EditorPanelKey =
  | "assetUrls"
  | "transcript"
  | "captions"
  | "plans"
  | "broll"
  | "music"
  | "effects"
  | "renders"
  | "exports"
  | "billing"
  | "visual"
  | "workflows";

export type EditorPanelError = { requestId: string | null };

export type EditorPanelErrors = Partial<
  Record<EditorPanelKey, EditorPanelError>
>;

export const EDITOR_PANEL_LABELS: Record<EditorPanelKey, string> = {
  assetUrls: "the video preview",
  transcript: "the transcript",
  captions: "captions",
  plans: "edit plans",
  broll: "B-roll",
  music: "music",
  effects: "look settings",
  renders: "renders",
  exports: "exports",
  billing: "your plan's export limits",
  visual: "visual analysis",
  workflows: "workflow status",
};

/** The data each inspector tab cannot work without. When one of these failed,
 * the tab shows the error and Retry instead of its (misleading) empty state. */
const TAB_REQUIRED_PANELS: Record<InspectorTab, EditorPanelKey[]> = {
  ai: ["plans", "transcript"],
  captions: ["captions", "plans"],
  broll: ["broll", "plans"],
  audio: ["music"],
  look: ["effects"],
  history: ["plans"],
  review: ["plans"],
  scale: [],
  localize: ["captions"],
  export: ["renders", "exports"],
};

export function failedPanelsForTab(
  tab: InspectorTab,
  errors: EditorPanelErrors,
): EditorPanelKey[] {
  return TAB_REQUIRED_PANELS[tab].filter((panel) => errors[panel]);
}

/** A stable string naming the failed panels, used as the editor's React key
 * so a successful Retry remounts it with the freshly loaded data. */
export function panelErrorKey(errors: EditorPanelErrors) {
  return Object.keys(errors).sort().join(",");
}
