import assert from "node:assert/strict";
import test from "node:test";
import { outputDurationMs, outputTimeToSourceTime, sourceTimelineClips } from "./timeline.js";
import type { MotionlyRenderProps } from "./types.js";

function props(cuts: MotionlyRenderProps["editPlan"]["cuts"]): MotionlyRenderProps {
  return {
    sourceVideo: { src: "https://media.example/source.mp4", width: 320, height: 180, duration_ms: 6000 },
    editPlan: {
      schema_version: 1,
      style: "founder",
      target_duration_s: 10,
      cuts,
      caption_highlights: [],
      broll_requests: [],
      zoom_moments: [],
      music_mood: "none",
      effects: [],
      color_grade: "clean_founder",
      risk_flags: [],
      manual_elements: [],
    },
    captions: [],
    words: [],
    assets: [],
    templateId: "founder_reel_v1",
    exportSettings: {
      width: 320,
      height: 180,
      fps: 30,
      video_codec: "h264",
      audio_codec: "aac",
      pixel_format: "yuv420p",
      format: "mp4",
    },
  };
}

test("editing the source cut list changes Remotion frame/time assembly", () => {
  const first = props([
    { start_ms: 0, end_ms: 1000, reason: "opening" },
    { start_ms: 3000, end_ms: 4000, reason: "payoff" },
  ]);
  const edited = props([
    { start_ms: 1000, end_ms: 2500, reason: "new opening" },
    { start_ms: 4500, end_ms: 6000, reason: "new payoff" },
  ]);

  assert.equal(outputDurationMs(first), 2000);
  assert.equal(outputDurationMs(edited), 3000);
  assert.equal(outputTimeToSourceTime(first, 1250).sourceTimeMs, 3250);
  assert.equal(outputTimeToSourceTime(edited, 1250).sourceTimeMs, 2250);
  assert.deepEqual(
    sourceTimelineClips(first).map((clip) => [clip.output_start_ms, clip.source_start_ms]),
    [[0, 0], [1000, 3000]],
  );
});

test("explicit immutable timeline is preferred over legacy edit-plan cuts", () => {
  const input = props([{ start_ms: 0, end_ms: 6000, reason: "legacy" }]);
  input.timeline = {
    schema_version: 1,
    duration_ms: 1200,
    clips: [
      {
        id: "cut-0",
        source_start_ms: 2400,
        source_end_ms: 3600,
        output_start_ms: 0,
        output_end_ms: 1200,
      },
    ],
  };
  assert.equal(outputDurationMs(input), 1200);
  assert.equal(outputTimeToSourceTime(input, 400).sourceTimeMs, 2800);
});
