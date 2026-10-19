#!/usr/bin/env node

import { Job } from "bullmq";
import { pathToFileURL } from "node:url";
import {
  bullConnection,
  allQueueNames,
  bullPriority,
  bullTransportAttempts,
  canceledJobsKey,
  defaultJobOptions,
  env,
  normalizeJobRecord,
  positiveInt,
  queueFor,
  workflowJobFields,
} from "./queue-common.mjs";

const queueNames = allQueueNames;
const JOB_SCAN_PAGE_SIZE = 500;

async function readStdinJson() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  return raw ? JSON.parse(raw) : {};
}

async function findJob(connection, jobId, preferredQueueName = null) {
  const names = preferredQueueName ? [preferredQueueName] : queueNames;
  for (const queueName of names) {
    const job = await Job.fromId(queueFor(queueName, connection), jobId);
    if (job) return job;
  }
  return null;
}

async function enqueue(connection, payload) {
  const queue = queueFor(payload.queue_name, connection);
  const now = new Date().toISOString();
  const workflowFields = workflowJobFields(payload);
  const transportAttempts = bullTransportAttempts(payload);
  const data = {
    id: payload.id,
    kind: payload.kind,
    queue_name: payload.queue_name,
    priority: payload.priority || 0,
    max_attempts: transportAttempts,
    enqueued_at: now,
    workspace_id: payload.workspace_id || null,
    project_id: payload.project_id || null,
    resource_id: payload.resource_id || null,
    render_engine: payload.render_engine || "ffmpeg_basic",
    trace_id: payload.trace_id || null,
    error_code: payload.error_code || null,
    error_message: payload.error_message || null,
    ...workflowFields,
  };
  const job = await queue.add(payload.kind, data, {
    ...defaultJobOptions(),
    jobId: payload.id,
    priority: bullPriority(payload.priority),
    attempts: transportAttempts,
  });
  const state = await job.getState();
  return normalizeJobRecord(job, state);
}

async function jobRecord(connection, payload) {
  const job = await findJob(connection, payload.id, payload.queue_name);
  if (!job) return null;
  return normalizeJobRecord(job, await job.getState());
}

async function jobs(connection) {
  const records = [];
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const queueJobs = await getJobsPaged(queue, [
      "waiting",
      "prioritized",
      "delayed",
      "active",
      "completed",
      "failed",
      "paused",
    ]);
    for (const job of queueJobs) {
      const state = await job.getState();
      records.push(normalizeJobRecord(job, state));
    }
  }
  return records.sort((left, right) => left.id.localeCompare(right.id));
}

export async function getJobsPaged(
  queue,
  states,
  { pageSize = JOB_SCAN_PAGE_SIZE } = {},
) {
  const records = [];
  let start = 0;
  while (true) {
    const page = await queue.getJobs(states, start, start + pageSize - 1, true);
    records.push(...page);
    if (page.length < pageSize) break;
    start += pageSize;
  }
  return records;
}

async function getJobsLimited(queue, states, limit) {
  if (limit <= 0) return [];
  return queue.getJobs(states, 0, limit - 1, true);
}

const CANCELED_JOB_MARKER_SCRIPT = `
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires_at = tonumber(ARGV[2])
local retention_seconds = tonumber(ARGV[3])
local job_id = ARGV[4]
redis.call("ZREMRANGEBYSCORE", key, "-inf", now)
if redis.call("ZSCORE", key, job_id) then
  return 0
end
redis.call("ZADD", key, expires_at, job_id)
redis.call("EXPIRE", key, retention_seconds)
return 1
`;

function canceledJobRetentionSeconds() {
  return positiveInt(
    "QUEUE_CANCELED_RETENTION_SECONDS",
    positiveInt("JOB_FAILED_RETENTION_SECONDS", 604_800),
  );
}

export async function recordCanceledJob(
  connection,
  queueName,
  jobId,
  { now = Date.now(), retentionSeconds = canceledJobRetentionSeconds() } = {},
) {
  const key = canceledJobsKey(queueName);
  const normalizedJobId = String(jobId || "").trim();
  if (!normalizedJobId)
    throw new Error("Canceled queue job identity is required");
  const expiresAt = now + retentionSeconds * 1000;
  const result = await connection.eval(
    CANCELED_JOB_MARKER_SCRIPT,
    1,
    key,
    now,
    expiresAt,
    retentionSeconds,
    normalizedJobId,
  );
  return Number(result) === 1;
}

async function retainedCanceledJobCount(connection, queueName, now) {
  const key = canceledJobsKey(queueName);
  await connection.zremrangebyscore(key, "-inf", now);
  return Number((await connection.zcard(key)) || 0);
}

