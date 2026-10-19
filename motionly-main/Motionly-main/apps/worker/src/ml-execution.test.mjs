import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import test from "node:test";

import {
  maintenanceRepeatJobs,
  mlExecutionPlan,
  workerHeartbeatPayload,
} from "./ml-execution.mjs";

const workerDirectory = resolve(import.meta.dirname, "..");

const cpuStage = {
  APP_ENV: "cpu-stage",
  ML_EXECUTION_MODE: "test_fixture",
  ALLOW_FIXTURE_PROVIDERS: "true",
  VISUAL_PROVIDER: "fixture",
};

test("GPU mode keeps ML queues on the dedicated ML worker image", () => {
  assert.throws(
    () => mlExecutionPlan(["visual_analysis"], { APP_ENV: "production" }),
    /visual_analysis and matte_extraction must run with ML_WORKER_ENABLED=true/,
  );
  const gpu = mlExecutionPlan(["visual_analysis", "transcription"], {
    APP_ENV: "production",
    ML_WORKER_ENABLED: "true",
  });
  assert.equal(gpu.mode, "gpu");
  assert.equal(gpu.role, "gpu");
  assert.deepEqual(gpu.fixtureCapabilities, []);
  assert.equal(
    mlExecutionPlan(["render_gpu"], { FFMPEG_GPU_ENABLED: "true" }).role,
    "gpu",
  );
  assert.equal(mlExecutionPlan(["planning"], {}).role, "cpu");
});

test("test_fixture mode serves GPU queues on a CPU worker with labelled fixtures only", () => {
  const plan = mlExecutionPlan(
    ["upload_preprocess", "visual_analysis", "matte_extraction"],
    cpuStage,
  );

  assert.equal(plan.mode, "test_fixture");
  assert.equal(plan.role, "cpu");
  assert.deepEqual(plan.fixtureCapabilities, [
    {
      queue: "visual_analysis",
      execution: "test_fixture",
      provider: "FixtureVisualIntelligenceProvider",
      label: "Test visual analysis",
    },
    {
      queue: "matte_extraction",
      execution: "test_fixture",
      provider: "fixture-matte",
      label: "Test matte",
    },
  ]);
});

test("test_fixture mode refuses anything that would load or claim a real GPU capability", () => {
  const cases = [
    [{ APP_ENV: "production" }, /not allowed in production/],
    [{ ML_WORKER_ENABLED: "true" }, /cannot load GPU models/],
    [{ FFMPEG_GPU_ENABLED: "true" }, /has no GPU encoder/],
    [{ VISUAL_PROVIDER: "local_ml" }, /requires VISUAL_PROVIDER=fixture/],
    [
      { ALLOW_FIXTURE_PROVIDERS: "false" },
      /requires ALLOW_FIXTURE_PROVIDERS=true/,
    ],
  ];
  for (const [override, message] of cases) {
    assert.throws(
      () => mlExecutionPlan(["visual_analysis"], { ...cpuStage, ...override }),
      message,
      JSON.stringify(override),
    );
  }
  assert.throws(
    () => mlExecutionPlan(["render_gpu"], cpuStage),
    /has no GPU encoder/,
  );
  assert.throws(
    () => mlExecutionPlan([], { ML_EXECUTION_MODE: "cpu" }),
    /ML_EXECUTION_MODE must be gpu or test_fixture/,
  );
});

test("heartbeats report the compute role, process role and fixture capabilities", () => {
  const payload = workerHeartbeatPayload(["visual_analysis"], {
    ...cpuStage,
    HOSTNAME: "cpu-worker-1",
    PROCESS_ROLE: "worker",
    WORKER_CONCURRENCY: "2",
    APP_VERSION: "1.2.3",
  });

  assert.equal(payload.worker_role, "cpu");
  assert.match(payload.worker_id, /^cpu-worker-1-\d+$/);
  assert.deepEqual(payload.queues, ["visual_analysis"]);
  assert.equal(payload.version, "1.2.3");
  assert.deepEqual(payload.metadata, {
    concurrency: "2",
    process_role: "worker",
    ml_execution_mode: "test_fixture",
    fixture_capabilities: [
      {
        queue: "visual_analysis",
        execution: "test_fixture",
        provider: "FixtureVisualIntelligenceProvider",
        label: "Test visual analysis",
      },
    ],
  });
});

