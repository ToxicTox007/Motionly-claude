"use client";

import React, { useEffect, useRef } from "react";
import { useDroppable } from "@dnd-kit/core";
import type { TimelineClip } from "@motionly/shared";
import { cn } from "@/lib/utils";
import { formatMs } from "@/lib/timeline";

export type ReorderDirection = -1 | 1;

export const MIN_CLIP_MS = 300;
const REORDER_THRESHOLD_MS = 120;
/** A pointer move shorter than this is a click, not a drag. */
const MOVE_DEAD_ZONE_MS = 80;
/** Snap to a nearby boundary when the pointer is this close to it. */
const SNAP_RANGE_MS = 150;
const KEYBOARD_NUDGE_MS = 500;
/** Arrow-key nudges save once: on key release, or after this long idle. */
export const NUDGE_COMMIT_DELAY_MS = 400;

type DragPreview = { clipId: string; startMs: number; endMs: number };

export interface ClipBlockProps {
  clip: TimelineClip;
  pct: (ms: number) => string;
  selected: boolean;
  onSelect: (id: string) => void;
  onTrim: (clip: TimelineClip, side: "left" | "right", nextMs: number) => void;
  onMove: (clip: TimelineClip, nextStartMs: number) => void;
  onReorder: (clip: TimelineClip, direction: ReorderDirection) => void;
  msFromPointer: (clientX: number) => number;
  trimDisabled: boolean;
  moveDisabled: boolean;
  reorderDisabled: boolean;
  getSnappedMs: (targetMs: number, ignoreClipId?: string) => number;
  setSnapMarkerMs: (ms: number | null) => void;
  setDragPreview: (preview: DragPreview | null) => void;
}

type DragState =
  | { kind: "trim"; side: "left" | "right"; pointerId: number }
  | {
      kind: "move";
      pointerId: number;
      startPointerMs: number;
      startClipMs: number;
    };

type PendingNudge = {
  /** The clip as it was when nudging started (the history "from" state). */
  clip: TimelineClip;
  startMs: number;
  timer: number;
};

/**
 * One clip on the timeline. Drags use pointer capture on the pressed element
 * instead of window listeners: a drag ends only on that pointer's pointerup,
 * and a pointercancel (the browser taking a touch for scrolling) or a lost
 * capture throws the preview away without saving. Arrow-key nudges move a
 * local preview and save once.
 */
