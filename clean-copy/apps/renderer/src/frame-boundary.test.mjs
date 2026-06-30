import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("renderer consumes the shared canonical frame conversion helpers", async () => {
  const sharedSource = await readFile(
    new URL("../../../packages/shared/src/timeline.ts", import.meta.url),
    "utf8",
  );
  const rendererSource = await readFile(
    new URL("./components/VideoScene.tsx", import.meta.url),
    "utf8",
  );

  assert.match(sharedSource, /DEFAULT_RENDER_FPS = 30/);
  assert.match(sharedSource, /millisecondsToFrameStart/);
  assert.match(sharedSource, /millisecondsToFrameEnd/);
  assert.match(sharedSource, /durationMsToFrameCount/);

  assert.match(rendererSource, /durationMsToFrameCount/);
  assert.match(rendererSource, /millisecondsToFrameStart/);
  assert.match(rendererSource, /millisecondsToFrameEnd/);
  assert.doesNotMatch(rendererSource, /function millisecondsToFrame\(/);
});
