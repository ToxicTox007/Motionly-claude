import { Queue } from "bullmq";
import IORedis from "ioredis";

export const queueNames = [
  "upload_preprocess",
  "transcription",
  "visual_analysis",
  "planning",
  "stock_search",
  "audio",
  "render",
];

export function env(name, fallback = "") {
  return process.env[name] || fallback;
}

export function isProduction() {
  return env("APP_ENV") === "production";
}

export function apiBaseUrl() {
  return env("WORKER_API_BASE_URL", env("API_PUBLIC_BASE_URL")).replace(/\/$/, "");
}

export function workerToken() {
  return env("WORKER_API_TOKEN", env("ADMIN_API_TOKEN"));
}

export function hasApiConfig() {
  return Boolean(apiBaseUrl() && workerToken());
}

export function redisUrl() {
  return env("REDIS_URL");
}

export function hasQueueConfig() {
  return Boolean(redisUrl());
}

export function bullConnection() {
  if (!hasQueueConfig()) {
    throw new Error("REDIS_URL is required for BullMQ queue access");
  }
  return new IORedis(redisUrl(), {
    maxRetriesPerRequest: null,
    enableReadyCheck: false,
  });
}

export function queueFor(name, connection) {
  return new Queue(name, {
    connection,
    defaultJobOptions: defaultJobOptions(),
  });
}

export function defaultJobOptions() {
  const attempts = positiveInt("JOB_ATTEMPTS", 3);
  const backoffMs = positiveInt("JOB_BACKOFF_MS", 30_000);
  return {
    attempts,
    backoff: {
      type: "exponential",
      delay: backoffMs,
    },
    removeOnComplete: {
      age: positiveInt("JOB_COMPLETE_RETENTION_SECONDS", 86_400),
      count: positiveInt("JOB_COMPLETE_RETENTION_COUNT", 1_000),
    },
    removeOnFail: {
      age: positiveInt("JOB_FAILED_RETENTION_SECONDS", 604_800),
      count: positiveInt("JOB_FAILED_RETENTION_COUNT", 5_000),
    },
  };
}

export function workerConcurrency(queueName) {
  const specific = queueName === "render" ? env("RENDER_WORKER_CONCURRENCY") : "";
  return positiveIntValue(specific || env("WORKER_CONCURRENCY", "3"), 3);
}

export function stalledIntervalMs() {
  return positiveInt("JOB_STALLED_INTERVAL_MS", 30_000);
}

export function positiveInt(name, fallback) {
  return positiveIntValue(env(name), fallback);
}

export function positiveIntValue(value, fallback) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function bullPriority(priority) {
  const parsed = Number.parseInt(String(priority || "0"), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return undefined;
  return Math.max(1, 100 - Math.min(parsed, 99));
}

export function normalizeJobRecord(job, state) {
  const data = job?.data || {};
  const optsAttempts =
    Number(job?.opts?.attempts || data.max_attempts || env("JOB_ATTEMPTS", "3")) || 3;
  return {
    id: String(job?.id || data.id || ""),
    kind: String(data.kind || job?.name || "job"),
    queue_name: String(data.queue_name || job?.queueName || ""),
    priority: Number(data.priority || 0),
    attempts: Number(job?.attemptsMade || data.attempts || 0),
    max_attempts: optsAttempts,
    status: mapBullState(state, job?.attemptsMade || 0, optsAttempts, data.status),
    enqueued_at: data.enqueued_at || new Date(job?.timestamp || Date.now()).toISOString(),
    workspace_id: data.workspace_id || null,
    project_id: data.project_id || null,
    resource_id: data.resource_id || null,
    render_engine: data.render_engine || "ffmpeg_basic",
  };
}

export function mapBullState(state, attemptsMade, maxAttempts, dataStatus) {
  if (dataStatus === "canceled") return "canceled";
  if (state === "completed") return "succeeded";
  if (state === "active") return "running";
  if (state === "failed") {
    return attemptsMade >= maxAttempts ? "dead_letter" : "failed";
  }
  return "queued";
}

export async function writeBuildStamp() {
  const fs = await import("node:fs/promises");
  await fs.mkdir(new URL("../dist/", import.meta.url), { recursive: true });
  await fs.writeFile(
    new URL("../dist/worker-check.json", import.meta.url),
    JSON.stringify({ checked_at: new Date().toISOString() }),
  );
}
