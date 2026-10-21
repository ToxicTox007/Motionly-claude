// The command palette is built from the neumorphic design system only: no
// neo-brutalist leftovers (hard offset shadows, dark borders, dark: variants).
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("command palette files are free of neo-brutalist legacy tokens", () => {
  assertNeumorphic([
    "components/command-palette.tsx",
    "components/motion-provider.tsx",
  ]);
});
