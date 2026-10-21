import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { toCanary } from "./k6-to-canary.mjs";
import { evaluate } from "./assert-slo.mjs";

const contractPath = new URL("slo-contract.cpu-stage.json", import.meta.url)
  .pathname;

function k6Summary(overrides = {}) {
  return {
    schema_version: 1,
    mode: "strict",
    revision: "candidate",
    http_requests: 500,
    http_error_rate: 0,
    status_5xx_rate: 0,
    checks_rate: 1,
    samples_by_surface: {
      projects: 60,
      uploads: 60,
      plans: 40,
      render_status: 40,
      ops: 40,
    },
    p95_ms_by_surface: {
      projects: 700,
      uploads: 900,
      plans: 1400,
      render_status: 500,
      ops: 1000,
    },
    ...overrides,
  };
}

test("adapts a clean k6 smoke summary into a canary assert-slo accepts", async () => {
  const contract = JSON.parse(await readFile(contractPath, "utf8"));
  const canary = toCanary(k6Summary(), { revision: "candidate" });

  assert.equal(canary.samples.http_requests, 500);
  assert.equal(canary.http.p95_ms_by_surface.plans, 1400);
  assert.deepEqual(canary.queues, {
    backpressure_count: 0,
    max_oldest_queued_seconds: 0,
  });

  const decision = evaluate({ contract, canary, revision: "candidate" });
  assert.equal(decision.passed, true, JSON.stringify(decision.failures));
});

test("a 5xx or failed request in the summary fails the SLO gate", async () => {
  const contract = JSON.parse(await readFile(contractPath, "utf8"));
  const canary = toCanary(
    k6Summary({ http_error_rate: 0.02, status_5xx_rate: 0.02 }),
  );

  const decision = evaluate({ contract, canary });
  assert.equal(decision.passed, false);
  assert.ok(
    decision.failures.some((f) => f.includes("status_5xx_rate")) ||
      decision.failures.some((f) => f.includes("http_error_rate")),
    JSON.stringify(decision.failures),
  );
});

test("too few samples fails the SLO gate", async () => {
  const contract = JSON.parse(await readFile(contractPath, "utf8"));
  const canary = toCanary(
    k6Summary({ http_requests: 10, samples_by_surface: { projects: 1 } }),
  );

  const decision = evaluate({ contract, canary });
  assert.equal(decision.passed, false);
  assert.ok(
    decision.failures.some((f) => f.startsWith("insufficient_samples")),
  );
});

test("a p95 over the surface budget fails the SLO gate", async () => {
  const contract = JSON.parse(await readFile(contractPath, "utf8"));
  const canary = toCanary(
    k6Summary({
      p95_ms_by_surface: {
        projects: 5000,
        uploads: 900,
        plans: 1400,
        render_status: 500,
        ops: 1000,
      },
    }),
  );

  const decision = evaluate({ contract, canary });
  assert.equal(decision.passed, false);
  assert.ok(decision.failures.includes("threshold_breached:p95_ms:projects"));
});

test("a malformed summary is rejected", () => {
  assert.throws(() => toCanary({}), /schema_version/);
  assert.throws(() => toCanary(null), /missing/);
});
