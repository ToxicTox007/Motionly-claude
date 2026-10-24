import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, statfsSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Worker } from "bullmq";
import {
  bullConnection,
  apiBaseUrl,
  assertQueuePayloadSafe,
  env,
  hasApiConfig,
  hasQueueConfig,
  isProduction,
  queueNames,
  queueFor,
  stalledIntervalMs,
  workerConcurrency,
  workerToken,
  workflowJobFieldNames,
  workflowJobFields,
  writeBuildStamp,
} from "./queue-common.mjs";
import {
  maintenanceRepeatJobs,
  assertNoFixturesInProduction,
  mlExecutionPlan,
  mlJobAllowed,
  workerHeartbeatPayload,
} from "./ml-execution.mjs";
import {
  createRedactingLineWriter,
  installLogRedaction,
  redactLogText,
} from "./log-redaction.mjs";
import { markAlive } from "./liveness.mjs";

function remotionEnabled() {
  return env("REMOTION_ENABLED") === "true";
}

function rendererPath() {
  return resolve(
    env("RENDERER_BASE_PATH", join(import.meta.dirname, "../../renderer")),
  );
}

const pythonExecutors = new Map();
const workerStartedAt = Date.now();
let firstJobStarted = false;

function diskFreeBytes(path = env("MEDIA_WORK_DIR", "/tmp")) {
  try {
    const stats = statfsSync(path);
    return Number(stats.bavail) * Number(stats.bsize);
  } catch {
    return null;
  }
}

export function pythonExecutorConfig(overrides = {}) {
  return {
    requestTimeoutMs: Number(
      overrides.requestTimeoutMs ||
        env("PYTHON_EXECUTOR_REQUEST_TIMEOUT_MS", "900000"),
    ),
    startupTimeoutMs: Number(
      overrides.startupTimeoutMs ||
        env("PYTHON_EXECUTOR_STARTUP_TIMEOUT_MS", "900000"),
    ),
    heartbeatIntervalMs: Number(
      overrides.heartbeatIntervalMs ||
        env("PYTHON_EXECUTOR_HEARTBEAT_INTERVAL_MS", "15000"),
    ),
    heartbeatTimeoutMs: Number(
      overrides.heartbeatTimeoutMs ||
        env("PYTHON_EXECUTOR_HEARTBEAT_TIMEOUT_MS", "120000"),
    ),
    restartBackoffMs: Number(
      overrides.restartBackoffMs ||
        env("PYTHON_EXECUTOR_RESTART_BACKOFF_MS", "1000"),
    ),
    restartBackoffCapMs: Number(
      overrides.restartBackoffCapMs ||
        env("PYTHON_EXECUTOR_RESTART_BACKOFF_CAP_MS", "30000"),
    ),
    restartWindowMs: Number(
      overrides.restartWindowMs ||
        env("PYTHON_EXECUTOR_RESTART_WINDOW_MS", "60000"),
    ),
    maxRestartsPerWindow: Number(
      overrides.maxRestartsPerWindow ||
        env("PYTHON_EXECUTOR_MAX_RESTARTS_PER_WINDOW", "5"),
    ),
    sleep: overrides.sleep || sleep,
    spawnImpl: overrides.spawnImpl || spawn,
    cwd:
      overrides.cwd ||
      env("PYTHON_MEDIA_CWD", resolve(import.meta.dirname, "../../api")),
    now: overrides.now || Date.now,
    log: overrides.log || ((event) => console.log(JSON.stringify(event))),
  };
}

function createSupervisorState(name, moduleName, config) {
  return {
    name,
    moduleName,
    config,
    child: null,
    nextId: 0,
    pending: new Map(),
    buffer: "",
    heartbeatTimer: null,
    starting: null,
    restartTimestamps: [],
    restartCount: 0,
    lastCrashAt: 0,
    shuttingDown: false,
  };
}

function pythonExecutorError(code, message, extra = {}) {
  const error = new Error(message);
  error.code = code;
  Object.assign(error, extra);
  return error;
}

function emitSupervisorEvent(state, event, extra = {}) {
  state.config.log({
    event,
    subprocess: state.name,
    module: state.moduleName,
    restart_count: state.restartCount,
    ...extra,
  });
}

async function ensurePythonExecutor(name, moduleName, options = {}) {
  let state = pythonExecutors.get(name);
  if (!state) {
    state = createSupervisorState(
      name,
      moduleName,
      pythonExecutorConfig(options),
    );
    pythonExecutors.set(name, state);
  }
  if (state.starting) {
    await state.starting;
    return state;
  }
  if (state.child && state.child.exitCode === null && !state.child.killed) {
    return state;
  }
  state.starting = startPythonExecutor(state);
  try {
    await state.starting;
    return state;
  } finally {
    state.starting = null;
  }
}

async function startPythonExecutor(state) {
  pruneRestartTimestamps(state);
  if (state.restartTimestamps.length >= state.config.maxRestartsPerWindow) {
    emitSupervisorEvent(state, "subprocess_restart_limit_exceeded", {
      restart_window_ms: state.config.restartWindowMs,
    });
    throw pythonExecutorError(
      "subprocess_restart_limit_exceeded",
      `${state.name} exceeded the subprocess restart limit`,
    );
  }
  const restartDelayMs = computeRestartDelayMs(state);
  if (restartDelayMs > 0) {
    emitSupervisorEvent(state, "subprocess_restart_scheduled", {
      backoff_ms: restartDelayMs,
    });
    await state.config.sleep(restartDelayMs);
  }
  const child = state.config.spawnImpl(
    env("PYTHON_MEDIA_COMMAND", "python"),
    ["-m", state.moduleName, "--serve"],
    {
      stdio: ["pipe", "pipe", "pipe"],
      env: processEnv(),
      cwd: state.config.cwd,
    },
  );
  state.child = child;
  state.buffer = "";
  state.shuttingDown = false;
  attachSupervisorListeners(state);
  emitSupervisorEvent(
    state,
    state.restartCount > 0 ? "subprocess_restarted" : "subprocess_started",
  );
  state.restartTimestamps.push(state.config.now());
  state.restartCount += 1;
  startHeartbeatTimer(state);
  await sendSupervisorRequest(state, null, {
    timeoutMs: state.config.startupTimeoutMs,
    control: "ping",
  });
}

