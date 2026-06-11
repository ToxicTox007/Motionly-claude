const queueIndexKey = "motionly:queue:jobs";

function env(name, fallback = "") {
  return process.env[name] || fallback;
}

function isProduction() {
  return env("APP_ENV") === "production";
}

function apiBaseUrl() {
  return env("WORKER_API_BASE_URL", env("API_PUBLIC_BASE_URL")).replace(/\/$/, "");
}

function workerToken() {
  return env("WORKER_API_TOKEN", env("ADMIN_API_TOKEN"));
}

function pollIntervalMs() {
  const parsed = Number.parseInt(env("WORKER_POLL_INTERVAL_MS", "5000"), 10);
  return Number.isFinite(parsed) && parsed >= 1000 ? parsed : 5000;
}

function batchSize() {
  const parsed = Number.parseInt(env("WORKER_BATCH_SIZE", "3"), 10);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : 3;
}

function hasQueueConfig() {
  return Boolean(env("UPSTASH_REDIS_REST_URL") && env("UPSTASH_REDIS_REST_TOKEN"));
}

function hasApiConfig() {
  return Boolean(apiBaseUrl() && workerToken());
}

async function upstashCommand(...parts) {
  const restUrl = env("UPSTASH_REDIS_REST_URL").replace(/\/$/, "");
  const token = env("UPSTASH_REDIS_REST_TOKEN");
  if (!restUrl || !token) {
    throw new Error("UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are required");
  }

  const response = await fetch(`${restUrl}/pipeline`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify([parts]),
  });
  if (!response.ok) {
    throw new Error(`Upstash command failed: ${response.status}`);
  }
  const payload = await response.json();
  return Array.isArray(payload) && payload[0] ? payload[0].result : null;
}

async function listRunnableJobs() {
  if (!env("UPSTASH_REDIS_REST_URL")) {
    return [];
  }
  const ids = await upstashCommand("SMEMBERS", queueIndexKey);
  if (!Array.isArray(ids)) {
    return [];
  }
  const jobs = [];
  for (const id of ids.sort()) {
    const raw = await upstashCommand("GET", `motionly:queue:job:${id}`);
    if (!raw) continue;
    const job = JSON.parse(raw);
    if (job.status === "queued") {
      jobs.push(job);
    }
  }
  return jobs.sort((left, right) => {
    const priorityDelta = Number(right.priority || 0) - Number(left.priority || 0);
    if (priorityDelta !== 0) return priorityDelta;
    return String(left.enqueued_at || "").localeCompare(String(right.enqueued_at || ""));
  });
}

async function runJob(job) {
  // Route to the Remotion renderer when REMOTION_ENABLED=true and the job
  // was enqueued with render_engine="remotion_premium".
  if (
    job.render_engine === "remotion_premium" &&
    env("REMOTION_ENABLED") === "true"
  ) {
    const { runRemotionJob } = await import("./remotion.mjs");
    return runRemotionJob(job, { apiBaseUrl: apiBaseUrl(), workerToken: workerToken() });
  }
  // Default: delegate to the FastAPI /internal/jobs/{id}/run endpoint (FFmpeg path).
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
  return payload;
}

async function processBatch() {
  const jobs = (await listRunnableJobs()).slice(0, batchSize());
  if (jobs.length === 0) {
    return 0;
  }

  let completed = 0;
  for (const job of jobs) {
    try {
      const result = await runJob(job);
      completed += 1;
      console.log(
        JSON.stringify({
          event: "job_completed",
          queue_job_id: job.id,
          kind: job.kind,
          status: result.status,
        }),
      );
    } catch (error) {
      console.error(
        JSON.stringify({
          event: "job_failed",
          queue_job_id: job.id,
          kind: job.kind,
          message: error instanceof Error ? error.message : String(error),
        }),
      );
    }
  }
  return completed;
}

async function main() {
  const mode = process.argv[2] || (isProduction() ? "--loop" : "--check");
  if (!["--check", "--once", "--loop"].includes(mode)) {
    throw new Error("Usage: node src/worker.mjs [--check|--once|--loop]");
  }

  if (!hasQueueConfig()) {
    await writeBuildStamp();
    if (isProduction()) {
      throw new Error("Worker queue config is required in production.");
    }
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

  if (mode === "--check") {
    await writeBuildStamp();
    console.log("Worker queue and API config are present.");
    return;
  }

  if (mode === "--once") {
    const processed = await processBatch();
    console.log(`Worker processed ${processed} queued job(s).`);
    return;
  }

  await writeBuildStamp();
  console.log(`Motionly worker started; polling every ${pollIntervalMs()}ms.`);
  let shuttingDown = false;
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      shuttingDown = true;
      console.log(`Received ${signal}; worker will stop after the current poll.`);
    });
  }

  while (!shuttingDown) {
    await processBatch();
    await sleep(pollIntervalMs());
  }
}

async function writeBuildStamp() {
  const fs = await import("node:fs/promises");
  await fs.mkdir(new URL("../dist/", import.meta.url), { recursive: true });
  await fs.writeFile(
    new URL("../dist/worker-check.json", import.meta.url),
    JSON.stringify({ checked_at: new Date().toISOString() }),
  );
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
