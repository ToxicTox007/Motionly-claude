import assert from "node:assert/strict";
import { mkdtempSync, rmSync, statSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { WORKER_LIVENESS_FILE, isAlive, markAlive } from "./liveness.mjs";

test("the liveness file is created and refreshed, and goes stale when not refreshed", () => {
  const directory = mkdtempSync(join(tmpdir(), "motionly-liveness-"));
  try {
    const file = join(directory, "alive");
    assert.equal(isAlive(file, 60_000), false);

    markAlive(file);
    assert.equal(isAlive(file, 60_000), true);

    const old = new Date(Date.now() - 120_000);
    utimesSync(file, old, old);
    assert.equal(isAlive(file, 60_000), false);
    markAlive(file);
    assert.ok(Date.now() - statSync(file).mtimeMs < 5_000);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("the default file is the one the Compose health checks read", () => {
  assert.equal(WORKER_LIVENESS_FILE, "/tmp/motionly-worker-alive");
});
