import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("media work is dispatched directly and never through the API job runner", async () => {
  const source = await readFile(new URL("./worker.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(source, /\/internal\/jobs\//);
  assert.match(source, /app\.workers\.domain_job/);
  assert.match(source, /app\.workers\.visual_job/);
  assert.match(source, /persistentPythonExecutor/);
  assert.match(source, /"--serve"/);
});

test("standard worker defaults to the isolated Remotion queue", async () => {
  const source = await readFile(new URL("./queue-common.mjs", import.meta.url), "utf8");
  assert.match(source, /defaultQueueNames = \["render_remotion"\]/);
  for (const queue of ["audio_demucs", "provider_calls", "visual_analysis"]) {
    assert.match(source, new RegExp(`"${queue}"`));
  }
});
