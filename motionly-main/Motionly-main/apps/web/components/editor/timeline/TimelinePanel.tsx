"use client";

import React, { useCallback, useRef } from "react";
import { ZoomIn, ZoomOut, Maximize2, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { rulerTicks, formatMs } from "@/lib/timeline";
import { useTimeline } from "./useTimeline";
import { usePlaybackSync } from "./usePlaybackSync";
import type { TimelineClip, TimelineState } from "@motionly/shared";

interface TimelinePanelProps {
  state: TimelineState;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  playheadMs: number;
  setPlayheadMs: (ms: number) => void;
  selectedClipId: string | null;
  onClipSelect: (clipId: string | null) => void;
}

const TRACK_HEIGHT = 32; // px per track row
const RULER_HEIGHT = 24; // px

export function TimelinePanel({
  state,
  videoRef,
  playheadMs,
  setPlayheadMs,
  selectedClipId,
  onClipSelect,
}: TimelinePanelProps) {
  const { totalDurationMs, tracks } = state;
  const scrollRef = useRef<HTMLDivElement>(null);

  const { zoomLevel, zoomIn, zoomOut, fitToContent, seekTo } = useTimeline({
    totalDurationMs,
    videoRef,
  });

  // Keep playheadMs in sync with the video element
  usePlaybackSync(videoRef, setPlayheadMs);

  // Convert ms to a percentage of the total duration (for CSS left/width)
  const pct = useCallback(
    (ms: number) => `${((ms / Math.max(totalDurationMs, 1)) * 100).toFixed(4)}%`,
    [totalDurationMs],
  );

  // Ruler click → seek
  function handleRulerPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const relX = e.clientX - rect.left;
    const ms = Math.round((relX / rect.width) * totalDurationMs);
    seekTo(ms);
    setPlayheadMs(ms);
  }

  // Deselect clip when clicking the empty track area
  function handleTrackAreaClick() {
    onClipSelect(null);
  }

  const ticks = rulerTicks(totalDurationMs);
  const LABEL_WIDTH = 52; // px — left label column

  return (
    <div className="flex flex-col border-t border-border bg-[hsl(var(--surface))] select-none">
      {/* ── Controls bar ───────────────────────────────────────────────── */}
      <div className="flex h-8 items-center gap-2 border-b border-border px-3">
        <span className="font-mono text-[10px] text-muted-foreground tabular-nums">
          {formatMs(playheadMs)}{" "}
          <span className="opacity-40">/</span>{" "}
          {formatMs(totalDurationMs)}
        </span>
        <div className="ml-auto flex items-center gap-0.5">
          <TimelineButton onClick={zoomOut} title="Zoom out">
            <Minus className="size-3" />
          </TimelineButton>
          <TimelineButton onClick={fitToContent} title="Fit to content">
            <Maximize2 className="size-3" />
          </TimelineButton>
          <TimelineButton onClick={zoomIn} title="Zoom in">
            <ZoomIn className="size-3" />
          </TimelineButton>
          {zoomLevel !== 1 && (
            <span className="ml-1 text-[9px] font-bold text-primary tabular-nums">
              {zoomLevel.toFixed(1)}×
            </span>
          )}
        </div>
      </div>

      {/* ── Main scrollable area ────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Fixed label column */}
        <div
          className="flex flex-shrink-0 flex-col border-r border-border bg-[hsl(var(--surface))]"
          style={{ width: LABEL_WIDTH }}
        >
          {/* Ruler placeholder */}
          <div style={{ height: RULER_HEIGHT }} />
          {tracks.map((track) => (
            <div
              key={track.kind}
              className="flex items-center justify-end pr-2"
              style={{ height: TRACK_HEIGHT }}
            >
              <span className="text-[9px] font-bold uppercase tracking-widest text-muted-foreground/60">
                {track.label}
              </span>
            </div>
          ))}
        </div>

        {/* Horizontally scrollable tracks canvas */}
        <div className="relative flex-1 overflow-x-auto overflow-y-hidden" ref={scrollRef}>
          {/* Inner canvas — grows with zoom level */}
          <div
            className="relative"
            style={{
              width: `${Math.round(zoomLevel * 100)}%`,
              minWidth: "100%",
            }}
            onClick={handleTrackAreaClick}
          >
            {/* ── Ruler ───────────────────────────────────────────────── */}
            <div
              className="relative cursor-pointer bg-[hsl(var(--background))]"
              style={{ height: RULER_HEIGHT }}
              onPointerDown={handleRulerPointerDown}
            >
              {ticks.map((tick) => (
                <div
                  key={tick.ms}
                  className="absolute top-0 flex flex-col items-center"
                  style={{ left: pct(tick.ms), transform: "translateX(-50%)" }}
                >
                  <span className="text-[8px] font-mono text-muted-foreground/50 leading-none pt-1">
                    {tick.label}
                  </span>
                  <div className="mt-0.5 h-2 w-px bg-border" />
                </div>
              ))}
            </div>

            {/* ── Track rows ──────────────────────────────────────────── */}
            {tracks.map((track, trackIdx) => (
              <div
                key={track.kind}
                className={cn(
                  "relative border-b border-border/40",
                  trackIdx % 2 === 0 ? "bg-[hsl(var(--background))]" : "bg-[hsl(var(--surface))]",
                )}
                style={{ height: TRACK_HEIGHT }}
              >
                {track.clips.map((clip) => (
                  <ClipBlock
                    key={clip.id}
                    clip={clip}
                    pct={pct}
                    selected={clip.id === selectedClipId}
                    onSelect={onClipSelect}
                  />
                ))}
              </div>
            ))}

            {/* ── Playhead ────────────────────────────────────────────── */}
            <Playhead
              pct={pct(playheadMs)}
              totalHeight={RULER_HEIGHT + tracks.length * TRACK_HEIGHT}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Clip block ────────────────────────────────────────────────────────────────

interface ClipBlockProps {
  clip: TimelineClip;
  pct: (ms: number) => string;
  selected: boolean;
  onSelect: (id: string) => void;
}

function ClipBlock({ clip, pct, selected, onSelect }: ClipBlockProps) {
  const widthMs = Math.max(clip.endMs - clip.startMs, 0);
  const widthPct = `${((widthMs / 1) * 100).toFixed(4)}%`; // relative to parent (which is 100% of duration)

  return (
    <div
      className={cn(
        "absolute top-1 bottom-1 rounded cursor-pointer overflow-hidden",
        "flex items-center px-1.5 text-[9px] font-bold text-white/90 leading-none",
        "ring-inset transition-all duration-100",
        clip.colorClass,
        clip.disabled && "opacity-30",
        selected
          ? "ring-2 ring-white shadow-[0_0_0_1px_white]"
          : "hover:brightness-110 ring-1 ring-white/10",
      )}
      style={{
        left: pct(clip.startMs),
        width: pct(widthMs),
      }}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(clip.id);
      }}
      title={`${clip.label}  |  ${formatMs(clip.startMs)} – ${formatMs(clip.endMs)}`}
    >
      {/* Left trim handle */}
      <TrimHandle side="left" />

      <span className="flex-1 truncate">{clip.label}</span>

      {/* Right trim handle */}
      <TrimHandle side="right" />
    </div>
  );
}