function pruneRestartTimestamps(state) {
  const cutoff = state.config.now() - state.config.restartWindowMs;
  state.restartTimestamps = state.restartTimestamps.filter(
    (timestamp) => timestamp >= cutoff,
  );
}

function computeRestartDelayMs(state) {
  if (state.restartCount <= 0) return 0;
  const exponent = Math.max(0, state.restartCount - 1);
  return Math.min(
    state.config.restartBackoffCapMs,
    state.config.restartBackoffMs * 2 ** exponent,
  );
}

function attachSupervisorListeners(state) {
  const child = state.child;
  if (!child) return;
  child.stdout.on("data", (data) =>
    handleSupervisorStdout(state, String(data)),
  );
  const stderrWriter = createRedactingLineWriter((line) =>
    globalThis.process.stderr.write(line),
  );
  child.stderr.on("data", (data) => {
    const text = String(data);
    stderrWriter.write(text);
    emitSupervisorEvent(state, "subprocess_stderr", {
      message: redactLogText(text.trim().slice(-1_000)),
      pending_requests: state.pending.size,
    });
  });
  child.on("error", (error) => {
    handleSupervisorFailure(
      state,
      pythonExecutorError(
        "subprocess_crashed",
        error instanceof Error
          ? error.message
          : `${state.name} subprocess error`,
      ),
    );
  });
  child.on("close", (code, signal) => {
    if (state.shuttingDown) return;
    handleSupervisorFailure(
      state,
      pythonExecutorError(
        "subprocess_crashed",
        `${state.name} exited with code ${code ?? "null"}${signal ? ` (${signal})` : ""}`,
      ),
    );
  });
}

