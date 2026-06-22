"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useDroppable } from "@dnd-kit/core";
import { Maximize2, Play, Pause, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMs, outputMsToSourceMs } from "@/lib/timeline";
import type {
  CaptionSegment,
  CaptionStyle,
  MotionKeyframe,
  MotionSettings,
  ManualElement,
  ProjectTimelineSourceClip,
  TranscriptWord,
  VisualPresetKey,
} from "@motionly/shared";
import { ManualElementsOverlay } from "./ManualElementsOverlay";
import { WebGLVideoCanvas } from "./WebGLVideoCanvas";

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
  motion?: MotionSettings | null;
  selectedSourceClipId?: string | null;
  selectedSourceClipStartMs?: number | null;
  selectedSourceClipEndMs?: number | null;
  zoomMoments?: Array<{ timestamp_ms: number; intensity: number }> | null;
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
  manualElements?: ManualElement[];
  selectedElementId?: string | null;
  onElementSelect?: (id: string) => void;
  sourceClips?: ProjectTimelineSourceClip[];
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
  manualElements = [],
  selectedElementId,
  onElementSelect,
  sourceClips = [],
}: VideoPreviewProps) {
  const { setNodeRef: setCanvasDropRef, isOver: isCanvasDropActive } = useDroppable({
    id: "canvas",
    data: { zone: "canvas" },
  });
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
  const previewAspectRatio = getPreviewAspectRatio(previewExport?.outputAspect, frameWidth, frameHeight);
  const previewMotion = getPreviewMotionState({
    look: previewLook,
    playheadMs,
    totalDurationMs,
    captionSegments,
  });
  const videoFilter = [getPreviewFilter(previewLook), previewMotion.filter].filter(Boolean).join(" ") || undefined;
  const objectFit =
    previewExport?.fitMode === "fit"
      ? "contain"
      : previewExport?.fitMode === "fill" || previewExport?.fitMode === "custom"
        ? "cover"
        : "contain";
  const objectPosition =
    previewMotion.objectPosition ??
    (previewExport && previewExport.fitMode !== "fit"
      ? `${previewExport.cropX}% ${previewExport.cropY}%`
      : "center");
  const cropScale =
    previewExport?.fitMode === "custom" ? Math.max(1, previewExport.cropZoom / 100) : 1;
  const videoTransform = [
    cropScale > 1 ? `scale(${cropScale})` : null,
    previewMotion.transform,
  ].filter(Boolean).join(" ") || undefined;
  const webglGrade = getWebGLGrade(previewLook);

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

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.playbackRate = previewMotion.playbackRate;
  }, [previewMotion.playbackRate, videoRef]);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) video.play();
    else              video.pause();
  }, [videoRef]);

  const skip = useCallback((deltaS: number) => {
    const video = videoRef.current;
    if (!video) return;
    const nextOutputMs = Math.max(
      0,
      Math.min(totalDurationMs, playheadMs + deltaS * 1000),
    );
    video.currentTime = outputMsToSourceMs(nextOutputMs, sourceClips) / 1000;
  }, [playheadMs, sourceClips, totalDurationMs, videoRef]);

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
          ref={setCanvasDropRef}
          className="pixel-corners relative h-auto max-h-[min(100%,calc(100dvh-220px))] min-h-0 w-full max-w-[min(100%,22rem)] overflow-hidden border border-foreground/20 bg-black shadow-[3px_3px_0_hsl(var(--foreground)/0.1)] sm:max-w-[28rem] sm:border-2 sm:shadow-[6px_6px_0_hsl(var(--foreground)/0.12)] lg:h-full lg:max-h-full lg:max-w-full"
          style={{ aspectRatio: previewAspectRatio, outline: isCanvasDropActive ? "3px solid hsl(var(--primary))" : undefined, outlineOffset: -3 }}
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
                transform: videoTransform,
                transformOrigin: previewMotion.transformOrigin,
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
              crossOrigin="anonymous"
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

          {sourceUrl ? (
            <WebGLVideoCanvas
              videoRef={videoRef}
              contrast={webglGrade.contrast}
              brightness={webglGrade.brightness}
              saturation={webglGrade.saturation}
              vignette={previewLook?.vignette ?? 0}
            />
          ) : null}

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

          {previewMotion.flashOpacity > 0 ? (
            <div
              className="pointer-events-none absolute inset-0 bg-white mix-blend-screen"
              style={{ opacity: previewMotion.flashOpacity }}
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

          <ManualElementsOverlay
            elements={manualElements}
            playheadMs={playheadMs}
            selectedId={selectedElementId}
            onSelect={onElementSelect}
          />

          {isCanvasDropActive ? (
            <div className="pointer-events-none absolute inset-0 z-30 grid grid-cols-10 grid-rows-10 bg-primary/10">
              {Array.from({ length: 100 }).map((_, index) => <span key={index} className="border-[0.5px] border-primary/15" />)}
            </div>
          ) : null}
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

function getWebGLGrade(look?: PreviewLookState | null) {
  const amount = Math.max(0, Math.min(1, look?.intensity ?? 0));
  const values: Record<string, [number, number, number]> = {
    vogue_luxury: [1.16, 0.02, 0.94],
    champagne_gold: [1.1, 0.04, 1.16],
    dubai_influencer: [1.08, 0.03, 1.24],
    old_money: [1.12, -0.01, 0.92],
    clean_founder: [1.04, 0.03, 0.9],
    cinematic_hotel: [1.18, -0.01, 1.08],
  };
  const target = values[String(look?.preset ?? "")] ?? [1, 0, 1];
  return {
    contrast: 1 + (target[0] - 1) * amount,
    brightness: target[1] * amount,
    saturation: 1 + (target[2] - 1) * amount,
  };
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

function getPreviewAspectRatio(
  outputAspect: string | null | undefined,
  sourceWidth: number,
  sourceHeight: number,
) {
  if (outputAspect === "9:16") return "9 / 16";
  if (outputAspect === "1:1") return "1 / 1";
  if (outputAspect === "4:5") return "4 / 5";
  if (outputAspect === "16:9") return "16 / 9";
  return `${sourceWidth} / ${sourceHeight}`;
}

function getPreviewMotionState({
  look,
  playheadMs,
  totalDurationMs,
  captionSegments,
}: {
  look: PreviewLookState | null | undefined;
  playheadMs: number;
  totalDurationMs: number;
  captionSegments: CaptionSegment[];
}) {
  const base = {
    transform: undefined as string | undefined,
    transformOrigin: "center",
    objectPosition: undefined as string | undefined,
    filter: undefined as string | undefined,
    playbackRate: 1,
    flashOpacity: 0,
  };
  if (!look || look.reducedMotion) return base;

  const effects = new Set(look.motionEffects);
  const motion = look.motion ?? null;
  let scale = 1;
  let translateX = 0;
  let translateY = 0;
  let rotateDeg = 0;
  let blurPx = 0;
  let hueRotateDeg = 0;
  let originX = 50;
  let originY = 50;
  let playbackRate = 1;
  let objectPosition: string | undefined;
  let flashOpacity = 0;

  const autoReframe = motion?.auto_reframe;
  if (autoReframe?.enabled) {
    objectPosition = `${Math.round(autoReframe.subject_x_pct)}% ${Math.round(autoReframe.subject_y_pct)}%`;
  }

  const kenBurns = motion?.ken_burns;
  if (kenBurns?.enabled || effects.has("ken_burns")) {
    const progress = easePreview(
      clampNumber(playheadMs / Math.max(1, totalDurationMs), 0, 1),
      kenBurns?.easing ?? "ease_in_out",
    );
    const intensity = kenBurns?.intensity ?? 0.08;
    const pan = intensity * 9;
    const direction = kenBurns?.direction ?? "push_in";
    if (direction === "pull_out") {
      scale *= 1 + intensity * (1 - progress);
    } else if (direction === "pan_left") {
      translateX -= pan * progress;
      scale *= 1 + intensity * 0.45;
    } else if (direction === "pan_right") {
      translateX += pan * progress;
      scale *= 1 + intensity * 0.45;
    } else if (direction === "pan_up") {
      translateY -= pan * progress;
      scale *= 1 + intensity * 0.45;
    } else if (direction === "pan_down") {
      translateY += pan * progress;
      scale *= 1 + intensity * 0.45;
    } else {
      scale *= 1 + intensity * progress;
    }
  }

  const keyframeState = interpolatePreviewKeyframes(
    (motion?.keyframes ?? []).filter(
      (keyframe) =>
        (keyframe.target === "source" || keyframe.target === "all") &&
        (!keyframe.clip_id || keyframe.clip_id === look.selectedSourceClipId),
    ),
    playheadMs,
  );
  if (keyframeState) {
    scale *= keyframeState.scale;
    rotateDeg += keyframeState.rotation_deg;
    originX = keyframeState.x_pct;
    originY = keyframeState.y_pct;
    objectPosition = `${Math.round(keyframeState.x_pct)}% ${Math.round(keyframeState.y_pct)}%`;
  }

  const boundaries = captionSegments.map((segment) => segment.start_ms).filter((start) => start > 0);
  const speedRamp = motion?.speed_ramp;
  if (speedRamp?.enabled || effects.has("speed_ramp")) {
    const amount = pulseAroundBoundaries({
      playheadMs,
      boundaries,
      fallbackDurationMs: totalDurationMs,
      rampInMs: speedRamp?.ramp_in_ms ?? 280,
      rampOutMs: speedRamp?.ramp_out_ms ?? 320,
      easing: speedRamp?.easing ?? "ease_in_out",
    });
    const peakSpeed = speedRamp?.peak_speed ?? 1.35;
    playbackRate = 1 + amount * (peakSpeed - 1);
    scale *= 1 + amount * 0.02;
    blurPx += amount * 1.4;
  }

  const beatImpact = motion?.beat_impact;
  if (beatImpact?.enabled || effects.has("beat_impact")) {
    const impactBoundaries = boundaries.length ? boundaries : fallbackImpactBoundaries(totalDurationMs);
    const amount =
      pulseAroundBoundaries({
        playheadMs,
        boundaries: impactBoundaries.slice(0, beatImpact?.max_impacts ?? 12),
        fallbackDurationMs: totalDurationMs,
        rampInMs: 90,
        rampOutMs: 150,
        easing: "ease_out",
      }) * (beatImpact?.intensity ?? 0.22);
    if (amount > 0) {
      const shakePx = beatImpact?.shake_px ?? 10;
      scale *= 1 + ((beatImpact?.zoom ?? 1.035) - 1) * amount * 2.4;
      translateX += Math.sin(playheadMs * 0.03) * shakePx * amount * 0.08;
      translateY += Math.cos(playheadMs * 0.037) * shakePx * amount * 0.08;
      rotateDeg += Math.sin(playheadMs * 0.021) * amount * 1.4;
    }
  }

  if (effects.has("motion_blur")) {
    blurPx += 0.65;
  }

  if (effects.has("flash")) {
    const flashAmount = pulseAroundBoundaries({
      playheadMs,
      boundaries: boundaries.length ? boundaries : fallbackImpactBoundaries(totalDurationMs),
      fallbackDurationMs: totalDurationMs,
      rampInMs: 50,
      rampOutMs: 180,
      easing: "ease_out",
    });
    flashOpacity = Math.min(0.36, flashAmount * Math.max(0.16, look.lightLeak * 0.8));
  }

  if (effects.has("glitch")) {
    const glitchAmount = pulseAroundBoundaries({
      playheadMs,
      boundaries: boundaries.length ? boundaries : fallbackImpactBoundaries(totalDurationMs),
      fallbackDurationMs: totalDurationMs,
      rampInMs: 60,
      rampOutMs: 220,
      easing: "ease_out",
    });
    translateX += Math.sin(playheadMs * 0.12) * glitchAmount * 1.4;
    hueRotateDeg += glitchAmount * 12;
  }

  const clipStartMs = look.selectedSourceClipStartMs ?? 0;
  const clipEndMs = look.selectedSourceClipEndMs ?? totalDurationMs;
  const clipDurationMs = Math.max(1, clipEndMs - clipStartMs);
  const localPlayheadMs = playheadMs - clipStartMs;
  const clipEffects = new Set(
    look.selectedSourceClipId && motion?.clip_effects?.[look.selectedSourceClipId]
      ? motion.clip_effects[look.selectedSourceClipId]
      : [],
  );
  const onSelectedClip =
    look.selectedSourceClipId && playheadMs >= clipStartMs && playheadMs <= clipEndMs;

  if (onSelectedClip) {
    if (clipEffects.has("ken_burns") || (motion?.ken_burns.enabled && clipEffects.has("ken_burns"))) {
      const progress = easePreview(clampNumber(localPlayheadMs / clipDurationMs, 0, 1), motion?.ken_burns.easing ?? "ease_in_out");
      const intensity = motion?.ken_burns.intensity ?? 0.08;
      const pan = intensity * 9;
      const direction = motion?.ken_burns.direction ?? "push_in";
      if (direction === "pull_out") {
        scale *= 1 + intensity * (1 - progress);
      } else if (direction === "pan_left") {
        translateX -= pan * progress;
        scale *= 1 + intensity * 0.45;
      } else if (direction === "pan_right") {
        translateX += pan * progress;
        scale *= 1 + intensity * 0.45;
      } else if (direction === "pan_up") {
        translateY -= pan * progress;
        scale *= 1 + intensity * 0.45;
      } else if (direction === "pan_down") {
        translateY += pan * progress;
        scale *= 1 + intensity * 0.45;
      } else {
        scale *= 1 + intensity * progress;
      }
    }

    if (clipEffects.has("zoom_punch")) {
      const nearest = (look.zoomMoments ?? []).find(
        (moment) => Math.abs(moment.timestamp_ms - playheadMs) <= 220,
      );
      const punch = nearest ? Math.min(1.12, nearest.intensity) : 1;
      scale *= punch;
    }

    if (clipEffects.has("speed_ramp") && motion?.speed_ramp.enabled) {
      const amount = clipSpeedRampPreview(localPlayheadMs, clipDurationMs, motion.speed_ramp);
      const peakSpeed = motion.speed_ramp.peak_speed;
      playbackRate = 1 + amount * (peakSpeed - 1);
      scale *= 1 + amount * 0.02;
      blurPx += amount * 1.4;
    }

    if (clipEffects.has("beat_impact") && motion?.beat_impact.enabled) {
      const settings = motion.beat_impact;
      const impactBoundaries = boundaries.length ? boundaries : fallbackImpactBoundaries(totalDurationMs);
      const amount =
        pulseAroundBoundaries({
          playheadMs,
          boundaries: impactBoundaries.slice(0, settings.max_impacts),
          fallbackDurationMs: totalDurationMs,
          rampInMs: 90,
          rampOutMs: 150,
          easing: "ease_out",
        }) * settings.intensity;
      if (amount > 0) {
        scale *= 1 + ((settings.zoom ?? 1.035) - 1) * amount * 2.4;
        translateX += Math.sin(playheadMs * 0.03) * settings.shake_px * amount * 0.08;
        translateY += Math.cos(playheadMs * 0.037) * settings.shake_px * amount * 0.08;
        rotateDeg += Math.sin(playheadMs * 0.021) * amount * 1.4;
      }
    }

    if (clipEffects.has("motion_blur")) {
      blurPx += 0.65;
    }

    if (clipEffects.has("flash")) {
      const flashAmount = pulseAroundBoundaries({
        playheadMs,
        boundaries: boundaries.length ? boundaries : fallbackImpactBoundaries(totalDurationMs),
        fallbackDurationMs: totalDurationMs,
        rampInMs: 50,
        rampOutMs: 180,
        easing: "ease_out",
      });
      flashOpacity = Math.max(flashOpacity, Math.min(0.36, flashAmount * Math.max(0.16, look.lightLeak * 0.8)));
    }

    if (clipEffects.has("glitch")) {
      const glitchAmount = pulseAroundBoundaries({
        playheadMs,
        boundaries: boundaries.length ? boundaries : fallbackImpactBoundaries(totalDurationMs),
        fallbackDurationMs: totalDurationMs,
        rampInMs: 60,
        rampOutMs: 220,
        easing: "ease_out",
      });
      translateX += Math.sin(playheadMs * 0.12) * glitchAmount * 1.4;
      hueRotateDeg += glitchAmount * 12;
    }
  }

  const transformParts = [
    translateX || translateY ? `translate(${translateX.toFixed(2)}%, ${translateY.toFixed(2)}%)` : null,
    scale !== 1 ? `scale(${scale.toFixed(4)})` : null,
    rotateDeg ? `rotate(${rotateDeg.toFixed(2)}deg)` : null,
  ].filter(Boolean);
  const filterParts = [
    blurPx ? `blur(${blurPx.toFixed(2)}px)` : null,
    hueRotateDeg ? `hue-rotate(${hueRotateDeg.toFixed(1)}deg)` : null,
  ].filter(Boolean);

  return {
    transform: transformParts.join(" ") || undefined,
    transformOrigin: `${Math.round(originX)}% ${Math.round(originY)}%`,
    objectPosition,
    filter: filterParts.join(" ") || undefined,
    playbackRate: clampNumber(playbackRate, 0.5, 3),
    flashOpacity,
  };
}

function interpolatePreviewKeyframes(keyframes: MotionKeyframe[], playheadMs: number) {
  if (!keyframes.length) return null;
  const sorted = [...keyframes].sort((a, b) => a.at_ms - b.at_ms);
  const first = sorted[0];
  if (playheadMs <= first.at_ms) return first;
  const last = sorted[sorted.length - 1];
  if (playheadMs >= last.at_ms) return last;
  const nextIndex = sorted.findIndex((keyframe) => keyframe.at_ms >= playheadMs);
  const from = sorted[Math.max(0, nextIndex - 1)];
  const to = sorted[nextIndex];
  const progress = easePreview(
    (playheadMs - from.at_ms) / Math.max(1, to.at_ms - from.at_ms),
    to.easing,
  );
  return {
    ...to,
    scale: lerpNumber(from.scale, to.scale, progress),
    x_pct: lerpNumber(from.x_pct, to.x_pct, progress),
    y_pct: lerpNumber(from.y_pct, to.y_pct, progress),
    rotation_deg: lerpNumber(from.rotation_deg, to.rotation_deg, progress),
    opacity: lerpNumber(from.opacity, to.opacity, progress),
  };
}

function clipSpeedRampPreview(
  localPlayheadMs: number,
  clipDurationMs: number,
  settings: MotionSettings["speed_ramp"],
) {
  let amount = 0;
  if (localPlayheadMs <= settings.ramp_in_ms) {
    amount = Math.max(amount, easePreview(localPlayheadMs / settings.ramp_in_ms, settings.easing));
  }
  const remaining = clipDurationMs - localPlayheadMs;
  if (remaining <= settings.ramp_out_ms) {
    amount = Math.max(amount, easePreview(remaining / settings.ramp_out_ms, settings.easing));
  }
  return clampNumber(amount, 0, 1);
}

function pulseAroundBoundaries({
  playheadMs,
  boundaries,
  fallbackDurationMs,
  rampInMs,
  rampOutMs,
  easing,
}: {
  playheadMs: number;
  boundaries: number[];
  fallbackDurationMs: number;
  rampInMs: number;
  rampOutMs: number;
  easing: MotionSettings["speed_ramp"]["easing"];
}) {
  const activeBoundaries = boundaries.length ? boundaries : [0, fallbackDurationMs];
  let amount = 0;
  for (const boundary of activeBoundaries) {
    const delta = playheadMs - boundary;
    if (delta < 0 && Math.abs(delta) <= rampInMs) {
      amount = Math.max(amount, easePreview(1 - Math.abs(delta) / rampInMs, easing));
    }
    if (delta >= 0 && delta <= rampOutMs) {
      amount = Math.max(amount, easePreview(1 - delta / rampOutMs, easing));
    }
  }
  return clampNumber(amount, 0, 1);
}

function fallbackImpactBoundaries(totalDurationMs: number) {
  const count = Math.max(1, Math.floor(totalDurationMs / 1200));
  return Array.from({ length: count }, (_, index) => index * 1200);
}

function easePreview(
  value: number,
  easing: MotionSettings["ken_burns"]["easing"] | "linear",
) {
  const t = clampNumber(value, 0, 1);
  if (easing === "ease_in") return t * t;
  if (easing === "ease_out") return 1 - Math.pow(1 - t, 2);
  if (easing === "spring") {
    return clampNumber(1 - Math.cos(t * Math.PI * 1.5) * Math.pow(1 - t, 2), 0, 1);
  }
  if (easing === "ease_in_out") {
    return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  }
  return t;
}

function lerpNumber(from: number, to: number, progress: number) {
  return from + (to - from) * progress;
}

function clampNumber(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
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

  const mode = style.mode ?? "word_pop";
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
    lineHeight:  mode === "minimal_line" ? 1.15 : 1.25,
    maxWidth:    mode === "impact_stamp" || mode === "creator_ticker" ? "88%" : "80%",
    width:       mode === "impact_stamp" || mode === "creator_ticker" ? "88%" : undefined,
    textAlign:   mode === "creator_ticker" ? "left" : "center",
    letterSpacing: `${style.letter_spacing_px ?? 0}px`,
    backgroundColor: captionBackground(style),
    border:
      style.border_color && style.background_opacity > 0
        ? `1px solid ${style.border_color}`
        : undefined,
    borderRadius: `${mode === "creator_ticker" ? 999 : style.border_radius_px ?? 0}px`,
    padding:
      style.background_opacity > 0
        ? `${style.padding_y_px ?? 6}px ${style.padding_x_px ?? 12}px`
        : undefined,
  };

  return (
    <div
      className={cn(
        "pointer-events-none absolute left-1/2 -translate-x-1/2 px-2",
        placementClass,
      )}
      style={positionedStyle}
    >
      <p style={inlineStyle}>
        {mode === "educational_card" ? (
          <span
            className="mr-2 inline-block rounded-full px-2 py-0.5 text-[10px] font-black text-white"
            style={{ backgroundColor: style.accent_color }}
          >
            STEP {active.index + 1}
          </span>
        ) : null}
        {active.words?.length
          ? active.words.map((word: TranscriptWord) => (
              <WordSpan
                key={word.index}
                word={word}
                playheadMs={playheadMs}
                accentColor={style.accent_color}
                mode={mode}
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
  mode,
  forceHighlight,
}: {
  word: TranscriptWord;
  playheadMs: number;
  accentColor: string;
  mode: CaptionStyle["mode"];
  forceHighlight?: boolean;
}) {
  const isActive =
    playheadMs >= word.start_ms && playheadMs <= word.end_ms;
  const highlighted = isActive || forceHighlight;
  if (mode === "creator_ticker" && highlighted) {
    return (
      <span
        className="mx-0.5 inline-block rounded-full px-1.5 text-black"
        style={{ backgroundColor: accentColor || "#22c55e" }}
      >
        {word.text}{" "}
      </span>
    );
  }
  if (mode === "minimal_line" && highlighted) {
    return (
      <span style={{ color: accentColor || "#94a3b8", borderBottom: `1px solid ${accentColor || "#94a3b8"}` }}>
        {word.text}{" "}
      </span>
    );
  }
  return (
    <span style={{ color: highlighted ? (accentColor || "#facc15") : "inherit" }}>
      {word.text}{" "}
    </span>
  );
}

function captionBackground(style: CaptionStyle) {
  if (!style.background_color || style.background_opacity <= 0) return undefined;
  return hexToRgba(style.background_color, style.background_opacity);
}

function hexToRgba(hex: string, opacity: number) {
  const normalized = hex.replace("#", "");
  if (normalized.length !== 6) return undefined;
  const red = parseInt(normalized.slice(0, 2), 16);
  const green = parseInt(normalized.slice(2, 4), 16);
  const blue = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${Math.max(0, Math.min(1, opacity))})`;
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
