"use client";

import { useCallback, useState } from "react";

interface UseTimelineOptions {
  totalDurationMs: number;
  videoRef: React.RefObject<HTMLVideoElement | null>;
}

interface UseTimelineReturn {
  /** 1 = fit-to-content, 2 = 2× zoom, etc. */
  zoomLevel: number;
  zoomIn: () => void;
  zoomOut: () => void;
  fitToContent: () => void;
  /** Seek the attached video element to a timestamp in ms */
  seekTo: (ms: number) => void;
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 8;
const ZOOM_STEP = 1.5;

export function useTimeline({
  totalDurationMs: _totalDurationMs,
  videoRef,
}: UseTimelineOptions): UseTimelineReturn {
  const [zoomLevel, setZoomLevel] = useState(1);

  const zoomIn = useCallback(
    () => setZoomLevel((z) => Math.min(MAX_ZOOM, +(z * ZOOM_STEP).toFixed(2))),
    [],
  );

  const zoomOut = useCallback(
    () => setZoomLevel((z) => Math.max(MIN_ZOOM, +(z / ZOOM_STEP).toFixed(2))),
    [],
  );

  const fitToContent = useCallback(() => setZoomLevel(1), []);

  const seekTo = useCallback(
    (ms: number) => {
      const video = videoRef.current;
      if (!video) return;
      video.currentTime = ms / 1000;
    },
    [videoRef],
  );

  return { zoomLevel, zoomIn, zoomOut, fitToContent, seekTo };
}
