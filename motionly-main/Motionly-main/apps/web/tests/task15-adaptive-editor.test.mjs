import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const read = (path) => readFile(new URL(path, root), "utf8");

function functionBody(source, name, nextName) {
  const start = source.indexOf(`function ${name}`);
  const end = nextName ? source.indexOf(`function ${nextName}`, start + 1) : source.length;
  assert.notEqual(start, -1, `${name} should exist`);
  assert.notEqual(end, -1, `${nextName} should exist after ${name}`);
  return source.slice(start, end);
}

test("creator mode is the default and editor mode preference persists", async () => {
  const [layout, modeHook, toolbar] = await Promise.all([
    read("components/editor-layout.tsx"),
    read("components/editor/layout/useEditorMode.ts"),
    read("components/editor/layout/EditorToolbar.tsx"),
  ]);

  assert.match(modeHook, /DEFAULT_EDITOR_MODE: EditorMode = "creator"/);
  assert.match(modeHook, /useSyncExternalStore/);
  assert.match(modeHook, /motionly:editor-mode/);
  assert.match(modeHook, /window\.localStorage\.getItem/);
  assert.match(modeHook, /window\.localStorage\.setItem/);
  assert.match(modeHook, /event\.altKey.*event\.shiftKey/s);
  assert.match(layout, /data-editor-mode=\{mode\}/);
  assert.match(toolbar, />\s*Creator\s*</);
  assert.match(toolbar, />\s*Professional\s*</);
  assert.match(toolbar, /aria-label="Editor mode"/);
});

test("creator and professional modes expose intentionally different workspaces", async () => {
  const workspaces = await read("components/editor/layout/EditorWorkspaces.tsx");
  const creator = functionBody(workspaces, "CreatorWorkspace", "ProfessionalWorkspace");
  const professional = functionBody(workspaces, "ProfessionalWorkspace", "MobileTab");

  assert.match(creator, /data-editor-workspace="creator"/);
  assert.match(creator, /CurrentTaskCard/);
  assert.match(creator, /panels\.centerPanel/);
  assert.match(creator, /panels\.rightPanel/);
  assert.match(creator, /panels\.bottomPanel/);
  assert.doesNotMatch(creator, /panels\.leftPanel/);

  assert.match(professional, /data-editor-workspace="professional"/);
  assert.match(professional, /panels\.leftPanel/);
  assert.match(professional, /panels\.centerPanel/);
  assert.match(professional, /panels\.rightPanel/);
  assert.match(professional, /panels\.bottomPanel/);
});

test("creator mode reduces tiny uppercase labels without weakening professional density", async () => {
  const [css, toolbar, progress, workspaces] = await Promise.all([
    read("app/globals.css"),
    read("components/editor/layout/EditorToolbar.tsx"),

    read("components/editor/layout/CreatorProgressRail.tsx"),
    read("components/editor/layout/EditorWorkspaces.tsx"),
  ]);

  assert.match(css, /data-editor-mode="creator"\] \.uppercase[\s\S]*text-transform: none/);
  assert.match(css, /data-editor-mode="creator"[\s\S]*text-\\\[9px\\\][\s\S]*font-size: 0\.75rem/);
  for (const source of [toolbar, progress, workspaces]) {
    assert.doesNotMatch(source, /text-\[(?:9|10)px\]/);
    assert.doesNotMatch(source, /\buppercase\b/);
  }
});

test("only one viewport workspace mounts at a time", async () => {
  const workspaces = await read("components/editor/layout/EditorWorkspaces.tsx");
  const selector = functionBody(workspaces, "EditorWorkspaces", "MobileWorkspace");

  assert.match(workspaces, /useSyncExternalStore/);
  assert.match(workspaces, /\(min-width: 1024px\)/);
  assert.match(selector, /if \(!desktopWorkspace\)/);
  assert.match(selector, /return \(\s*<MobileWorkspace/);
  assert.match(selector, /return mode === "creator"/);
  assert.doesNotMatch(selector, /<>/);
});

test("precision keyboard workflows remain active and discoverable in both modes", async () => {
  const [editor, timelinePanel, timelineShortcuts, toolbar, workspaces] = await Promise.all([
    read("components/editor-client.tsx"),
    read("components/editor/timeline/TimelinePanel.tsx"),
    read("components/editor/timeline/useTimelineKeyboardShortcuts.ts"),
    read("components/editor/layout/EditorToolbar.tsx"),
    read("components/editor/layout/EditorWorkspaces.tsx"),
  ]);
  const timeline = `${timelinePanel}\n${timelineShortcuts}`;

  assert.match(editor, /event\.key\.toLowerCase\(\) === "z"/);
  assert.match(editor, /event\.key\.toLowerCase\(\) === "y"/);
  assert.match(timeline, /const key = event\.key\.toLowerCase\(\)/);
  assert.match(timeline, /key === "j"/);
  assert.match(timeline, /key === "k"/);
  assert.match(timeline, /key === "l"/);
  assert.match(timeline, /event\.key === "\["/);
  assert.match(timeline, /event\.key === "\]"/);
  assert.match(toolbar, /Precision controls/);
  assert.match(toolbar, /J K L/);
  assert.match(toolbar, /Ctrl\/⌘ Z/);

  const creator = functionBody(workspaces, "CreatorWorkspace", "ProfessionalWorkspace");
  const professional = functionBody(workspaces, "ProfessionalWorkspace", "MobileTab");
  assert.match(creator, /panels\.bottomPanel/);
  assert.match(professional, /panels\.bottomPanel/);
});

test("editor monoliths are decomposed behind focused module boundaries", async () => {
  const [layout, editorClient, timelinePanel, creatorFlow, timelineShortcuts] =
    await Promise.all([
      read("components/editor-layout.tsx"),
      read("components/editor-client.tsx"),
      read("components/editor/timeline/TimelinePanel.tsx"),
      read("components/editor/creator-flow.ts"),
      read("components/editor/timeline/useTimelineKeyboardShortcuts.ts"),
    ]);
  const lineCount = layout.trimEnd().split("\n").length;

  assert.ok(lineCount < 120, `editor-layout.tsx should stay small; found ${lineCount} lines`);
  assert.match(layout, /EditorToolbar/);
  assert.match(layout, /CreatorProgressRail/);
  assert.match(layout, /EditorWorkspaces/);
  assert.match(layout, /useEditorMode/);
  assert.doesNotMatch(layout, /function TopToolbar/);
  assert.doesNotMatch(layout, /function CurrentTaskCard/);
  assert.doesNotMatch(layout, /activeSheet/);

  assert.match(editorClient, /from "\.\/editor\/creator-flow"/);
  assert.doesNotMatch(editorClient, /function buildCreatorTasks/);
  assert.match(creatorFlow, /export function buildCreatorTasks/);
  assert.match(timelinePanel, /useTimelineKeyboardShortcuts/);
  assert.doesNotMatch(timelinePanel, /function onKeyDown\(event: KeyboardEvent\)/);
  assert.match(timelineShortcuts, /export function useTimelineKeyboardShortcuts/);
});
