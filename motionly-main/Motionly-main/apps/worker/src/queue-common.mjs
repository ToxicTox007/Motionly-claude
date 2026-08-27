import { Queue } from "bullmq";
import IORedis from "ioredis";
import { readFileSync } from "node:fs";

export const allQueueNames = [
  "upload_preprocess",
  "transcription",
  "visual_analysis",
  "matte_extraction",
  "planning",
  "stock_search",
  "stock_asset_enrichment",
  "audio",
  "render",
  "render_gpu",
  "render_remotion",
  "publish",
  "intelligence",
  "audio_demucs",
  "provider_calls",
  "maintenance",
];
const defaultQueueNames = ["render_remotion"];

export const workflowJobFieldNames = Object.freeze([
  "workflow_run_id",
  "workflow_step_id",
  "step_key",
  "step_kind",
  "attempt_key",
  "workflow_attempt_number",
  "workflow_max_attempts",
  "input_hash",
  "next_run_state",
]);

const workflowIntegerFields = new Set([
  "workflow_attempt_number",
  "workflow_max_attempts",
]);

const workflowFieldMaxLengths = Object.freeze({
  workflow_run_id: 128,
  workflow_step_id: 128,
  step_key: 200,
  step_kind: 80,
  attempt_key: 200,
  input_hash: 128,
  next_run_state: 64,
});

const forbiddenPayloadKeys = new Set([
  "api_key",
  "authorization",
  "credentials",
  "password",
  "secret",
  "service_role_key",
  "signed_url",
  "signed_urls",
  "source_bytes",
  "source_data",
  "media_bytes",
  "media_data",
  "model_blob",
  "model_bytes",
  "model_data",
  "token",
  "access_token",
  "auth_token",
  "refresh_token",
  "worker_token",
]);

function isForbiddenPayloadKey(key) {
  const normalized = String(key).toLowerCase().replaceAll("-", "_");
  return forbiddenPayloadKeys.has(normalized)
    || normalized.endsWith("_credential")
    || normalized.endsWith("_credentials")
    || normalized.endsWith("_secret")
    || normalized.endsWith("_blob");
}

function isSignedOrInlineMedia(value) {
  if (typeof value !== "string") return false;
  if (/^data:(?:audio|video|image|application\/octet-stream)[;,]/i.test(value)) return true;
  if (!/^https?:\/\//i.test(value)) return false;
  try {
    const url = new URL(value);
    for (const key of url.searchParams.keys()) {
      if (/^(?:token|signature|sig|x-amz-|x-goog-|expires$)/i.test(key)) return true;
    }
  } catch {
    return true;
  }
  return false;
}

export function assertQueuePayloadSafe(value, path = "payload", seen = new Set()) {
  if (value === null || value === undefined) return;
  if (typeof value === "string") {
    if (isSignedOrInlineMedia(value)) {
      throw new Error(`${path} must not contain signed URLs or inline media`);
    }
    return;
  }
  if (["boolean", "number"].includes(typeof value)) return;
  if (["bigint", "function", "symbol"].includes(typeof value)) {
    throw new Error(`${path} must contain only JSON-safe values`);
  }
  if (ArrayBuffer.isView(value) || value instanceof ArrayBuffer) {
    throw new Error(`${path} must not contain binary data`);
  }
  if (typeof value !== "object") return;
  if (seen.has(value)) throw new Error(`${path} must not contain circular data`);
  seen.add(value);
  try {
    for (const key of Object.keys(value)) {
      if (isForbiddenPayloadKey(key)) {
        throw new Error(`${path}.${key} is forbidden in queue payloads`);
      }
      assertQueuePayloadSafe(value[key], `${path}.${key}`, seen);
    }
  } finally {
    seen.delete(value);
  }
}

export function workflowJobFields(payload = {}) {
  assertQueuePayloadSafe(payload);
  const fields = {};
  for (const name of workflowJobFieldNames) {
    const value = payload[name];
    if (value === undefined || value === null) continue;
    if (workflowIntegerFields.has(name)) {
      if (!Number.isInteger(value) || value < 1 || value > 20) {
        throw new Error(`${name} must be an integer between 1 and 20`);
      }
      fields[name] = value;
      continue;
    }
    if (typeof value !== "string" || value.length < 1 || value.length > workflowFieldMaxLengths[name]) {
      throw new Error(`${name} must be a non-empty identifier no longer than ${workflowFieldMaxLengths[name]} characters`);
    }
    if (/[\u0000-\u001f\u007f]/.test(value)) {
      throw new Error(`${name} must not contain control characters`);
    }
    fields[name] = value;
  }
  return fields;
}

export const queueNames = env("WORKER_QUEUES")
  ? env("WORKER_QUEUES").split(",").map((name) => name.trim()).filter((name) => allQueueNames.includes(name))
  : defaultQueueNames;

export function env(name, fallback = "") {
  if (process.env[name]) return process.env[name];
  const secretFile = process.env[`${name}_FILE`];
  if (secretFile) return readFileSync(secretFile, "utf8").trim();
  return fallback;
}

export function isProduction() {
  return env("APP_ENV") === "production";
}

export function apiBaseUrl() {
  return env("WORKER_API_BASE_URL", env("API_PUBLIC_BASE_URL")).replace(/\/$/, "");
}

export function workerToken() {
  return env("WORKER_API_TOKEN");
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
  const specific = queueName === "render_gpu"
    ? env("GPU_RENDER_WORKER_CONCURRENCY")
    : queueName === "render"
    ? env("RENDER_WORKER_CONCURRENCY")
    : queueName === "visual_analysis"
    ? env("VISUAL_ANALYSIS_WORKER_CONCURRENCY", "1")
    : queueName === "matte_extraction"
    ? env("MATTE_WORKER_CONCURRENCY", "1")
    : "";
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

export function bullTransportAttempts(payload = {}, fallback = defaultJobOptions().attempts) {
  return payload.workflow_run_id ? 1 : positiveIntValue(payload.max_attempts, fallback);
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
    trace_id: data.trace_id || null,
    error_code: data.error_code || null,
    error_message: data.error_message || null,
    workflow_run_id: data.workflow_run_id || null,
    workflow_step_id: data.workflow_step_id || null,
    step_key: data.step_key || null,
    step_kind: data.step_kind || null,
    attempt_key: data.attempt_key || null,
    workflow_attempt_number: data.workflow_attempt_number || null,
    workflow_max_attempts: data.workflow_max_attempts || null,
    input_hash: data.input_hash || null,
    next_run_state: data.next_run_state || null,
  };
}

export function mapBullState(state, attemptsMade, maxAttempts, dataStatus) {
  if (dataStatus === "canceled") return "canceled";
  if (dataStatus === "dead_letter") return "dead_letter";
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