function handleSupervisorStdout(state, chunk) {
  state.buffer += chunk;
  let newline;
  while ((newline = state.buffer.indexOf("\n")) >= 0) {
    const line = state.buffer.slice(0, newline).trim();
    state.buffer = state.buffer.slice(newline + 1);
    if (!line) continue;
    let response;
    try {
      response = JSON.parse(line);
    } catch {
      if (/^[\[{]/.test(line) && state.pending.size > 0) {
        handleSupervisorFailure(
          state,
          pythonExecutorError(
            "python_protocol_invalid",
            `${state.name} emitted invalid JSON`,
          ),
        );
        return;
      }
      // A log line, not a reply: pass it on and keep reading the chunk.
      globalThis.process.stderr.write(`[${state.name} stdout] ${line}\n`);
      continue;
    }
    const pending = state.pending.get(response.id);
    if (!pending) {
      continue;
    }
    clearTimeout(pending.timer);
    state.pending.delete(response.id);
    if (response.error) {
      const error = pythonExecutorError(
        response.error_code || "python_worker_error",
        String(response.error),
      );
      pending.reject(error);
      continue;
    }
    pending.resolve(response.result);
  }
}

function startHeartbeatTimer(state) {
  if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
  if (state.config.heartbeatIntervalMs <= 0) return;
  state.heartbeatTimer = setInterval(() => {
    if (!state.child || state.pending.size > 0) return;
    void sendSupervisorControl(state, "ping").catch(() => {});
  }, state.config.heartbeatIntervalMs);
}

function stopHeartbeatTimer(state) {
  if (state.heartbeatTimer) {
    clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = null;
  }
}

function handleSupervisorFailure(state, error) {
  stopHeartbeatTimer(state);
  if (error.code === "subprocess_heartbeat_failed") {
    emitSupervisorEvent(state, "subprocess_heartbeat_failed", {
      error_code: error.code,
    });
  } else {
    emitSupervisorEvent(state, "subprocess_crashed", {
      error_code: error.code || "subprocess_crashed",
      message: error.message,
    });
  }
  const child = state.child;
  state.child = null;
  state.lastCrashAt = state.config.now();
  for (const pending of state.pending.values()) {
    clearTimeout(pending.timer);
    pending.reject(error);
  }
  state.pending.clear();
  if (child && child.exitCode === null && !child.killed) {
    state.shuttingDown = true;
    child.kill("SIGTERM");
  }
}

function sendSupervisorControl(state, control) {
  return sendSupervisorRequest(state, null, {
    timeoutMs: state.config.heartbeatTimeoutMs,
    control,
  });
}

function sendSupervisorRequest(
  state,
  payload,
  { timeoutMs, control = null } = {},
) {
  const child = state.child;
  if (!child || child.exitCode !== null || child.killed) {
    throw pythonExecutorError(
      "subprocess_crashed",
      `${state.name} is not running`,
    );
  }
  const stdin = child.stdin;
  if (!stdin || stdin.destroyed || !stdin.writable) {
    const error = pythonExecutorError(
      "python_broken_pipe",
      `${state.name} stdin is unavailable`,
    );
    handleSupervisorFailure(state, error);
    throw error;
  }
  const id = ++state.nextId;
  return new Promise((resolveJob, rejectJob) => {
    const timer = setTimeout(() => {
      const error = pythonExecutorError(
        control ? "subprocess_heartbeat_failed" : "python_response_timeout",
        control
          ? `${state.name} heartbeat failed`
          : `${state.name} did not respond before the timeout`,
      );
      handleSupervisorFailure(state, error);
    }, timeoutMs || state.config.requestTimeoutMs);
    state.pending.set(id, { resolve: resolveJob, reject: rejectJob, timer });
    const message = JSON.stringify({ id, payload, control }) + "\n";
    stdin.write(message, (error) => {
      if (!error) return;
      const brokenPipe = pythonExecutorError(
        "python_broken_pipe",
        `${state.name} write failed`,
      );
      handleSupervisorFailure(state, brokenPipe);
    });
  });
}

export async function persistentPythonExecutor(
  name,
  moduleName,
  payload,
  options = {},
) {
  const state = await ensurePythonExecutor(name, moduleName, options);
  return sendSupervisorRequest(state, payload);
}

export function closePythonExecutors() {
  for (const state of pythonExecutors.values()) {
    stopHeartbeatTimer(state);
    state.shuttingDown = true;
    if (state.child && state.child.stdin) state.child.stdin.end();
    if (state.child && state.child.exitCode === null && !state.child.killed) {
      state.child.kill("SIGTERM");
    }
  }
  pythonExecutors.clear();
}

export function resetPythonExecutorsForTest() {
  closePythonExecutors();
}

export function queueWaitMs(job, now = Date.now()) {
  const timestamp = Date.parse(job?.data?.enqueued_at || "");
  if (Number.isFinite(timestamp)) return Math.max(0, now - timestamp);
  return Math.max(0, now - Number(job?.timestamp || now));
}

export function jobRuntimeTelemetry(
  job,
  { startedAt, finishedAt = Date.now() } = {},
) {
  return {
    runtime_ms: Math.max(0, finishedAt - startedAt),
    queue_wait_ms: queueWaitMs(job, startedAt),
  };
}

function isSubprocessFailureCode(code) {
  return new Set([
    "subprocess_crashed",
    "subprocess_heartbeat_failed",
    "python_broken_pipe",
    "python_protocol_invalid",
    "python_response_timeout",
    "subprocess_restart_limit_exceeded",
    "python_stderr_fatal",
  ]).has(String(code || ""));
}

async function runRenderJob(job) {
  if (
    job.data.render_engine === "remotion_premium" &&
    env("REMOTION_ENABLED") === "true"
  ) {
    const { runRemotionJob } = await import("./remotion.mjs");
    return runRemotionJob(job.data, {
      queueIdentity: {
        id: String(job.id),
        attemptsMade: Number(job.attemptsMade || 0),
      },
      onProgress: async ({ progressPct, message, eventType, metadata }) => {
        await job.updateProgress(progressPct);
        console.log(
          JSON.stringify({
            event: eventType || "worker_progress",
            progress_pct: progressPct,
            message,
            metadata: metadata || {},
          }),
        );
      },
    });
  }
  throw new Error(
    "remotion_premium requires REMOTION_ENABLED=true on the Chromium worker",
  );
}

async function runVisualJob(job) {
  if (!mlJobAllowed("visual_analysis")) {
    throw new Error(
      "visual_analysis requires the dedicated GPU/ML worker image, or a cpu-vision worker with VISUAL_PROVIDER=remote",
    );
  }
  await ensureUploadRecordExists(job);
  return persistentPythonExecutor("visual-ml", "app.workers.visual_job", {
    workspace_id: String(job.data.workspace_id),
    project_id: String(job.data.project_id),
    video_id: String(job.data.resource_id),
  });
}

async function runMatteJob(job) {
  if (!mlJobAllowed("matte_extraction")) {
    throw new Error(
      "matte_extraction requires the dedicated GPU/media worker image, or a cpu-vision worker with MATTE_PROVIDER=local_cpu",
    );
  }
  await ensureUploadRecordExists(job);
  return persistentPythonExecutor("matte-gpu", "app.workers.matte_job", {
    workspace_id: String(job.data.workspace_id),
    project_id: String(job.data.project_id),
    video_id: String(job.data.resource_id),
    matte_id: String(job.data.render_engine || ""),
  });
}

export function domainJobPayload(job) {
  const data = job?.data || {};
  return {
    job_id: String(job?.id),
    kind: String(data.kind),
    queue_name: String(job?.queueName),
    workspace_id: String(data.workspace_id),
    project_id: String(data.project_id),
    resource_id: String(data.resource_id),
    render_engine: String(data.render_engine || "ffmpeg_basic"),
    ...workflowJobFields(data),
  };
}

async function runDomainJob(job) {
  if (env("PYTHON_MEDIA_WORKER_ENABLED") !== "true") {
    throw new Error(
      `${job.data.kind} requires a dedicated Python media worker image`,
    );
  }
  return persistentPythonExecutor(
    "domain",
    "app.workers.domain_job",
    domainJobPayload(job),
  );
}

async function runRetentionJob() {
  if (!hasApiConfig()) {
    throw new Error(
      "maintenance retention requires WORKER_API_BASE_URL and WORKER_API_TOKEN",
    );
  }
  const response = await fetch(`${apiBaseUrl()}/internal/retention/run`, {
    method: "POST",
    headers: { "x-motionly-worker-token": workerToken() },
  });
  if (!response.ok)
    throw new Error(`retention endpoint returned HTTP ${response.status}`);
  return response.json();
}

async function runGpuAutoscalingJob() {
  if (!hasApiConfig()) {
    throw new Error(
      "maintenance autoscaling requires WORKER_API_BASE_URL and WORKER_API_TOKEN",
    );
  }
  const response = await fetch(
    `${apiBaseUrl()}/internal/operations/gpu-autoscaling/run`,
    {
      method: "POST",
      headers: { "x-motionly-worker-token": workerToken() },
    },
  );
  if (!response.ok)
    throw new Error(
      `gpu autoscaling endpoint returned HTTP ${response.status}`,
    );
  return response.json();
}

async function runBatchJob(job) {
  if (!hasApiConfig()) {
    throw new Error(
      "batch planning requires WORKER_API_BASE_URL and WORKER_API_TOKEN",
    );
  }
  const response = await fetch(
    `${apiBaseUrl()}/internal/batches/${job.data.workspace_id}/${job.data.resource_id}/run`,
    { method: "POST", headers: { "x-motionly-worker-token": workerToken() } },
  );
  if (!response.ok)
    throw new Error(`batch endpoint returned HTTP ${response.status}`);
  return response.json();
}

async function sendHeartbeat() {
  if (!hasApiConfig()) return;
  const response = await fetch(
    `${apiBaseUrl()}/internal/operations/heartbeat`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-motionly-worker-token": workerToken(),
      },
      body: JSON.stringify(workerHeartbeatPayload(queueNames)),
    },
  );
  if (!response.ok)
    throw new Error(`heartbeat endpoint returned HTTP ${response.status}`);
}

