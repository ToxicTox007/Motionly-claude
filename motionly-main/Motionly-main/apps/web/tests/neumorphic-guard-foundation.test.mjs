// The foundation agent's own component files contain no neo-brutalist legacy
// tokens. globals.css and tailwind.config.ts are excluded on purpose: they keep
// the legacy alias definitions so un-migrated screens still render softly.
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("foundation component files are free of neo-brutalist legacy tokens", () => {
  assertNeumorphic([
    "components/button.tsx",
    "components/brutal-ui.tsx",
    "components/editor/ui.tsx",
    "components/toast.tsx",
    "components/empty-state.tsx",
    "components/pixel-icons.tsx",
    "components/mobile-nav.tsx",
    "app/layout.tsx",
  ]);
});
