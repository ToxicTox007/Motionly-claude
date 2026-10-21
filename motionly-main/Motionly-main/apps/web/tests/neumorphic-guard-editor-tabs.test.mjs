// The editor tab panels (Audio, Captions, Look, B-roll) are migrated to the
// neumorphic design system only: no neo-brutalist leftovers (hard offset
// shadows, dark borders, font-black, dark: variants, legacy class tokens).
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("editor tab files are free of neo-brutalist legacy tokens", () => {
  assertNeumorphic([
    "components/editor/tabs/audio-tab.tsx",
    "components/editor/tabs/captions-tab.tsx",
    "components/editor/tabs/look-tab.tsx",
    "components/editor/tabs/broll-tab.tsx",
  ]);
});