async function reportProviderSlo(job, success, latencyMs, errorCode = null) {
  if (!hasApiConfig()) return;
  const providerKinds = new Set([
    "transcription",
    "visual_analysis",
    "matte_extraction",
    "phase5",
    "edit_plan",
    "batch_plan",
  ]);
  if (!providerKinds.has(String(job.data.kind))) return;
  const response = await fetch(
    `${apiBaseUrl()}/internal/operations/provider-slo`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-motionly-worker-token": workerToken(),
      },
      body: JSON.stringify({
        provider: String(job.data.provider || job.queueName),
        operation: String(job.data.kind),
        success,
        latency_ms: latencyMs,
        error_code: errorCode,
      }),
    },
  );
  if (!response.ok)
    throw new Error(`provider SLO endpoint returned HTTP ${response.status}`);
}

function processEnv() {
  return { ...globalThis.process.env };
}

function supabaseHeaders() {
  const serviceRoleKey = env("SUPABASE_SERVICE_ROLE_KEY");
  return {
    apikey: serviceRoleKey,
    authorization: `Bearer ${serviceRoleKey}`,
    "content-type": "application/json",
  };
}

export const MAX_WORKFLOW_RESULT_BYTES = 1_048_576;
const MIN_WORKFLOW_RESULT_BYTES = 1_024;
const DEFAULT_WORKFLOW_RESULT_BYTES = 262_144;
const DEFAULT_WORKFLOW_LEASE_SECONDS = 3_600;

function workflowError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function boundedText(value, maximum, fallback) {
  const text = String(value ?? fallback)
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .trim();
  return (text || fallback).slice(0, maximum);
}

export function normalizeWorkflowErrorCode(value) {
  const source = String(value || "worker_error").trim();
  const normalized = source
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[^A-Za-z0-9_.-]+/g, "_")
    .replace(/^[_.-]+|[_.-]+$/g, "")
    .toLowerCase();
  return (
    normalized && /^[a-z0-9]/.test(normalized) ? normalized : "worker_error"
  ).slice(0, 120);
}

export function deterministicWorkflowAttemptKey(job) {
  const attemptKey = workflowJobFields(job?.data || {}).attempt_key;
  if (!attemptKey) {
    throw workflowError(
      "workflow_payload_invalid",
      "attempt_key is required for workflow jobs",
    );
  }
  return attemptKey;
}

export function workflowRetryable(job) {
  if (job?.data?.status === "canceled") return false;
  const context = workflowContextForJob(job);
  if (!context) {
    const attempts = Math.max(
      1,
      Number(job?.opts?.attempts || job?.data?.max_attempts || 1),
    );
    return Number(job?.attemptsMade || 0) + 1 < attempts;
  }
  return context.attemptNumber < context.maxAttempts;
}

export function workflowContextForJob(job) {
  const data = job?.data || {};
  const hasWorkflowFields = workflowJobFieldNames.some(
    (name) => data[name] !== undefined && data[name] !== null,
  );
  if (!hasWorkflowFields) return null;

  const fields = workflowJobFields(data);
  for (const name of [
    "workflow_run_id",
    "step_key",
    "step_kind",
    "attempt_key",
  ]) {
    if (!fields[name]) {
      throw workflowError(
        "workflow_payload_invalid",
        `${name} is required for workflow jobs`,
      );
    }
  }
  for (const name of ["workflow_attempt_number", "workflow_max_attempts"]) {
    if (!Number.isInteger(fields[name])) {
      throw workflowError(
        "workflow_payload_invalid",
        `${name} is required for workflow jobs`,
      );
    }
  }
  if (fields.workflow_attempt_number > fields.workflow_max_attempts) {
    throw workflowError(
      "workflow_payload_invalid",
      "workflow_attempt_number must not exceed workflow_max_attempts",
    );
  }
  for (const name of ["workspace_id", "project_id", "kind"]) {
    if (typeof data[name] !== "string" || data[name].length < 1) {
      throw workflowError(
        "workflow_payload_invalid",
        `${name} is required for workflow jobs`,
      );
    }
  }
  return {
    workspaceId: data.workspace_id,
    projectId: data.project_id,
    workflowRunId: fields.workflow_run_id,
    workflowStepId: fields.workflow_step_id || null,
    stepKey: fields.step_key,
    stepKind: fields.step_kind,
    attemptKey: fields.attempt_key,
    attemptNumber: fields.workflow_attempt_number,
    maxAttempts: fields.workflow_max_attempts,
    inputHash: fields.input_hash || null,
    nextRunState: fields.next_run_state || null,
  };
}

export function workflowBeginParameters(job, options = {}) {
  const context = workflowContextForJob(job);
  if (!context) return null;
  const maxAttempts = context.maxAttempts;
  const configuredLeaseSeconds = boundedInteger(
    env("WORKFLOW_STEP_LEASE_SECONDS"),
    DEFAULT_WORKFLOW_LEASE_SECONDS,
    5,
    3_600,
  );
  const leaseSeconds = boundedInteger(
    options.leaseSeconds,
    configuredLeaseSeconds,
    5,
    3_600,
  );
  const leaseOwner = boundedText(options.leaseOwner, 160, "bullmq-worker");
  return {
    p_workspace_id: context.workspaceId,
    p_project_id: context.projectId,
    p_workflow_run_id: context.workflowRunId,
    p_step_key: context.stepKey,
    p_step_kind: context.stepKind,
    p_attempt_key: deterministicWorkflowAttemptKey(job),
    p_lease_owner: leaseOwner,
    p_lease_seconds: leaseSeconds,
    p_input_hash: context.inputHash,
    p_max_attempts: maxAttempts,
  };
}

