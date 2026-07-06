import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("ai-step.tsx implements enqueued async plan polling correctly", async () => {
  const source = await readFile(new URL("components/editor/ai-step.tsx", root), "utf8");

  // Verify imports
  assert.match(source, /pollPlanJob,[\s\S]*from "@\/lib\/client-api"/);

  // Verify status checking and polling invocation
  assert.match(source, /payload\.status === "queued"/);
  assert.match(source, /payload\.status_url/);
  assert.match(source, /pollPlanJob\(/);

  // Verify plan fetching list fallback after polling
  assert.match(source, /editorApiRequest[\s\S]*projects\/\$\{projectId\}\/plans/);
  assert.match(source, /\.find\(.*p\.id === planId\)/);
});

test("pollPlanJob pauses polling when hidden or offline", async () => {
  const source = await readFile(new URL("lib/client-api.ts", root), "utf8");
  assert.match(source, /waitForInteractivePollingTurn/);
  assert.match(source, /startAdaptivePolling/);
  assert.match(source, /document\.visibilityState !== "visible"/);
  assert.match(source, /visibilitychange/);
  assert.match(source, /navigator\.onLine === false/);
  assert.match(source, /window\.addEventListener\("online"/);
  assert.match(source, /Math\.min\(3000, 1000 \+ i \* 100\)/);
});

test("long-running editor status polling uses the shared adaptive poller", async () => {
  const editor = await readFile(new URL("components/editor-client.tsx", root), "utf8");
  const ai = await readFile(new URL("components/editor/ai-step.tsx", root), "utf8");
  const scale = await readFile(new URL("components/editor/ScalePanel.tsx", root), "utf8");
  const exportStep = await readFile(new URL("components/editor/export-step.tsx", root), "utf8");

  assert.match(editor, /startAdaptivePolling\(\(\) => poll\(\)/);
  assert.match(ai, /startAdaptivePolling\(\(\) => poll\(\)/);
  assert.match(scale, /startAdaptivePolling\(\(\) => load\(\)/);
  assert.match(exportStep, /startAdaptivePolling\(\(\) => refreshPublishing\(\)/);
});

test("render progress uses a stream with visibility-aware polling fallback", async () => {
  const source = await readFile(new URL("components/editor/export-step.tsx", root), "utf8");

  assert.match(source, /renders\/\$\{renderJobId\}\/events/);
  assert.match(source, /response\.body\.getReader\(\)/);
  assert.match(source, /Render event stream unavailable; falling back to polling\./);
  assert.match(source, /await pollRender\(renderJobId\)/);
  assert.match(source, /waitForInteractivePollingTurn/);
  assert.match(source, /Math\.min\(8000, 2500 \+ attempt \* 250\)/);
  assert.doesNotMatch(source, /router\.refresh\(\)/);
});

test("locally-updated editor tabs do not force full route refreshes", async () => {
  const tabs = [
    {
      path: "components/editor/tabs/audio-tab.tsx",
      callbacks: [/onSelectionUpdated\(payload\.selection\)/],
    },
    {
      path: "components/editor/tabs/look-tab.tsx",
      callbacks: [/onConfigUpdated\(payload\.config\)/],
    },
    {
      path: "components/editor/tabs/broll-tab.tsx",
      callbacks: [
        /onSlotsUpdated\(payload\.slots \?\? \[\]\)/,
        /onSlotUpdated\(payload\.slot\)/,
      ],
    },
    {
      path: "components/editor/tabs/captions-tab.tsx",
      callbacks: [/applyTrack\(payload\.track\)/],
    },
  ];

  for (const tab of tabs) {
    const source = await readFile(new URL(tab.path, root), "utf8");
    assert.doesNotMatch(source, /router\.refresh\(\)/, `${tab.path} should update local editor state`);
    assert.doesNotMatch(source, /useRouter/, `${tab.path} should not import the route router`);
    for (const callback of tab.callbacks) {
      assert.match(source, callback);
    }
  }
});

test("editor-client.tsx implements enqueued async plan polling correctly", async () => {
  const source = await readFile(new URL("components/editor-client.tsx", root), "utf8");

  // Verify status checking and polling invocation
  assert.match(source, /payload\.status === "queued"/);
  assert.match(source, /payload\.status_url/);
  assert.match(source, /pollPlanJob\(/);

  // Verify plan fetching list fallback after polling
  assert.match(source, /editorApiRequest[\s\S]*projects\/\$\{projectId\}\/plans/);
  assert.match(source, /\.find\(.*p\.id === planId\)/);
});

test("ScalePanel.tsx template and style applications implement enqueued async plan polling correctly", async () => {
  const source = await readFile(new URL("components/editor/ScalePanel.tsx", root), "utf8");

  // Verify imports
  assert.match(source, /pollPlanJob,[\s\S]*from "@\/lib\/client-api"/);

  // Verify two separate enqueued checks for template and style profile apply actions
  const queuedMatches = source.match(/payload\.status === "queued"/g);
  assert.equal(queuedMatches?.length, 2, "Should handle enqueued status in two functions (applySavedTemplate and applyStyleProfile)");

  const pollMatches = source.match(/pollPlanJob\(/g);
  assert.equal(pollMatches?.length, 2, "Should trigger pollPlanJob in both applySavedTemplate and applyStyleProfile");

  // Verify plans lookup list logic
  assert.match(source, /editorApiRequest[\s\S]*projects\/\$\{projectId\}\/plans/);
  assert.match(source, /\.find\(.*p\.id === planId\)/);
});
