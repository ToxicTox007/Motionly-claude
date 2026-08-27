import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  normalizeSourceClips,
  outputMsToSourceMs,
  outputTimelineDurationMs,
} from "../../../packages/shared/src/timeline-eval.mjs";

const clips = [
  {
    source_start_ms: 0,
    source_end_ms: 1000,
    output_start_ms: 0,
    output_end_ms: 1000,
  },
  {
    source_start_ms: 3000,
    source_end_ms: 4500,
    output_start_ms: 1000,
    output_end_ms: 2500,
  },
];

test("editor output playhead maps through removed source gaps", () => {
  assert.equal(outputMsToSourceMs(500, clips), 500);
  assert.equal(outputMsToSourceMs(1200, clips), 3200);

test("web preview matches the shared preview/export timing golden", async () => {
  const golden = JSON.parse(
    await readFile(
      new URL("../../../packages/shared/src/timeline-golden.json", import.meta.url),
      "utf8",
    ),
  );
  const clips = normalizeSourceClips(golden.clips);
  assert.equal(outputTimelineDurationMs(clips), golden.duration_ms);
  for (const sample of golden.samples) {
    assert.equal(
      outputMsToSourceMs(sample.output_ms, clips, sample.edge),
      sample.source_ms,
      `output ${sample.output_ms}ms (${sample.edge})`,
    );
  }
});
  assert.equal(outputMsToSourceMs(2500, clips), 4500);
});

test("clip-boundary end timing stays on the ending source clip", () => {
  assert.equal(outputMsToSourceMs(1000, clips, "start"), 3000);
  assert.equal(outputMsToSourceMs(1000, clips, "end"), 1000);
});