function canonicalJsonValue(value, path, seen) {
  if (value === null || typeof value === "boolean" || typeof value === "string")
    return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw workflowError(
        "workflow_result_unsafe",
        `${path} contains a non-finite number`,
      );
    }
    return Object.is(value, -0) ? 0 : value;
  }
  if (typeof value !== "object") {
    throw workflowError("workflow_result_unsafe", `${path} is not JSON-safe`);
  }
  if (ArrayBuffer.isView(value) || value instanceof ArrayBuffer) {
    throw workflowError(
      "workflow_result_unsafe",
      `${path} contains binary data`,
    );
  }
  if (seen.has(value)) {
    throw workflowError(
      "workflow_result_unsafe",
      `${path} contains circular data`,
    );
  }
  seen.add(value);
  try {
    if (Array.isArray(value)) {
      if (Object.getOwnPropertySymbols(value).length > 0) {
        throw workflowError(
          "workflow_result_unsafe",
          `${path} contains symbol keys`,
        );
      }
      const descriptors = Object.getOwnPropertyDescriptors(value);
      const indexKeys = Object.keys(descriptors).filter(
        (key) => key !== "length",
      );
      if (indexKeys.length !== value.length) {
        throw workflowError(
          "workflow_result_unsafe",
          `${path} must not contain sparse arrays`,
        );
      }
      return indexKeys
        .sort((left, right) => Number(left) - Number(right))
        .map((key) => {
          const descriptor = descriptors[key];
          if (!descriptor.enumerable || descriptor.get || descriptor.set) {
            throw workflowError(
              "workflow_result_unsafe",
              `${path}[${key}] is not plain JSON data`,
            );
          }
          return canonicalJsonValue(descriptor.value, `${path}[${key}]`, seen);
        });
    }
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw workflowError(
        "workflow_result_unsafe",
        `${path} must contain only plain objects`,
      );
    }
    const symbolKeys = Object.getOwnPropertySymbols(value);
    if (symbolKeys.length > 0) {
      throw workflowError(
        "workflow_result_unsafe",
        `${path} contains symbol keys`,
      );
    }
    const descriptors = Object.getOwnPropertyDescriptors(value);
    const output = {};
    for (const key of Object.keys(descriptors).sort()) {
      const descriptor = descriptors[key];
      if (!descriptor.enumerable || descriptor.get || descriptor.set) {
        throw workflowError(
          "workflow_result_unsafe",
          `${path}.${key} is not plain JSON data`,
        );
      }
      output[key] = canonicalJsonValue(
        descriptor.value,
        `${path}.${key}`,
        seen,
      );
    }
    return output;
  } finally {
    seen.delete(value);
  }
}

export function canonicalizeWorkflowResult(result, options = {}) {
  assertQueuePayloadSafe(result, "workflow_result");
  const value = canonicalJsonValue(result, "workflow_result", new Set());
  const canonical = JSON.stringify(value);
  const bytes = Buffer.byteLength(canonical, "utf8");
  const configuredMaxBytes = boundedInteger(
    env("WORKFLOW_STEP_RESULT_MAX_BYTES"),
    DEFAULT_WORKFLOW_RESULT_BYTES,
    MIN_WORKFLOW_RESULT_BYTES,
    MAX_WORKFLOW_RESULT_BYTES,
  );
  const maxBytes = boundedInteger(
    options.maxBytes,
    configuredMaxBytes,
    MIN_WORKFLOW_RESULT_BYTES,
    MAX_WORKFLOW_RESULT_BYTES,
  );
  if (bytes > maxBytes) {
    throw workflowError(
      "workflow_result_oversized",
      `workflow result is ${bytes} bytes; maximum is ${maxBytes}`,
    );
  }
  return {
    value,
    canonical,
    bytes,
    hash: createHash("sha256").update(canonical).digest("hex"),
  };
}

function firstRpcResult(value, rpcName) {
  const result = Array.isArray(value) ? value[0] : value;
  if (!result || typeof result !== "object") {
    throw workflowError(
      "workflow_rpc_invalid",
      `${rpcName} returned no result`,
    );
  }
  return result;
}

