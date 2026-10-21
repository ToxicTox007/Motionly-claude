// Every UI file in the app is migrated to the light-only neumorphic design: no
// neo-brutalist leftovers anywhere under app/ or components/, and the design
// system no longer defines the temporary legacy aliases.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import { assertNeumorphic } from "./neumorphic-guard-helper.mjs";

const webDir = new URL("..", import.meta.url).pathname;

function uiFiles(dir) {
  return readdirSync(join(webDir, dir)).flatMap((name) => {
    const path = join(webDir, dir, name);
    if (statSync(path).isDirectory()) return uiFiles(join(dir, name));
    return /\.(tsx|ts)$/.test(name) ? [relative(webDir, path)] : [];
  });
}

test("every app and component file is free of neo-brutalist legacy tokens", () => {
  const files = [...uiFiles("app"), ...uiFiles("components")];
  assert.ok(files.length > 80, `expected the whole UI, found ${files.length}`);
  assertNeumorphic(files);
});

test("the design system no longer defines the temporary legacy aliases", () => {
  const css = readFileSync(join(webDir, "app/globals.css"), "utf8");
  const tailwind = readFileSync(join(webDir, "tailwind.config.ts"), "utf8");
  for (const selector of [
    ".card",
    ".btn",
    ".pixel-button",
    ".pixel-input",
    ".input-base",
    ".badge",
    ".glass",
    ".pixel-panel",
    ".pixel-skeleton",
    ".progress-bar",
  ]) {
    assert.doesNotMatch(
      css,
      new RegExp(`\\${selector}(?![\\w-])`),
      `globals.css still defines ${selector}`,
    );
  }
  assert.doesNotMatch(tailwind, /brutal/);
  assert.doesNotMatch(tailwind, /"pixel-border"/);
  assert.doesNotMatch(tailwind, /foreground: "hsl\(var\(--hairline\)\)"/);
  assert.doesNotMatch(css, /:root\[data-theme="dark"\]\s*\{/);
});
