// Task 13 / B34: dashboard delete reports the real outcome.
// The old code treated a thrown NEXT_REDIRECT as success; the action now
// returns a result object and the modal surfaces failures with an error toast
// while keeping the card.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("deleteProjectResult action returns a result object instead of only redirecting", async () => {
  const source = await readFile(
    new URL("app/(app)/projects/actions.ts", root),
    "utf8",
  );
  assert.match(source, /export async function deleteProjectResult/);
  assert.match(source, /export type DeleteProjectResult/);
  // Success and failure are explicit result shapes.
  assert.match(source, /return \{ ok: true \}/);
  assert.match(source, /ok: false, error:/);
  // Failure is reported for a non-ok response or a non-deleted payload.
  assert.match(source, /!response\.ok \|\| !payload\.deleted/);
});

test("DashboardClient confirmDelete no longer treats NEXT_REDIRECT as success", async () => {
  const source = await readFile(
    new URL("components/DashboardClient.tsx", root),
    "utf8",
  );
  // The brittle NEXT_REDIRECT success heuristic is gone.
  assert.doesNotMatch(source, /NEXT_REDIRECT/);
  // It calls the result-returning action and branches on the real outcome.
  assert.match(source, /deleteProjectResult\(projectToDelete\.id\)/);
  assert.match(source, /if \(!result\.ok\)/);
  // A failed delete shows an error toast and keeps the card: the failure
  // branch surfaces an error variant and returns before mutating local state.
  assert.match(
    source,
    /if \(!result\.ok\)[\s\S]*?variant: "error"[\s\S]*?return;/,
  );
});
