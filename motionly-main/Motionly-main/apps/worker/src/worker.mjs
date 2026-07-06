import { spawn } from "node:child_process";
import { existsSync, statfsSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Worker } from "bullmq";
import {
  bullConnection,
  apiBaseUrl,
  hasApiConfig,
  hasQueueConfig,
  isProduction,
  queueNames,
  queueFor,
  stalledIntervalMs,
  workerConcurrency,
  workerToken,
  writeBuildStamp,
} from "./queue-common.mjs";

function env(name, fallback = "") {
  return process.env[name] || fallback;
}

function remotionEnabled() {
  return env("REMOTION_ENABLED") === "true";
}

function rendererPath() {
  return resolve(env("RENDERER_BASE_PATH", join(import.meta.dirname, "../../renderer")));
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
    requestTimeoutMs: Number(overrides.requestTimeoutMs || env("PYTHON_EXECUTOR_REQUEST_TIMEOUT_MS", "900000")),
    startupTimeoutMs: Number(overrides.startupTimeoutMs || env("PYTHON_EXECUTOR_STARTUP_TIMEOUT_MS", "900000")),
    heartbeatIntervalMs: Number(overrides.heartbeatIntervalMs || env("PYTHON_EXECUTOR_HEARTBEAT_INTERVAL_MS", "15000")),
    heartbeatTimeoutMs: Number(overrides.heartbeatTimeoutMs || env("PYTHON_EXECUTOR_HEARTBEAT_TIMEOUT_MS", "120000")),
    restartBackoffMs: Number(overrides.restartBackoffMs || env("PYTHON_EXECUTOR_RESTART_BACKOFF_MS", "1000")),
    restartBackoffCapMs: Number(overrides.restartBackoffCapMs || env("PYTHON_EXECUTOR_RESTART_BACKOFF_CAP_MS", "30000")),
    restartWindowMs: Number(overrides.restartWindowMs || env("PYTHON_EXECUTOR_RESTART_WINDOW_MS", "60000")),
    maxRestartsPerWindow: Number(overrides.maxRestartsPerWindow || env("PYTHON_EXECUTOR_MAX_RESTARTS_PER_WINDOW", "5")),
    sleep: overrides.sleep || sleep,
    spawnImpl: overrides.spawnImpl || spawn,
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
    state = createSupervisorState(name, moduleName, pythonExecutorConfig(options));
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
    { stdio: ["pipe", "pipe", "pipe"], env: processEnv() },
  );
  state.child = child;
  state.buffer = "";
  state.shuttingDown = false;
  attachSupervisorListeners(state);
  emitSupervisorEvent(state, state.restartCount > 0 ? "subprocess_restarted" : "subprocess_started");
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
  state.restartTimestamps = state.restartTimestamps.filter((timestamp) => timestamp >= cutoff);
}

function computeRestartDelayMs(state) {
  if (state.restartCount <= 0) return 0;
  const exponent = Math.max(0, state.restartCount - 1);
  return Math.min(
    state.config.restartBackoffCapMs,
    state.config.restartBackoffMs * (2 ** exponent),
  );
}

