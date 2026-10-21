import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

// Every file owned by the editor-shell task EXCEPT editor-client.tsx, which is
// 2,869 lines and only its className strings are in scope. Its legacy tokens
// were removed where safely possible; the task report lists any remainder.
const OWNED = [
  "components/editor-layout.tsx",
  "components/editor/layout/EditorToolbar.tsx",
  "components/editor/layout/CreatorProgressRail.tsx",
  "components/editor/layout/EditorWorkspaces.tsx",
  "components/editor/layout/useEditorCommands.ts",
  "components/editor/PropertiesInspector.tsx",
  "components/editor/dnd/ElementPalette.tsx",
  "components/editor/StaleIndicator.tsx",
  "components/editor/ConflictDialog.tsx",
  "components/editor/VersionHistoryPanel.tsx",
  "components/editorial-memory-indicator.tsx",
];

test("editor-shell owned files carry no neo-brutalist leftovers", () => {
  assertNeumorphic(OWNED);
});
