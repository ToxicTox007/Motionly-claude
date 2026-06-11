"use client";

import { useEffect } from "react";

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
): void {
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    function sync() {
      setPlayheadMs(Math.round((videoRef.current?.currentTime ?? 0) * 1000));
    }

    video.addEventListener("timeupdate", sync);
    video.addEventListener("seeked", sync);
    video.addEventListener("play", sync);

    return () => {
      video.removeEventListener("timeupdate", sync);
      video.removeEventListener("seeked", sync);
      video.removeEventListener("play", sync);
    };
  }, [videoRef, setPlayheadMs]);
}
