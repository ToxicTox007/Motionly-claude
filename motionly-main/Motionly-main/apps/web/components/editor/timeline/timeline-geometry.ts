import type { TimelineClip, TimelineState } from "@motionly/shared";

/** How close (ms) a dragged edge must come to a clip edge or the playhead to snap. */
export const SNAP_THRESHOLD_MS = 150;

export type TimeRange = { startMs: number; endMs: number };

/**
 * The nearest clip edge or the playhead within `thresholdMs` of `targetMs`,
 * or `targetMs` itself when nothing is close. A clip never snaps to its own
 * edges (`ignoreClipId`).
 */
export function snapToNearestEdge(
  targetMs: number,
  {
    playheadMs,
    tracks,
    ignoreClipId,
    thresholdMs = SNAP_THRESHOLD_MS,
  }: {
    playheadMs: number;
    tracks: TimelineState["tracks"];
    ignoreClipId?: string;
    thresholdMs?: number;
  },
): number {
  let closestMs = targetMs;
  let minDiff = thresholdMs;
  const consider = (candidateMs: number) => {
    const diff = Math.abs(targetMs - candidateMs);
    if (diff < minDiff) {
      minDiff = diff;
      closestMs = candidateMs;
    }
  };

  consider(playheadMs);
  for (const track of tracks) {
    for (const clip of track.clips) {
      if (clip.id === ignoreClipId) continue;
      consider(clip.startMs);
      consider(clip.endMs);
    }
  }
  return closestMs;
}

/**
 * The part of the timeline worth rendering: what is scrolled into view, plus
 * a buffer either side (a quarter of the viewport, at least 2 s) so clips do
 * not pop in while scrolling.
 */
export function visibleTimeRange({
  scrollLeft,
  clientWidth,
  canvasWidth,
  totalDurationMs,
}: {
  scrollLeft: number;
  clientWidth: number;
  canvasWidth: number;
  totalDurationMs: number;
}): TimeRange {
  const pxToMs = totalDurationMs / (canvasWidth || 1);
  const startMs = scrollLeft * pxToMs;
  const endMs = (scrollLeft + clientWidth) * pxToMs;
  const bufferMs = Math.max(2000, clientWidth * pxToMs * 0.25);
  return {
    startMs: Math.max(0, startMs - bufferMs),
    endMs: Math.min(totalDurationMs, endMs + bufferMs),
  };
}

/**
 * Whether a clip is rendered: it overlaps the visible range, or it is the
 * selected clip (which may be mid-drag outside the range).
 */
export function isClipInRange(
  clip: Pick<TimelineClip, "id" | "startMs" | "endMs">,
  range: TimeRange,
  selectedClipId: string | null,
): boolean {
  if (clip.id === selectedClipId) return true;
  return clip.endMs >= range.startMs && clip.startMs <= range.endMs;
}
