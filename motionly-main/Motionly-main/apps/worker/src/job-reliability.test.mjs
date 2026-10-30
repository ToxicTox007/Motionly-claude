import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  executorPoolSize,
  isPermanentJobFailure,
  isTransientWorkflowRpcError,
  persistentPythonExecutor,
  registerMaintenanceSchedulers,
  resetPythonExecutorsForTest,
  runJob,
  withWorkflowRpcRetry,
} from "./worker.mjs";

// A persistent executor stand-in that, like app/workers/*_job.py --serve,
// answers one request at a time after `delayFor(payload)` milliseconds
// (`null` = never answers).
class SerialChild extends EventEmitter {
  constructor(delayFor) {
    super();
    this.exitCode = null;
    this.killed = false;
    this.stdout = new EventEmitter();
    this.stderr = new EventEmitter();
    this.busy = Promise.resolve();
    this.stdin = {
      destroyed: false,
      writable: true,
      write: (chunk, callback) => {
        const request = JSON.parse(String(chunk).trim());
        callback?.(null);
        if (request.control === "ping") {
          queueMicrotask(() =>
            this.reply({ id: request.id, result: { ok: true } }),
          );
          return true;
        }
        this.busy = this.busy.then(
          () =>
            new Promise((resolve) => {
              const delay = delayFor(request.payload);
              if (delay === null) return; // hangs forever
              setTimeout(() => {
                this.reply({
                  id: request.id,
                  result: { job: request.payload.job },
                });
                resolve();
              }, delay);
            }),
        );
        return true;
      },
      end: () => {
        this.stdin.destroyed = true;
        this.stdin.writable = false;
      },
    };
  }

  reply(message) {
    if (!this.killed) this.stdout.emit("data", `${JSON.stringify(message)}\n`);
  }

  kill() {
    this.killed = true;
    this.exitCode = 1;
    this.emit("close", 1, "SIGTERM");
  }
}

test.afterEach(() => {
  resetPythonExecutorsForTest();
});

function executorOptions(children, delayFor, overrides = {}) {
  return {
    heartbeatIntervalMs: 0,
    startupTimeoutMs: 1_000,
    requestTimeoutMs: 120,
    restartBackoffMs: 1,
    restartBackoffCapMs: 1,
    sleep: async () => {},
    log: () => {},
    spawnImpl: () => {
      const child = new SerialChild(delayFor);
      children.push(child);
      return child;
    },
    ...overrides,
  };
}

test("jobs queued behind a busy executor never time out while waiting", async () => {
  // Three jobs of 80 ms each through one serial executor take 240 ms in total,
  // twice the 120 ms per-request timeout. Each job's timeout covers only its own
  // execution, so all three succeed (before WJ-03 the second and third failed).
  const children = [];
  const options = executorOptions(children, () => 80, { poolSize: 1 });
  const results = await Promise.all(
    ["A", "B", "C"].map((job) =>
      persistentPythonExecutor(
        "domain",
        "app.workers.domain_job",
        { job },
        options,
      ),
    ),
  );
  assert.deepEqual(results, [{ job: "A" }, { job: "B" }, { job: "C" }]);
  assert.equal(children.length, 1);
});

test("a hung job fails alone; jobs waiting behind it run on a fresh executor", async () => {
  const children = [];
  const options = executorOptions(
    children,
    (payload) => (payload.job === "hung" ? null : 20),
    { poolSize: 1 },
  );
  const outcomes = await Promise.all(
    ["hung", "B", "C"].map((job) =>
      persistentPythonExecutor(
        "domain",
        "app.workers.domain_job",
        { job },
        options,
      ).then(
        (result) => result.job,
        (error) => error.code,
      ),
    ),
  );
  assert.deepEqual(outcomes, ["python_response_timeout", "B", "C"]);
  assert.equal(children.length, 2, "the hung executor is replaced once");
});

test("the executor pool runs up to poolSize jobs in parallel", async () => {
  const children = [];
  const options = executorOptions(children, () => 60, { poolSize: 2 });
  const started = Date.now();
  await Promise.all(
    ["A", "B"].map((job) =>
      persistentPythonExecutor(
        "domain",
        "app.workers.domain_job",
        { job },
        options,
      ),
    ),
  );
  assert.equal(children.length, 2);
  assert.ok(Date.now() - started < 115, "two 60 ms jobs ran concurrently");
});

test("executor pool size follows queue concurrency with a domain cap", () => {
  const previous = { ...process.env };
  try {
    process.env.WORKER_CONCURRENCY = "3";
    delete process.env.PYTHON_EXECUTOR_POOL_SIZE;
    delete process.env.VISUAL_ANALYSIS_WORKER_CONCURRENCY;
    delete process.env.MATTE_WORKER_CONCURRENCY;
    const planning = ["planning", "stock_search", "audio", "maintenance"];
    assert.equal(executorPoolSize("domain", planning), 2);
    process.env.PYTHON_EXECUTOR_POOL_SIZE = "8";
    assert.equal(executorPoolSize("domain", planning), 8);
    process.env.PYTHON_EXECUTOR_POOL_SIZE = "1";
    assert.equal(executorPoolSize("domain", planning), 1);
    // GPU executors follow their queue's own concurrency (1 by default).
    assert.equal(
      executorPoolSize("visual-ml", ["visual_analysis", "transcription"]),
      1,
    );
    assert.equal(executorPoolSize("matte-gpu", ["matte_extraction"]), 1);
  } finally {
    process.env = previous;
  }
});

