// Shared GPU-stage gate for node:test and Playwright suites.
//
// A GPU-stage test only runs when RUN_GPU_STAGE_TESTS=1. Otherwise it is
// skipped with a "gpu-stage[<id>]: ..." reason that scripts/verify/skip_audit.py
// matches against tests/gpu-stage.json, so deferred checks are never silent.
import { readFileSync } from "node:fs";

const manifest = JSON.parse(
  readFileSync(new URL("../gpu-stage.json", import.meta.url), "utf8"),
);
const declared = new Set(manifest.gpu_stage.map((entry) => entry.id));

export const gpuStageEnabled = process.env.RUN_GPU_STAGE_TESTS === "1";

export function gpuStageSkip(id) {
  if (!declared.has(id)) {
    throw new Error(
      `GPU-stage id "${id}" is not declared in tests/gpu-stage.json`,
    );
  }
  return gpuStageEnabled
    ? false
    : `gpu-stage[${id}]: deferred until the GPU stage is approved`;
}