export async function callWorkflowRpc(
  rpcName,
  parameters,
  { fetchImpl = globalThis.fetch } = {},
) {
  const supabaseUrl = env("SUPABASE_URL");
  const serviceRoleKey = env("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) {
    throw workflowError(
      "workflow_rpc_config_missing",
      "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for workflow jobs",
    );
  }
  const url = new URL(`/rest/v1/rpc/${rpcName}`, supabaseUrl);
  const response = await fetchImpl(url, {
    method: "POST",
    headers: supabaseHeaders(),
    body: JSON.stringify(parameters),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // An invalid or empty body is handled below without exposing response content.
  }
  if (!response.ok) {
    const message = boundedText(
      payload?.message || `${rpcName} returned HTTP ${response.status}`,
      500,
      "workflow RPC failed",
    );
    throw workflowError(payload?.code || "workflow_rpc_failed", message);
  }
  return firstRpcResult(payload, rpcName);
}

export async function beginWorkflowStep(job, options = {}) {
  const rpcCall = options.rpcCall || callWorkflowRpc;
  const parameters = workflowBeginParameters(job, options);
  if (!parameters) return null;
  return firstRpcResult(
    await rpcCall("motionly_begin_workflow_step", parameters),
    "motionly_begin_workflow_step",
  );
}

export async function completeWorkflowStep(
  job,
  claimToken,
  result,
  options = {},
) {
  const context = workflowContextForJob(job);
  if (!context)
    throw workflowError(
      "workflow_payload_invalid",
      "workflow context is required",
    );
  if (!claimToken)
    throw workflowError(
      "workflow_claim_missing",
      "workflow claim token is required",
    );
  const compact = canonicalizeWorkflowResult(result, options);
  const parameters = {
    p_workspace_id: context.workspaceId,
    p_project_id: context.projectId,
    p_workflow_run_id: context.workflowRunId,
    p_step_key: context.stepKey,
    p_claim_token: claimToken,
    p_output_hash: compact.hash,
    p_output: compact.value,
    p_next_run_state: context.nextRunState,
  };
  const rpcCall = options.rpcCall || callWorkflowRpc;
  const rpcResult = firstRpcResult(
    await rpcCall("motionly_complete_workflow_step", parameters),
    "motionly_complete_workflow_step",
  );
  return { compact, rpcResult };
}

export async function failWorkflowStep(
  job,
  claimToken,
  originalError,
  options = {},
) {
  const context = workflowContextForJob(job);
  if (!context)
    throw workflowError(
      "workflow_payload_invalid",
      "workflow context is required",
    );
  if (!claimToken)
    throw workflowError(
      "workflow_claim_missing",
      "workflow claim token is required",
    );
  const errorCode = normalizeWorkflowErrorCode(
    originalError instanceof Error
      ? originalError.code || originalError.name
      : "worker_error",
  );
  const errorMessage = boundedText(
    originalError instanceof Error ? originalError.message : originalError,
    500,
    "Worker execution failed",
  );
  const parameters = {
    p_workspace_id: context.workspaceId,
    p_project_id: context.projectId,
    p_workflow_run_id: context.workflowRunId,
    p_step_key: context.stepKey,
    p_claim_token: claimToken,
    p_error_code: errorCode,
    p_error_message: errorMessage,
    p_retryable: options.retryable ?? workflowRetryable(job),
  };
  const rpcCall = options.rpcCall || callWorkflowRpc;
  return firstRpcResult(
    await rpcCall("motionly_fail_workflow_step", parameters),
    "motionly_fail_workflow_step",
  );
}

export function classifyWorkflowBeginResult(beginResult, context) {
  const step = beginResult?.step || {};
  const attempt = beginResult?.attempt || null;
  if (context.workflowStepId && step.id !== context.workflowStepId) {
    throw workflowError(
      "workflow_step_identity_mismatch",
      "workflow begin result did not match workflow_step_id",
    );
  }
  if (step.state === "succeeded") {
    const compact = canonicalizeWorkflowResult(step.output);
    if (step.output_hash && step.output_hash !== compact.hash) {
      throw workflowError(
        "workflow_result_hash_mismatch",
        "stored workflow result hash is invalid",
      );
    }
    return { action: "succeeded", result: compact.value };
  }
  if (beginResult?.exhausted || step.state === "failed") {
    return {
      action: "terminal",
      result: {
        status: "workflow_step_exhausted",
        workflow_run_id: context.workflowRunId,
        workflow_step_id: step.id || context.workflowStepId,
        step_key: context.stepKey,
      },
    };
  }
  const leaseExpiresAt = Date.parse(attempt?.lease_expires_at || "");
  if (
    attempt?.state === "active" &&
    Number.isFinite(leaseExpiresAt) &&
    leaseExpiresAt <= Date.now()
  ) {
    throw workflowError(
      "workflow_stale_claim",
      "workflow begin returned an expired claim",
    );
  }
  if (attempt?.state && attempt.state !== "active") {
    return {
      action: "terminal",
      result: {
        status: "workflow_step_not_claimed",
        workflow_run_id: context.workflowRunId,
        workflow_step_id: step.id || context.workflowStepId,
        step_key: context.stepKey,
        attempt_state: attempt.state,
      },
    };
  }
  if (attempt?.state !== "active" || !attempt.claim_token) {
    throw workflowError(
      "workflow_claim_missing",
      "workflow begin did not return an active claim",
    );
  }
  return { action: "execute", claimToken: attempt.claim_token };
}

export async function ensureUploadRecordExists(
  job,
  { fetchImpl = globalThis.fetch } = {},
) {
  const supabaseUrl = env("SUPABASE_URL");
  const serviceRoleKey = env("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error(
      "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for GPU/media queues.",
    );
  }
  const url = new URL("/rest/v1/uploaded_videos", supabaseUrl);
  url.searchParams.set("select", "id,status");
  url.searchParams.set("id", `eq.${job.data.resource_id}`);
  url.searchParams.set("project_id", `eq.${job.data.project_id}`);
  url.searchParams.set("workspace_id", `eq.${job.data.workspace_id}`);
  url.searchParams.set("limit", "1");
  const response = await fetchImpl(url, { headers: supabaseHeaders() });
  if (!response.ok) {
    throw pythonExecutorError(
      "supabase_upload_preflight_failed",
      `Upload preflight returned HTTP ${response.status}`,
    );
  }
  const rows = await response.json();
  if (!Array.isArray(rows) || rows.length === 0) {
    throw pythonExecutorError("upload_not_found", "Upload was not found.");
  }
  return rows[0];
}

async function latestJobData(job) {
  if (typeof job?.getData === "function") {
    try {
      const data = await job.getData();
      if (data && typeof data === "object") return data;
    } catch {
      // Fall through to the queue hash lookup. A read failure is not cancellation.
    }
  }
  try {
    const queue = job?.queue;
    const client = queue?.client ? await queue.client : null;
    const key = queue?.toKey?.(job.id);
    if (!client || !key || typeof client.hget !== "function")
      return job?.data || {};
    const raw = await client.hget(key, "data");
    const data = raw ? JSON.parse(raw) : null;
    return data && typeof data === "object" ? data : job?.data || {};
  } catch {
    return job?.data || {};
  }
}

async function assertJobNotCanceled(job) {
  const data = await latestJobData(job);
  if (data?.status !== "canceled") return;
  const error = new Error(`Queue job ${job.id} was canceled`);
  error.code = "job_canceled";
  throw error;
}

function throwIfJobResultFailed(job, result) {
  if (!result?.failed) return;
  const isRender = job?.data?.kind === "render";
  const defaultCode = isRender ? "domain_render_failed" : "domain_job_failed";
  const defaultMessage = isRender
    ? "Domain render failed."
    : "Domain job failed.";
  const message =
    typeof result.error_message === "string" && result.error_message.trim()
      ? result.error_message
      : defaultMessage;
  const error = new Error(message);
  error.code =
    typeof result.error_code === "string" && result.error_code.trim()
      ? result.error_code
      : defaultCode;
  throw error;
}

