import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const provider = await readFile(
  new URL("../components/command-palette.tsx", import.meta.url),
  "utf8",
);
// The dialog lives in its own lazily loaded module (FE-40).
const panel = await readFile(
  new URL("../components/command-palette-panel.tsx", import.meta.url),
  "utf8",
);
const source = `${provider}\n${panel}`;

test("the panel (and cmdk) load only when the palette first opens", () => {
  assert.match(
    provider,
    /dynamic\(\s*\(\) =>\s*import\("\.\/command-palette-panel"\)/,
  );
  assert.doesNotMatch(provider, /from "cmdk"/);
  assert.match(panel, /from "cmdk"/);
});

test("does not use Command.Dialog (Radix Dialog logs console errors)", () => {
  assert.doesNotMatch(source, /Command\.Dialog/);
});

test("builds its own accessible dialog", () => {
  assert.match(source, /role="dialog"/);
  assert.match(source, /aria-modal="true"/);
  assert.match(source, /aria-label="Command palette"/);
});

test("uses the shared focus trap", () => {
  assert.match(source, /useFocusTrap\(/);
});

test("installs a single capturing, propagation-stopping global shortcut", () => {
  assert.match(source, /capture:\s*true/);
  assert.match(source, /stopPropagation/);
  assert.match(source, /isPaletteShortcut/);
});

test("imports the pure shortcut predicate from the registry module", () => {
  assert.match(source, /from "@\/lib\/command-registry\.mjs"/);
  assert.match(source, /isPaletteShortcut/);
});

test("trigger advertises its keyboard shortcut", () => {
  assert.match(source, /aria-keyshortcuts="Control\+K Meta\+K"/);
  assert.match(source, /aria-label="Search commands"/);
});

test("the backdrop is pointer-only and the empty state uses the agreed copy", () => {
  // Escape (focus trap) closes the palette; the backdrop is not a second,
  // unlabelled-in-context button in the Tab order.
  assert.match(source, /aria-hidden="true"\s+onClick=\{onClose\}/);
  assert.doesNotMatch(source, /aria-label="Close command palette"/);
  assert.match(source, /No matching commands/);
});

test("is light-only (no dark: classes)", () => {
  assert.doesNotMatch(source, /\bdark:/);
});

test("keeps the required public exports", () => {
  for (const name of [
    "CommandPaletteProvider",
    "RegisterCommands",
    "useRegisterCommands",
    "useCommandPalette",
    "CommandPaletteTrigger",
  ]) {
    assert.match(source, new RegExp(`export function ${name}\\b`));
  }
});
