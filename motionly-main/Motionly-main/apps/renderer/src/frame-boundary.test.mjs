import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  outputMsToSourceMs,
  normalizeSourceClips,
  outputTimelineDurationMs,
} from "../../../packages/shared/src/timeline-eval.mjs";

const rel = (path) => new URL(path, import.meta.url);

test("renderer consumes the shared canonical frame conversion helpers", async () => {
  const sharedSource = await readFile(rel("../../../packages/shared/src/timeline.ts"), "utf8");
  const rendererSource = await readFile(rel("./components/VideoScene.tsx"), "utf8");

  assert.match(sharedSource, /DEFAULT_RENDER_FPS = 30/);
  assert.match(sharedSource, /millisecondsToFrameStart/);
  assert.match(sharedSource, /millisecondsToFrameEnd/);
  assert.match(sharedSource, /durationMsToFrameCount/);

  assert.match(rendererSource, /durationMsToFrameCount/);
  assert.match(rendererSource, /millisecondsToFrameStart/);
  assert.match(rendererSource, /millisecondsToFrameEnd/);
  assert.doesNotMatch(rendererSource, /function millisecondsToFrame\(/);
});

test("all renderer timeline components use shared frame helpers, not ad-hoc round math", async () => {
  for (const file of [
    "./Composition.tsx",
    "./components/ManualElements.tsx",
    "./components/Captions.tsx",
  ]) {
    const source = await readFile(rel(file), "utf8");
    assert.match(source, /from "@motionly\/shared"/, `${file} should import shared helpers`);
    assert.match(source, /millisecondsToFrameStart/, `${file} should use millisecondsToFrameStart`);
    assert.doesNotMatch(
      source,
      /Math\.round\([^)]*\/\s*1000[^)]*\*\s*fps/,
      `${file} should not hand-roll ms->frame math`,
    );
  }
});

test("renderer timeline evaluator delegates to the shared canonical evaluator", async () => {
  const source = await readFile(rel("./timeline.ts"), "utf8");
  assert.match(source, /from "@motionly\/shared\/timeline-eval\.mjs"/);
  assert.match(source, /outputMsToSourceMs/);
  assert.match(source, /normalizeSourceClips/);
  // No renderer-local re-implementation of the clip walker.
  assert.doesNotMatch(source, /function normalizeClips\(/);
});

test("web preview consumes the shared canonical timeline evaluator", async () => {
  const libTimeline = await readFile(rel("../../web/lib/timeline.ts"), "utf8");
  const playbackSync = await readFile(
    rel("../../web/components/editor/timeline/usePlaybackSync.ts"),
    "utf8",
  );

  assert.match(libTimeline, /export \{ outputMsToSourceMs \} from "@motionly\/shared"/);
  assert.match(playbackSync, /resolveSourceClipAtOutputMs/);
  assert.match(playbackSync, /from "@motionly\/shared"/);
  // The browser-only boundary fudge and the inline clip walker are gone.
  assert.doesNotMatch(playbackSync, /source_end_ms - 20/);
  assert.doesNotMatch(playbackSync, /sourceClips\.find\(/);
});

test("the duplicated web timeline mapping module has been removed", async () => {
  await assert.rejects(() => readFile(rel("../../web/lib/timeline-time.mjs"), "utf8"));
});

test("canonical evaluator matches the shared preview/export timing golden", async () => {
  const golden = JSON.parse(
    await readFile(rel("../../../packages/shared/src/timeline-golden.json"), "utf8"),
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