export const ClipBlock = React.memo(
  function ClipBlock({
    clip,
    pct,
    selected,
    onSelect,
    onTrim,
    onMove,
    onReorder,
    msFromPointer,
    trimDisabled,
    moveDisabled,
    reorderDisabled,
    getSnappedMs,
    setSnapMarkerMs,
    setDragPreview,
  }: ClipBlockProps) {
    const widthMs = Math.max(clip.endMs - clip.startMs, 0);
    const { setNodeRef: setDropRef, isOver } = useDroppable({
      id: `clip:${clip.id}`,
      data: { zone: "clip", clip },
    });
    const dragRef = useRef<DragState | null>(null);
    const nudgeRef = useRef<PendingNudge | null>(null);
    // The memo comparator below ignores callback identity, so read the latest
    // onMove when a delayed nudge saves.
    const onMoveRef = useRef(onMove);
    useEffect(() => {
      onMoveRef.current = onMove;
    }, [onMove]);

    function clearDragPreview() {
      setSnapMarkerMs(null);
      setDragPreview(null);
    }

    function commitNudge() {
      const nudge = nudgeRef.current;
      if (!nudge) return;
      window.clearTimeout(nudge.timer);
      nudgeRef.current = null;
      setDragPreview(null);
      onMoveRef.current(nudge.clip, nudge.startMs);
    }

    // Unmounting mid-gesture: drop a drag's preview, but keep a nudge the
    // user already made.
    useEffect(
      () => () => {
        if (dragRef.current) {
          dragRef.current = null;
          setSnapMarkerMs(null);
          setDragPreview(null);
        }
        const nudge = nudgeRef.current;
        if (nudge) {
          window.clearTimeout(nudge.timer);
          nudgeRef.current = null;
          setDragPreview(null);
          onMoveRef.current(nudge.clip, nudge.startMs);
        }
      },
      [setDragPreview, setSnapMarkerMs],
    );

    function capturePointer(event: React.PointerEvent<HTMLElement>) {
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // The pointer is already gone (released before the handler ran).
      }
    }

    function startTrim(
      side: "left" | "right",
      event: React.PointerEvent<HTMLDivElement>,
    ) {
      event.stopPropagation();
      if (trimDisabled) return;
      commitNudge();
      capturePointer(event);
      dragRef.current = { kind: "trim", side, pointerId: event.pointerId };
    }

    function startMove(event: React.PointerEvent<HTMLDivElement>) {
      event.stopPropagation();
      onSelect(clip.id);
      commitNudge();
      capturePointer(event);
      dragRef.current = {
        kind: "move",
        pointerId: event.pointerId,
        startPointerMs: msFromPointer(event.clientX),
        startClipMs: clip.startMs,
      };
    }

    function activeDrag(event: React.PointerEvent) {
      const drag = dragRef.current;
      return drag && drag.pointerId === event.pointerId ? drag : null;
    }

    function previewSnap(nextMs: number) {
      const snapped = getSnappedMs(nextMs, clip.id);
      const near = Math.abs(snapped - nextMs) <= SNAP_RANGE_MS;
      setSnapMarkerMs(near ? snapped : null);
      return near ? snapped : nextMs;
    }

    function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
      const drag = activeDrag(event);
      if (!drag) return;
      if (drag.kind === "trim") {
        const previewMs = previewSnap(msFromPointer(event.clientX));
        setDragPreview({
          clipId: clip.id,
          startMs:
            drag.side === "left"
              ? Math.min(previewMs, clip.endMs - MIN_CLIP_MS)
              : clip.startMs,
          endMs:
            drag.side === "right"
              ? Math.max(previewMs, clip.startMs + MIN_CLIP_MS)
              : clip.endMs,
        });
        return;
      }
      const deltaMs = msFromPointer(event.clientX) - drag.startPointerMs;
      const previewStart = previewSnap(drag.startClipMs + deltaMs);
      setDragPreview({
        clipId: clip.id,
        startMs: previewStart,
        endMs: previewStart + (clip.endMs - clip.startMs),
      });
    }

    function handlePointerUp(event: React.PointerEvent<HTMLDivElement>) {
      const drag = activeDrag(event);
      if (!drag) return;
      dragRef.current = null;
      clearDragPreview();

      if (drag.kind === "trim") {
        const finalMs = getSnappedMs(msFromPointer(event.clientX), clip.id);
        onTrim(clip, drag.side, finalMs);
        return;
      }

      const deltaMs = msFromPointer(event.clientX) - drag.startPointerMs;
      if (Math.abs(deltaMs) < MOVE_DEAD_ZONE_MS) return;
      const finalMs = getSnappedMs(drag.startClipMs + deltaMs, clip.id);
      if (!moveDisabled) {
        onMove(clip, finalMs);
        return;
      }
      if (!reorderDisabled) {
        if (deltaMs > REORDER_THRESHOLD_MS) {
          onReorder(clip, 1);
        } else if (deltaMs < -REORDER_THRESHOLD_MS) {
          onReorder(clip, -1);
        }
      }
    }

    // pointercancel and lostpointercapture: the gesture was taken away (a
    // scroll, a system gesture, the element went away). Never save it.
    function abandonDrag(event: React.PointerEvent<HTMLDivElement>) {
      if (!activeDrag(event)) return;
      dragRef.current = null;
      clearDragPreview();
    }

    function nudge(direction: ReorderDirection) {
      const pending = nudgeRef.current;
      const base = pending?.clip ?? clip;
      const startMs = Math.max(
        0,
        (pending?.startMs ?? clip.startMs) + direction * KEYBOARD_NUDGE_MS,
      );
      if (pending) window.clearTimeout(pending.timer);
      nudgeRef.current = {
        clip: base,
        startMs,
        timer: window.setTimeout(commitNudge, NUDGE_COMMIT_DELAY_MS),
      };
      setDragPreview({
        clipId: clip.id,
        startMs,
        endMs: startMs + (base.endMs - base.startMs),
      });
    }

    function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onSelect(clip.id);
      } else if (event.key === "ArrowLeft" && !moveDisabled) {
        event.preventDefault();
        nudge(-1);
      } else if (event.key === "ArrowRight" && !moveDisabled) {
        event.preventDefault();
        nudge(1);
      } else if (
        event.key === "ArrowLeft" &&
        !reorderDisabled &&
        moveDisabled
      ) {
        event.preventDefault();
        onReorder(clip, -1);
      } else if (
        event.key === "ArrowRight" &&
        !reorderDisabled &&
        moveDisabled
      ) {
        event.preventDefault();
        onReorder(clip, 1);
      }
    }

    function onKeyUp(event: React.KeyboardEvent<HTMLDivElement>) {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        commitNudge();
      }
    }

    return (
      <div
        ref={setDropRef}
        role="button"
        tabIndex={0}
        aria-pressed={selected}
        aria-label={`${clip.trackKind} clip ${clip.label}. Starts ${formatMs(clip.startMs)}, ends ${formatMs(clip.endMs)}, duration ${formatMs(widthMs)}.`}
        className={cn(
          "group absolute bottom-1 top-1 cursor-pointer overflow-hidden rounded-neu",
          "flex items-center gap-2 px-2 text-2xs font-semibold leading-none text-white",
          "shadow-neu-raised-sm ring-inset transition-all duration-100",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-1 focus-visible:ring-offset-background",
          clip.colorClass,
          clip.disabled && "opacity-30",
          selected
            ? "z-30 shadow-neu-glow ring-2 ring-primary"
            : "ring-0 hover:brightness-110",
          isOver && "z-20 ring-2 ring-primary brightness-125",
        )}
        style={{
          left: pct(clip.startMs),
          width: `max(3.75rem, ${pct(widthMs)})`,
        }}
        onPointerDown={startMove}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={abandonDrag}
        onLostPointerCapture={abandonDrag}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        onBlur={commitNudge}
        onClick={(event) => {
          event.stopPropagation();
          onSelect(clip.id);
        }}
        title={`${clip.label} | ${formatMs(clip.startMs)} - ${formatMs(clip.endMs)}${
          moveDisabled
            ? reorderDisabled
              ? " | Use toolbar actions"
              : " | Drag left/right to reorder"
            : " | Drag to move"
        }`}
      >
        <TrimHandle
          side="left"
          selected={selected}
          disabled={trimDisabled}
          onPointerDown={(event) => startTrim("left", event)}
        />
        <span className="min-w-0 flex-1 truncate">{clip.label}</span>
        <span className="hidden shrink-0 rounded-full bg-ink/40 px-1.5 font-mono text-2xs tabular-nums sm:inline">
          {formatMs(widthMs)}
        </span>
        <TrimHandle
          side="right"
          selected={selected}
          disabled={trimDisabled}
          onPointerDown={(event) => startTrim("right", event)}
        />
      </div>
    );
  },
  (prev, next) => {
    return (
      prev.selected === next.selected &&
      prev.clip.id === next.clip.id &&
      prev.clip.startMs === next.clip.startMs &&
      prev.clip.endMs === next.clip.endMs &&
      prev.clip.label === next.clip.label &&
      prev.trimDisabled === next.trimDisabled &&
      prev.moveDisabled === next.moveDisabled &&
      prev.reorderDisabled === next.reorderDisabled
    );
  },
);

function TrimHandle({
  side,
  selected,
  disabled,
  onPointerDown,
}: {
  side: "left" | "right";
  selected: boolean;
  disabled: boolean;
  onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      className={cn(
        // touch-none: a drag that starts on a handle is a trim, never a page
        // scroll, so the browser does not cancel it mid-gesture.
        "absolute bottom-0 top-0 z-20 flex w-5 touch-none items-center justify-center transition",
        disabled
          ? "cursor-not-allowed opacity-0 pointer-events-none"
          : cn(
              "cursor-ew-resize opacity-70 hover:opacity-100 group-hover:opacity-100 active:opacity-100",
              selected ? "opacity-100" : "sm:opacity-0",
            ),
        side === "left" ? "left-0 -translate-x-2" : "right-0 translate-x-2",
      )}
      title={disabled ? "Edit in Audio" : "Trim clip"}
      onPointerDown={onPointerDown}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="h-7 w-2 rounded-full bg-white shadow-neu-raised-sm" />
    </div>
  );
}
