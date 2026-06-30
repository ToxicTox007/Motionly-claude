export const DEFAULT_RENDER_FPS = 30;

export function normalizeRenderFps(fps: number | null | undefined): number {
  if (!Number.isFinite(fps) || fps === null || fps === undefined) {
    return DEFAULT_RENDER_FPS;
  }
  const normalized = Math.round(fps);
  return normalized > 0 ? normalized : DEFAULT_RENDER_FPS;
}

export function millisecondsToFrameStart(
  milliseconds: number,
  fps: number | null | undefined,
): number {
  const safeFps = normalizeRenderFps(fps);
  return Math.max(0, Math.floor((Math.max(0, milliseconds) / 1000) * safeFps));
}

export function millisecondsToFrameEnd(
  milliseconds: number,
  fps: number | null | undefined,
): number {
  const safeFps = normalizeRenderFps(fps);
  return Math.max(0, Math.ceil((Math.max(0, milliseconds) / 1000) * safeFps));
}

export function millisecondsToNearestFrame(
  milliseconds: number,
  fps: number | null | undefined,
): number {
  const safeFps = normalizeRenderFps(fps);
  return Math.max(0, Math.round((Math.max(0, milliseconds) / 1000) * safeFps));
}

export function frameToMilliseconds(
  frame: number,
  fps: number | null | undefined,
): number {
  const safeFps = normalizeRenderFps(fps);
  return Math.max(0, Math.round((Math.max(0, frame) * 1000) / safeFps));
}

export function durationMsToFrameCount(
  durationMs: number,
  fps: number | null | undefined,
): number {
  return Math.max(1, millisecondsToFrameEnd(Math.max(0, durationMs), fps));
}
