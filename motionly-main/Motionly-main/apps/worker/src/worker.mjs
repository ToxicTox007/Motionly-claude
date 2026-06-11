import { Worker } from "bullmq";
import {
  apiBaseUrl,
  bullConnection,
  hasApiConfig,
  hasQueueConfig,
  isProduction,
  queueNames,
  stalledIntervalMs,
  workerConcurrency,
  workerToken,
  writeBuildStamp,
} from "./queue-common.mjs";

function env(name, fallback = "") {
  return process.env[name] || fallback;
}

async function postJson(path, body) {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-motionly-worker-token": workerToken(),
    },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text.slice(0, 500) };
    }
  }
  if (!response.ok) {
    const code = payload?.error?.code || payload?.code || "worker_callback_failed";
    throw new Error(`${path} failed with ${response.status} ${code}`);
  }
  return payload;
}

async function runApiJob(job) {
  const response = await fetch(`${apiBaseUrl()}/internal/jobs/${encodeURIComponent(job.id)}/run`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-motionly-worker-token": workerToken(),
    },
  });
  const body = await response.text();
  let payload = {};
  if (body) {
    try {
      payload = JSON.parse(body);
    } catch {
      payload = { raw: body.slice(0, 500) };
    }
  }
  if (!response.ok) {
    const code = payload?.error?.code || payload?.code || "job_run_failed";
    throw new Error(`${job.id} failed with ${response.status} ${code}`);
  }
  if (payload?.result?.failed) {
    throw new Error(`${job.id} completed API run with failed result`);
  }
  return payload;
}

async function runRenderJob(job) {
  if (job.data.render_engine === "remotion_premium" && env("REMOTION_ENABLED") === "true") {
    const { runRemotionJob } = await import("./remotion.mjs");
    return runRemotionJob(job.data, {
      apiBaseUrl: apiBaseUrl(),
      workerToken: workerToken(),
      onProgress: async ({ progressPct, message, eventType, metadata }) => {
        await job.updateProgress(progressPct);
        await postJson(`/internal/renders/${job.data.resource_id}/progress`, {
          workspace_id: job.data.workspace_id,
          project_id: job.data.project_id,
          status: "rendering",
          progress_pct: progressPct,
          message,
          event_type: eventType || "worker_progress",
          metadata: metadata || {},
        });
      },
    });
  }
  return runApiJob(job);
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
    }),
  );

  await job.updateProgress(1);
  const result = job.data.kind === "render" ? await runRenderJob(job) : await runApiJob(job);
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
        await postJson(`/internal/renders/${jobData.resource_id}/failed`, {
          workspace_id: jobData.workspace_id,
          project_id: jobData.project_id,
          error_code:
            job && job.attemptsMade < Number(job.opts?.attempts || 1)
              ? "render_worker_retrying"
              : "render_worker_failed",
          error_message: error instanceof Error ? error.message : String(error),
          retrying: Boolean(job && job.attemptsMade < Number(job.opts?.attempts || 1)),
          metadata: {
            queue_job_id: job?.id,
            attempts_made: job?.attemptsMade,
            attempts: job?.opts?.attempts,
          },
        });
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
  if (!hasApiConfig()) {
    await writeBuildStamp();
    if (isProduction()) {
      throw new Error("WORKER_API_BASE_URL and WORKER_API_TOKEN are required in production.");
    }
    console.log("Worker API config is not set; build check only.");
    return;
  }

  await writeBuildStamp();
  if (mode === "--check") {
    console.log("BullMQ worker queue and API config are present.");
    return;
  }

  const connection = bullConnection();
  const workers = queueNames.map((queueName) => createWorker(queueName, connection));
  let shuttingDown = false;
  const shutdown = async (signal) => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`Received ${signal}; closing BullMQ workers.`);
    await Promise.allSettled(workers.map((worker) => worker.close()));
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
