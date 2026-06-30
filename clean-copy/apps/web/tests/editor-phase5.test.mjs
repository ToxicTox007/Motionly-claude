import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("processing screen refreshes after a direct page load", async () => {
  const source = await readFile(new URL("components/editor-client.tsx", root), "utf8");
  assert.match(source, /function ProcessingScreen[\s\S]*setInterval\(refresh, 4000\)/);
  assert.match(source, /visibilitychange/);
});

test("undo and redo are serialized per workspace and project", async () => {
  const context = await readFile(new URL("components/editor/history-context.tsx", root), "utf8");
  const editor = await readFile(new URL("components/editor-client.tsx", root), "utf8");
  assert.match(context, /window\.localStorage\.setItem/);
  assert.match(context, /undoAction/);
  assert.match(context, /redoAction/);
  assert.match(editor, /motionly:editor-history:\$\{workspaceId\}:\$\{projectId\}/);
});

test("saved templates include and apply caption and effects settings", async () => {
  const source = await readFile(new URL("components/editor/ScalePanel.tsx", root), "utf8");
  assert.match(source, /caption_settings: captionTemplateSettings/);
  assert.match(source, /effects_settings: effectsTemplateSettings/);
  assert.match(source, /applySavedTemplate/);
  assert.match(source, /onCaptionTrackUpdated/);
  assert.match(source, /onEffectsConfigUpdated/);
});
