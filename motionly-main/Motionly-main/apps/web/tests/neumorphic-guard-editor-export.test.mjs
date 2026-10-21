// The editor-export agent's migrated files contain no neo-brutalist legacy
// tokens. Covers every file this task owns:
//   ScalePanel, export-step, ExportsPanel, PublishingLibraryPanel, BrandKitPanel.
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("editor-export component files are free of neo-brutalist legacy tokens", () => {
  assertNeumorphic([
    "components/editor/ScalePanel.tsx",
    "components/editor/export-step.tsx",
    "components/editor/ExportsPanel.tsx",
    "components/editor/PublishingLibraryPanel.tsx",
    "components/editor/BrandKitPanel.tsx",
  ]);
});
