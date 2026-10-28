import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(resolve(root, relativePath), "utf8");
}

test("editorial learning is explicit, revisioned, and withdrawable in account settings", async () => {
  const component = await source("components/editorial-learning-settings.tsx");
  const account = await source("app/(app)/account/page.tsx");

  assert.match(component, /\/editorial-learning\/status/);
  assert.match(component, /\/editorial-learning\/policy/);
  assert.match(component, /expected_revision: policy\?\.revision \?\? 0/);
  assert.match(component, /I acknowledge policy/);
  assert.match(component, /Withdraw all\s+editorial-learning consent/);
  assert.match(component, /memory:\s*true,\s*training:\s*true/);
  assert.match(
    component,
    /Source video,\s*transcript text,\s*prompts,\s*URLs,\s*and\s*raw editor content are not used/,
  );
  assert.match(
    account,
    /<EditorialLearningSettings workspaceId=\{workspaceId\} \/>/,
  );
});

test("project preference training is blocked by policy instead of analytics signals", async () => {
  const scalePanel = await source("components/editor/ScalePanel.tsx");

  assert.match(
    scalePanel,
    /editorialStatus\?\.enabled\s*&&\s*editorialPolicy\?\.training_enabled/,
  );
  assert.match(scalePanel, /Analytics signals never imply consent/);
  assert.match(scalePanel, /href="\/account"/);
  assert.match(scalePanel, /minimum_examples: trainingMinimumExamples/);
  assert.doesNotMatch(scalePanel, /minimum_examples: 3/);
});

test("editorial deployment operations require typed confirmations through server actions", async () => {
  const actions = await source("app/(app)/admin/editorial-learning/actions.ts");
  const page = await source("app/(app)/admin/editorial-learning/page.tsx");

  assert.match(actions, /formData\.get\("confirmation"\) !== "PROMOTE"/);
  assert.match(actions, /formData\.get\("confirmation"\) !== "ROLLBACK"/);
  assert.match(actions, /if \(!result\.ok\)/);
  assert.match(
    page,
    /Only IDs, item counts, state, and immutable manifest hashes are\s+shown/,
  );
  assert.match(page, /Type <strong>PROMOTE<\/strong>/);
});
