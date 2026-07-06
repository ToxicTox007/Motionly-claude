#!/usr/bin/env node

import assert from "node:assert/strict";
import {
  allQueueNames,
  bullConnection,
  queueFor,
} from "../../apps/worker/src/queue-common.mjs";
import { getJobsPaged } from "../../apps/worker/src/queue-bridge.mjs";

const queueNames = (process.env.LOAD_TEST_QUEUES || "render")
  .split(",")
  .map((queue) => queue.trim())
  .filter((queue) => allQueueNames.includes(queue));
const jobsPerQueue = Number.parseInt(process.env.LOAD_TEST_JOBS_PER_QUEUE || "750", 10);
const runLive = process.env.RUN_BULLMQ_LOAD_TEST === "1";

if (!runLive) {
  console.log("Set RUN_BULLMQ_LOAD_TEST=1 with REDIS_URL to run the live BullMQ load test.");
  console.log(`Configured load: ${jobsPerQueue} jobs per queue across ${queueNames.join(", ")}`);
  process.exit(0);
}

if (!process.env.REDIS_URL) {
  throw new Error("REDIS_URL is required when RUN_BULLMQ_LOAD_TEST=1");
}
if (queueNames.length === 0) {
  throw new Error("LOAD_TEST_QUEUES did not include any known queues");
}
assert.ok(jobsPerQueue > 500, "LOAD_TEST_JOBS_PER_QUEUE must stay above 500");

const connection = bullConnection();
const batchId = `load-${Date.now()}`;

try {
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    for (let index = 0; index < jobsPerQueue; index += 1) {
      await queue.add(
        "load_test",
        {
          id: `${batchId}-${queueName}-${index}`,
          kind: "load_test",
          queue_name: queueName,
          enqueued_at: new Date().toISOString(),
          priority: 0,
          max_attempts: 1,
          load_test_batch_id: batchId,
        },
        {
          jobId: `${batchId}-${queueName}-${index}`,
          attempts: 1,
          removeOnComplete: true,
          removeOnFail: true,
        },
      );
    }

    const jobs = await getJobsPaged(queue, ["waiting", "prioritized", "delayed"]);
    const seeded = jobs.filter((job) => job.id?.startsWith(`${batchId}-${queueName}-`));
    assert.equal(
      seeded.length,
      jobsPerQueue,
      `${queueName} should return every seeded job across pagination`,
    );
    const oldest = seeded.reduce((min, job) => Math.min(min, Number(job.timestamp)), Infinity);
    console.log(
      JSON.stringify({
        queue: queueName,
        jobs_seeded: seeded.length,
        oldest_queued_seconds: Math.round((Date.now() - oldest) / 1000),
      }),
    );
  }
} finally {
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const jobs = await getJobsPaged(queue, ["waiting", "prioritized", "delayed"]);
    await Promise.all(
      jobs
        .filter((job) => job.id?.startsWith(`${batchId}-${queueName}-`))
        .map((job) => job.remove()),
    );
  }
  await connection.quit();
}
