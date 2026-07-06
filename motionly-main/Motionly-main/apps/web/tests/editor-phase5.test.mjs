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

test("heavy inspector panels are lazy-loaded from editor-client", async () => {
  const source = await readFile(new URL("components/editor-client.tsx", root), "utf8");
  const lazyPanels = [
    ["AIStep", /import\("\.\/editor\/ai-step"\)/],
    ["CaptionsTab", /import\("\.\/editor\/tabs\/captions-tab"\)/],
    ["BrollTab", /import\("\.\/editor\/tabs\/broll-tab"\)/],
    ["AudioTab", /import\("\.\/editor\/tabs\/audio-tab"\)/],
    ["LookTab", /import\("\.\/editor\/tabs\/look-tab"\)/],
    ["ExportStep", /import\("\.\/editor\/export-step"\)/],
    ["VersionHistoryPanel", /import\("\.\/editor\/VersionHistoryPanel"\)/],
    ["ScalePanel", /import\("\.\/editor\/ScalePanel"\)/],
  ];

  for (const [name, importPattern] of lazyPanels) {
    assert.match(source, new RegExp(`const ${name} = dynamic`));
    assert.match(source, importPattern);
  }

  assert.doesNotMatch(source, /import \{ AIStep \}/);
  assert.doesNotMatch(source, /import \{ CaptionsTab \}/);
  assert.doesNotMatch(source, /import \{ VersionHistoryPanel \}/);
  assert.doesNotMatch(source, /import \{ ScalePanel \}/);
});

test("core app surfaces provide route-level skeleton loading states", async () => {
  const loadingRoutes = [
    "app/(app)/dashboard/loading.tsx",
    "app/(app)/projects/loading.tsx",
    "app/(app)/projects/[projectId]/loading.tsx",
    "app/(app)/billing/loading.tsx",
    "app/(app)/account/loading.tsx",
    "app/(app)/admin/ops/loading.tsx",
  ];

  for (const route of loadingRoutes) {
    const source = await readFile(new URL(route, root), "utf8");
    assert.match(source, /LoadingPixelSkeleton/);
    if (!route.includes("[projectId]")) {
      assert.match(source, /AppShell/);
    }
  }
});

test("video preview defers canvas work until visible", async () => {
  const preview = await readFile(
    new URL("components/editor/preview/VideoPreview.tsx", root),
    "utf8",
  );
  const webgl = await readFile(
    new URL("components/editor/preview/WebGLVideoCanvas.tsx", root),
    "utf8",
  );

  assert.match(preview, /function usePreviewVisibility/);
  assert.match(preview, /new IntersectionObserver/);
  assert.match(preview, /active=\{isPreviewVisible\}/);
  assert.match(preview, /if \(!active\) return/);
  assert.match(webgl, /active: boolean/);
  assert.match(webgl, /if \(!active\) \{/);
  assert.match(webgl, /setAvailable\(false\)/);
});

test("saved templates include and apply caption and effects settings", async () => {
  const source = await readFile(new URL("components/editor/ScalePanel.tsx", root), "utf8");
  assert.match(source, /caption_settings: captionTemplateSettings/);
  assert.match(source, /effects_settings: effectsTemplateSettings/);
  assert.match(source, /applySavedTemplate/);
  assert.match(source, /onCaptionTrackUpdated/);
  assert.match(source, /onEffectsConfigUpdated/);
});
