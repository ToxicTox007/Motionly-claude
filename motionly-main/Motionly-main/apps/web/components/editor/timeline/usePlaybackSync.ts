"use client";

import { useEffect, useEffectEvent } from "react";
import { resolveSourceClipAtOutputMs } from "@motionly/shared";
import type { ProjectTimelineSourceClip } from "@motionly/shared";

const SYNC_EVENTS = ["timeupdate", "seeked", "play"] as const;

/**
 * Keeps a numeric `playheadMs` state in sync with a <video> element.
 * Fires `setPlayheadMs` on every `timeupdate` and `seeked` event so the
 * timeline playhead moves in real time during playback and after scrubbing.
 *
 * Clip resolution uses the shared canonical source-timeline evaluator so the
 * preview playhead advances through removed source gaps exactly as the export
 * renderer assembles them (no browser-only boundary fudge).
 *
 * The listeners are attached once. Media events do not bubble, but they do
 * pass through the document in the capture phase, so one capturing listener
 * there follows whichever element `videoRef` points at, including one that
 * mounts later or is swapped for a new source, without re-subscribing on
 * every playhead tick. The latest clips and playhead are read through an
 * effect event.
 *
 * Usage:
 *   const [playheadMs, setPlayheadMs] = useState(0);
 *   usePlaybackSync(videoRef, setPlayheadMs);
 */
export function usePlaybackSync(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  setPlayheadMs: (ms: number) => void,
  sourceClips: ProjectTimelineSourceClip[] = [],
  playheadMs = 0,
): void {
  const sync = useEffectEvent((video: HTMLVideoElement) => {
    const sourceMs = Math.round((video.currentTime ?? 0) * 1000);
    if (!sourceClips.length) {
      setPlayheadMs(sourceMs);
      return;
    }
    const currentClip =
      resolveSourceClipAtOutputMs(playheadMs, sourceClips) ?? sourceClips[0];
    if (sourceMs >= currentClip.source_end_ms) {
      const nextIndex = sourceClips.indexOf(currentClip) + 1;
      const next = sourceClips[nextIndex];
      if (!next) {
        video.pause();
        setPlayheadMs(currentClip.output_end_ms);
        return;
      }
      video.currentTime = next.source_start_ms / 1000;
      setPlayheadMs(next.output_start_ms);
      return;
    }
    setPlayheadMs(
      currentClip.output_start_ms +
        Math.max(0, sourceMs - currentClip.source_start_ms),
    );
  });

  useEffect(() => {
    const onMediaEvent = (event: Event) => {
      const video = videoRef.current;
      if (video && event.target === video) sync(video);
    };
    for (const type of SYNC_EVENTS) {
      document.addEventListener(type, onMediaEvent, true);
    }
    return () => {
      for (const type of SYNC_EVENTS) {
        document.removeEventListener(type, onMediaEvent, true);
      }
    };
  }, [videoRef]);
}
