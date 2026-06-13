"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Maximize2, Play, Pause, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMs } from "@/lib/timeline";
import type { CaptionSegment, CaptionStyle, TranscriptWord, VisualPresetKey } from "@motionly/shared";

export interface PreviewAudioState {
  includeMusic: boolean;
  musicVolume: number;
  sfxIntensity: number;
  mood: string;
  selectedTrackTitle?: string | null;
}

export interface PreviewBrollState {
  density: string | null;
  slotsCount: number;
}

export interface PreviewLookState {
  preset: VisualPresetKey | string;
  intensity: number;
  grain: number;
  vignette: number;
  lightLeak: number;
  gradientRamp?: {
    enabled: boolean;
    startColor: string;
    endColor: string;
    opacity: number;
    angleDeg: number;
  };
  motionEffects: string[];
  reducedMotion: boolean;
}

export interface PreviewExportState {
  outputAspect: string;
  fitMode: "fill" | "fit" | "custom" | string;
  cropX: number;
  cropY: number;
  cropZoom: number;
}

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
  previewAudio?: PreviewAudioState | null;
  previewBroll?: PreviewBrollState | null;
  previewLook?: PreviewLookState | null;
  previewExport?: PreviewExportState | null;
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
  previewAudio,
  previewBroll,
  previewLook,
  previewExport,
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
  const videoFilter = getPreviewFilter(previewLook);
  const objectFit =
    previewExport?.fitMode === "fit"
      ? "contain"
      : previewExport?.fitMode === "fill" || previewExport?.fitMode === "custom"
        ? "cover"
        : "contain";
  const objectPosition =
    previewExport && previewExport.fitMode !== "fit"
      ? `${previewExport.cropX}% ${previewExport.cropY}%`
      : "center";
  const cropScale =
    previewExport?.fitMode === "custom" ? Math.max(1, previewExport.cropZoom / 100) : 1;

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
    <div className="pixel-grid-fine flex h-full min-h-0 max-w-full flex-col items-center justify-center gap-2 overflow-hidden bg-foreground/[0.02] p-1.5 sm:gap-3 sm:p-4">
      <h2 id="preview-heading" className="sr-only">Preview</h2>
      {/* ── Video + overlays ─────────────────────────────────────────────── */}
      <div
        className="relative flex min-h-0 w-full flex-1 items-center justify-center"
      >
        <div
          className="pixel-corners relative h-auto max-h-[min(100%,calc(100dvh-220px))] min-h-0 w-full max-w-[min(100%,22rem)] overflow-hidden border border-foreground/20 bg-black shadow-[3px_3px_0_hsl(var(--foreground)/0.1)] sm:max-w-[28rem] sm:border-2 sm:shadow-[6px_6px_0_hsl(var(--foreground)/0.12)] lg:h-full lg:max-h-full lg:max-w-full"
          style={{ aspectRatio: sourceAspectRatio }}
        >
          {/* Video element */}
          {sourceUrl ? (
            <video
              ref={videoRef}
              src={sourceUrl}
              poster={thumbnailUrl ?? undefined}
              className="absolute inset-0 h-full w-full transition-[filter,transform] duration-150"
              style={{
                filter: videoFilter,
                objectFit,
                objectPosition,
                transform: cropScale > 1 ? `scale(${cropScale})` : undefined,
              }}
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

          {previewLook?.gradientRamp?.enabled ? (
            <div
              className="pointer-events-none absolute inset-0 mix-blend-soft-light"
              style={{
                opacity: previewLook.gradientRamp.opacity,
                background: `linear-gradient(${previewLook.gradientRamp.angleDeg}deg, ${previewLook.gradientRamp.startColor}, ${previewLook.gradientRamp.endColor})`,
              }}
            />
          ) : null}

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

      <PreviewSyncBadges
        audio={previewAudio}
        broll={previewBroll}
        look={previewLook}
        exportState={previewExport}
      />

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

function getPreviewFilter(look: PreviewLookState | null | undefined) {
  if (!look) return undefined;
  const intensity = Math.max(0.5, Math.min(1, look.intensity / 100));
  const contrast = 1 + 0.18 * intensity;
  const saturate = 1 + 0.12 * intensity;
  const brightness = look.preset === "old_money" ? 0.96 : 1.01;
  const grain = Math.max(0, Math.min(look.grain, 1));
  const vignette = Math.max(0, Math.min(look.vignette, 1));
  return `contrast(${contrast}) saturate(${saturate + grain * 0.08}) brightness(${brightness - vignette * 0.04})`;
}

function PreviewSyncBadges({
  audio,
  broll,
  look,
  exportState,
}: {
  audio?: PreviewAudioState | null;
  broll?: PreviewBrollState | null;
  look?: PreviewLookState | null;
  exportState?: PreviewExportState | null;
}) {
  const badges = [
    look ? `Look ${look.intensity}%` : null,
    broll ? `B-roll ${broll.density ?? "auto"} (${broll.slotsCount})` : null,
    audio ? `${audio.includeMusic ? "Music" : "No music"} ${audio.musicVolume}%` : null,
    exportState ? `Crop ${exportState.cropZoom}%` : null,
  ].filter(Boolean);
  if (!badges.length) return null;
  return (
    <div className="flex max-w-full flex-wrap justify-center gap-1.5 px-1" aria-live="polite">
      {badges.map((badge) => (
        <span
          key={badge}
          className="border border-border bg-surface px-2 py-1 text-[10px] font-bold text-muted-foreground shadow-soft"
        >
          {badge}
        </span>
      ))}
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
