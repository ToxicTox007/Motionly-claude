// Task 13 / B29: the dashboard reads each project's latest upload from the
// embedded field on GET /projects, making ONE API call instead of /projects
// plus one /uploads/{id} per project.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("dashboard reads latest_upload from the projects response", async () => {
  const source = await readFile(
    new URL("app/(app)/dashboard/page.tsx", root),
    "utf8",
  );
  assert.match(source, /latest_upload/);
  // The per-project uploads request is gone.
  assert.doesNotMatch(source, /\/uploads\/\$\{/);
  // Only one fetch to the projects collection remains in the data path.
  const projectsFetches =
    source.match(/fetch\(`\$\{env\.apiBaseUrl\}\/projects/g) ?? [];
  assert.equal(projectsFetches.length, 1);
});

test("projects page makes a single projects call", async () => {
  const source = await readFile(
    new URL("app/(app)/projects/page.tsx", root),
    "utf8",
  );
  const fetches = source.match(/await fetch\(/g) ?? [];
  assert.equal(fetches.length, 1);
  assert.doesNotMatch(source, /\/uploads\/\$\{/);
});
