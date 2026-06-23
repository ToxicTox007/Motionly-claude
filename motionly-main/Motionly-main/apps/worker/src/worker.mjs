import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
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

function persistentPythonExecutor(name, moduleName, payload) {
  let state = pythonExecutors.get(name);
  if (!state || state.child.exitCode !== null) {
    const child = spawn(env("PYTHON_MEDIA_COMMAND", "python"), [
      "-m", moduleName, "--serve",
    ], { stdio: ["pipe", "pipe", "pipe"], env: processEnv() });
    state = { child, nextId: 0, pending: new Map(), buffer: "" };
    pythonExecutors.set(name, state);
    child.stdout.on("data", (data) => {
      state.buffer += String(data);
      let newline;
      while ((newline = state.buffer.indexOf("\n")) >= 0) {
        const line = state.buffer.slice(0, newline).trim();
        state.buffer = state.buffer.slice(newline + 1);
        if (!line) continue;
        let response;
        try { response = JSON.parse(line); }
        catch { continue; }
        const pending = state.pending.get(response.id);
        if (!pending) continue;
        state.pending.delete(response.id);
        if (response.error) pending.reject(new Error(response.error));
        else pending.resolve(response.result);
      }
    });
    child.stderr.on("data", (data) => globalThis.process.stderr.write(data));
    const rejectPending = (error) => {
      for (const pending of state.pending.values()) pending.reject(error);
      state.pending.clear();
      pythonExecutors.delete(name);
    };
    child.on("error", rejectPending);
    child.on("close", (code) => rejectPending(new Error(`${name} exited ${code}`)));
  }
  const id = ++state.nextId;
  return new Promise((resolveJob, rejectJob) => {
    state.pending.set(id, { resolve: resolveJob, reject: rejectJob });
    state.child.stdin.write(`${JSON.stringify({ id, payload })}\n`);
  });
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
  return persistentPythonExecutor("visual-ml", "app.workers.visual_job", {
    workspace_id: String(job.data.workspace_id),
    project_id: String(job.data.project_id),
    video_id: String(job.data.resource_id),
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
  const providerKinds = new Set(["transcription", "visual_analysis", "phase5", "edit_plan", "batch_plan"]);
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

async function runJob(job) {
  if (job.data.status === "canceled") {
    throw new Error(`Queue job ${job.id} was canceled`);
  }

  console.log(
    JSON.stringify({
      event: "job_started",
      queue_job_id: job.id,
      kind: job.data.kind,
      queue_name: job.queueName,
      attempt: job.attemptsMade + 1,
      trace_id: job.data.trace_id || null,
    }),
  );

  await job.updateProgress(1);
  const startedAt = Date.now();
  let result;
  try {
    result =
      job.data.kind === "retention"
        ? await runRetentionJob()
        : job.data.kind === "batch_plan"
        ? await runBatchJob(job)
        : job.data.kind === "render"
        ? job.data.render_engine === "remotion_premium"
          ? await runRenderJob(job)
          : await runDomainJob(job)
        : job.data.kind === "visual_analysis"
          ? await runVisualJob(job)
          : await runDomainJob(job);
    await reportProviderSlo(job, true, Date.now() - startedAt);
  } catch (error) {
    await reportProviderSlo(
      job,
      false,
      Date.now() - startedAt,
      error instanceof Error ? error.name : "worker_error",
    ).catch(() => {});
    throw error;
  }
  await job.updateProgress(100);
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
    console.error(
      JSON.stringify({
        event: "job_failed",
        queue_job_id: job?.id,
        queue_name: queueName,
        kind: jobData.kind,
        attempts_made: job?.attemptsMade,
        attempts: job?.opts?.attempts,
        message: error instanceof Error ? error.message : String(error),
      }),
    );
    if (jobData.kind === "render" && jobData.resource_id) {
      try {
        const retrying = Boolean(job && job.attemptsMade < Number(job.opts?.attempts || 1));
        const { persistRenderFailureDirect } = await import("./remotion.mjs");
        await persistRenderFailureDirect(
          jobData,
          error instanceof Error ? error.message : String(error),
          retrying,
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
  if (queueNames.includes("visual_analysis")) {
    if (env("ML_WORKER_ENABLED") !== "true") {
      throw new Error("visual_analysis must run with ML_WORKER_ENABLED=true.");
    }
    if (!env("SUPABASE_URL") || !env("SUPABASE_SERVICE_ROLE_KEY")) {
      throw new Error("visual_analysis requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.");
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
  }
  const workers = queueNames.map((queueName) => createWorker(queueName, connection));
  await sendHeartbeat();
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
    for (const state of pythonExecutors.values()) state.child.stdin.end();
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

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
