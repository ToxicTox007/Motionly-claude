#!/usr/bin/env node
// Adapt a k6 smoke summary (scripts/perf/k6-api-workflows.js handleSummary)
// into the canary shape scripts/perf/assert-slo.mjs enforces, so the CPU-stage
// perf stage can gate the smoke run against scripts/perf/slo-contract.cpu-stage.json.
//
//   k6-to-canary.mjs --summary <k6-summary.json> --out <canary.json> \
//     [--revision <sha>] [--window-seconds 60]
//
// The k6 run must have failed already on any non-2xx (K6_STRICT_SLO=1 makes the
// checks and http_req_failed thresholds abort the run), so by the time this
// runs the summary describes a 2xx-only workload. The CPU stage serves only
// fixture-backed providers with no queue backpressure, so provider and queue
// surfaces are reported as zero-error / zero-backlog; assert-slo still enforces
// the per-surface p95 and sample-count floors from the contract.

import { readFile, writeFile } from "node:fs/promises";

export function toCanary(
  summary,
  { revision = null, windowSeconds = 60 } = {},
) {
  if (!summary || summary.schema_version !== 1) {
    throw new Error(
      "k6 summary is missing or has an unexpected schema_version",
    );
  }
  const surfaces = summary.samples_by_surface || {};
  return {
    schema_version: 1,
    revision: revision || summary.revision || "unversioned",
    window_seconds: windowSeconds,
    samples: {
      http_requests: summary.http_requests ?? 0,
      projects: surfaces.projects ?? 0,
      uploads: surfaces.uploads ?? 0,
      plans: surfaces.plans ?? 0,
      render_status: surfaces.render_status ?? 0,
      ops: surfaces.ops ?? 0,
    },
    http: {
      error_rate: summary.http_error_rate ?? 0,
      // The strict k6 run already failed on any non-2xx; absent a dedicated
      // 5xx metric the overall request-failure rate is the ceiling.
      status_5xx_rate: summary.status_5xx_rate ?? summary.http_error_rate ?? 0,
      p95_ms_by_surface: { ...(summary.p95_ms_by_surface || {}) },
    },
    // CPU-stage fixtures: no real provider calls, no queue backpressure.
    queues: { backpressure_count: 0, max_oldest_queued_seconds: 0 },
    providers: { error_rate: 0, p95_latency_ms: 0 },
  };
}

function parseArgs(values) {
  const args = {};
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (!value.startsWith("--")) continue;
    args[value.slice(2)] = values[index + 1];
    index += 1;
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.summary || !args.out) {
    throw new Error(
      "Usage: k6-to-canary.mjs --summary <path> --out <path> [--revision s] [--window-seconds n]",
    );
  }
  const summary = JSON.parse(await readFile(args.summary, "utf8"));
  const canary = toCanary(summary, {
    revision: args.revision,
    windowSeconds: args["window-seconds"] ? Number(args["window-seconds"]) : 60,
  });
  await writeFile(args.out, `${JSON.stringify(canary, null, 2)}\n`);
  console.log(`k6-to-canary: wrote ${args.out} (revision ${canary.revision})`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error(`k6-to-canary: ${error.message}`);
    process.exitCode = 1;
  });
}
