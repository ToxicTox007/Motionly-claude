import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("media work is dispatched directly and never through the API job runner", async () => {
  const source = await readFile(
    new URL("./worker.mjs", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(source, /\/internal\/jobs\//);
  assert.match(source, /app\.workers\.domain_job/);
  assert.match(source, /app\.workers\.visual_job/);
  assert.match(source, /app\.workers\.matte_job/);
  assert.match(source, /persistentPythonExecutor/);
  assert.match(source, /"--serve"/);
});

test("standard worker defaults to the isolated Remotion queue", async () => {
  const source = await readFile(
    new URL("./queue-common.mjs", import.meta.url),
    "utf8",
  );
  assert.match(source, /defaultQueueNames = \["render_remotion"\]/);
  for (const queue of [
    "audio_demucs",
    "provider_calls",
    "visual_analysis",
    "matte_extraction",
  ]) {
    assert.match(source, new RegExp(`"${queue}"`));
  }
});

test("visual_analysis concurrency defaults to 1 and is configurable", async () => {
  const { workerConcurrency } = await import("./queue-common.mjs");

  assert.equal(workerConcurrency("visual_analysis"), 1);

  process.env.VISUAL_ANALYSIS_WORKER_CONCURRENCY = "4";
  assert.equal(workerConcurrency("visual_analysis"), 4);
  delete process.env.VISUAL_ANALYSIS_WORKER_CONCURRENCY;

  assert.equal(workerConcurrency("matte_extraction"), 1);
  process.env.MATTE_WORKER_CONCURRENCY = "2";
  assert.equal(workerConcurrency("matte_extraction"), 2);
  delete process.env.MATTE_WORKER_CONCURRENCY;

  assert.equal(workerConcurrency("transcription"), 3);
});

test("Docker Compose statically assigns GPU/media queues", async () => {
  const compose = await readFile(
    new URL("../../../infra/docker-compose.gpu.yml", import.meta.url),
    "utf8",
  );

  assert.match(compose, /WORKER_QUEUES: visual_analysis,transcription/);
  assert.match(compose, /WORKER_QUEUES: matte_extraction/);
  assert.match(compose, /WORKER_QUEUES: render_gpu/);
  assert.match(compose, /HF_HOME: \/var\/cache\/motionly\/huggingface/);
  assert.match(compose, /TORCH_HOME: \/var\/cache\/motionly\/torch/);
  assert.match(compose, /XDG_CACHE_HOME: \/var\/cache\/motionly/);
});

test("worker runtime telemetry reports queue wait and runtime", async () => {
  const { jobRuntimeTelemetry, queueWaitMs } = await import("./worker.mjs");
  const job = {
    timestamp: Date.parse("2026-01-01T00:00:01.000Z"),
    data: {
      enqueued_at: "2026-01-01T00:00:00.000Z",
    },
  };

  assert.equal(queueWaitMs(job, Date.parse("2026-01-01T00:00:05.000Z")), 5000);
  assert.deepEqual(
    jobRuntimeTelemetry(job, {
      startedAt: Date.parse("2026-01-01T00:00:05.000Z"),
      finishedAt: Date.parse("2026-01-01T00:00:08.250Z"),
    }),
    {
      runtime_ms: 3250,
      queue_wait_ms: 5000,
    },
  );
});

test("worker startup emits explicit readiness fields", async () => {
  const source = await readFile(
    new URL("./worker.mjs", import.meta.url),
    "utf8",
  );
  assert.match(source, /event: "worker_ready"/);
  assert.match(source, /process_alive: true/);
  assert.match(source, /redis_connected: true/);
  assert.match(source, /api_reachable: hasApiConfig\(\)/);
  assert.match(source, /disk_free_bytes: diskFreeBytes\(\)/);
  assert.match(source, /queue_consumers_attached: workers\.length/);
});