function attachSupervisorListeners(state) {
  const child = state.child;
  if (!child) return;
  child.stdout.on("data", (data) => handleSupervisorStdout(state, String(data)));
  child.stderr.on("data", (data) => {
    const text = String(data);
    globalThis.process.stderr.write(text);
    emitSupervisorEvent(state, "subprocess_stderr", {
      message: text.trim().slice(-1_000),
      pending_requests: state.pending.size,
    });
  });
  child.on("error", (error) => {
    handleSupervisorFailure(
      state,
      pythonExecutorError(
        "subprocess_crashed",
        error instanceof Error ? error.message : `${state.name} subprocess error`,
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
          pythonExecutorError("python_protocol_invalid", `${state.name} emitted invalid JSON`),
        );
        return;
      }
      globalThis.process.stderr.write(`[${state.name} stdout] ${line}\n`);
      return;
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

function sendSupervisorRequest(state, payload, { timeoutMs, control = null } = {}) {
  const child = state.child;
  if (!child || child.exitCode !== null || child.killed) {
    throw pythonExecutorError("subprocess_crashed", `${state.name} is not running`);
  }
  const stdin = child.stdin;
  if (!stdin || stdin.destroyed || !stdin.writable) {
    const error = pythonExecutorError("python_broken_pipe", `${state.name} stdin is unavailable`);
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

export async function persistentPythonExecutor(name, moduleName, payload, options = {}) {
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

export function jobRuntimeTelemetry(job, { startedAt, finishedAt = Date.now() } = {}) {
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
  if (job.data.render_engine === "remotion_premium" && env("REMOTION_ENABLED") === "true") {
    const { runRemotionJob } = await import("./remotion.mjs");
    return runRemotionJob(job.data, {
      onProgress: async ({ progressPct, message, eventType, metadata }) => {
        await job.updateProgress(progressPct);
        console.log(JSON.stringify({ event: eventType || "worker_progress", progress_pct: progressPct, message, metadata: metadata || {} }));
      },
    });
  }
  throw new Error("remotion_premium requires REMOTION_ENABLED=true on the Chromium worker");
}

async function runVisualJob(job) {
  if (env("ML_WORKER_ENABLED") !== "true") {
    throw new Error("visual_analysis requires the dedicated GPU/ML worker image");
  }
  await ensureUploadRecordExists(job);
  return persistentPythonExecutor("visual-ml", "app.workers.visual_job", {
    workspace_id: String(job.data.workspace_id),
    project_id: String(job.data.project_id),
    video_id: String(job.data.resource_id),
  });
}

async function runMatteJob(job) {
  if (env("ML_WORKER_ENABLED") !== "true") {
    throw new Error("matte_extraction requires the dedicated GPU/media worker image");
  }
  await ensureUploadRecordExists(job);
  return persistentPythonExecutor("matte-gpu", "app.workers.matte_job", {
    workspace_id: String(job.data.workspace_id),
    project_id: String(job.data.project_id),
    video_id: String(job.data.resource_id),
    matte_id: String(job.data.render_engine || ""),
  });
}

async function runDomainJob(job) {
  if (env("PYTHON_MEDIA_WORKER_ENABLED") !== "true") {
    throw new Error(`${job.data.kind} requires a dedicated Python media worker image`);
  }
  return persistentPythonExecutor("domain", "app.workers.domain_job", {
    job_id: String(job.id),
    kind: String(job.data.kind),
    queue_name: String(job.queueName),
    workspace_id: String(job.data.workspace_id),
    project_id: String(job.data.project_id),
    resource_id: String(job.data.resource_id),
    render_engine: String(job.data.render_engine || "ffmpeg_basic"),
  });
}

async function runRetentionJob() {
  if (!hasApiConfig()) {
    throw new Error("maintenance retention requires WORKER_API_BASE_URL and WORKER_API_TOKEN");
  }
  const response = await fetch(`${apiBaseUrl()}/internal/retention/run`, {
    method: "POST",
    headers: { "x-motionly-worker-token": workerToken() },
  });
  if (!response.ok) throw new Error(`retention endpoint returned HTTP ${response.status}`);
  return response.json();
}

async function runGpuAutoscalingJob() {
  if (!hasApiConfig()) {
    throw new Error("maintenance autoscaling requires WORKER_API_BASE_URL and WORKER_API_TOKEN");
  }
  const response = await fetch(`${apiBaseUrl()}/internal/operations/gpu-autoscaling/run`, {
    method: "POST",
    headers: { "x-motionly-worker-token": workerToken() },
  });
  if (!response.ok) throw new Error(`gpu autoscaling endpoint returned HTTP ${response.status}`);
  return response.json();
}

async function runBatchJob(job) {
  if (!hasApiConfig()) {
    throw new Error("batch planning requires WORKER_API_BASE_URL and WORKER_API_TOKEN");
  }
  const response = await fetch(
    `${apiBaseUrl()}/internal/batches/${job.data.workspace_id}/${job.data.resource_id}/run`,
    { method: "POST", headers: { "x-motionly-worker-token": workerToken() } },
  );
  if (!response.ok) throw new Error(`batch endpoint returned HTTP ${response.status}`);
  return response.json();
}

async function sendHeartbeat() {
  if (!hasApiConfig()) return;
  const response = await fetch(`${apiBaseUrl()}/internal/operations/heartbeat`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-motionly-worker-token": workerToken(),
    },
    body: JSON.stringify({
      worker_id: env("WORKER_ID", `${env("HOSTNAME", "worker")}-${process.pid}`),
      worker_role: env("PROCESS_ROLE", "worker"),
      queues: queueNames,
      version: env("APP_VERSION") || null,
      metadata: { concurrency: env("WORKER_CONCURRENCY", "1") },
    }),
  });
  if (!response.ok) throw new Error(`heartbeat endpoint returned HTTP ${response.status}`);
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
  const response = await fetch(`${apiBaseUrl()}/internal/operations/provider-slo`, {
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
  });
  if (!response.ok) throw new Error(`provider SLO endpoint returned HTTP ${response.status}`);
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

export async function ensureUploadRecordExists(job, { fetchImpl = globalThis.fetch } = {}) {
  const supabaseUrl = env("SUPABASE_URL");
  const serviceRoleKey = env("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for GPU/media queues.");
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

async function runJob(job) {
  if (job.data.status === "canceled") {
    throw new Error(`Queue job ${job.id} was canceled`);
  }

  const startedAt = Date.now();
  const coldStartMs = firstJobStarted ? null : Math.max(0, startedAt - workerStartedAt);
  firstJobStarted = true;
  console.log(
    JSON.stringify({
      event: "job_started",
      queue_job_id: job.id,
      kind: job.data.kind,
      queue_name: job.queueName,
      attempt: job.attemptsMade + 1,
      queue_wait_ms: queueWaitMs(job, startedAt),
      cold_start_ms: coldStartMs,
      trace_id: job.data.trace_id || null,
    }),
  );

  await job.updateProgress(1);
  let result;
  try {
    result =
      job.data.kind === "retention"
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
    await reportProviderSlo(job, true, Date.now() - startedAt);
  } catch (error) {
    if (error instanceof Error && error.code === "upload_not_found") {
      job.discard();
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
    await reportProviderSlo(
      job,
      false,
      Date.now() - startedAt,
      error instanceof Error ? error.code || error.name : "worker_error",
    ).catch(() => {});
    throw error;
  }
  await job.updateProgress(100);
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
    const errorCode = error instanceof Error ? error.code || error.name : "worker_error";
    const retrying = Boolean(job && job.attemptsMade < Number(job.opts?.attempts || 1));
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
    if (retrying && isSubprocessFailureCode(errorCode) && jobData.status !== "canceled") {
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
        const failureCode =
          error instanceof Error && typeof error.code === "string"
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
              callbackError instanceof Error ? callbackError.message : String(callbackError),
          }),
        );
      }
    }
  });

  worker.on("stalled", (jobId) => {
    console.error(JSON.stringify({ event: "job_stalled", queue_job_id: jobId, queue_name: queueName }));
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

  const browserPath = env("BROWSER_EXECUTABLE_PATH", env("PUPPETEER_EXECUTABLE_PATH"));
  if (browserPath && !existsSync(browserPath)) {
    throw new Error(`Configured Chromium executable was not found: ${browserPath}`);
  }
  if (isProduction() && !browserPath) {
    throw new Error(
      "REMOTION_ENABLED=true in production requires BROWSER_EXECUTABLE_PATH or PUPPETEER_EXECUTABLE_PATH.",
    );
  }
}

async function main() {
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
  if (queueNames.includes("visual_analysis") || queueNames.includes("matte_extraction")) {
    if (env("ML_WORKER_ENABLED") !== "true") {
      throw new Error("visual_analysis and matte_extraction must run with ML_WORKER_ENABLED=true.");
    }
    if (!env("SUPABASE_URL") || !env("SUPABASE_SERVICE_ROLE_KEY")) {
      throw new Error("GPU/media queues require SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.");
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
    await maintenanceQueue.add(
      "daily-retention",
      { kind: "retention", queue_name: "maintenance" },
      { jobId: "daily-retention", repeat: { pattern: env("RETENTION_CRON", "0 3 * * *") } },
    );
    await maintenanceQueue.add(
      "gpu-autoscaling",
      { kind: "gpu_autoscaling", queue_name: "maintenance" },
      {
        jobId: "gpu-autoscaling",
        repeat: { pattern: env("GPU_AUTOSCALING_CRON", "*/2 * * * *") },
      },
    );
  }
  const workers = queueNames.map((queueName) => createWorker(queueName, connection));
  await sendHeartbeat();
  console.log(
    JSON.stringify({
      event: "worker_ready",
      process_alive: true,
      redis_connected: true,
      api_reachable: hasApiConfig(),
      models_loaded: env("MODEL_PREFETCH") === "true" ? "prefetch_requested" : "not_requested",
      disk_free_bytes: diskFreeBytes(),
      queue_consumers_attached: workers.length,
      queues: queueNames,
    }),
  );
  const heartbeatTimer = setInterval(() => {
    void sendHeartbeat().catch((error) => console.error(JSON.stringify({
      event: "worker_heartbeat_failed",
      message: error instanceof Error ? error.message : String(error),
    })));
  }, Number(env("WORKER_HEARTBEAT_INTERVAL_MS", "30000")));
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

const isMainModule = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  });
}
