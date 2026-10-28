import type { TimelineClip, TimelineState } from "@motionly/shared";
import { describe, expect, it } from "vitest";
import {
  SNAP_THRESHOLD_MS,
  isClipInRange,
  snapToNearestEdge,
  visibleTimeRange,
} from "@/components/editor/timeline/timeline-geometry";

// Snapping and timeline virtualisation, as behaviour (replaces the
// final-qa.test.mjs regexes that only pinned the hook names).

function clip(id: string, startMs: number, endMs: number) {
  return { id, startMs, endMs } as TimelineClip;
}

const tracks = [
  { clips: [clip("a", 0, 2_000), clip("b", 5_000, 8_000)] },
  { clips: [clip("c", 3_000, 4_000)] },
] as TimelineState["tracks"];

describe("snapToNearestEdge", () => {
  it("snaps to the nearest clip edge within the threshold", () => {
    expect(snapToNearestEdge(2_100, { playheadMs: 10_000, tracks })).toBe(
      2_000,
    );
    expect(snapToNearestEdge(4_940, { playheadMs: 10_000, tracks })).toBe(
      5_000,
    );
    // Start and end edges both count, on every track.
    expect(snapToNearestEdge(3_950, { playheadMs: 10_000, tracks })).toBe(
      4_000,
    );
  });

  it("snaps to the playhead", () => {
    expect(snapToNearestEdge(6_420, { playheadMs: 6_500, tracks })).toBe(6_500);
  });

  it("leaves a time beyond the threshold alone", () => {
    const far = 2_000 + SNAP_THRESHOLD_MS + 1;
    expect(snapToNearestEdge(far, { playheadMs: 10_000, tracks })).toBe(far);
  });

  it("never snaps a clip to its own edges", () => {
    expect(
      snapToNearestEdge(2_050, {
        playheadMs: 10_000,
        tracks,
        ignoreClipId: "a",
      }),
    ).toBe(2_050);
  });

  it("prefers the closest candidate", () => {
    // 2,000 (clip a end) is 90 ms away, the playhead 40 ms.
    expect(snapToNearestEdge(2_090, { playheadMs: 2_130, tracks })).toBe(2_130);
  });
});

describe("visibleTimeRange", () => {
  it("covers what is scrolled into view plus a buffer either side", () => {
    // 60 s over 6,000 px: 10 ms per px. Viewport 1,000 px from 2,000 px.
    expect(
      visibleTimeRange({
        scrollLeft: 2_000,
        clientWidth: 1_000,
        canvasWidth: 6_000,
        totalDurationMs: 60_000,
      }),
    ).toEqual({ startMs: 17_500, endMs: 32_500 });
  });

  it("buffers at least 2 s and stays inside the timeline", () => {
    expect(
      visibleTimeRange({
        scrollLeft: 0,
        clientWidth: 100,
        canvasWidth: 1_000,
        totalDurationMs: 10_000,
      }),
    ).toEqual({ startMs: 0, endMs: 3_000 });
  });

  it("does not divide by zero before the canvas has a width", () => {
    const range = visibleTimeRange({
      scrollLeft: 0,
      clientWidth: 0,
      canvasWidth: 0,
      totalDurationMs: 5_000,
    });
    expect(Number.isFinite(range.endMs)).toBe(true);
  });
});

describe("isClipInRange", () => {
  const range = { startMs: 10_000, endMs: 20_000 };

  it("renders clips that overlap the range, including at its edges", () => {
    expect(isClipInRange(clip("x", 9_000, 10_000), range, null)).toBe(true);
    expect(isClipInRange(clip("x", 15_000, 16_000), range, null)).toBe(true);
    expect(isClipInRange(clip("x", 20_000, 21_000), range, null)).toBe(true);
  });

  it("skips clips entirely outside it", () => {
    expect(isClipInRange(clip("x", 0, 9_999), range, null)).toBe(false);
    expect(isClipInRange(clip("x", 20_001, 25_000), range, null)).toBe(false);
  });

  it("always renders the selected clip", () => {
    expect(isClipInRange(clip("sel", 0, 1_000), range, "sel")).toBe(true);
  });
});
