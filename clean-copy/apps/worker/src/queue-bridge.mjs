#!/usr/bin/env node

import { Job } from "bullmq";
import {
  bullConnection,
  allQueueNames,
  bullPriority,
  defaultJobOptions,
  normalizeJobRecord,
  queueFor,
} from "./queue-common.mjs";

const queueNames = allQueueNames;

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

async function jobs(connection) {
  const records = [];
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const queueJobs = await queue.getJobs(
      ["waiting", "prioritized", "delayed", "active", "completed", "failed", "paused"],
      0,
      499,
      true,
    );
    for (const job of queueJobs) {
      const state = await job.getState();
      records.push(normalizeJobRecord(job, state));
    }
  }
  return records.sort((left, right) => left.id.localeCompare(right.id));
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

async function markStuckDeadLetter(connection, payload) {
  const cutoff = Date.now() - Math.max(1, Number(payload.timeout_seconds || 1)) * 1000;
  const transitioned = [];
  for (const queueName of queueNames) {
    const queue = queueFor(queueName, connection);
    const active = await queue.getJobs(["active"], 0, 499, true);
    for (const job of active) {
      const startedAt = Number(job.processedOn || job.timestamp || Date.now());
      if (startedAt > cutoff) continue;
      await job.updateData({
        ...job.data,
        status: "dead_letter",
        dead_lettered_at: new Date().toISOString(),
        dead_letter_reason: "stuck_timeout",
      });
      transitioned.push(normalizeJobRecord(job, "active"));
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
    const active = await queue.getJobs(["active"], 0, 499, true);
    for (const job of active) {
      const startedAt = Number(job.processedOn || job.timestamp || Date.now());
      if (startedAt > cutoff) continue;
      const attemptsMade = Number(job.attemptsMade || 0);
      const maxAttempts = Number(job.opts?.attempts || job.data.max_attempts || 3);
      const exhausted = attemptsMade + 1 >= maxAttempts;
      const status = exhausted ? "dead_letter" : "queued";
      await job.updateData({
        ...job.data,
        status,
        enqueued_at: new Date(Date.now() + backoffSeconds * 1000).toISOString(),
        error_code: "stuck_timeout",
        error_message: exhausted
          ? "Worker job exceeded the stuck timeout and hit the retry limit."
          : "Worker job exceeded the stuck timeout and was requeued.",
      });
      transitioned.push(normalizeJobRecord(job, status === "dead_letter" ? "failed" : "waiting"));
    }
  }
  return transitioned;
}

async function execute(command, connection, payload) {
  return command === "enqueue"
    ? enqueue(connection, payload)
    : command === "jobs"
      ? jobs(connection)
      : command === "retry"
        ? retry(connection, payload)
      : command === "cancel"
        ? cancel(connection, payload)
        : command === "recover-stuck"
          ? recoverStuck(connection, payload)
        : markStuckDeadLetter(connection, payload);
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
  if (!["enqueue", "jobs", "retry", "cancel", "mark-stuck-dead-letter", "recover-stuck", "serve"].includes(command)) {
    throw new Error("Usage: queue-bridge.mjs [enqueue|jobs|retry|cancel|mark-stuck-dead-letter|recover-stuck|serve]");
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

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
});
