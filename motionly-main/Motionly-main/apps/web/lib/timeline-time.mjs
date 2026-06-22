export function outputMsToSourceMs(outputMs, clips, edge = "start") {
  if (!clips.length) return Math.max(0, outputMs);
  const clip =
    clips.find((candidate) =>
      edge === "end"
        ? outputMs > candidate.output_start_ms && outputMs <= candidate.output_end_ms
        : outputMs >= candidate.output_start_ms && outputMs < candidate.output_end_ms,
    ) ?? clips[clips.length - 1];
  return Math.max(
    clip.source_start_ms,
    Math.min(
      clip.source_end_ms,
      clip.source_start_ms + outputMs - clip.output_start_ms,
    ),
  );
}
