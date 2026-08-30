import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const root = new URL("../../", import.meta.url);
const contractPath = new URL("slo-contract.json", import.meta.url).pathname;
const scriptPath = new URL("assert-slo.mjs", import.meta.url).pathname;

async function fixture(overrides = {}) {
  const dir = await mkdtemp(join(tmpdir(), "motionly-slo-"));
  const payload = {
    schema_version: 1,
    revision: "candidate",
    stable_revision: "stable",
    window_seconds: 300,
    samples: { http_requests: 100, projects: 20, uploads: 20, plans: 20, render_status: 20, ops: 20 },
    http: { error_rate: 0.001, status_5xx_rate: 0.001, p95_ms_by_surface: { projects: 900, uploads: 1200, plans: 1700, render_status: 650, ops: 1100 } },
    queues: { backpressure_count: 0, max_oldest_queued_seconds: 45 },
    providers: { error_rate: 0.002, p95_latency_ms: 1400 },
    ...overrides,
  };
  const path = join(dir, "canary.json");
  await writeFile(path, JSON.stringify(payload));
  return path;
}

async function run(path, extra = []) {
  return spawnSync(process.execPath, [scriptPath, "--contract", contractPath, "--canary", path, "--revision", "candidate", ...extra], { encoding: "utf8" });
}

test("SLO assertion accepts a complete in-budget candidate", async () => {
  const result = await run(await fixture());
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).passed, true);
});

test("SLO assertion fails closed for insufficient samples", async () => {
  const result = await run(await fixture({ samples: { http_requests: 99 } }));
  assert.equal(result.status, 1);
  assert.match(result.stdout, /insufficient_samples/);
});

test("SLO assertion rejects a regression and supports observe mode", async () => {
  const path = await fixture({ http: { error_rate: 0.2, status_5xx_rate: 0.001, p95_ms_by_surface: { projects: 900, uploads: 1200, plans: 1700, render_status: 650, ops: 1100 } } });
  const enforcing = await run(path);
  const observing = await run(path, ["--mode", "observe"]);
  assert.equal(enforcing.status, 1);
  assert.equal(observing.status, 0);
  assert.match(observing.stdout, /threshold_breached:http_error_rate/);
});

test("SLO assertion rejects a mismatched deployment revision", async () => {
  const result = await run(await fixture(), ["--revision", "other"]);
  assert.equal(result.status, 1);
  assert.match(result.stdout, /canary_revision_mismatch/);
});

test("SLO assertion rejects an over-age queued workload", async () => {
  const result = await run(await fixture({
    queues: { backpressure_count: 0, max_oldest_queued_seconds: 121 },
  }));
  assert.equal(result.status, 1);
  assert.match(result.stdout, /threshold_breached:queue_oldest_queued_seconds/);
});
