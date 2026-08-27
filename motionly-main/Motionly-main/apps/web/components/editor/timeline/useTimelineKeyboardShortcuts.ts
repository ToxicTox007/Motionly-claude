import { useEffect } from "react";
import type { TimelineClip } from "@motionly/shared";

type PlaybackMode = "pause" | "forward" | "backward";

export function useTimelineKeyboardShortcuts({
  playheadMs,
  selectedClip,
  togglePlayback,
  trimSelectedClip,
}: {
  playheadMs: number;
  selectedClip: TimelineClip | null;
  togglePlayback: (mode: PlaybackMode) => void;
  trimSelectedClip: (
    clip: TimelineClip,
    side: "left" | "right",
    playheadMs: number,
  ) => void;
}) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const isTyping = ["INPUT", "TEXTAREA", "SELECT"].includes(
        target?.tagName ?? "",
      );
      if (isTyping) return;

      const key = event.key.toLowerCase();
      if (key === "j") {
        event.preventDefault();
        togglePlayback("backward");
      } else if (key === "k") {
        event.preventDefault();
        togglePlayback("pause");
      } else if (key === "l") {
        event.preventDefault();
        togglePlayback("forward");
      } else if (event.key === "[") {
        event.preventDefault();
        if (selectedClip) trimSelectedClip(selectedClip, "left", playheadMs);
      } else if (event.key === "]") {
        event.preventDefault();
        if (selectedClip) trimSelectedClip(selectedClip, "right", playheadMs);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [playheadMs, selectedClip, togglePlayback, trimSelectedClip]);
}