export async function snapshots(
  connection,
  {
    queueNames: snapshotQueueNames = queueNames,
    queueFactory = queueFor,
    now = Date.now(),
    failedScanLimit = positiveInt("QUEUE_SNAPSHOT_FAILED_SCAN_LIMIT", 500),
  } = {},
) {
  const records = [];
  for (const queueName of snapshotQueueNames) {
    const queue = queueFactory(queueName, connection);
    const counts = await queue.getJobCounts(
      "waiting",
      "prioritized",
      "delayed",
      "active",
      "completed",
      "failed",
      "paused",
    );
    const waiting = Number(counts.waiting || 0);
    const prioritized = Number(counts.prioritized || 0);
    const delayed = Number(counts.delayed || 0);
    const paused = Number(counts.paused || 0);
    const active = Number(counts.active || 0);
    const completed = Number(counts.completed || 0);
    const failedCount = Number(counts.failed || 0);
    const failedJobs = await getJobsLimited(
      queue,
      ["failed"],
      Math.min(failedCount, Math.max(1, failedScanLimit)),
    );
    let retryableFailed = 0;
    let deadLetter = 0;
    for (const job of failedJobs) {
      if (job.data?.status === "canceled") continue;
      const attemptsMade = Number(job.attemptsMade || 0);
      const maxAttempts = Number(
        job.opts?.attempts ||
          job.data?.max_attempts ||
          env("JOB_ATTEMPTS", "3"),
      );
      if (job.data?.status === "dead_letter" || attemptsMade >= maxAttempts) {
        deadLetter += 1;
      } else {
        retryableFailed += 1;
      }
    }
    const failedScanTruncated = failedCount > failedJobs.length;
    if (failedScanTruncated) {
      // Uninspected retained failures remain visible as retryable until a
      // bounded scan can classify them. The flag makes this approximation
      // explicit to control-plane callers rather than doing an unbounded scan.
      retryableFailed += failedCount - failedJobs.length;
    }
    const canceled = await retainedCanceledJobCount(connection, queueName, now);
    let oldestQueuedSeconds = null;
    const oldest = await queue.getJobs(
      ["waiting", "prioritized", "delayed", "paused"],
      0,
      0,
      true,
    );
    if (oldest.length > 0) {
      const timestamp = Number(
        oldest[0].data?.enqueued_at
          ? Date.parse(oldest[0].data.enqueued_at)
          : oldest[0].timestamp,
      );
      if (Number.isFinite(timestamp)) {
        oldestQueuedSeconds = Math.max(0, Math.round((now - timestamp) / 1000));
      }
    }
    records.push({
      queue_name: queueName,
      queued: waiting + prioritized + delayed + paused,
      running: active,
      succeeded: completed,
      failed: retryableFailed,
      dead_letter: deadLetter,
      canceled,
      failed_scan_truncated: failedScanTruncated,
      oldest_queued_seconds: oldestQueuedSeconds,
    });
  }
  return records;
}

async function retry(connection, payload) {
  const job = await findJob(connection, payload.id, payload.queue_name);
  if (!job) throw new Error(`Queue job was not found: ${payload.id}`);
  const state = await job.getState();
  if (job.data?.status === "canceled") {
    throw new Error(`Canceled queue job cannot be retried: ${payload.id}`);
  }
  if (state === "active" || state === "completed") {
    throw new Error(
      `Queue job cannot be retried while ${state}: ${payload.id}`,
    );
  }
  if (
    !["failed", "delayed", "waiting", "prioritized", "paused"].includes(state)
  ) {
    throw new Error(
      `Queue job is not retryable in state ${state}: ${payload.id}`,
    );
  }
  await job.updateData({
    ...job.data,
    status: "queued",
    enqueued_at: new Date().toISOString(),
  });
  if (state === "failed") {
    await job.retry();
  } else if (state === "delayed") {
    await job.promote();
  }
  const refreshed = await findJob(connection, payload.id, payload.queue_name);
  return normalizeJobRecord(
    refreshed,
    refreshed ? await refreshed.getState() : "waiting",
  );
}

async function cancel(connection, payload) {
  const job = await findJob(connection, payload.id, payload.queue_name);
  if (!job) throw new Error(`Queue job was not found: ${payload.id}`);
  const state = await job.getState();
  if (state === "completed" || state === "failed") {
    throw new Error(
      `Queue job can no longer be canceled in state ${state}: ${payload.id}`,
    );
  }
  const wasCanceled = job.data?.status === "canceled";
  await job.updateData({ ...job.data, status: "canceled" });
  if (!wasCanceled) {
    await recordCanceledJob(
      connection,
      job.queueName || payload.queue_name,
      job.id,
    );
  }
  if (state !== "active") {
    await job.remove();
  }
  return normalizeJobRecord(job, state);
}

const STALLED_CHECK_INTERVAL_MS = 25;

