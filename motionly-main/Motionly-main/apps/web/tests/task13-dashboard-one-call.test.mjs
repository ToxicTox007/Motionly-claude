// Task 13 / B29: the dashboard reads each project's latest upload from the
// embedded field on GET /projects, making ONE API call instead of /projects
// plus one /uploads/{id} per project. The call count itself is asserted by
// behaviour in tests/unit/dashboard-data.test.ts (it records every request);
// these checks keep the pages on that loader.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("dashboard reads latest_upload through the one-call loader", async () => {
  const [page, loader] = await Promise.all([
    readFile(new URL("app/(app)/dashboard/page.tsx", root), "utf8"),
    readFile(new URL("lib/dashboard-data.ts", root), "utf8"),
  ]);
  assert.match(page, /loadDashboardData\(context\.headers\)/);
  assert.doesNotMatch(page, /\/uploads\/\$\{/);
  assert.match(loader, /latest_upload/);
  assert.doesNotMatch(loader, /\/uploads\//);
  // One projects request (the latest page, built by projectListApiPath).
  const projectsCalls = loader.match(
    /serverApiFetch<[^>]*>\(\s*(?:"\/projects|projectListApiPath\()/g,
  );
  assert.equal(projectsCalls?.length, 1);
});

test("projects page makes a single projects call", async () => {
  const source = await readFile(
    new URL("app/(app)/projects/page.tsx", root),
    "utf8",
  );
  const calls = source.match(/serverApiFetch[<(]/g) ?? [];
  assert.equal(calls.length, 1);
  assert.doesNotMatch(source, /await fetch\(/);
  assert.doesNotMatch(source, /\/uploads\/\$\{/);
});