export async function runJob(job, testOverrides = {}) {
  const overrides =
    testOverrides && typeof testOverrides === "object" ? testOverrides : {};
  const executeJob = overrides.executeJob;
  const reportSlo = overrides.reportProviderSlo || reportProviderSlo;
  const rpcCall = overrides.workflowRpc || overrides.rpcCall || callWorkflowRpc;
  const workflowOptions = {
    rpcCall,
    leaseOwner:
      overrides.leaseOwner ||
      `${env("WORKER_ID", `${env("HOSTNAME", "worker")}-${process.pid}`)}:${job.queueName}`,
    leaseSeconds: overrides.leaseSeconds,
    maxBytes: overrides.maxWorkflowResultBytes,
  };
  await assertJobNotCanceled(job);

  const startedAt = Date.now();
  const coldStartMs = firstJobStarted
    ? null
    : Math.max(0, startedAt - workerStartedAt);
  firstJobStarted = true;
  console.log(
    JSON.stringify({
      event: "job_started",
      queue_job_id: job.id,
      kind: job.data.kind,
      queue_name: job.queueName,
      attempt: job.data.workflow_attempt_number || job.attemptsMade + 1,
      queue_wait_ms: queueWaitMs(job, startedAt),
      cold_start_ms: coldStartMs,
      trace_id: job.data.trace_id || null,
    }),
  );

  await job.updateProgress(1);
  let result;
  let workflowContext = null;
  let claimToken = null;
  let specializedExecutionStarted = false;
  try {
    workflowContext = workflowContextForJob(job);
    if (workflowContext) {
      const beginResult = await beginWorkflowStep(job, workflowOptions);
      const beginAction = classifyWorkflowBeginResult(
        beginResult,
        workflowContext,
      );
      if (beginAction.action === "execute") {
        claimToken = beginAction.claimToken;
      } else {
        result = beginAction.result;
      }
    }

    await assertJobNotCanceled(job);
    if (!workflowContext || claimToken) {
      specializedExecutionStarted = true;
      result = executeJob
        ? await executeJob(job)
        : job.data.kind === "retention"
          ? await runRetentionJob()
          : job.data.kind === "gpu_autoscaling"
            ? await runGpuAutoscalingJob()
            : job.data.kind === "batch_plan"
              ? await runBatchJob(job)
              : job.data.kind === "render"
                ? job.data.render_engine === "remotion_premium"
                  ? await runRenderJob(job)
                  : await runDomainJob(job)
                : job.data.kind === "visual_analysis"
                  ? await runVisualJob(job)
                  : job.data.kind === "matte_extraction"
                    ? await runMatteJob(job)
                    : await runDomainJob(job);
      await assertJobNotCanceled(job);
      if (workflowContext && workflowContext.stepKind === "render_draft") {
        const normalizedRender = {
          render_job_id: String(job.data.resource_id),
          render_status: String(result?.render_status || result?.status || ""),
          failed: Boolean(result?.failed),
        };
        if (normalizedRender.failed) {
          normalizedRender.error_code =
            result?.error_code || "domain_render_failed";
          normalizedRender.error_message =
            result?.error_message || "Domain render failed.";
        }
        result = normalizedRender;
      }
      throwIfJobResultFailed(job, result);
      if (workflowContext) {
        const completion = await completeWorkflowStep(
          job,
          claimToken,
          result,
          workflowOptions,
        );
        result = completion.compact.value;
      }
      await reportSlo(job, true, Date.now() - startedAt).catch((error) => {
        console.error(
          JSON.stringify({
            event: "provider_slo_report_failed",
            queue_job_id: job.id,
            message: error instanceof Error ? error.message : String(error),
          }),
        );
      });
    }
  } catch (error) {
    const uploadMissing =
      error instanceof Error && error.code === "upload_not_found";
    const canceled = error instanceof Error && error.code === "job_canceled";
    if ((uploadMissing || canceled) && typeof job.discard === "function") {
      job.discard();
    }
    if (workflowContext && claimToken) {
      await failWorkflowStep(job, claimToken, error, {
        ...workflowOptions,
        retryable: uploadMissing || canceled ? false : workflowRetryable(job),
      }).catch((callbackError) => {
        console.error(
          JSON.stringify({
            event: "workflow_failure_callback_failed",
            queue_job_id: job.id,
            message:
              callbackError instanceof Error
                ? callbackError.message
                : String(callbackError),
          }),
        );
      });
    }
    console.error(
      JSON.stringify({
        event: "job_runtime_metrics",
        queue_job_id: job.id,
        queue_name: job.queueName,
        kind: job.data.kind,
        success: false,
        ...jobRuntimeTelemetry(job, { startedAt }),
      }),
    );
    if (specializedExecutionStarted) {
      await reportSlo(
        job,
        false,
        Date.now() - startedAt,
        error instanceof Error ? error.code || error.name : "worker_error",
      ).catch(() => {});
    }
    throw error;
  }
  await assertJobNotCanceled(job);
  await job.updateProgress(100).catch((error) => {
    console.error(
      JSON.stringify({
        event: "job_progress_report_failed",
        queue_job_id: job.id,
        message: error instanceof Error ? error.message : String(error),
      }),
    );
  });
  console.log(
    JSON.stringify({
      event: "job_runtime_metrics",
      queue_job_id: job.id,
      queue_name: job.queueName,
      kind: job.data.kind,
      success: true,
      ...jobRuntimeTelemetry(job, { startedAt }),
    }),
  );
  return result;
}

function createWorker(queueName, connection) {
  const worker = new Worker(queueName, runJob, {
    connection,
    concurrency: workerConcurrency(queueName),
    stalledInterval: stalledIntervalMs(),
  });

  worker.on("completed", (job, result) => {
    console.log(
      JSON.stringify({
        event: "job_completed",
        queue_job_id: job.id,
        queue_name: queueName,
        kind: job.data.kind,
        trace_id: job.data.trace_id || null,
        result_status: result?.status || result?.render_status || "ok",
      }),
    );
  });

  worker.on("failed", async (job, error) => {
    const jobData = job?.data || {};
    const errorCode =
      error instanceof Error ? error.code || error.name : "worker_error";
    const canceled =
      errorCode === "job_canceled" || jobData.status === "canceled";
    const retrying =
      !canceled &&
      Boolean(job && job.attemptsMade < Number(job.opts?.attempts || 1));
    console.error(
      JSON.stringify({
        event: "job_failed",
        queue_job_id: job?.id,
        queue_name: queueName,
        kind: jobData.kind,
        attempts_made: job?.attemptsMade,
        attempts: job?.opts?.attempts,
        error_code: errorCode,
        message: error instanceof Error ? error.message : String(error),
      }),
    );
    if (
      retrying &&
      isSubprocessFailureCode(errorCode) &&
      jobData.status !== "canceled"
    ) {
      console.error(
        JSON.stringify({
          event: "job_requeued_after_subprocess_crash",
          queue_job_id: job?.id,
          queue_name: queueName,
          kind: jobData.kind,
          error_code: errorCode,
          next_attempt: Number(job?.attemptsMade || 0) + 1,
        }),
      );
    }
    if (jobData.kind === "render" && jobData.resource_id) {
      try {
        const { persistRenderFailureDirect } = await import("./remotion.mjs");
        const failureCode = canceled
          ? "render_cancelled"
          : error instanceof Error && typeof error.code === "string"
            ? error.code
            : "render_worker_failed";
        await persistRenderFailureDirect(
          jobData,
          error instanceof Error ? error.message : String(error),
          retrying,
          failureCode,
        );
      } catch (callbackError) {
        console.error(
          JSON.stringify({
            event: "render_failure_callback_failed",
            queue_job_id: job?.id,
            message:
              callbackError instanceof Error
                ? callbackError.message
                : String(callbackError),
          }),
        );
      }
    }
  });

  worker.on("stalled", (jobId) => {
    console.error(
      JSON.stringify({
        event: "job_stalled",
        queue_job_id: jobId,
        queue_name: queueName,
      }),
    );
  });

  worker.on("error", (error) => {
    console.error(
      JSON.stringify({
        event: "worker_error",
        queue_name: queueName,
        message: error instanceof Error ? error.message : String(error),
      }),
    );
  });

  return worker;
}

