import assert from "node:assert/strict";
import test from "node:test";
import {
  EDITOR_COMMAND_GROUP,
  EDITOR_MODE_COMMAND_SHORTCUT,
  buildEditorCommands,
} from "../lib/editor-commands.mjs";

test("buildEditorCommands returns the six editor descriptors", () => {
  const commands = buildEditorCommands({ mode: "creator" });
  assert.deepEqual(
    commands.map((command) => command.id),
    [
      "editor.export",
      "editor.ai-edit",
      "editor.toggle-mode",
      "editor.jump-preview",
      "editor.jump-timeline",
      "editor.jump-export",
    ],
  );
});

test("every editor command belongs to the Editor group", () => {
  for (const mode of ["creator", "professional"]) {
    const commands = buildEditorCommands({ mode });
    for (const command of commands) {
      assert.equal(command.group, "Editor");
      assert.equal(command.group, EDITOR_COMMAND_GROUP);
    }
  }
});

test("stable commands keep their labels across modes", () => {
  const creator = buildEditorCommands({ mode: "creator" });
  const professional = buildEditorCommands({ mode: "professional" });
  const label = (commands, id) =>
    commands.find((command) => command.id === id)?.label;

  assert.equal(label(creator, "editor.export"), "Export video");
  assert.equal(label(creator, "editor.ai-edit"), "Open AI edit");
  assert.equal(label(creator, "editor.jump-preview"), "Jump to preview");
  assert.equal(label(creator, "editor.jump-timeline"), "Jump to timeline");
  assert.equal(label(creator, "editor.jump-export"), "Jump to export settings");
  assert.equal(label(professional, "editor.export"), "Export video");
});

test("the mode toggle label depends on the current mode", () => {
  const creator = buildEditorCommands({ mode: "creator" });
  const professional = buildEditorCommands({ mode: "professional" });
  const toggle = (commands) =>
    commands.find((command) => command.id === "editor.toggle-mode");

  assert.equal(toggle(creator).label, "Switch to Professional mode");
  assert.equal(toggle(professional).label, "Switch to Creator mode");
});

test("the mode toggle command carries the Alt+Shift+M shortcut", () => {
  const commands = buildEditorCommands({ mode: "creator" });
  const toggle = commands.find(
    (command) => command.id === "editor.toggle-mode",
  );
  assert.equal(toggle.shortcut, "Alt+Shift+M");
  assert.equal(toggle.shortcut, EDITOR_MODE_COMMAND_SHORTCUT);
});

test("jump commands point at the editor landmark ids", () => {
  const commands = buildEditorCommands({ mode: "professional" });
  const target = (id) => commands.find((command) => command.id === id)?.target;
  assert.equal(target("editor.jump-preview"), "editor-preview");
  assert.equal(target("editor.jump-timeline"), "editor-timeline");
  assert.equal(target("editor.jump-export"), "editor-export");
});

test("descriptors are serializable (no functions)", () => {
  const commands = buildEditorCommands({ mode: "creator" });
  assert.doesNotThrow(() => JSON.stringify(commands));
  for (const command of commands) {
    for (const value of Object.values(command)) {
      assert.notEqual(typeof value, "function");
    }
  }
});
