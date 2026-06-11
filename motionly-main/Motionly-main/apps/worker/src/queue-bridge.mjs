#!/usr/bin/env node

import { Job } from "bullmq";
import {
  bullConnection,
  bullPriority,
  defaultJobOptions,
  normalizeJobRecord,
  queueFor,
  queueNames,
} from "./queue-common.mjs";

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

async function main() {
  const command = process.argv[2];
  if (!["enqueue", "jobs", "retry", "cancel"].includes(command)) {
    throw new Error("Usage: queue-bridge.mjs [enqueue|jobs|retry|cancel]");
  }
  const payload = await readStdinJson();
  const connection = bullConnection();
  try {
    const result =
      command === "enqueue"
        ? await enqueue(connection, payload)
        : command === "jobs"
          ? await jobs(connection)
          : command === "retry"
            ? await retry(connection, payload)
            : await cancel(connection, payload);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } finally {
    await connection.quit();
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
});
