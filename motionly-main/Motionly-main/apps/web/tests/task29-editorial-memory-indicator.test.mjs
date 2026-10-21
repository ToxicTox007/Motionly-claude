import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(resolve(root, relativePath), "utf8");
}

test("editorial memory indicator shows 'personalized from N edits' with an opt-out", async () => {
  const component = await source("components/editorial-memory-indicator.tsx");

  // Reads the per-project memory indicator endpoint.
  assert.match(component, /\/editorial-learning\/projects\/\$\{/);
  assert.match(component, /\/memory`/);

  // Only renders when the feature is enabled AND the plan is personalized.
  assert.match(
    component,
    /!indicator\?\.enabled \|\| !indicator\.personalized/,
  );

  // Shows the "personalized from N edits" count with singular/plural handling.
  assert.match(component, /Personalized from \{count\}/);
  assert.match(component, /count === 1 \? "edit" : "edits"/);

  // Provides an opt-out that routes to account settings consent withdrawal.
  assert.match(component, /opt_out_available/);
  assert.match(component, /href="\/account"/);
  assert.match(component, /Opt out/);
});

test("editorial memory indicator is mounted in the AI proposal panel", async () => {
  const aiStep = await source("components/editor/ai-step.tsx");

  assert.match(
    aiStep,
    /import \{ EditorialMemoryIndicator \} from "\.\.\/editorial-memory-indicator"/,
  );
  assert.match(
    aiStep,
    /<EditorialMemoryIndicator\s+workspaceId=\{workspaceId\}\s+projectId=\{projectId\}\s*\/>/,
  );
});

test("account settings still expose the editorial-learning consent withdrawal", async () => {
  const settings = await source("components/editorial-learning-settings.tsx");
  assert.match(settings, /\/editorial-learning\/revoke/);
  assert.match(settings, /Withdraw all\s+editorial-learning consent/);
});
