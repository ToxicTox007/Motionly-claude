import assert from "node:assert/strict";
import test from "node:test";

import {
  commandKey,
  groupCommands,
  isPaletteShortcut,
  mergeCommands,
} from "../lib/command-registry.mjs";

test("isPaletteShortcut: Meta+K and Ctrl+K open the palette", () => {
  assert.equal(isPaletteShortcut({ key: "k", metaKey: true }), true);
  assert.equal(isPaletteShortcut({ key: "k", ctrlKey: true }), true);
  // Upper-case key (Shift not pressed but some layouts report "K").
  assert.equal(isPaletteShortcut({ key: "K", metaKey: true }), true);
});

test("isPaletteShortcut: plain k and modifier combos do not open", () => {
  assert.equal(isPaletteShortcut({ key: "k" }), false);
  // Both modifiers held (exactly one is required).
  assert.equal(
    isPaletteShortcut({ key: "k", metaKey: true, ctrlKey: true }),
    false,
  );
  // Alt or Shift disqualify.
  assert.equal(
    isPaletteShortcut({ key: "k", metaKey: true, altKey: true }),
    false,
  );
  assert.equal(
    isPaletteShortcut({ key: "k", ctrlKey: true, shiftKey: true }),
    false,
  );
  // Mid-IME composition is ignored.
  assert.equal(
    isPaletteShortcut({ key: "k", metaKey: true, isComposing: true }),
    false,
  );
  // A different key.
  assert.equal(isPaletteShortcut({ key: "j", metaKey: true }), false);
  assert.equal(isPaletteShortcut(null), false);
  assert.equal(isPaletteShortcut(undefined), false);
});

test("mergeCommands: de-duplicates by id, latest source wins, stable order", () => {
  const sources = new Map();
  sources.set("a", [
    { id: "home", label: "Home", group: "Navigate" },
    { id: "logout", label: "Log out", group: "Account" },
  ]);
  sources.set("b", [
    // Replaces source a's "home" (latest wins) but keeps first-seen position.
    { id: "home", label: "Home (updated)", group: "Navigate" },
    { id: "new", label: "New project", group: "Create" },
  ]);

  const merged = mergeCommands(sources);
  assert.deepEqual(
    merged.map((command) => command.id),
    ["home", "logout", "new"],
  );
  const home = merged.find((command) => command.id === "home");
  assert.equal(home.label, "Home (updated)");
});

test("mergeCommands: tolerates empty / missing input", () => {
  assert.deepEqual(mergeCommands(new Map()), []);
  assert.deepEqual(mergeCommands(), []);
  const sources = new Map([["a", null]]);
  assert.deepEqual(mergeCommands(sources), []);
});

test("groupCommands: fixed order, empty groups omitted", () => {
  const commands = [
    { id: "1", label: "Help", group: "Help" },
    { id: "2", label: "New", group: "Create" },
    { id: "3", label: "Home", group: "Navigate" },
    { id: "4", label: "Logout", group: "Account" },
    // Unknown group is dropped.
    { id: "5", label: "Rogue", group: "Nope" },
  ];
  const grouped = groupCommands(commands);
  assert.deepEqual(
    grouped.map(([group]) => group),
    ["Navigate", "Create", "Account", "Help"],
  );
  // "Editor" had no commands, so it is omitted (order otherwise preserved).
  assert.equal(
    grouped.some(([group]) => group === "Editor"),
    false,
  );
});

test("groupCommands: preserves within-group order", () => {
  const commands = [
    { id: "a", label: "A", group: "Navigate" },
    { id: "b", label: "B", group: "Navigate" },
    { id: "c", label: "C", group: "Navigate" },
  ];
  const [[, group]] = groupCommands(commands);
  assert.deepEqual(
    group.map((command) => command.id),
    ["a", "b", "c"],
  );
});

test("commandKey: stable across array identity, changes on content change", () => {
  const first = [
    { id: "home", label: "Home", group: "Navigate", href: "/dashboard" },
  ];
  const sameContentNewArray = [
    { id: "home", label: "Home", group: "Navigate", href: "/dashboard" },
  ];
  assert.equal(commandKey(first), commandKey(sameContentNewArray));

  const changedLabel = [
    { id: "home", label: "Home page", group: "Navigate", href: "/dashboard" },
  ];
  assert.notEqual(commandKey(first), commandKey(changedLabel));

  const changedHref = [
    { id: "home", label: "Home", group: "Navigate", href: "/elsewhere" },
  ];
  assert.notEqual(commandKey(first), commandKey(changedHref));

  assert.equal(commandKey([]), "[]");
  assert.equal(commandKey(), "[]");
});
