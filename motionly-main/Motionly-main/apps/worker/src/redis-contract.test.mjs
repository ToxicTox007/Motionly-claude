import assert from "node:assert/strict";
import test from "node:test";
import { Queue, QueueEvents, Worker } from "bullmq";
import IORedis from "ioredis";

const enabled = process.env.RUN_REDIS_CONTRACTS === "true";

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
