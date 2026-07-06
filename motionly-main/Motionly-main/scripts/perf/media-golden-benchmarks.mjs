#!/usr/bin/env node

import { spawn } from "node:child_process";
import { mkdir, statfs, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { performance } from "node:perf_hooks";

const runLive = process.env.RUN_MEDIA_BENCHMARKS === "1";
const outputPath = process.env.MEDIA_BENCHMARK_OUTPUT || "test-results/media-benchmarks.json";
const scenarios = [
  {
    name: "visual_golden",
    surface: "visual",
    command: "pytest",
    args: ["apps/api/tests/test_visual_golden.py"],
  },
  {
    name: "matte_segmentation",
    surface: "matte",
    command: "pytest",
    args: ["apps/api/tests/test_segmentation_matte.py"],
  },
  {
    name: "ffmpeg_render_golden",
    surface: "ffmpeg_render_export",
    command: "pytest",
    args: ["apps/api/tests/test_golden_media_pipeline.py", "apps/api/tests/test_ffmpeg_render.py"],
  },
  {
    name: "remotion_golden",
    surface: "remotion_render",
    command: "pnpm",
    args: ["--filter", "@motionly/renderer", "test:golden"],
  },
  {
    name: "remotion_encoded",
    surface: "remotion_export",
    command: "pnpm",
    args: ["--filter", "@motionly/renderer", "test:encoded"],
  },
  {
    name: "publish_pipeline",
    surface: "publish",
    command: "pytest",
    args: ["apps/api/tests/test_publishing.py"],
  },
];

if (!runLive) {
  await writeBenchmarkOutput({
    mode: "dry_run",
    message: "Set RUN_MEDIA_BENCHMARKS=1 to execute golden media benchmarks.",
    scenarios: scenarios.map((scenario) => ({
      name: scenario.name,
      surface: scenario.surface,
      command: [scenario.command, ...scenario.args].join(" "),
    })),
  });
  console.log(`Wrote dry-run media benchmark manifest to ${outputPath}`);
  process.exit(0);
}

const results = [];
for (const scenario of scenarios) {
  results.push(await runScenario(scenario));
}

await writeBenchmarkOutput({
  mode: "live",
  generated_at: new Date().toISOString(),
  resource_samples: {
    disk_free_bytes: await diskFreeBytes("."),
    memory_rss_bytes: process.memoryUsage().rss,
    heap_used_bytes: process.memoryUsage().heapUsed,
    gpu: process.env.NVIDIA_VISIBLE_DEVICES || process.env.CUDA_VISIBLE_DEVICES || "unreported",
    cache_dir: process.env.MODEL_CACHE_DIR || process.env.MOTIONLY_MODEL_CACHE_DIR || "unreported",
  },
  results,
});

const failed = results.filter((result) => result.exit_code !== 0);
if (failed.length > 0) {
  console.error(`Media benchmark failures: ${failed.map((item) => item.name).join(", ")}`);
  process.exit(1);
}

async function runScenario(scenario) {
  const startedAt = performance.now();
  const child = spawn(scenario.command, scenario.args, {
    env: {
      ...process.env,
      RUN_RENDERER_GOLDEN_TESTS: "true",
      RUN_RENDERER_ENCODED_TESTS: "true",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => {
    stdout += chunk.toString();
  });
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
  });
  const exitCode = await new Promise((resolve) => {
    child.on("close", resolve);
  });
  return {
    name: scenario.name,
    surface: scenario.surface,
    command: [scenario.command, ...scenario.args].join(" "),
    exit_code: exitCode,
    duration_ms: Math.round(performance.now() - startedAt),
    stdout_tail: tail(stdout),
    stderr_tail: tail(stderr),
    memory_rss_bytes: process.memoryUsage().rss,
    disk_free_bytes: await diskFreeBytes("."),
  };
}

async function writeBenchmarkOutput(payload) {
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

function tail(value) {
  return value.split("\n").slice(-20).join("\n").trim();
}

async function diskFreeBytes(path) {
  const stats = await statfs(path);
  return Number(stats.bavail) * Number(stats.bsize);
}