test("permanent failures are discarded instead of retried", async () => {
  let discarded = 0;
  const job = {
    id: "job-permanent",
    queueName: "planning",
    timestamp: Date.now(),
    attemptsMade: 0,
    data: { kind: "edit_plan" },
    updateProgress: async () => {},
    discard: () => {
      discarded += 1;
    },
  };
  const permanent = Object.assign(new Error("payload invalid"), {
    code: "queue_job_payload_invalid",
    retryable: false,
  });
  await assert.rejects(
    runJob(job, {
      executeJob: async () => {
        throw permanent;
      },
      reportProviderSlo: async () => {},
    }),
  );
  assert.equal(discarded, 1);

  const transient = Object.assign(new Error("provider unavailable"), {
    code: "provider_unavailable",
  });
  await assert.rejects(
    runJob(job, {
      executeJob: async () => {
        throw transient;
      },
      reportProviderSlo: async () => {},
    }),
  );
  assert.equal(discarded, 1, "transient failures keep their BullMQ retries");
});

test("skipped or non-retryable domain results are permanent failures", async () => {
  let discarded = 0;
  const job = {
    id: "job-skipped",
    queueName: "planning",
    timestamp: Date.now(),
    attemptsMade: 0,
    data: { kind: "mystery_kind" },
    updateProgress: async () => {},
    discard: () => {
      discarded += 1;
    },
  };
  await assert.rejects(
    runJob(job, {
      executeJob: async () => ({
        failed: true,
        skipped: true,
        message: "Unhandled worker job kind: mystery_kind",
      }),
      reportProviderSlo: async () => {},
    }),
    (error) => isPermanentJobFailure(error),
  );
  assert.equal(discarded, 1);
});

test("workflow RPCs retry transient failures but never business rejections", async () => {
  const delays = [];
  let calls = 0;
  const flaky = await withWorkflowRpcRetry(
    async () => {
      calls += 1;
      if (calls < 3) throw new TypeError("fetch failed");
      return "ok";
    },
    { sleepImpl: async (delay) => delays.push(delay) },
  );
  assert.equal(flaky, "ok");
  assert.deepEqual(delays, [1000, 2000]);

  let staleCalls = 0;
  const stale = Object.assign(new Error("WORKFLOW_STALE_CLAIM"), {
    code: "P0001",
  });
  await assert.rejects(
    withWorkflowRpcRetry(
      async () => {
        staleCalls += 1;
        throw stale;
      },
      { sleepImpl: async () => {} },
    ),
    (error) => error === stale,
  );
  assert.equal(staleCalls, 1);
  assert.equal(isTransientWorkflowRpcError(stale), false);
  assert.equal(
    isTransientWorkflowRpcError(
      Object.assign(new Error("x"), { code: "workflow_rpc_failed" }),
    ),
    true,
  );
});

test("maintenance schedulers are upserted in UTC and stale schedules removed", async () => {
  const schedulers = new Map([
    [
      "legacy-hash-key",
      { key: "legacy-hash-key", name: "daily-retention", pattern: "0 3 * * *" },
    ],
    [
      "gpu-autoscaling",
      {
        key: "gpu-autoscaling",
        name: "gpu-autoscaling",
        pattern: "*/2 * * * *",
      },
    ],
  ]);
  const calls = [];
  const queue = {
    async upsertJobScheduler(id, repeat, template) {
      calls.push(["upsert", id, repeat, template]);
      schedulers.set(id, { key: id, name: template.name, ...repeat });
    },
    async getJobSchedulers() {
      return [...schedulers.values()];
    },
    async removeJobScheduler(id) {
      calls.push(["removeJobScheduler", id]);
      if (id === "legacy-hash-key") return false; // legacy entries need removeRepeatableByKey
      return schedulers.delete(id);
    },
    async removeRepeatableByKey(key) {
      calls.push(["removeRepeatableByKey", key]);
      return schedulers.delete(key);
    },
  };
  const result = await registerMaintenanceSchedulers(queue, [
    {
      name: "daily-retention",
      data: { kind: "retention", queue_name: "maintenance" },
      pattern: "30 4 * * *",
    },
  ]);
  assert.deepEqual(result.registered, ["daily-retention"]);
  assert.deepEqual(result.removed.sort(), [
    "gpu-autoscaling",
    "legacy-hash-key",
  ]);
  assert.deepEqual([...schedulers.keys()], ["daily-retention"]);
  assert.deepEqual(schedulers.get("daily-retention").tz, "UTC");
  assert.deepEqual(calls[0], [
    "upsert",
    "daily-retention",
    { pattern: "30 4 * * *", tz: "UTC" },
    {
      name: "daily-retention",
      data: { kind: "retention", queue_name: "maintenance" },
    },
  ]);
});
