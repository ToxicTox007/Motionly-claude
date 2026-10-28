import { renderHook } from "@testing-library/react";
import type { ProjectTimelineSourceClip } from "@motionly/shared";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePlaybackSync } from "@/components/editor/timeline/usePlaybackSync";

function mountVideo(currentTime = 0) {
  const video = document.createElement("video");
  Object.defineProperty(video, "currentTime", {
    configurable: true,
    writable: true,
    value: currentTime,
  });
  document.body.append(video);
  return video;
}

function fire(video: HTMLVideoElement, type: string) {
  // Media events do not bubble.
  video.dispatchEvent(new Event(type));
}

afterEach(() => {
  document.body.replaceChildren();
});

describe("usePlaybackSync", () => {
  it("follows timeupdate and seeked on the video", () => {
    const video = mountVideo(1.5);
    const ref = { current: video };
    const setPlayheadMs = vi.fn();
    renderHook(() => usePlaybackSync(ref, setPlayheadMs));
    fire(video, "timeupdate");
    expect(setPlayheadMs).toHaveBeenLastCalledWith(1500);
    video.currentTime = 2;
    fire(video, "seeked");
    expect(setPlayheadMs).toHaveBeenLastCalledWith(2000);
  });

  it("attaches its listeners once, not on every playhead tick", () => {
    const video = mountVideo();
    const ref = { current: video };
    const setPlayheadMs = vi.fn();
    const addSpy = vi.spyOn(document, "addEventListener");
    const videoAddSpy = vi.spyOn(video, "addEventListener");
    const { rerender } = renderHook(
      ({ playheadMs }: { playheadMs: number }) =>
        usePlaybackSync(ref, setPlayheadMs, [], playheadMs),
      { initialProps: { playheadMs: 0 } },
    );
    const initialCalls =
      addSpy.mock.calls.length + videoAddSpy.mock.calls.length;
    for (let ms = 250; ms <= 2500; ms += 250) {
      rerender({ playheadMs: ms });
    }
    expect(addSpy.mock.calls.length + videoAddSpy.mock.calls.length).toBe(
      initialCalls,
    );
  });

  it("syncs a video element that mounts after the hook", () => {
    const ref: { current: HTMLVideoElement | null } = { current: null };
    const setPlayheadMs = vi.fn();
    renderHook(() => usePlaybackSync(ref, setPlayheadMs));
    const video = mountVideo(3);
    ref.current = video;
    fire(video, "timeupdate");
    expect(setPlayheadMs).toHaveBeenLastCalledWith(3000);
  });

  it("ignores other media on the page", () => {
    const video = mountVideo(1);
    const other = mountVideo(9);
    const ref = { current: video };
    const setPlayheadMs = vi.fn();
    renderHook(() => usePlaybackSync(ref, setPlayheadMs));
    fire(other, "timeupdate");
    expect(setPlayheadMs).not.toHaveBeenCalled();
  });

  it("uses the latest source clips when jumping over a removed gap", () => {
    const video = mountVideo(0);
    const ref = { current: video };
    const setPlayheadMs = vi.fn();
    const clips = [
      {
        source_start_ms: 0,
        source_end_ms: 1000,
        output_start_ms: 0,
        output_end_ms: 1000,
      },
      {
        source_start_ms: 4000,
        source_end_ms: 6000,
        output_start_ms: 1000,
        output_end_ms: 3000,
      },
    ] as ProjectTimelineSourceClip[];
    const { rerender } = renderHook(
      ({ sourceClips, playheadMs }) =>
        usePlaybackSync(ref, setPlayheadMs, sourceClips, playheadMs),
      {
        initialProps: {
          sourceClips: [] as ProjectTimelineSourceClip[],
          playheadMs: 0,
        },
      },
    );
    rerender({ sourceClips: clips, playheadMs: 900 });
    video.currentTime = 1.05;
    fire(video, "timeupdate");
    expect(video.currentTime).toBe(4);
    expect(setPlayheadMs).toHaveBeenLastCalledWith(1000);
  });
});
