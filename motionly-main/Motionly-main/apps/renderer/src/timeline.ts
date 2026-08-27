import {
  normalizeSourceClips,
  outputMsToSourceMs,
  outputTimelineDurationMs,
  resolveSourceClipAtOutputMs,
} from "@motionly/shared/timeline-eval.mjs";

import type { MotionlyRenderProps, SourceTimelineClip } from "./types.js";

// The renderer consumes the single canonical source-timeline evaluator from
// @motionly/shared so exported frames map to source time identically to the
// browser preview. This module only adapts render props into the clip list.

export function sourceTimelineClips(input: MotionlyRenderProps): SourceTimelineClip[] {
  const explicit = normalizeSourceClips(input.timeline?.clips ?? []);
  if (explicit.length) return explicit;

  const cuts = input.editPlan.cuts.map((cut, index) => ({
    id: `cut-${index}`,
    source_start_ms: cut.start_ms,
    source_end_ms: cut.end_ms,
    output_start_ms: 0,
    output_end_ms: 0,
    reason: cut.reason,
  }));
  const normalizedCuts = normalizeSourceClips(cuts);
  if (normalizedCuts.length) return normalizedCuts;

  return [
    {
      id: "source-full",
      source_start_ms: 0,
      source_end_ms: Math.max(1, input.sourceVideo.duration_ms),
      output_start_ms: 0,
      output_end_ms: Math.max(1, input.sourceVideo.duration_ms),
      reason: "full source fallback",
    },
  ];
}

export function outputDurationMs(input: MotionlyRenderProps): number {
  return outputTimelineDurationMs(sourceTimelineClips(input));
}

export function outputTimeToSourceTime(
  input: MotionlyRenderProps,
  outputTimeMs: number,
): { clip: SourceTimelineClip; sourceTimeMs: number } {
  const clips = sourceTimelineClips(input);
  const clip = resolveSourceClipAtOutputMs(outputTimeMs, clips) ?? clips[clips.length - 1];
  return { clip, sourceTimeMs: outputMsToSourceMs(outputTimeMs, clips) };
}
