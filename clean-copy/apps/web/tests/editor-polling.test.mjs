import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("ai-step.tsx implements enqueued async plan polling correctly", async () => {
  const source = await readFile(new URL("components/editor/ai-step.tsx", root), "utf8");

  // Verify imports
  assert.match(source, /import.*pollPlanJob.*from.*@\/lib\/client-api/);

  // Verify status checking and polling invocation
  assert.match(source, /payload\.status === "queued"/);
  assert.match(source, /payload\.status_url/);
  assert.match(source, /pollPlanJob\(/);

  // Verify plan fetching list fallback after polling
  assert.match(source, /editorApiRequest[\s\S]*projects\/\$\{projectId\}\/plans/);
  assert.match(source, /\.find\(.*p\.id === planId\)/);
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
  assert.match(source, /import.*pollPlanJob.*from.*@\/lib\/client-api/);

  // Verify two separate enqueued checks for template and style profile apply actions
  const queuedMatches = source.match(/payload\.status === "queued"/g);
  assert.equal(queuedMatches?.length, 2, "Should handle enqueued status in two functions (applySavedTemplate and applyStyleProfile)");

  const pollMatches = source.match(/pollPlanJob\(/g);
  assert.equal(pollMatches?.length, 2, "Should trigger pollPlanJob in both applySavedTemplate and applyStyleProfile");

  // Verify plans lookup list logic
  assert.match(source, /editorApiRequest[\s\S]*projects\/\$\{projectId\}\/plans/);
  assert.match(source, /\.find\(.*p\.id === planId\)/);
});
