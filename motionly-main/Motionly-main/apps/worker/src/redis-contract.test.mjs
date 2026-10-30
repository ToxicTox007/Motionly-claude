import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { Queue, QueueEvents, Worker } from "bullmq";
import IORedis from "ioredis";

import { registerMaintenanceSchedulers } from "./worker.mjs";

const enabled = process.env.RUN_REDIS_CONTRACTS === "true";
const contractRedisUrl = () =>
  process.env.REDIS_URL || "redis://127.0.0.1:6379/15";

function bridgeEnqueue(payload) {
  const out = spawnSync("node", ["src/queue-bridge.mjs", "enqueue"], {
    cwd: new URL("..", import.meta.url),
    env: { ...process.env, REDIS_URL: contractRedisUrl(), JOB_ATTEMPTS: "1" },
    input: JSON.stringify(payload),
    encoding: "utf8",
  });
  if (out.status !== 0) throw new Error(out.stderr.trim());
  return JSON.parse(out.stdout.trim().split("\n").pop());
}

test(
  "a deliberate re-run of a finished job executes again; a plain duplicate still dedupes",
  { skip: enabled ? false : "set RUN_REDIS_CONTRACTS=true" },
  async () => {
    // Audit WJ-02: BullMQ resolves add(jobId) to a retained finished job, so a
    // render retry or forced re-transcription used to be a silent no-op.
    const queueName = "transcription";
    const connection = new IORedis(contractRedisUrl(), {
      maxRetriesPerRequest: null,
    });
    const queue = new Queue(queueName, { connection });
    await queue.obliterate({ force: true }).catch(() => {});
    let executions = 0;
    const workerConnection = connection.duplicate();
    const worker = new Worker(
      queueName,
      async () => {
        executions += 1;
        throw new Error("recorded failure");
      },
      { connection: workerConnection, concurrency: 1 },
    );
    await worker.waitUntilReady();
    const id = `transcribe--contract--${process.pid}--${Date.now()}`;
    const payload = { id, kind: "transcription", queue_name: queueName };
    try {
      bridgeEnqueue(payload);
      await waitUntil(() => executions === 1);
      await waitUntil(async () => (await queue.getJobState(id)) === "failed");

      const duplicate = bridgeEnqueue(payload);
      assert.equal(
        duplicate.status,
        "failed",
        "a plain duplicate returns the retained job",
      );
      await new Promise((resolve) => setTimeout(resolve, 300));
      assert.equal(executions, 1);

      const rerun = bridgeEnqueue({ ...payload, replace_terminal: true });
      assert.equal(rerun.status, "queued");
      await waitUntil(() => executions === 2);
    } finally {
      await worker.close();
      await queue.obliterate({ force: true });
      await queue.close();
      workerConnection.disconnect();
      connection.disconnect();
    }
  },
);

test(
  "maintenance schedules converge on one UTC scheduler per cron after a pattern change",
  { skip: enabled ? false : "set RUN_REDIS_CONTRACTS=true" },
  async () => {
    const connection = new IORedis(contractRedisUrl(), {
      maxRetriesPerRequest: null,
    });
    const queue = new Queue(
      `maintenance-contract-${process.pid}-${Date.now()}`,
      {
        connection,
      },
    );
    try {
      // What earlier releases registered on every start:
      await queue.add(
        "daily-retention",
        { kind: "retention" },
        {
          jobId: "daily-retention",
          repeat: { pattern: "0 3 * * *" },
        },
      );
      const job = (pattern) => ({
        name: "daily-retention",
        data: { kind: "retention", queue_name: "maintenance" },
        pattern,
      });
      await registerMaintenanceSchedulers(queue, [job("0 3 * * *")]);
      await registerMaintenanceSchedulers(queue, [job("30 4 * * *")]);
      await registerMaintenanceSchedulers(queue, [job("30 4 * * *")]);
      const schedulers = await queue.getJobSchedulers();
      assert.deepEqual(
        schedulers.map((s) => [s.key, s.pattern, s.tz]),
        [["daily-retention", "30 4 * * *", "UTC"]],
      );
    } finally {
      await queue.obliterate({ force: true });
      await queue.close();
      connection.disconnect();
    }
  },
);

async function waitUntil(predicate, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error("condition was not met in time");
}

test(
  "real Redis preserves BullMQ retry, terminal failure, and job-id idempotency",
  { skip: enabled ? false : "set RUN_REDIS_CONTRACTS=true" },
  async () => {
    const redisUrl = process.env.REDIS_URL || "redis://127.0.0.1:6379/15";
    const queueName = `motionly-contract-${process.pid}-${Date.now()}`;
    const connection = new IORedis(redisUrl, { maxRetriesPerRequest: null });
    const eventsConnection = connection.duplicate();
    const workerConnection = connection.duplicate();
    const queue = new Queue(queueName, { connection });
    const events = new QueueEvents(queueName, { connection: eventsConnection });
    let retryExecutions = 0;
    let terminalExecutions = 0;
    const worker = new Worker(
      queueName,
      async (job) => {
        if (job.name === "retry-once") {
          retryExecutions += 1;
          if (retryExecutions === 1)
            throw new Error("recorded transient failure");
          return { outcome: "success" };
        }
        terminalExecutions += 1;
        throw new Error("recorded terminal failure");
      },
      { connection: workerConnection, concurrency: 1 },
    );
    await Promise.all([
      queue.waitUntilReady(),
      events.waitUntilReady(),
      worker.waitUntilReady(),
    ]);
    try {
      const completed = waitFor(events, "completed", "retry-idempotent");
      const first = await queue.add(
        "retry-once",
        { source_id: "render-1" },
        {
          jobId: "retry-idempotent",
          attempts: 3,
          backoff: { type: "fixed", delay: 20 },
        },
      );
      const duplicate = await queue.add(
        "retry-once",
        { source_id: "render-1" },
        { jobId: "retry-idempotent", attempts: 3 },
      );
      assert.equal(first.id, duplicate.id);
      await completed;
      assert.equal(retryExecutions, 2);
      assert.equal(await first.getState(), "completed");

      const failed = waitFor(events, "failed", "terminal-failure");
      const terminal = await queue.add(
        "always-fails",
        {},
        {
          jobId: "terminal-failure",
          attempts: 2,
          backoff: { type: "fixed", delay: 20 },
        },
      );
      await failed;
      assert.equal(terminalExecutions, 2);
      assert.equal(await terminal.getState(), "failed");
      assert.equal(
        (await queue.getJob(terminal.id)).failedReason,
        "recorded terminal failure",
      );
    } finally {
      await Promise.allSettled([
        worker.close(),
        events.close(),
        queue.obliterate({ force: true }),
      ]);
      await queue.close();
      for (const redis of [workerConnection, eventsConnection, connection])
        redis.disconnect();
    }
  },
);

function waitFor(events, event, jobId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error(`Timed out waiting for ${event}`)),
      15_000,
    );
    const listener = (args) => {
      if (String(args.jobId) !== jobId) return;
      clearTimeout(timeout);
      events.off(event, listener);
      resolve(args);
    };
    events.on(event, listener);
  });
}
