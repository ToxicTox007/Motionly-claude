#!/usr/bin/env node

import { readFile } from "node:fs/promises";

const args = parseArgs(process.argv.slice(2));
if (!args.contract || !args.canary) {
  throw new Error("Usage: assert-slo.mjs --contract <path> --canary <path> [--baseline <path>] [--revision <sha>] [--mode observe|enforce]");
}
const mode = args.mode || "enforce";
if (!new Set(["observe", "enforce"]).has(mode)) {
  throw new Error("--mode must be observe or enforce");
}

const contract = await readJson(args.contract, "SLO contract");
const canary = await readJson(args.canary, "canary metrics");
const baseline = args.baseline ? await readJson(args.baseline, "baseline metrics") : null;
const decision = evaluate({ contract, canary, baseline, revision: args.revision });
console.log(JSON.stringify({ mode, ...decision }, null, 2));
if (!decision.passed && mode === "enforce") process.exitCode = 1;

export function evaluate({ contract, canary, baseline = null, revision = null }) {
  const failures = [];
  const requiredSampleSurfaces = [
    "http_requests",
    "projects",
    "uploads",
    "plans",
    "render_status",
    "ops",
  ];
  const requiredP95Surfaces = ["projects", "uploads", "plans", "render_status", "ops"];

  if (contract?.schema_version !== 1) failures.push("contract_schema_invalid");
  if (!nonEmptyString(contract?.contract_revision)) failures.push("contract_revision_missing");
  if (!positiveNumber(contract?.window_seconds)) failures.push("contract_window_invalid");
  if (canary?.schema_version !== 1) failures.push("canary_schema_invalid");
  if (!nonEmptyString(canary?.revision)) failures.push("canary_revision_missing");
  if (revision && canary?.revision !== revision) failures.push("canary_revision_mismatch");
  if (!positiveNumber(canary?.window_seconds) || canary.window_seconds < Number(contract?.window_seconds || 300)) {
    failures.push("canary_window_too_short");
  }

  const minimumSamples = contract?.minimum_samples || {};
  for (const surface of requiredSampleSurfaces) {
    const minimum = minimumSamples[surface];
    if (!positiveNumber(minimum)) {
      failures.push(`contract_minimum_invalid:${surface}`);
    } else if (!positiveNumber(canary?.samples?.[surface]) || canary.samples[surface] < minimum) {
      failures.push(`insufficient_samples:${surface}`);
    }
  }

  const thresholds = contract?.thresholds || {};
  compareAtMost(failures, "http_error_rate", canary?.http?.error_rate, thresholds.http_error_rate);
  compareAtMost(failures, "status_5xx_rate", canary?.http?.status_5xx_rate, thresholds.status_5xx_rate);
  compareAtMost(
    failures,
    "queue_backpressure_count",
    canary?.queues?.backpressure_count,
    thresholds.queue_backpressure_count,
  );
  compareAtMost(
    failures,
    "queue_oldest_queued_seconds",
    canary?.queues?.max_oldest_queued_seconds,
    thresholds.queue_oldest_queued_seconds,
  );
  compareAtMost(failures, "provider_error_rate", canary?.providers?.error_rate, thresholds.provider_error_rate);
  compareAtMost(
    failures,
    "provider_p95_latency_ms",
    canary?.providers?.p95_latency_ms,
    thresholds.provider_p95_latency_ms,
  );
  for (const surface of requiredP95Surfaces) {
    compareAtMost(
      failures,
      `p95_ms:${surface}`,
      canary?.http?.p95_ms_by_surface?.[surface],
      thresholds.p95_ms_by_surface?.[surface],
    );
  }
  const p95Multiplier = thresholds.relative_p95_regression;
  if (!finiteNumber(p95Multiplier) || p95Multiplier < 1) {
    failures.push("contract_relative_p95_regression_invalid");
  }
  const errorRateIncrease = thresholds.relative_error_rate_increase;
  if (!finiteNumber(errorRateIncrease) || errorRateIncrease < 0) {
    failures.push("contract_relative_error_rate_increase_invalid");
  }

  if (baseline) {
    if (baseline.schema_version !== 1 || !nonEmptyString(baseline.revision)) {
      failures.push("baseline_invalid");
    } else {
      for (const surface of requiredP95Surfaces) {
        const candidate = canary?.http?.p95_ms_by_surface?.[surface];
        const stable = baseline?.http?.p95_ms_by_surface?.[surface];
        if (!positiveNumber(candidate) || !positiveNumber(stable)) {
          failures.push(`baseline_p95_missing:${surface}`);
        } else if (finiteNumber(p95Multiplier) && candidate > stable * p95Multiplier) {
          failures.push(`p95_regression:${surface}`);
        }
      }
      const candidateErrorRate = canary?.http?.error_rate;
      const stableErrorRate = baseline?.http?.error_rate;
      if (!finiteNumber(candidateErrorRate) || !finiteNumber(stableErrorRate)) {
        failures.push("baseline_error_rate_missing");
      } else if (finiteNumber(errorRateIncrease) && candidateErrorRate > Math.max(
        Number(thresholds.http_error_rate),
        stableErrorRate + errorRateIncrease,
      )) {
        failures.push("error_rate_regression");
      }
    }
  }
  return { passed: failures.length === 0, failures };
}

function compareAtMost(failures, name, actual, maximum) {
  if (!finiteNumber(actual)) failures.push(`metric_missing:${name}`);
  else if (!finiteNumber(maximum) || actual > maximum) failures.push(`threshold_breached:${name}`);
}
function finiteNumber(value) { return typeof value === "number" && Number.isFinite(value); }
function positiveNumber(value) { return finiteNumber(value) && value > 0; }
function nonEmptyString(value) { return typeof value === "string" && value.trim().length > 0; }
async function readJson(path, label) {
  try { return JSON.parse(await readFile(path, "utf8")); }
  catch (error) { throw new Error(`${label} is invalid: ${error.message}`); }
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
