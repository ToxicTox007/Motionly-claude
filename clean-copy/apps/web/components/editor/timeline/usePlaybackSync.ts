"use client";

import { useEffect } from "react";
import type { ProjectTimelineSourceClip } from "@motionly/shared";

/**
 * Keeps a numeric `playheadMs` state in sync with a <video> element.
 * Fires `setPlayheadMs` on every `timeupdate` and `seeked` event so the
 * timeline playhead moves in real time during playback and after scrubbing.
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
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    function sync() {
      const sourceMs = Math.round((videoRef.current?.currentTime ?? 0) * 1000);
      if (!sourceClips.length) {
        setPlayheadMs(sourceMs);
        return;
      }
      const currentClip =
        sourceClips.find(
          (clip) =>
            playheadMs >= clip.output_start_ms &&
            playheadMs < clip.output_end_ms,
        ) ?? sourceClips[0];
      if (sourceMs >= currentClip.source_end_ms - 20) {
        const nextIndex = sourceClips.indexOf(currentClip) + 1;
        const next = sourceClips[nextIndex];
        if (!next) {
          videoRef.current?.pause();
          setPlayheadMs(currentClip.output_end_ms);
          return;
        }
        if (videoRef.current) videoRef.current.currentTime = next.source_start_ms / 1000;
        setPlayheadMs(next.output_start_ms);
        return;
      }
      setPlayheadMs(
        currentClip.output_start_ms +
          Math.max(0, sourceMs - currentClip.source_start_ms),
      );
    }

    video.addEventListener("timeupdate", sync);
    video.addEventListener("seeked", sync);
    video.addEventListener("play", sync);

    return () => {
      video.removeEventListener("timeupdate", sync);
      video.removeEventListener("seeked", sync);
      video.removeEventListener("play", sync);
    };
  }, [playheadMs, setPlayheadMs, sourceClips, videoRef]);
}
