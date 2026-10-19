import {
  AbsoluteFill,
  Audio,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type React from "react";
import {
  durationMsToFrameCount,
  millisecondsToFrameEnd,
  millisecondsToFrameStart,
} from "@motionly/shared";
import { AnimatedCaptions } from "./components/Captions";
import { VideoScene } from "./components/VideoScene";
import { ManualElements } from "./components/ManualElements";
import { getTemplate } from "./templates/registry";
import type { MotionlyRenderProps } from "./types";
import { sourceTimelineClips } from "./timeline";

export function MotionlyPremiumComposition(input: MotionlyRenderProps) {
  const template = getTemplate(input.templateId);

  return (
    <AbsoluteFill style={{ backgroundColor: template.background }}>
      <VisualGrade input={input}>
        <VideoScene input={input} template={template} />
      </VisualGrade>
      <CutMappedVocalStem input={input} />
      <MusicAndSfxMix input={input} />
      <ManualElements elements={input.editPlan.manual_elements} />
      <AnimatedCaptions
        segments={input.captions}
        words={input.words}
        style={input.captionStyle}
        template={template}
      />
      {input.watermark ? (
        <div
          style={{
            position: "absolute",
            right: 32,
            bottom: 26,
            padding: "8px 12px",
            background: "rgba(0,0,0,.38)",
            color: "rgba(255,255,255,.82)",
            fontFamily: "Inter, sans-serif",
            fontSize: 22,
            fontWeight: 800,
            letterSpacing: 2,
          }}
        >
          MOTIONLY
        </div>
      ) : null}
    </AbsoluteFill>
  );
}

function activeStemName(input: MotionlyRenderProps): "vocals" | "no_vocals" {
  return input.sourceStems?.active ?? "vocals";
}

function activeStemSource(input: MotionlyRenderProps) {
  const stems = input.sourceStems;
  if (!stems) return undefined;
  const source = stems[activeStemName(input)]?.src;
  if (!source) {
    throw new Error("The selected source stem is unavailable for rendering.");
  }
  return source;
}

function CutMappedVocalStem({ input }: { input: MotionlyRenderProps }) {
  const source = activeStemSource(input);
  const stemName = activeStemName(input);
  const { fps } = useVideoConfig();
  if (!source) return null;
  return (
    <>
      {sourceTimelineClips(input).map((clip) => (
        <Sequence
          key={`${stemName}-${clip.id}`}
          from={millisecondsToFrameStart(clip.output_start_ms, fps)}
          durationInFrames={durationMsToFrameCount(
            clip.output_end_ms - clip.output_start_ms,
            fps,
          )}
        >
          <Audio
            src={source}
            startFrom={millisecondsToFrameStart(clip.source_start_ms, fps)}
            endAt={millisecondsToFrameEnd(clip.source_end_ms, fps)}
          />
        </Sequence>
      ))}
    </>
  );
}

function VisualGrade({
  input,
  children,
}: {
  input: MotionlyRenderProps;
  children: React.ReactNode;
}) {
  const config = input.effectsConfig;
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!config) return <>{children}</>;

  const presetFilter = presetCssFilter(config.preset, config.lut_intensity);
  const vignetteOpacity = Math.min(0.38, config.texture.vignette * 0.62);
  const grainOpacity = Math.min(0.18, config.texture.grain * 0.36);
  const leakOpacity = lightLeakOpacity({
    frame,
    fps,
    intensity: config.texture.light_leak,
    enabled: config.motion_effects.includes("flash"),
  });
  const flareOpacity = config.texture.anamorphic_flare
    ? Math.min(0.22, config.lut_intensity * 0.18)
    : 0;
  const ramp = config.gradient_ramp;

  return (
    <AbsoluteFill>
      <AbsoluteFill
        style={{
          filter: presetFilter,
          transform: motionTransform(input, frame, fps),
          transformOrigin: "center",
        }}
      >
        {children}
      </AbsoluteFill>
      {ramp.enabled ? (
        <AbsoluteFill
          style={{
            pointerEvents: "none",
            opacity: ramp.opacity,
            background: `linear-gradient(${ramp.angle_deg}deg, ${ramp.start_color}, ${ramp.end_color})`,
            mixBlendMode: ramp.blend_mode,
          }}
        />
      ) : null}
      <AbsoluteFill
        style={{
          pointerEvents: "none",
          background: `radial-gradient(circle at 50% 46%, transparent 42%, rgba(0,0,0,${vignetteOpacity}) 100%)`,
          mixBlendMode: "multiply",
        }}
      />
      <AbsoluteFill
        style={{
          pointerEvents: "none",
          opacity: grainOpacity,
          backgroundImage:
            "repeating-radial-gradient(circle at 20% 30%, rgba(255,255,255,.18) 0 1px, transparent 1px 3px)",
          mixBlendMode: "overlay",
        }}
      />
      <AbsoluteFill
        style={{
          pointerEvents: "none",
          opacity: leakOpacity,
          background:
            "linear-gradient(112deg, transparent 0%, rgba(255,216,154,.36) 48%, rgba(255,120,92,.2) 100%)",
          mixBlendMode: "screen",
        }}
      />
      <AbsoluteFill
        style={{
          pointerEvents: "none",
          opacity: flareOpacity,
          background:
            "linear-gradient(90deg, transparent 0%, rgba(255,255,255,.24) 49%, transparent 62%)",
          transform: "translateY(-12%) skewY(-7deg)",
          mixBlendMode: "screen",
        }}
      />
    </AbsoluteFill>
  );
}

