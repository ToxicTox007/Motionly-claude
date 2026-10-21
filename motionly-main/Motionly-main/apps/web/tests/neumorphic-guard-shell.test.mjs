// The app-shell files (shell, nav, workspace controls, page transition and the
// usage ring) must use the neumorphic design system only — no neo-brutalist
// leftovers (hard offset shadows, dark borders, dark: variants, font-black).
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("shell files are free of neo-brutalist legacy tokens", () => {
  assertNeumorphic([
    "components/app-shell.tsx",
    "components/sidebar-link.tsx",
    "components/progress-ring.tsx",
    "components/workspace-switcher.tsx",
    "components/workspace-picker.tsx",
    "app/template.tsx",
  ]);
});
