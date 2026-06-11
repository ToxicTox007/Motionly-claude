"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Play, Pause, SkipBack, SkipForward } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMs } from "@/lib/timeline";
import type { CaptionSegment, CaptionStyle, TranscriptWord } from "@motionly/shared";

interface VideoPreviewProps {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  sourceUrl: string | null;
  thumbnailUrl: string | null;
  playheadMs: number;
  captionSegments: CaptionSegment[];
  captionStyle: CaptionStyle | null;
  totalDurationMs: number;
  isProcessing?: boolean;
}

export function VideoPreview({
  videoRef,
  sourceUrl,
  thumbnailUrl,
  playheadMs,
  captionSegments,
  captionStyle,
  totalDurationMs,
  isProcessing,
}: VideoPreviewProps) {
  const [isPlaying, setIsPlaying] = useState(false);

  // Keep isPlaying in sync with the actual video state
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const onPlay  = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onEnded = () => setIsPlaying(false);
    video.addEventListener("play",  onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("ended", onEnded);
    return () => {
      video.removeEventListener("play",  onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("ended", onEnded);
    };
  }, [videoRef]);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) video.play();
    else              video.pause();
  }, [videoRef]);

  const skip = useCallback((deltaS: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + deltaS));
  }, [videoRef]);

  return (
    <div className="pixel-grid-fine flex h-full min-h-0 flex-col items-center justify-center gap-3 bg-foreground/[0.02] p-3 sm:p-4">
      {/* ── Video + overlays ─────────────────────────────────────────────── */}
      <div
        className="relative flex min-h-0 w-full flex-1 items-center justify-center"
      >
        <div className="pixel-corners relative aspect-[9/16] h-full max-h-[min(100%,calc(100vh-310px))] min-h-[320px] w-auto max-w-full overflow-hidden border-2 border-foreground/20 bg-black shadow-[8px_8px_0_hsl(var(--foreground)/0.14)] lg:min-h-0 lg:max-h-full">
          {/* Video element */}
          {sourceUrl ? (
            <video
              ref={videoRef}
              src={sourceUrl}
              poster={thumbnailUrl ?? undefined}
              className="absolute inset-0 h-full w-full object-contain"
              playsInline
              preload="metadata"
            />
          ) : (
            /* Empty state when no source yet */
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-zinc-900">
              <div className="grid size-12 grid-cols-3 gap-1 opacity-30">
                {Array.from({ length: 9 }).map((_, index) => (
                  <span key={index} className="bg-white/50" />
                ))}
              </div>
              <p className="text-xs text-muted-foreground/50">
                {isProcessing ? "Processing video…" : "No video yet"}
              </p>
            </div>
          )}

          {/* Safe zone overlay — 90 % × 90 % dashed border */}
          <SafeZoneOverlay />

          {/* Live caption overlay */}
          {captionSegments.length > 0 && captionStyle && (
            <CaptionOverlay
              segments={captionSegments}
              style={captionStyle}
              playheadMs={playheadMs}
            />
          )}
        </div>
      </div>

      {/* ── Playback controls ────────────────────────────────────────────── */}
      <div className="flex w-full max-w-[240px] items-center justify-between gap-2">
        <ControlButton onClick={() => skip(-5)} title="–5 s">
          <SkipBack className="size-3.5" />
        </ControlButton>

        <button
          onClick={togglePlay}
          className="pixel-button flex size-10 items-center justify-center bg-primary text-primary-foreground"
        >
          {isPlaying
            ? <Pause  className="size-4" />
            : <Play   className="size-4 translate-x-px" />}
        </button>

        <ControlButton onClick={() => skip(5)} title="+5 s">
          <SkipForward className="size-3.5" />
        </ControlButton>
      </div>

      {/* Timecode */}
      <p className="border border-border bg-surface px-2 py-1 font-mono text-[10px] tabular-nums text-muted-foreground/70 shadow-soft">
        {formatMs(playheadMs)} / {formatMs(totalDurationMs)}
      </p>
    </div>
  );
}

// ── Safe zone overlay ─────────────────────────────────────────────────────────

function SafeZoneOverlay() {
  return (
    <div className="pointer-events-none absolute inset-0">
      {/* Action safe — 90% */}
      <div className="absolute inset-[5%] rounded-sm border border-dashed border-white/10" />
      {/* Title safe — 80% */}
      <div className="absolute inset-[10%] rounded-sm border border-dashed border-white/5" />
    </div>
  );
}

// ── Caption overlay ───────────────────────────────────────────────────────────

interface CaptionOverlayProps {
  segments:  CaptionSegment[];
  style:     CaptionStyle;
  playheadMs: number;
}

function CaptionOverlay({ segments, style, playheadMs }: CaptionOverlayProps) {
  const active = segments.find(
    (s) => playheadMs >= s.start_ms && playheadMs <= s.end_ms,
  );
  if (!active) return null;

  const placementClass =
    style.placement === "upper"
      ? "top-[12%]"
      : style.placement === "middle"
        ? "top-[50%] -translate-y-1/2"
        : "bottom-[12%]";

  const inlineStyle: React.CSSProperties = {
    fontFamily:      style.font_family || "Inter, sans-serif",
    fontWeight:      style.font_weight || 700,
    color:           style.text_color  || "#ffffff",
    textTransform:   style.text_case === "uppercase"
                       ? "uppercase"
                       : style.text_case === "title"
                         ? "capitalize"
                         : "none",
    WebkitTextStroke:
      style.stroke_width_px && style.stroke_color
        ? `${style.stroke_width_px}px ${style.stroke_color}`
        : undefined,
    textShadow: style.shadow ?? undefined,
    fontSize:   `clamp(10px, ${(style.font_size_px ?? 24) * 0.042}vw, ${style.font_size_px ?? 24}px)`,
    lineHeight:  1.25,
    maxWidth:    "80%",
    textAlign:   "center",
  };

  return (
    <div
      className={cn(
        "pointer-events-none absolute left-1/2 -translate-x-1/2 px-2",
        placementClass,
      )}
    >
      <p style={inlineStyle}>
        {active.words?.length
          ? active.words.map((word: TranscriptWord) => (
              <WordSpan
                key={word.index}
                word={word}
                playheadMs={playheadMs}
                accentColor={style.accent_color}
              />
            ))
          : active.text}
      </p>
    </div>
  );
}

function WordSpan({
  word,
  playheadMs,
  accentColor,
}: {
  word: TranscriptWord;
  playheadMs: number;
  accentColor: string;
}) {
  const isActive =
    playheadMs >= word.start_ms && playheadMs <= word.end_ms;
  return (
    <span style={{ color: isActive ? (accentColor || "#facc15") : "inherit" }}>
      {word.text}{" "}
    </span>
  );
}

// ── Small helper button ───────────────────────────────────────────────────────

function ControlButton({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title:   string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex size-8 items-center justify-center border border-border bg-surface text-muted-foreground transition hover:border-primary/40 hover:bg-muted hover:text-foreground"
    >
      {children}
    </button>
  );
}
