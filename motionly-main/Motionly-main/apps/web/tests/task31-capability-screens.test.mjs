import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// Task 31: every capability the code supports can be reached from the app.
// These are source-contract tests — one per control — that assert each UI
// control calls the real API route and carries the required accessibility
// affordances (labels, confirmation dialogs for destructive actions).

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(resolve(root, relativePath), "utf8");
}

test("Audio tab: a stem can be chosen and persisted via the select route", async () => {
  const tab = await source("components/editor/tabs/audio-tab.tsx");
  // Loads the latest stem separation resource so the choice can be shown.
  assert.match(tab, /phase5\/resources\?kind=stem_separation/);
  // Persists the choice through the no_vocals/vocals selection route.
  assert.match(
    tab,
    /\/projects\/\$\{projectId\}\/phase5\/stems\/\$\{stem\.id\}/,
  );
  assert.match(tab, /method: "PATCH"/);
  assert.match(tab, /body: \{ active, expected_revision: stem\.revision \}/);
  // Both stems are selectable and the control is an accessible radio group.
  assert.match(tab, /role="radiogroup"/);
  assert.match(tab, /"no_vocals", "Instrumental only \(no vocals\)"/);
  assert.match(tab, /aria-checked=\{active\}/);
});

test("Audio tab: fixture stems show the 'Test stems' badge", async () => {
  const tab = await source("components/editor/tabs/audio-tab.tsx");
  assert.match(tab, /stem\?\.result\?\.stem_provider === "fixture"/);
  assert.match(tab, />\s*Test stems\s*</);
  assert.match(tab, /aria-label="Test stems from the fixture separator"/);
});

test("Scale panel: regenerate controls call the unified regenerate route", async () => {
  const panel = await source("components/editor/ScalePanel.tsx");
  assert.match(panel, /\/projects\/\$\{projectId\}\/regenerate`/);
  // Captions, B-roll, music, and effects are each selectable layers.
  for (const layer of ["captions", "broll", "music", "effects"]) {
    assert.match(panel, new RegExp(`\\["${layer}",`));
  }
  assert.match(panel, /aria-label=\{`Regenerate \$\{label\}`\}/);
  assert.match(panel, /body: \{ layers: regenerateSelection, force: true \}/);
});

test("Scale panel: a saved template can be renamed via PATCH", async () => {
  const panel = await source("components/editor/ScalePanel.tsx");
  assert.match(
    panel,
    /\/phase5\/templates\/\$\{template\.id\}`,[\s\S]*method: "PATCH"/,
  );
  assert.match(panel, /aria-label=\{`Rename \$\{template\.name\}`\}/);
  assert.match(panel, /body: \{ name: nextName \}/);
});

test("Scale panel: a saved template can be deleted with confirmation", async () => {
  const panel = await source("components/editor/ScalePanel.tsx");
  assert.match(
    panel,
    /\/phase5\/templates\/\$\{template\.id\}`,[\s\S]*method: "DELETE"/,
  );
  assert.match(panel, /aria-label=\{`Delete \$\{template\.name\}`\}/);
  // Destructive action is guarded by a confirmation dialog.
  assert.match(panel, /role="alertdialog"/);
  assert.match(panel, /Confirm delete/);
  assert.match(panel, /setPendingDeleteTemplateId/);
});

test("Scale panel: a batch can be canceled via the cancel route", async () => {
  const panel = await source("components/editor/ScalePanel.tsx");
  assert.match(panel, /\/phase5\/batches\/\$\{batch\.id\}\/cancel`/);
  assert.match(panel, /aria-label=\{`Cancel batch \$\{batch\.name\}`\}/);
  // Only cancelable batches expose an enabled control.
  assert.match(panel, /completed", "partial", "canceled"/);
});

test("Publishing panel: an asset-library item can be previewed", async () => {
  const panel = await source("components/editor/PublishingLibraryPanel.tsx");
  assert.match(panel, /\/asset-library\/items\/\$\{item\.id\}\/media-url/);
  assert.match(panel, /aria-label=\{`Preview \$\{item\.name\}`\}/);
  assert.match(panel, /role="dialog"/);
});

test("Publishing panel: an asset-library item can be downloaded", async () => {
  const panel = await source("components/editor/PublishingLibraryPanel.tsx");
  assert.match(panel, /async function downloadAssetItem/);
  assert.match(panel, /aria-label=\{`Download \$\{item\.name\}`\}/);
  assert.match(panel, /anchor\.download = item\.name/);
});

test("Admin ops: a button dead-letters stuck jobs via the admin route", async () => {
  const page = await source("app/(app)/admin/ops/page.tsx");
  const actions = await source("app/(app)/admin/ops/actions.ts");
  assert.match(actions, /\/admin\/queues\/stuck\/dead-letter/);
  assert.match(actions, /timeout_seconds: timeoutSeconds/);
  assert.match(page, /action=\{deadLetterStuckJobs\}/);
  assert.match(page, /Dead-letter stuck jobs/);
  assert.match(page, /htmlFor="dead-letter-timeout"/);
});
