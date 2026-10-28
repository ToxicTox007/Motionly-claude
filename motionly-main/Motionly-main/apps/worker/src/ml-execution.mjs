// Where the GPU-dependent queues run, and what a worker reports about it.
//
// ML_EXECUTION_MODE=gpu (the default): visual_analysis and matte_extraction
// run only on the dedicated ML worker image (ML_WORKER_ENABLED=true).
//
// ML_EXECUTION_MODE=test_fixture (CPU stage only): a CPU worker serves those
// queues with deterministic, labelled test fixtures. The mode refuses anything
// that would load real GPU models, encode on a GPU, or run in production, so a
// fixture can never be presented as the real capability.

export const ML_QUEUES = ["visual_analysis", "matte_extraction"];
export const ML_EXECUTION_MODES = ["gpu", "test_fixture"];

// Labels match the fixture register (fixtures/registry.json, Task 7).
const TEST_FIXTURE_CAPABILITIES = {
  visual_analysis: {
    provider: "FixtureVisualIntelligenceProvider",
    label: "Test visual analysis",
  },
  matte_extraction: { provider: "fixture-matte", label: "Test matte" },
};

function value(environment, name) {
  return String(environment[name] ?? "").trim();
}

// Settings that turn a registered fixture on (fixtures/registry.json). A
// production worker refuses to start with any of them, so a fixture can never
// stand in for the real capability there.
const PRODUCTION_FIXTURE_SWITCHES = {
  ALLOW_FIXTURE_PROVIDERS: (v) => v === "true",
  VISUAL_PROVIDER: (v) => v.toLowerCase() === "fixture",
  ML_EXECUTION_MODE: (v) => v === "test_fixture",
  MOTIONLY_DEMO_MODE: (v) => v === "true",
};

export function assertNoFixturesInProduction(environment = process.env) {
  if (value(environment, "APP_ENV") !== "production") return;
  for (const [name, enables] of Object.entries(PRODUCTION_FIXTURE_SWITCHES)) {
    if (enables(value(environment, name))) {
      throw new Error(
        `${name}=${value(environment, name)} turns on a test fixture and is not allowed in production.`,
      );
    }
  }
}

export function mlExecutionMode(environment = process.env) {
  const mode = value(environment, "ML_EXECUTION_MODE") || "gpu";
  if (!ML_EXECUTION_MODES.includes(mode)) {
    throw new Error(
      `ML_EXECUTION_MODE must be gpu or test_fixture, got ${JSON.stringify(mode)}.`,
    );
  }
  return mode;
}

// GPU-free execution of an ML queue: the cpu-vision worker image
// (CPU_VISION_ENABLED=true) serves a queue when its feature switch selects a
// CPU or remote provider. Without that switch the queue stays on the GPU image.
const CPU_VISION_QUEUE_SWITCHES = {
  visual_analysis: { setting: "VISUAL_PROVIDER", values: ["remote"] },
  matte_extraction: { setting: "MATTE_PROVIDER", values: ["local_cpu"] },
};

export function cpuVisionQueues(queueNames, environment = process.env) {
  if (value(environment, "CPU_VISION_ENABLED") !== "true") return [];
  return queueNames.filter((queue) => {
    const rule = CPU_VISION_QUEUE_SWITCHES[queue];
    return Boolean(
      rule &&
      rule.values.includes(value(environment, rule.setting).toLowerCase()),
    );
  });
}