function validateRemotionConfig() {
  if (!remotionEnabled()) return;

  const rPath = rendererPath();
  const requiredFiles = [
    join(rPath, "package.json"),
    join(rPath, "src/render-cli.ts"),
    join(rPath, "src/index.ts"),
  ];
  const missingFiles = requiredFiles.filter((path) => !existsSync(path));
  if (missingFiles.length > 0) {
    throw new Error(
      `REMOTION_ENABLED=true but renderer files are missing: ${missingFiles.join(", ")}`,
    );
  }

  if (!env("SUPABASE_URL") || !env("SUPABASE_SERVICE_ROLE_KEY")) {
    throw new Error(
      "REMOTION_ENABLED=true requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
    );
  }

  const browserPath = env(
    "BROWSER_EXECUTABLE_PATH",
    env("PUPPETEER_EXECUTABLE_PATH"),
  );
  if (browserPath && !existsSync(browserPath)) {
    throw new Error(
      `Configured Chromium executable was not found: ${browserPath}`,
    );
  }
  if (isProduction() && !browserPath) {
    throw new Error(
      "REMOTION_ENABLED=true in production requires BROWSER_EXECUTABLE_PATH or PUPPETEER_EXECUTABLE_PATH.",
    );
  }
}

async function main() {
  // Before anything logs: every write to stdout/stderr is credential-redacted.
  installLogRedaction();
  const mode = process.argv[2] || (isProduction() ? "--loop" : "--check");
  if (!["--check", "--once", "--loop"].includes(mode)) {
    throw new Error("Usage: node src/worker.mjs [--check|--once|--loop]");
  }

  if (!hasQueueConfig()) {
    await writeBuildStamp();
    if (isProduction()) throw new Error("REDIS_URL is required in production.");
    console.log("Worker queue config is not set; build check only.");
    return;
  }
  // Rejects ML queues without the ML image, and any test_fixture setup that
  // could load real models, encode on a GPU, or run in production.
  assertNoFixturesInProduction();
  mlExecutionPlan(queueNames);
  if (
    queueNames.includes("visual_analysis") ||
    queueNames.includes("matte_extraction")
  ) {
    if (!env("SUPABASE_URL") || !env("SUPABASE_SERVICE_ROLE_KEY")) {
      throw new Error(
        "GPU/media queues require SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
      );
    }
  }

  await writeBuildStamp();
  validateRemotionConfig();
  if (mode === "--check") {
    console.log(
      remotionEnabled()
        ? "BullMQ worker, API config, and Remotion renderer prerequisites are present."
        : "BullMQ worker queue and API config are present.",
    );
    return;
  }

  const connection = bullConnection();
  let maintenanceQueue = null;
  if (queueNames.includes("maintenance")) {
    maintenanceQueue = queueFor("maintenance", connection);
    for (const job of maintenanceRepeatJobs()) {
      await maintenanceQueue.add(job.name, job.data, {
        jobId: job.name,
        repeat: { pattern: job.pattern },
      });
    }
  }
  const workers = queueNames.map((queueName) =>
    createWorker(queueName, connection),
  );
  await sendHeartbeat().catch((error) => {
    console.error(
      JSON.stringify({
        event: "worker_initial_heartbeat_failed",
        message: error instanceof Error ? error.message : String(error),
      }),
    );
  });
  markAlive();
  console.log(
    JSON.stringify({
      event: "worker_ready",
      process_alive: true,
      redis_connected: true,
      api_reachable: hasApiConfig(),
      models_loaded:
        env("MODEL_PREFETCH") === "true"
          ? "prefetch_requested"
          : "not_requested",
      disk_free_bytes: diskFreeBytes(),
      queue_consumers_attached: workers.length,
      queues: queueNames,
    }),
  );
  const heartbeatTimer = setInterval(
    () => {
      markAlive();
      void sendHeartbeat().catch((error) =>
        console.error(
          JSON.stringify({
            event: "worker_heartbeat_failed",
            message: error instanceof Error ? error.message : String(error),
          }),
        ),
      );
    },
    Number(env("WORKER_HEARTBEAT_INTERVAL_MS", "30000")),
  );
  let shuttingDown = false;
  const shutdown = async (signal) => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`Received ${signal}; closing BullMQ workers.`);
    await Promise.allSettled(workers.map((worker) => worker.close()));
    clearInterval(heartbeatTimer);
    if (maintenanceQueue) await maintenanceQueue.close();
    closePythonExecutors();
    await connection.quit();
    process.exit(0);
  };
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => void shutdown(signal));
  }

  console.log(
    JSON.stringify({
      event: "worker_started",
      queues: queueNames,
      render_concurrency: workerConcurrency("render"),
    }),
  );

  if (mode === "--once") {
    await sleep(5_000);
    await shutdown("once_complete");
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const isMainModule =
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  });
}
