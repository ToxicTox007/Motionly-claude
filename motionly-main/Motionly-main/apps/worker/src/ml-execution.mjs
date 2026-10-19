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
  } else if (
    mlQueues.length > 0 &&
    value(environment, "ML_WORKER_ENABLED") !== "true"
  ) {
    throw new Error(
      "visual_analysis and matte_extraction must run with ML_WORKER_ENABLED=true.",
    );
  }

  const gpu =
    mode === "gpu" &&
    (value(environment, "ML_WORKER_ENABLED") === "true" || gpuEncoder);
  return {
    mode,
    role: gpu ? "gpu" : "cpu",
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

export function workerHeartbeatPayload(queueNames, environment = process.env) {
  const plan = mlExecutionPlan(queueNames, environment);
  return {
    worker_id:
      value(environment, "WORKER_ID") ||
      `${value(environment, "HOSTNAME") || "worker"}-${process.pid}`,
    worker_role: plan.role,
    queues: queueNames,
    version: value(environment, "APP_VERSION") || null,
    metadata: {
      concurrency: value(environment, "WORKER_CONCURRENCY") || "1",
      process_role: value(environment, "PROCESS_ROLE") || "worker",
      ml_execution_mode: plan.mode,
      fixture_capabilities: plan.fixtureCapabilities,
    },
  };
}

// Repeatable maintenance jobs. The GPU capacity observer (autoscaler) belongs
// to the GPU stage, so a test_fixture worker never schedules it.
export function maintenanceRepeatJobs(environment = process.env) {
  const jobs = [
    {
      name: "daily-retention",
      data: { kind: "retention", queue_name: "maintenance" },
      pattern: value(environment, "RETENTION_CRON") || "0 3 * * *",
    },
  ];
  if (mlExecutionMode(environment) === "gpu") {
    jobs.push({
      name: "gpu-autoscaling",
      data: { kind: "gpu_autoscaling", queue_name: "maintenance" },
      pattern: value(environment, "GPU_AUTOSCALING_CRON") || "*/2 * * * *",
    });
  }
  return jobs;
}
