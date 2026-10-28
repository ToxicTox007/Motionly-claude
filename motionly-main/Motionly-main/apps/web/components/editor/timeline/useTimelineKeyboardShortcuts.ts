import { useEffect, useEffectEvent } from "react";
import type { TimelineClip } from "@motionly/shared";
import { shouldIgnoreShortcut } from "@/lib/keyboard-shortcuts";

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
  // An effect event always sees the latest playhead and selection, so the
  // listener is attached once instead of on every playhead tick.
  const onKeyDown = useEffectEvent((event: KeyboardEvent) => {
    // Single keys only: typing, IME composition, open dialogs and browser
    // chords such as Ctrl+L are left alone.
    if (shouldIgnoreShortcut(event, { requireNoModifiers: true })) return;

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
  });

  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKeyDown(event);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
}