async function runBullMqStalledCheck(connection, queueName) {
  const { Worker } = await import("bullmq");
  const checker = new Worker(queueName, null, {
    connection,
    autorun: false,
    stalledInterval: STALLED_CHECK_INTERVAL_MS,
  });
  checker.on("error", () => undefined);
  try {
    await checker.startStalledCheckTimer();
    // BullMQ marks candidates on one pass and moves lock-less jobs on the next.
    await new Promise((resolve) =>
      setTimeout(resolve, STALLED_CHECK_INTERVAL_MS * 3),
    );
  } finally {
    await checker.close(true);
  }
}

function stuckCandidates(active, cutoff) {
  return active.filter((job) => {
    const startedAt = Number(job.processedOn || job.timestamp || Date.now());
    return startedAt <= cutoff;
  });
}

async function markStuckDeadLetter(connection, payload) {
  const cutoff =
    Date.now() - Math.max(1, Number(payload.timeout_seconds || 1)) * 1000;
  const transitioned = [];
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const candidates = stuckCandidates(
      await getJobsPaged(queue, ["active"]),
      cutoff,
    );
    if (candidates.length === 0) continue;
    await runBullMqStalledCheck(connection, queueName);
    for (const candidate of candidates) {
      const job = await findJob(connection, candidate.id, queueName);
      if (!job || (await job.getState()) !== "failed") continue;
      await job.updateData({
        ...job.data,
        status: "dead_letter",
        dead_lettered_at: new Date().toISOString(),
        dead_letter_reason: "stuck_timeout",
      });
      transitioned.push(normalizeJobRecord(job, "failed"));
    }
  }
  return transitioned;
}

async function recoverStuck(connection, payload) {
  const cutoff =
    Date.now() - Math.max(1, Number(payload.timeout_seconds || 1)) * 1000;
  const backoffSeconds = Math.max(1, Number(payload.backoff_seconds || 30));
  const transitioned = [];
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const candidates = stuckCandidates(
      await getJobsPaged(queue, ["active"]),
      cutoff,
    );
    if (candidates.length === 0) continue;
    await runBullMqStalledCheck(connection, queueName);
    for (const candidate of candidates) {
      const job = await findJob(connection, candidate.id, queueName);
      if (!job) continue;
      const state = await job.getState();
      if (state === "active") continue;
      const exhausted = state === "failed";
      await job.updateData({
        ...job.data,
        status: exhausted ? "dead_letter" : "queued",
        recovered_at: new Date().toISOString(),
        retry_backoff_seconds: backoffSeconds,
        error_code: "stuck_timeout",
        error_message: exhausted
          ? "Worker job exceeded BullMQ's stalled retry limit."
          : "BullMQ recovered a lock-less stalled job.",
      });
      transitioned.push(normalizeJobRecord(job, state));
    }
  }
  return transitioned;
}

async function execute(command, connection, payload) {
  if (command === "enqueue") return enqueue(connection, payload);
  if (command === "job") return jobRecord(connection, payload);
  if (command === "jobs") return jobs(connection);
  if (command === "snapshots") return snapshots(connection);
  if (command === "retry") return retry(connection, payload);
  if (command === "cancel") return cancel(connection, payload);
  if (command === "recover-stuck") return recoverStuck(connection, payload);
  if (command === "mark-stuck-dead-letter")
    return markStuckDeadLetter(connection, payload);
  throw new Error(`Unsupported queue bridge command: ${command}`);
}

async function serve(connection) {
  process.stdin.setEncoding("utf8");
  let buffer = "";
  for await (const chunk of process.stdin) {
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      let request = {};
      try {
        request = JSON.parse(line);
        const result = await execute(
          request.command,
          connection,
          request.payload || {},
        );
        process.stdout.write(`${JSON.stringify({ id: request.id, result })}\n`);
      } catch (error) {
        process.stdout.write(
          `${JSON.stringify({
            id: request.id || null,
            error: error instanceof Error ? error.message : String(error),
          })}\n`,
        );
      }
    }
  }
}

async function main() {
  const command = process.argv[2];
  if (
    ![
      "enqueue",
      "job",
      "jobs",
      "snapshots",
      "retry",
      "cancel",
      "mark-stuck-dead-letter",
      "recover-stuck",
      "serve",
    ].includes(command)
  ) {
    throw new Error(
      "Usage: queue-bridge.mjs [enqueue|job|jobs|snapshots|retry|cancel|mark-stuck-dead-letter|recover-stuck|serve]",
    );
  }
  const connection = bullConnection();
  try {
    if (command === "serve") {
      await serve(connection);
    } else {
      const payload = await readStdinJson();
      const result = await execute(command, connection, payload);
      process.stdout.write(`${JSON.stringify(result)}\n`);
    }
  } finally {
    await connection.quit();
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch((error) => {
    process.stderr.write(
      `${error instanceof Error ? error.message : String(error)}\n`,
    );
    process.exit(1);
  });
}
