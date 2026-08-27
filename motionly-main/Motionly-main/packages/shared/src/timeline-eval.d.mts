export interface CanonicalSourceClip {
  source_start_ms: number;
  source_end_ms: number;
  output_start_ms: number;
  output_end_ms: number;
}

export function normalizeSourceClips<T extends CanonicalSourceClip>(
  clips: readonly T[],
): T[];

export function resolveSourceClipAtOutputMs<T extends CanonicalSourceClip>(
  outputMs: number,
  clips: readonly T[],
  edge?: "start" | "end",
): T | null;

export function outputMsToSourceMs(
  outputMs: number,
  clips: readonly CanonicalSourceClip[],
  edge?: "start" | "end",
): number;

export function outputTimelineDurationMs(
  clips: readonly CanonicalSourceClip[],
): number;
