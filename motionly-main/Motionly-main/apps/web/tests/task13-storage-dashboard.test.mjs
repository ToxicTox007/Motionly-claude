// Task 13 / B23: the dashboard storage gauge uses the real workspace quota
// from the API, not a fabricated projects-count heuristic.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("DashboardClient renders the real storage gauge, not a fake bar", async () => {
  const source = await readFile(
    new URL("components/DashboardClient.tsx", root),
    "utf8",
  );
  // The fabricated "projects * 10%" storage bar is gone.
  assert.doesNotMatch(source, /localProjects\.length \* 10/);
  // The gauge is fed by the storage prop and exposes an accessible progressbar.
  assert.match(source, /storage\?\.percentLabel \?\? "Not metered"/);
  assert.match(source, /role="progressbar"/);
  assert.match(source, /aria-valuenow=\{storage\.percent\}/);
});

test("dashboard page sources storage from GET /storage/quota", async () => {
  const source = await readFile(
    new URL("app/(app)/dashboard/page.tsx", root),
    "utf8",
  );
  assert.match(source, /storageGauge/);
  assert.match(source, /\/storage\/quota/);
  assert.match(source, /storage=\{storage\}/);
});
