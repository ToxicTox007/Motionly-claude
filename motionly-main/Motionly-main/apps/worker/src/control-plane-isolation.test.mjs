import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("workers execute media without the API job runner", async () => {
  const worker = await readFile(new URL("./worker.mjs", import.meta.url), "utf8");
  const remotion = await readFile(new URL("./remotion.mjs", import.meta.url), "utf8");
  assert.equal(worker.includes("/internal/jobs"), false);
  assert.equal(remotion.includes("/internal/renders"), false);
  assert.equal(remotion.includes("persistCompleteDirect"), true);
});
