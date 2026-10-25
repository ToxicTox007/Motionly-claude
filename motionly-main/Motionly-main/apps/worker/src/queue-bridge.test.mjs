import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_ENQUEUE_DELAY_SECONDS,
  bullDelayMs,
  canceledJobsKey,
} from "./queue-common.mjs";
import { getJobsPaged, recordCanceledJob, snapshots } from "./queue-bridge.mjs";

test("bullDelayMs converts positive seconds to bounded milliseconds", () => {
  assert.equal(bullDelayMs(5), 5000);
  assert.equal(bullDelayMs("2.5"), 2500);
  assert.equal(bullDelayMs(MAX_ENQUEUE_DELAY_SECONDS + 100), MAX_ENQUEUE_DELAY_SECONDS * 1000);
});

test("bullDelayMs treats missing/zero/invalid as immediate (undefined)", () => {
  for (const value of [undefined, null, "", 0, -1, "abc", Number.NaN]) {
    assert.equal(bullDelayMs(value), undefined);
  }
});

test("getJobsPaged keeps reading past the first 500 jobs", async () => {
  const calls = [];
  const first = Array.from({ length: 500 }, (_, index) => ({
    id: `job-${index}`,
  }));
  const second = Array.from({ length: 250 }, (_, index) => ({
    id: `job-${index + 500}`,
  }));
  const queue = {
    async getJobs(states, start, end, asc) {
      calls.push({ states, start, end, asc });
      if (start === 0) return first;
      if (start === 500) return second;
      return [];
    },
  };

  const jobs = await getJobsPaged(queue, ["active"]);

  assert.equal(jobs.length, 750);
  assert.deepEqual(calls, [
    { states: ["active"], start: 0, end: 499, asc: true },
    { states: ["active"], start: 500, end: 999, asc: true },
  ]);
});

test("recordCanceledJob uses an expiring atomic marker keyed by queue and job", async () => {
  const calls = [];
  const connection = {
    async eval(...args) {
      calls.push(args);
      return 1;
    },
  };

  const recorded = await recordCanceledJob(
    connection,
    "maintenance",
    "queue-job-123",
    { now: 1_000, retentionSeconds: 60 },
  );

  assert.equal(recorded, true);
  assert.equal(calls.length, 1);
  assert.match(String(calls[0][0]), /ZADD/);
  assert.equal(calls[0][1], 1);
  assert.equal(calls[0][2], canceledJobsKey("maintenance"));
  assert.deepEqual(calls[0].slice(3), [1000, 61_000, 60, "queue-job-123"]);
});

function snapshotConnection(canceled = 0) {
  return {
    async zremrangebyscore() {},
    async zcard() {
      return canceled;
    },
    async expire() {},
  };
}

function snapshotQueue({ counts, failedJobs = [], oldestJobs = [] }) {
  return {
    async getJobCounts() {
      return counts;
    },
    async getJobs(states, start, end) {
      if (states.length === 1 && states[0] === "failed") {
        return failedJobs.slice(start, end + 1);
      }
      return oldestJobs.slice(start, end + 1);
    },
  };
}

test("snapshots exclude canceled failed jobs and report retained cancellation markers", async () => {
  const queue = snapshotQueue({
    counts: { failed: 3 },
    failedJobs: [
      { attemptsMade: 1, opts: { attempts: 3 }, data: {} },
      { attemptsMade: 3, opts: { attempts: 3 }, data: {} },
      { attemptsMade: 3, opts: { attempts: 3 }, data: { status: "canceled" } },
    ],
  });

  const [snapshot] = await snapshots(snapshotConnection(2), {
    queueNames: ["maintenance"],
    queueFactory: () => queue,
    now: 10_000,
    failedScanLimit: 3,
  });

  assert.deepEqual(snapshot, {
    queue_name: "maintenance",
    queued: 0,
    running: 0,
    succeeded: 0,
    failed: 1,
    dead_letter: 1,
    canceled: 2,
    failed_scan_truncated: false,
    oldest_queued_seconds: null,
  });
});

test("snapshots bound failed-job inspection and identify a partial classification", async () => {
  const queue = snapshotQueue({
    counts: { failed: 12 },
    failedJobs: [
      { attemptsMade: 1, opts: { attempts: 3 }, data: {} },
      { attemptsMade: 3, opts: { attempts: 3 }, data: {} },
      ...Array.from({ length: 10 }, () => ({
        attemptsMade: 3,
        opts: { attempts: 3 },
        data: {},
      })),
    ],
  });

  const [snapshot] = await snapshots(snapshotConnection(), {
    queueNames: ["maintenance"],
    queueFactory: () => queue,
    now: 10_000,
    failedScanLimit: 2,
  });

  assert.equal(snapshot.failed, 11);
  assert.equal(snapshot.dead_letter, 1);
  assert.equal(snapshot.failed_scan_truncated, true);
});