export function mlExecutionPlan(queueNames, environment = process.env) {
  const mode = mlExecutionMode(environment);
  const mlQueues = queueNames.filter((queue) => ML_QUEUES.includes(queue));
  const gpuEncoder =
    value(environment, "FFMPEG_GPU_ENABLED") === "true" ||
    queueNames.includes("render_gpu");

  if (mode === "test_fixture") {
    if (value(environment, "APP_ENV") === "production") {
      throw new Error(
        "ML_EXECUTION_MODE=test_fixture is not allowed in production.",
      );
    }
    if (value(environment, "ML_WORKER_ENABLED") === "true") {
      throw new Error(
        "ML_EXECUTION_MODE=test_fixture cannot load GPU models; unset ML_WORKER_ENABLED.",
      );
    }
    if (gpuEncoder) {
      throw new Error(
        "ML_EXECUTION_MODE=test_fixture has no GPU encoder; do not serve render_gpu or set FFMPEG_GPU_ENABLED.",
      );
    }
    if (mlQueues.length > 0) {
      if (value(environment, "VISUAL_PROVIDER").toLowerCase() !== "fixture") {
        throw new Error(
          "ML_EXECUTION_MODE=test_fixture requires VISUAL_PROVIDER=fixture for visual_analysis and matte_extraction.",
        );
      }
      if (value(environment, "ALLOW_FIXTURE_PROVIDERS") !== "true") {
        throw new Error(
          "ML_EXECUTION_MODE=test_fixture requires ALLOW_FIXTURE_PROVIDERS=true.",
        );
      }
    }
  } else if (value(environment, "ML_WORKER_ENABLED") !== "true") {
    const cpuServed = cpuVisionQueues(mlQueues, environment);
    const gpuOnly = mlQueues.filter((queue) => !cpuServed.includes(queue));
    if (gpuOnly.length > 0) {
      throw new Error(
        "visual_analysis and matte_extraction must run with ML_WORKER_ENABLED=true " +
          `(GPU queues here: ${gpuOnly.join(", ")}), or on a cpu-vision worker ` +
          "(CPU_VISION_ENABLED=true) with a CPU provider such as MATTE_PROVIDER=local_cpu.",
      );
    }
  }

  const gpu =
    mode === "gpu" &&
    (value(environment, "ML_WORKER_ENABLED") === "true" || gpuEncoder);
  return {
    mode,
    role: gpu ? "gpu" : "cpu",
    cpuVisionQueues:
      mode === "gpu" && value(environment, "ML_WORKER_ENABLED") !== "true"
        ? cpuVisionQueues(mlQueues, environment)
        : [],
    fixtureCapabilities:
      mode === "test_fixture"
        ? mlQueues.map((queue) => ({
            queue,
            execution: "test_fixture",
            ...TEST_FIXTURE_CAPABILITIES[queue],
          }))
        : [],
  };
}

// True when this worker may execute visual_analysis / matte_extraction jobs.
export function mlJobsAllowed(environment = process.env) {
  return (
    value(environment, "ML_WORKER_ENABLED") === "true" ||
    mlExecutionMode(environment) === "test_fixture"
  );
}

// True when this worker may execute jobs from one ML queue: the GPU/ML image,
// the CPU stage's test fixtures, or a cpu-vision worker with a CPU provider.
export function mlJobAllowed(queue, environment = process.env) {
  return (
    mlJobsAllowed(environment) ||
    cpuVisionQueues([queue], environment).includes(queue)
  );
}

export function workerHeartbeatPayload(queueNames, environment = process.env) {
  const plan = mlExecutionPlan(queueNames, environment);
  const metadata = {
    concurrency: value(environment, "WORKER_CONCURRENCY") || "1",
    process_role: value(environment, "PROCESS_ROLE") || "worker",
    ml_execution_mode: plan.mode,
    fixture_capabilities: plan.fixtureCapabilities,
  };
  // Only GPU-free workers report it, so existing heartbeats are unchanged.
  if (plan.cpuVisionQueues.length > 0) {
    metadata.cpu_vision_queues = plan.cpuVisionQueues;
  }
  return {
    worker_id:
      value(environment, "WORKER_ID") ||
      `${value(environment, "HOSTNAME") || "worker"}-${process.pid}`,
    worker_role: plan.role,
    queues: queueNames,
    version: value(environment, "APP_VERSION") || null,
    metadata,
  };
}

// Repeatable maintenance jobs. The GPU capacity observer (autoscaler) belongs
// to the GPU stage, so a test_fixture worker and a GPU-free deployment
// (GPU_WORKERS_ENABLED=false, infra/docker-compose.remote-ai.yml) never
// schedule it.
export function maintenanceRepeatJobs(environment = process.env) {
  const jobs = [
    {
      name: "daily-retention",
      data: { kind: "retention", queue_name: "maintenance" },
      pattern: value(environment, "RETENTION_CRON") || "0 3 * * *",
    },
  ];
  if (
    mlExecutionMode(environment) === "gpu" &&
    value(environment, "GPU_WORKERS_ENABLED").toLowerCase() !== "false"
  ) {
    jobs.push({
      name: "gpu-autoscaling",
      data: { kind: "gpu_autoscaling", queue_name: "maintenance" },
      pattern: value(environment, "GPU_AUTOSCALING_CRON") || "*/2 * * * *",
    });
  }
  return jobs;
}
