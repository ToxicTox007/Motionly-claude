import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(resolve(root, relativePath), "utf8");
}

test("creative variants panel exposes Apply and Apply & render actions", async () => {
  const component = await source("components/editor/CreativeVariantsPanel.tsx");

  // Both actions exist and are accessible (aria-labels per variant).
  assert.match(component, /Apply direction/);
  assert.match(component, /Apply &amp; render/);
  assert.match(
    component,
    /aria-label=\{`Apply \$\{variant\.label\} to the edit`\}/,
  );
  assert.match(
    component,
    /aria-label=\{`Apply \$\{variant\.label\} and render the new version`\}/,
  );

  // Apply with render=false, Apply & render with render=true.
  assert.match(component, /applyVariant\(variant\.id, false\)/);
  assert.match(component, /applyVariant\(variant\.id, true\)/);
});

test("apply posts to the apply route with both revision guards and render flag", async () => {
  const component = await source("components/editor/CreativeVariantsPanel.tsx");

  // Hits the apply endpoint with POST.
  assert.match(component, /\/creative-variants\/\$\{activeSet\.id\}\/apply`/);

  // Guards the variant set AND the active plan revision, and passes render.
  assert.match(component, /variant_id: variantId/);
  assert.match(component, /expected_revision: activeSet\.revision/);
  assert.match(component, /expected_plan_revision: expectedPlanRevision/);
  assert.match(component, /\brender,/);
});

test("apply routes a 409 through a reload + retry (handleConflict pattern)", async () => {
  const component = await source("components/editor/CreativeVariantsPanel.tsx");

  // Catches the standard revision conflict and retries the SAME change once
  // against the freshly fetched revision — not a silent swallow, not a blind
  // retry with the stale revision.
  assert.match(component, /error instanceof RevisionConflictError/);
  assert.match(component, /fetchLatestPlanRevision\(\)/);
  assert.match(component, /result = await runApply\(latest\)/);
});

test("applying a variant surfaces the new plan version to the editor", async () => {
  const component = await source("components/editor/CreativeVariantsPanel.tsx");

  // The applied plan version is pushed back up so the editor can preview it.
  assert.match(component, /onPlanUpdated\?\.\(result\.plan\)/);

  // ScalePanel wires the editor's plan-updated callback into the panel.
  const scalePanel = await source("components/editor/ScalePanel.tsx");
  assert.match(scalePanel, /onPlanUpdated=\{onPlanUpdated\}/);
});