test("worker startup enforces the ML execution mode", () => {
  const base = {
    PATH: process.env.PATH || "",
    REDIS_URL: "redis://127.0.0.1:6379/0",
    SUPABASE_URL: "https://supabase.example.invalid",
    SUPABASE_SERVICE_ROLE_KEY: "service-role-key",
    WORKER_QUEUES: "visual_analysis,matte_extraction",
  };
  const run = (environment) =>
    spawnSync(process.execPath, ["src/worker.mjs", "--check"], {
      cwd: workerDirectory,
      encoding: "utf8",
      env: { ...base, ...environment },
    });

  const fixture = run(cpuStage);
  assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);

  const leaked = run({ ...cpuStage, VISUAL_PROVIDER: "local_ml" });
  assert.notEqual(leaked.status, 0);
  assert.match(leaked.stderr, /requires VISUAL_PROVIDER=fixture/);

  const gpuWithoutModels = run({ APP_ENV: "cpu-stage" });
  assert.notEqual(gpuWithoutModels.status, 0);
  assert.match(gpuWithoutModels.stderr, /must run with ML_WORKER_ENABLED=true/);
});

test("the GPU autoscaler is scheduled only when the GPU stage is enabled", () => {
  const names = (environment) =>
    maintenanceRepeatJobs(environment).map((job) => job.name);

  assert.deepEqual(names({}), ["daily-retention", "gpu-autoscaling"]);
  assert.deepEqual(names(cpuStage), ["daily-retention"]);
  assert.deepEqual(maintenanceRepeatJobs({ RETENTION_CRON: "5 4 * * *" })[0], {
    name: "daily-retention",
    data: { kind: "retention", queue_name: "maintenance" },
    pattern: "5 4 * * *",
  });
  assert.equal(
    maintenanceRepeatJobs({ GPU_AUTOSCALING_CRON: "*/5 * * * *" })[1].pattern,
    "*/5 * * * *",
  );
});

test("a production worker refuses every setting that turns a fixture on", async () => {
  const { assertNoFixturesInProduction } = await import("./ml-execution.mjs");
  const production = { APP_ENV: "production" };

  assert.doesNotThrow(() => assertNoFixturesInProduction(production));
  for (const [name, value] of [
    ["ALLOW_FIXTURE_PROVIDERS", "true"],
    ["VISUAL_PROVIDER", "fixture"],
    ["ML_EXECUTION_MODE", "test_fixture"],
    ["MOTIONLY_DEMO_MODE", "true"],
  ]) {
    assert.throws(
      () => assertNoFixturesInProduction({ ...production, [name]: value }),
      new RegExp(name),
      name,
    );
  }
  // Outside production the switches are the CPU stage's business.
  assert.doesNotThrow(() =>
    assertNoFixturesInProduction({
      APP_ENV: "cpu-stage",
      ALLOW_FIXTURE_PROVIDERS: "true",
    }),
  );
});

test("render media allows fixture references only outside production", async () => {
  const { fixtureMediaAllowed } = await import("./remotion.mjs");

  assert.equal(fixtureMediaAllowed({ APP_ENV: "cpu-stage" }), true);
  assert.equal(fixtureMediaAllowed({ APP_ENV: "production" }), false);
  assert.equal(
    fixtureMediaAllowed({ APP_ENV: "production", MOTIONLY_DEMO_MODE: "true" }),
    false,
  );
  assert.equal(
    fixtureMediaAllowed({
      APP_ENV: "production",
      ALLOW_FIXTURE_PROVIDERS: "true",
    }),
    false,
  );
});
