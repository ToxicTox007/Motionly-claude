"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Maximize2, Play, Pause, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMs } from "@/lib/timeline";
import type { CaptionSegment, CaptionStyle, TranscriptWord } from "@motionly/shared";

interface VideoPreviewProps {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  sourceUrl: string | null;
  thumbnailUrl: string | null;
  sourceWidth?: number | null;
  sourceHeight?: number | null;
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
  sourceWidth,
  sourceHeight,
  playheadMs,
  captionSegments,
  captionStyle,
  totalDurationMs,
  isProcessing,
}: VideoPreviewProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(80);
  const [intrinsicSize, setIntrinsicSize] = useState<{
    sourceUrl: string;
    width: number;
    height: number;
  } | null>(null);
  const activeIntrinsicSize =
    intrinsicSize?.sourceUrl === sourceUrl ? intrinsicSize : null;
  const frameWidth = sourceWidth || activeIntrinsicSize?.width || 16;
  const frameHeight = sourceHeight || activeIntrinsicSize?.height || 9;
  const sourceAspectRatio = `${frameWidth} / ${frameHeight}`;

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

  const toggleMute = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const nextMuted = !video.muted;
    video.muted = nextMuted;
    setMuted(nextMuted);
  }, [videoRef]);

  const updateVolume = useCallback((nextVolume: number) => {
    const video = videoRef.current;
    const clamped = Math.max(0, Math.min(100, nextVolume));
    setVolume(clamped);
    if (video) {
      video.volume = clamped / 100;
      video.muted = clamped === 0;
      setMuted(video.muted);
    }
  }, [videoRef]);

  const requestFullscreen = useCallback(() => {
    const video = videoRef.current;
    if (!video?.parentElement?.requestFullscreen) return;
    void video.parentElement.requestFullscreen();
  }, [videoRef]);

  return (
    <div className="pixel-grid-fine flex h-full min-h-0 flex-col items-center justify-center gap-2 bg-foreground/[0.02] p-1.5 sm:gap-3 sm:p-4">
      <h2 id="preview-heading" className="sr-only">Preview</h2>
      {/* ── Video + overlays ─────────────────────────────────────────────── */}
      <div
        className="relative flex min-h-0 w-full flex-1 items-center justify-center"
      >
        <div
          className="pixel-corners relative h-full max-h-[min(100%,calc(100vh-180px))] min-h-[430px] w-auto max-w-full overflow-hidden border-2 border-foreground/20 bg-black shadow-[6px_6px_0_hsl(var(--foreground)/0.12)] sm:min-h-[520px] lg:min-h-0 lg:max-h-full"
          style={{ aspectRatio: sourceAspectRatio }}
        >
          {/* Video element */}
          {sourceUrl ? (
            <video
              ref={videoRef}
              src={sourceUrl}
              poster={thumbnailUrl ?? undefined}
              className="absolute inset-0 h-full w-full object-contain"
              onLoadedMetadata={(event) => {
                const video = event.currentTarget;
                if (video.videoWidth && video.videoHeight) {
                  setIntrinsicSize({
                    sourceUrl,
                    width: video.videoWidth,
                    height: video.videoHeight,
                  });
                }
              }}
              playsInline
              preload="metadata"
              aria-label="Source video preview"
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
      <div className="flex w-full max-w-md flex-wrap items-center justify-center gap-2" aria-label="Video controls">
        <ControlButton onClick={() => skip(-5)} title="Back 5 seconds" ariaLabel="Seek back 5 seconds">
          <span className="flex items-center gap-1">
            <SkipBack className="size-3.5" />
            <span className="text-[10px] font-bold">5s</span>
          </span>
        </ControlButton>

        <button
          onClick={togglePlay}
          aria-label={isPlaying ? "Pause preview video" : "Play preview video"}
          className="pixel-button flex size-11 items-center justify-center bg-primary text-primary-foreground"
        >
          {isPlaying
            ? <Pause  className="size-4" />
            : <Play   className="size-4 translate-x-px" />}
        </button>

        <ControlButton onClick={() => skip(5)} title="Forward 5 seconds" ariaLabel="Seek forward 5 seconds">
          <span className="flex items-center gap-1">
            <span className="text-[10px] font-bold">5s</span>
            <SkipForward className="size-3.5" />
          </span>
        </ControlButton>
        <ControlButton onClick={toggleMute} title={muted ? "Unmute" : "Mute"} ariaLabel={muted ? "Unmute preview video" : "Mute preview video"}>
          {muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
        </ControlButton>
        <label className="flex min-h-11 items-center gap-2 text-sm font-bold text-muted-foreground">
          <span>Volume</span>
          <input
            type="range"
            min={0}
            max={100}
            value={volume}
            aria-label="Preview volume"
            aria-valuetext={`${volume} percent`}
            onChange={(event) => updateVolume(Number(event.target.value))}
            className="w-24 accent-primary"
          />
        </label>
        <ControlButton onClick={requestFullscreen} title="Fullscreen" ariaLabel="Open preview fullscreen">
          <Maximize2 className="size-4" />
        </ControlButton>
      </div>

      {/* Timecode */}
      <p className="border border-border bg-surface px-2 py-1 font-mono text-xs tabular-nums text-muted-foreground shadow-soft" aria-live="polite">
        <span className="sr-only">Current time </span>{formatMs(playheadMs)}
        <span aria-hidden="true"> / </span>
        <span className="sr-only">Duration </span>{formatMs(totalDurationMs)}
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
    style.position_x_pct != null && style.position_y_pct != null
      ? ""
      : style.placement === "upper"
      ? "top-[12%]"
      : style.placement === "middle"
        ? "top-[50%] -translate-y-1/2"
        : "bottom-[16%]";
  const positionedStyle: React.CSSProperties =
    style.position_x_pct != null && style.position_y_pct != null
      ? {
          left: `${Math.max(12, Math.min(88, style.position_x_pct))}%`,
          top: `${Math.max(12, Math.min(82, style.position_y_pct))}%`,
          transform: "translate(-50%, -50%)",
        }
      : {};

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
    textShadow: style.shadow ?? "0 3px 12px rgba(0,0,0,0.85)",
    fontSize:   `${Math.max(18, Math.min(style.font_size_px ?? 32, 52))}px`,
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
      style={positionedStyle}
    >
      <p className="rounded bg-black/20 px-2 py-1" style={inlineStyle}>
        {active.words?.length
          ? active.words.map((word: TranscriptWord) => (
              <WordSpan
                key={word.index}
                word={word}
                playheadMs={playheadMs}
                accentColor={style.accent_color}
                forceHighlight={active.highlight_words?.includes(word.text)}
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
  forceHighlight,
}: {
  word: TranscriptWord;
  playheadMs: number;
  accentColor: string;
  forceHighlight?: boolean;
}) {
  const isActive =
    playheadMs >= word.start_ms && playheadMs <= word.end_ms;
  const highlighted = isActive || forceHighlight;
  return (
    <span style={{ color: highlighted ? (accentColor || "#facc15") : "inherit" }}>
      {word.text}{" "}
    </span>
  );
}

// ── Small helper button ───────────────────────────────────────────────────────

function ControlButton({
  onClick,
  title,
  ariaLabel,
  children,
}: {
  onClick: () => void;
  title:   string;
  ariaLabel: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      className="flex size-11 items-center justify-center border border-border bg-surface text-muted-foreground transition hover:border-primary/40 hover:bg-muted hover:text-foreground"
    >
      {children}
    </button>
  );
}