function MusicAndSfxMix({ input }: { input: MotionlyRenderProps }) {
  const { fps, durationInFrames } = useVideoConfig();
  const selection = input.musicSelection;
  const track = selection?.track;
  const musicSrc = mediaUrl(track);
  if (!selection || !track || !musicSrc) return null;

  const musicVolume = Math.max(0, Math.min(1, selection.music_volume ?? 0.25));
  const duckedVolume = volumeWithSpeechDucking({
    captions: input.captions,
    fps,
    baseVolume: musicVolume,
    attenuationDb: selection.mix?.ducking.speech_attenuation_db ?? -12,
  });
  const sfxUrls = sfxCueUrls(track.raw_metadata);
  const sfxIntensity = Math.max(
    0,
    Math.min(1, selection.sfx_intensity ?? 0.25),
  );
  const cueFrames = beatCueFrames(track.beat_map, fps, durationInFrames).slice(
    0,
    Math.round(sfxIntensity * 6),
  );

  return (
    <>
      <Audio src={musicSrc} loop volume={duckedVolume} />
      {sfxUrls.map((src, index) => {
        const from = cueFrames[index % cueFrames.length] ?? 0;
        return (
          <Sequence
            key={`${src}-${index}`}
            from={from}
            durationInFrames={Math.min(
              durationInFrames - from,
              Math.round(fps * 1.4),
            )}
          >
            <Audio src={src} volume={sfxIntensity * 0.35} />
          </Sequence>
        );
      })}
    </>
  );
}

function mediaUrl(
  track:
    | NonNullable<MotionlyRenderProps["musicSelection"]>["track"]
    | undefined
    | null,
) {
  const trackRecord = track as Record<string, unknown> | undefined | null;
  const metadata = track?.raw_metadata;
  const value =
    trackRecord?.full_track_url ??
    trackRecord?.preview_url ??
    metadata?.audio_url ??
    metadata?.download_url ??
    metadata?.preview_url ??
    metadata?.src;
  return typeof value === "string" && !value.startsWith("fixture://")
    ? value
    : null;
}

function sfxCueUrls(metadata: Record<string, unknown> | undefined) {
  const value = metadata?.sfx_urls;
  return Array.isArray(value)
    ? value.filter(
        (item): item is string =>
          typeof item === "string" && !item.startsWith("fixture://"),
      )
    : [];
}

function volumeWithSpeechDucking({
  captions,
  fps,
  baseVolume,
  attenuationDb,
}: {
  captions: MotionlyRenderProps["captions"];
  fps: number;
  baseVolume: number;
  attenuationDb: number;
}) {
  const duckMultiplier = Math.pow(10, attenuationDb / 20);
  return (frame: number) => {
    const timeMs = (frame / fps) * 1000;
    const speechActive = captions.some(
      (caption) =>
        timeMs >= caption.start_ms - 120 && timeMs <= caption.end_ms + 180,
    );
    return speechActive ? baseVolume * duckMultiplier : baseVolume;
  };
}

function beatCueFrames(
  beatMap: number[] | undefined,
  fps: number,
  durationInFrames: number,
) {
  const beats = (beatMap ?? [])
    .map((beatMs) => millisecondsToFrameStart(beatMs, fps))
    .filter((frame) => frame >= 0 && frame < durationInFrames);
  return beats.length ? beats : [0];
}

function presetCssFilter(preset: string, intensity: number) {
  const amount = Math.max(0.5, Math.min(1, intensity));
  const filterByPreset: Record<string, string> = {
    vogue_luxury: `contrast(${1 + amount * 0.16}) saturate(${1 - amount * 0.06}) brightness(${1 + amount * 0.04})`,
    champagne_gold: `sepia(${amount * 0.18}) saturate(${1 + amount * 0.16}) brightness(${1 + amount * 0.06})`,
    dubai_influencer: `saturate(${1 + amount * 0.24}) contrast(${1 + amount * 0.08}) brightness(${1 + amount * 0.05})`,
    old_money: `sepia(${amount * 0.14}) saturate(${1 - amount * 0.08}) contrast(${1 + amount * 0.12})`,
    clean_founder: `saturate(${1 - amount * 0.1}) brightness(${1 + amount * 0.08}) contrast(${1 + amount * 0.04})`,
    cinematic_hotel: `sepia(${amount * 0.12}) contrast(${1 + amount * 0.18}) saturate(${1 + amount * 0.08})`,
  };
  return filterByPreset[preset] ?? "none";
}

function lightLeakOpacity({
  frame,
  fps,
  intensity,
  enabled,
}: {
  frame: number;
  fps: number;
  intensity: number;
  enabled: boolean;
}) {
  const base = Math.min(0.28, intensity * 0.5);
  if (!enabled) return base;
  const pulse = Math.sin((frame / fps) * Math.PI * 2) * 0.5 + 0.5;
  return Math.min(0.42, base + pulse * intensity * 0.12);
}

function motionTransform(
  input: MotionlyRenderProps,
  frame: number,
  fps: number,
) {
  const config = input.effectsConfig;
  if (!config?.motion_effects.includes("zoom_punch")) return "scale(1)";
  const timeMs = (frame / fps) * 1000;
  const nearest = input.editPlan.zoom_moments.find(
    (moment) => Math.abs(moment.timestamp_ms - timeMs) <= 220,
  );
  const punch = nearest ? Math.min(1.12, nearest.intensity) : 1;
  return `scale(${punch})`;
}
