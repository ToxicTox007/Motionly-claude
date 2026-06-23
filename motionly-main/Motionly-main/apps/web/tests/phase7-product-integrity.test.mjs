import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const webRoot = new URL("../", import.meta.url);
const repoRoot = new URL("../../../", import.meta.url);

test("editor labels describe the implemented behavior", async () => {
  const scale = await readFile(new URL("components/editor/ScalePanel.tsx", webRoot), "utf8");
  const look = await readFile(new URL("components/editor/tabs/look-tab.tsx", webRoot), "utf8");
  const catalog = await readFile(
    new URL("components/editor/dnd/element-catalog.ts", webRoot),
    "utf8",
  );

  assert.doesNotMatch(scale, /Custom style training|Train from overrides/);
  assert.match(scale, /Reference style analysis/);
  assert.doesNotMatch(look, />Background removal</);
  assert.match(look, /Portrait backdrop blur/);
  assert.doesNotMatch(catalog, /screen-record|Screen Record/);
});

test("shell, dashboard, and feedback use live application paths", async () => {
  const shell = await readFile(new URL("components/app-shell.tsx", webRoot), "utf8");
  const dashboard = await readFile(new URL("app/(app)/dashboard/page.tsx", webRoot), "utf8");
  const feedback = await readFile(new URL("app/feedback/page.tsx", webRoot), "utf8");

  assert.match(shell, /\/billing\/status/);
  assert.doesNotMatch(shell, /3\/10 Exports/);
  assert.doesNotMatch(dashboard, /\.from\(["']projects["']\)/);
  assert.match(feedback, /submitFeedback/);
  assert.doesNotMatch(feedback, /mailto:/);
});

test("shared stock provider type includes Runway", async () => {
  const shared = await readFile(new URL("packages/shared/src/index.ts", repoRoot), "utf8");
  assert.match(shared, /StockProviderKey[\s\S]*"runway"/);
});
