#!/usr/bin/env node

import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { performance } from "node:perf_hooks";
import { allQueueNames, bullConnection, queueFor } from "../../apps/worker/src/queue-common.mjs";
import { getJobsPaged } from "../../apps/worker/src/queue-bridge.mjs";

const queueNames = (process.env.LOAD_TEST_QUEUES || "render").split(",").map((queue) => queue.trim()).filter((queue) => allQueueNames.includes(queue));
const jobsPerQueue = Number.parseInt(process.env.LOAD_TEST_JOBS_PER_QUEUE || "750", 10);
const runLive = process.env.RUN_BULLMQ_LOAD_TEST === "1";
const outputPath = process.env.BULLMQ_LOAD_OUTPUT || "test-results/bullmq-load.json";
const enforce = process.env.PERF_ENFORCE === "1";
const budgets = {
  seed_max_ms: Number(process.env.BULLMQ_SEED_MAX_MS || 30_000),
  snapshot_max_ms: Number(process.env.BULLMQ_SNAPSHOT_MAX_MS || 10_000),
  cleanup_max_ms: Number(process.env.BULLMQ_CLEANUP_MAX_MS || 30_000),
};

if (!runLive) {
  await writeOutput({ schema_version: 1, mode: "dry_run", message: "Set RUN_BULLMQ_LOAD_TEST=1 with REDIS_URL to run the live BullMQ load test.", configured_jobs_per_queue: jobsPerQueue, queues: queueNames, budgets });
  console.log("Set RUN_BULLMQ_LOAD_TEST=1 with REDIS_URL to run the live BullMQ load test.");
  process.exit(0);
}
if (!process.env.REDIS_URL) throw new Error("REDIS_URL is required when RUN_BULLMQ_LOAD_TEST=1");
if (queueNames.length === 0) throw new Error("LOAD_TEST_QUEUES did not include any known queues");
assert.ok(jobsPerQueue > 500, "LOAD_TEST_JOBS_PER_QUEUE must stay above 500");

const connection = bullConnection();
const batchId = `load-${Date.now()}`;
const results = [];
try {
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const seedStart = performance.now();
    for (let index = 0; index < jobsPerQueue; index += 1) {
      await queue.add("load_test", { id: `${batchId}-${queueName}-${index}`, kind: "load_test", queue_name: queueName, enqueued_at: new Date().toISOString(), priority: 0, max_attempts: 1, load_test_batch_id: batchId }, { jobId: `${batchId}-${queueName}-${index}`, attempts: 1, removeOnComplete: true, removeOnFail: true });
    }
    const seededMs = Math.round(performance.now() - seedStart);
    const snapshotStart = performance.now();
    const jobs = await getJobsPaged(queue, ["waiting", "prioritized", "delayed"]);
    const snapshotMs = Math.round(performance.now() - snapshotStart);
    const seeded = jobs.filter((job) => job.id?.startsWith(`${batchId}-${queueName}-`));
    assert.equal(seeded.length, jobsPerQueue, `${queueName} should return every seeded job across pagination`);
    const oldest = seeded.reduce((min, job) => Math.min(min, Number(job.timestamp)), Infinity);
    const result = { queue: queueName, jobs_seeded: seeded.length, enqueue_duration_ms: seededMs, pagination_duration_ms: snapshotMs, oldest_queued_seconds: Math.round((Date.now() - oldest) / 1000), cleanup_duration_ms: null, cleanup_verified: false };
    results.push(result);
    console.log(JSON.stringify(result));
  }
} finally {
  for (const result of results) {
    const queue = queueFor(result.queue, connection);
    const cleanupStart = performance.now();
    const jobs = await getJobsPaged(queue, ["waiting", "prioritized", "delayed"]);
    await Promise.all(jobs.filter((job) => job.id?.startsWith(`${batchId}-${result.queue}-`)).map((job) => job.remove()));
    result.cleanup_duration_ms = Math.round(performance.now() - cleanupStart);
    const remaining = await getJobsPaged(queue, ["waiting", "prioritized", "delayed"]);
    result.cleanup_verified = !remaining.some((job) => job.id?.startsWith(`${batchId}-${result.queue}-`));
  }
  await connection.quit();
}
const violations = results.flatMap((result) => [
  result.enqueue_duration_ms > budgets.seed_max_ms && `${result.queue}:seed`,
  result.pagination_duration_ms > budgets.snapshot_max_ms && `${result.queue}:snapshot`,
  result.cleanup_duration_ms > budgets.cleanup_max_ms && `${result.queue}:cleanup`,
  !result.cleanup_verified && `${result.queue}:cleanup_verification`,
].filter(Boolean));
await writeOutput({ schema_version: 1, mode: "live", enforce, batch_id: batchId, budgets, results, violations });
if (enforce && violations.length) throw new Error(`BullMQ load budgets failed: ${violations.join(", ")}`);

async function writeOutput(payload) {
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}