function TrimHandle({ side }: { side: "left" | "right" }) {
  return (
    <div
      className={cn(
        "absolute top-0 bottom-0 w-1.5 cursor-ew-resize z-10",
        "flex items-center justify-center",
        "opacity-0 hover:opacity-100 group-hover:opacity-100 transition",
        "bg-white/20 hover:bg-white/50",
        side === "left" ? "left-0 rounded-l" : "right-0 rounded-r",
      )}
      // Actual trim logic comes in Sprint C; showing the affordance now
      onClick={(e) => e.stopPropagation()}
    />
  );
}

// ── Playhead ──────────────────────────────────────────────────────────────────

function Playhead({ pct, totalHeight }: { pct: string; totalHeight: number }) {
  return (
    <div
      className="pointer-events-none absolute top-0 z-20 flex flex-col items-center"
      style={{ left: pct, transform: "translateX(-50%)" }}
    >
      {/* Diamond handle */}
      <div className="h-2 w-2 rotate-45 bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.8)]" />
      {/* Vertical line */}
      <div
        className="w-px bg-red-500/80 shadow-[0_0_3px_rgba(239,68,68,0.5)]"
        style={{ height: totalHeight - 8 }}
      />
    </div>
  );
}

// ── Toolbar button ────────────────────────────────────────────────────────────

function TimelineButton({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex size-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition"
    >
      {children}
    </button>
  );
}
