// Canonical source-timeline evaluator.
//
// This is the SINGLE implementation of how an output/playback time maps back to
// a position in the source media, shared by the browser preview and the export
// renderer so both agree frame-for-frame. It is authored as plain ESM so it can
// be consumed by the TypeScript renderer, the Next.js web app, and `node --test`
// suites alike (types live in ./timeline-eval.d.mts).

export function normalizeSourceClips(clips) {
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

export function resolveSourceClipAtOutputMs(outputMs, clips, edge = "start") {
  if (!clips.length) return null;
  const found = clips.find((clip) =>
    edge === "end"
      ? outputMs > clip.output_start_ms && outputMs <= clip.output_end_ms
      : outputMs >= clip.output_start_ms && outputMs < clip.output_end_ms,
  );
  return found ?? clips[clips.length - 1];
}

export function outputMsToSourceMs(outputMs, clips, edge = "start") {
  if (!clips.length) return Math.max(0, outputMs);
  const clip = resolveSourceClipAtOutputMs(outputMs, clips, edge);
  return Math.max(
    clip.source_start_ms,
    Math.min(
      clip.source_end_ms,
      clip.source_start_ms + outputMs - clip.output_start_ms,
    ),
  );
}

export function outputTimelineDurationMs(clips) {
  if (!clips.length) return 1;
  return clips[clips.length - 1].output_end_ms;
}
