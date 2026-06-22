import type { MotionlyRenderProps, SourceTimelineClip } from "./types.js";

export function sourceTimelineClips(input: MotionlyRenderProps): SourceTimelineClip[] {
  const explicit = normalizeClips(input.timeline?.clips ?? []);
  if (explicit.length) return explicit;

  const cuts = input.editPlan.cuts.map((cut, index) => ({
    id: `cut-${index}`,
    source_start_ms: cut.start_ms,
    source_end_ms: cut.end_ms,
    output_start_ms: 0,
    output_end_ms: 0,
    reason: cut.reason,
  }));
  const normalizedCuts = normalizeClips(cuts);
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
  const clips = sourceTimelineClips(input);
  return clips[clips.length - 1]?.output_end_ms ?? 1;
}

export function outputTimeToSourceTime(
  input: MotionlyRenderProps,
  outputTimeMs: number,
): { clip: SourceTimelineClip; sourceTimeMs: number } {
  const clips = sourceTimelineClips(input);
  const clip =
    clips.find(
      (candidate) =>
        outputTimeMs >= candidate.output_start_ms &&
        outputTimeMs < candidate.output_end_ms,
    ) ?? clips[clips.length - 1];
  const localTime = Math.max(
    0,
    Math.min(
      clip.output_end_ms - clip.output_start_ms,
      outputTimeMs - clip.output_start_ms,
    ),
  );
  return { clip, sourceTimeMs: clip.source_start_ms + localTime };
}

function normalizeClips(clips: SourceTimelineClip[]): SourceTimelineClip[] {
  let outputCursor = 0;
  return [...clips]
    .filter((clip) => clip.source_end_ms > clip.source_start_ms)
    .sort((left, right) => left.output_start_ms - right.output_start_ms)
    .map((clip) => {
      const durationMs = clip.source_end_ms - clip.source_start_ms;
      const normalized = {
        ...clip,
        output_start_ms: outputCursor,
        output_end_ms: outputCursor + durationMs,
      };
      outputCursor += durationMs;
      return normalized;
    });
}
