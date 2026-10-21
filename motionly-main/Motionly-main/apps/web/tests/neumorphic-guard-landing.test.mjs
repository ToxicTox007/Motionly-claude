// Guards the landing page and its components against neo-brutalist leftovers.
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

test("landing files use only neumorphic tokens", () => {
  assertNeumorphic([
    "app/page.tsx",
    "components/landing/hero-console.tsx",
    "components/landing/reveal.tsx",
  ]);
});
