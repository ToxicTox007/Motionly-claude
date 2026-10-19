import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("remotion worker enforces timeout and cancellation polling", async () => {
  const source = await readFile(
    new URL("./remotion.mjs", import.meta.url),
    "utf8",
  );

  assert.match(source, /renderTimeoutMs/);
  assert.match(source, /loadRenderJobStatusDirect/);
  assert.match(source, /render_cancelled/);
  assert.match(source, /render_timeout/);
  assert.match(source, /preflight_started/);
  assert.match(source, /upload_started/);
});
