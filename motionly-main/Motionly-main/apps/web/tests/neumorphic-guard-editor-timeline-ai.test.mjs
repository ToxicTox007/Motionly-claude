import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

// Guards every file owned by the editor-timeline-ai task against neo-brutalist
// leftovers (hard offset shadows, dark control borders, font-black, dark:
// variants, legacy pixel-*/glass/btn classes). Presentation-only migration.
test("editor timeline, preview and AI panels use only neumorphic tokens", () => {
  assertNeumorphic([
    "components/editor/timeline/TimelinePanel.tsx",
    "components/editor/preview/VideoPreview.tsx",
    "components/editor/preview/ManualElementsOverlay.tsx",
    "components/editor/preview/WebGLVideoCanvas.tsx",
    "components/editor/ai-step.tsx",
    "components/editor/ReviewPanel.tsx",
    "components/editor/repurpose-panel.tsx",
    "components/editor/CreativeVariantsPanel.tsx",
  ]);
});
