#!/usr/bin/env node

import { Job } from "bullmq";
import { pathToFileURL } from "node:url";
import {
  bullConnection,
  allQueueNames,
  bullPriority,
  defaultJobOptions,
  normalizeJobRecord,
  queueFor,
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
  const data = {
    id: payload.id,
    kind: payload.kind,
    queue_name: payload.queue_name,
    priority: payload.priority || 0,
    max_attempts: payload.max_attempts || defaultJobOptions().attempts,
    enqueued_at: now,
    workspace_id: payload.workspace_id || null,
    project_id: payload.project_id || null,
    resource_id: payload.resource_id || null,
    render_engine: payload.render_engine || "ffmpeg_basic",
    trace_id: payload.trace_id || null,
    error_code: payload.error_code || null,
    error_message: payload.error_message || null,
  };
  const job = await queue.add(payload.kind, data, {
    ...defaultJobOptions(),
    jobId: payload.id,
    priority: bullPriority(payload.priority),
    attempts: data.max_attempts,
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

export async function getJobsPaged(queue, states, { pageSize = JOB_SCAN_PAGE_SIZE } = {}) {
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

async function snapshots(connection) {
  const records = [];
  const now = Date.now();
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
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
    const failed = Number(counts.failed || 0);
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
      failed: 0,
      dead_letter: failed,
      // Canceled non-active jobs are removed, not represented by BullMQ's paused state.
      canceled: 0,
      oldest_queued_seconds: oldestQueuedSeconds,
    });
  }
  return records;
}

async function retry(connection, payload) {
  const job = await findJob(connection, payload.id, payload.queue_name);
  if (!job) throw new Error(`Queue job was not found: ${payload.id}`);
  await job.updateData({ ...job.data, status: "queued", enqueued_at: new Date().toISOString() });
  const state = await job.getState();
  if (state === "failed") {
    await job.retry();
  } else if (state === "delayed") {
    await job.promote();
  }
  const refreshed = await findJob(connection, payload.id, payload.queue_name);
  return normalizeJobRecord(refreshed, refreshed ? await refreshed.getState() : "waiting");
}

async function cancel(connection, payload) {
  const job = await findJob(connection, payload.id, payload.queue_name);
  if (!job) throw new Error(`Queue job was not found: ${payload.id}`);
  await job.updateData({ ...job.data, status: "canceled" });
  const state = await job.getState();
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
    await new Promise((resolve) => setTimeout(resolve, STALLED_CHECK_INTERVAL_MS * 3));
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
  const cutoff = Date.now() - Math.max(1, Number(payload.timeout_seconds || 1)) * 1000;
  const transitioned = [];
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const candidates = stuckCandidates(await getJobsPaged(queue, ["active"]), cutoff);
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
  const cutoff = Date.now() - Math.max(1, Number(payload.timeout_seconds || 1)) * 1000;
  const backoffSeconds = Math.max(1, Number(payload.backoff_seconds || 30));
  const transitioned = [];
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const candidates = stuckCandidates(await getJobsPaged(queue, ["active"]), cutoff);
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
  if (command === "mark-stuck-dead-letter") return markStuckDeadLetter(connection, payload);
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
        const result = await execute(request.command, connection, request.payload || {});
        process.stdout.write(`${JSON.stringify({ id: request.id, result })}\n`);
      } catch (error) {
        process.stdout.write(`${JSON.stringify({
          id: request.id || null,
          error: error instanceof Error ? error.message : String(error),
        })}\n`);
      }
    }
  }
}

async function main() {
  const command = process.argv[2];
  if (!["enqueue", "job", "jobs", "snapshots", "retry", "cancel", "mark-stuck-dead-letter", "recover-stuck", "serve"].includes(command)) {
    throw new Error("Usage: queue-bridge.mjs [enqueue|job|jobs|snapshots|retry|cancel|mark-stuck-dead-letter|recover-stuck|serve]");
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

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
}
