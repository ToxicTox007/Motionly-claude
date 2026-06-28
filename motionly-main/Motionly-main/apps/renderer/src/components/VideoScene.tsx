import {
  AbsoluteFill,
  interpolate,
  OffthreadVideo,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { MotionKeyframe, MotionSettings } from "@motionly/shared";
import type { CSSProperties } from "react";
import type { MotionlyRenderProps, TemplateDefinition } from "../types";
import type { SourceTimelineClip } from "../types";
import { sourceTimelineClips } from "../timeline";

type VideoSceneProps = {
  input: MotionlyRenderProps;
  template: TemplateDefinition;
};

export function VideoScene({ input, template }: VideoSceneProps) {
  const { fps, durationInFrames } = useVideoConfig();
  const introFrames = template.intro ? Math.min(60, durationInFrames / 5) : 0;
  const outroFrames = template.outro ? Math.min(72, durationInFrames / 5) : 0;
  const outroStart = Math.max(0, durationInFrames - outroFrames);
  const backgroundRemoval = input.effectsConfig?.background_removal;

  if (
    !input.sourceVideo.src ||
    input.sourceVideo.src.startsWith("fixture://")
  ) {
    throw new Error("A real source video URL is required for rendering.");
  }

  return (
    <AbsoluteFill style={{ backgroundColor: template.background }}>
      {backgroundRemoval?.enabled ? (
        <RemovedBackgroundPlate input={input} template={template} />
      ) : null}
      <SourceClipAssembly input={input} template={template} />
      {input.assets
        .filter(
          (asset) =>
            asset.kind === "video" &&
            asset.src &&
            !asset.src.startsWith("fixture://") &&
            asset.start_ms !== undefined &&
            asset.end_ms !== undefined,
        )
        .map((asset) => (
          <BrollOverlay
            key={asset.id}
            asset={asset}
            input={input}
            template={template}
          />
        ))}
      <Vignette template={template} />
      {template.intro ? (
        <Sequence durationInFrames={introFrames}>
          <HookCard input={input} template={template} />
        </Sequence>
      ) : null}
      {template.id === "podcast_reel_v1" ? (
        <Waveform accent={template.accent} />
      ) : null}
      {template.outro ? (
        <Sequence from={outroStart} durationInFrames={outroFrames}>
          <OutroCard template={template} />
        </Sequence>
      ) : null}
    </AbsoluteFill>
  );
}

function SourceClipAssembly({
  input,
  template,
}: VideoSceneProps) {
  const { fps } = useVideoConfig();
  return (
    <>
      {sourceTimelineClips(input).map((clip) => {
        const from = millisecondsToFrame(clip.output_start_ms, fps);
        const durationInFrames = Math.max(
          1,
          millisecondsToFrame(
            clip.output_end_ms - clip.output_start_ms,
            fps,
          ),
        );
        return (
          <Sequence key={clip.id} from={from} durationInFrames={durationInFrames}>
            <SourceClipSurface input={input} template={template} clip={clip} />
          </Sequence>
        );
      })}
    </>
  );
}

function SourceClipSurface({
  input,
  template,
  clip,
}: VideoSceneProps & { clip: SourceTimelineClip }) {
  const localFrame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const outputFrame = millisecondsToFrame(clip.output_start_ms, fps) + localFrame;
  const framing = input.exportSettings.framing;
  const crop = cropWithAutoReframe(
    input,
    framing?.crop ?? { x: 0.5, y: 0.5, zoom: 1 },
    framing?.output_aspect,
    clip.source_start_ms + (localFrame / fps) * 1000,
  );
  const framingZoom =
    framing?.fit_mode === "custom" ? Math.max(1, Math.min(3, crop.zoom)) : 1;
  const zoom = activeZoom(input.editPlan.zoom_moments, outputFrame, fps);
  const sourceMotion = sourceMotionState({
    input,
    frame: outputFrame,
    fps,
    durationInFrames,
    baseScale: zoom * framingZoom,
    crop,
    activeClipId: clip.id,
  });
  const style: CSSProperties = {
    width: "100%",
    height: "100%",
    objectFit: framing?.fit_mode === "fit" ? "contain" : "cover",
    objectPosition: `${Math.round(sourceMotion.xPct)}% ${Math.round(sourceMotion.yPct)}%`,
    transform: motionTransformCss(sourceMotion),
    transformOrigin: `${Math.round(sourceMotion.xPct)}% ${Math.round(sourceMotion.yPct)}%`,
    opacity: sourceMotion.opacity,
    filter: [
      gradeFilter(input.editPlan.color_grade, template.default_color_grade),
      sourceMotion.filter,
      segmentationSpillFilter(input),
    ].filter(Boolean).join(" "),
    ...(usesLegacyBackgroundIsolation(input)
      ? subjectIsolationStyle(input.effectsConfig!.background_removal!.feather_px)
      : {}),
  };
  return (
    <OffthreadVideo
      src={input.sourceVideo.src}
      startFrom={millisecondsToFrame(clip.source_start_ms, fps)}
      endAt={millisecondsToFrame(clip.source_end_ms, fps)}
      muted={Boolean(input.sourceStems?.vocals?.src)}
      playbackRate={sourceMotion.playbackRate}
      style={style}
    />
  );
}

function millisecondsToFrame(milliseconds: number, fps: number) {
  return Math.max(0, Math.round((milliseconds / 1000) * fps));
}

function BrollOverlay({
  asset,
  input,
  template,
}: {
  asset: MotionlyRenderProps["assets"][number];
  input: MotionlyRenderProps;
  template: TemplateDefinition;
}) {
  const { fps } = useVideoConfig();
  const startFrame = Math.max(
    0,
    Math.round(((asset.start_ms ?? 0) / 1000) * fps),
  );
  const endFrame = Math.max(
    startFrame + 1,
    Math.round(((asset.end_ms ?? 0) / 1000) * fps),
  );
  const durationInFrames = endFrame - startFrame;
  return (
    <Sequence from={startFrame} durationInFrames={durationInFrames}>
      <BrollSurface
        asset={asset}
        input={input}
        template={template}
        durationInFrames={durationInFrames}
      />
    </Sequence>
  );
}

function BrollSurface({
  asset,
  input,
  template,
  durationInFrames,
}: {
  asset: MotionlyRenderProps["assets"][number];
  input: MotionlyRenderProps;
  template: TemplateDefinition;
  durationInFrames: number;
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const fadeFrames = Math.max(
    4,
    Math.round(((asset.crossfade_ms ?? 260) / 1000) * fps),
  );
  const opacity = interpolate(
    frame,
    [
      0,
      fadeFrames,
      Math.max(fadeFrames + 1, durationInFrames - fadeFrames),
      durationInFrames,
    ],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const scale = interpolate(frame, [0, durationInFrames], [1.03, 1.09], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const slideX = input.effectsConfig?.motion_effects.includes("broll_slide_in")
    ? interpolate(frame, [0, Math.min(18, durationInFrames)], [140, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;
  const motion = brollMotionState({
    input,
    asset,
    frame,
    fps,
    durationInFrames,
    baseScale: scale,
    baseTranslateX: slideX,
  });
  const isolateBroll = Boolean(
    input.effectsConfig?.background_removal.enabled &&
    input.effectsConfig.background_removal.apply_to_broll,
  );
  return (
    <OffthreadVideo
      src={asset.src}
      muted
      playbackRate={motion.playbackRate}
      style={{
        width: "100%",
        height: "100%",
        objectFit: "cover",
        opacity: opacity * motion.opacity,
        objectPosition: `${Math.round(motion.xPct)}% ${Math.round(motion.yPct)}%`,
        transform: motionTransformCss(motion),
        transformOrigin: `${Math.round(motion.xPct)}% ${Math.round(motion.yPct)}%`,
        filter: ["contrast(1.04) saturate(1.05)", motion.filter].filter(Boolean).join(" "),
        ...(isolateBroll
          ? subjectIsolationStyle(
              input.effectsConfig?.background_removal.feather_px ?? 18,
            )
          : {}),
      }}
    />
  );
}

function RemovedBackgroundPlate({
  input,
  template,
}: {
  input: MotionlyRenderProps;
  template: TemplateDefinition;
}) {
  const frame = useCurrentFrame();
  const settings = input.effectsConfig?.background_removal;
  const ramp = input.effectsConfig?.gradient_ramp;
  const background =
    settings?.replacement === "blurred_source"
      ? `linear-gradient(${frame * 0.2}deg, ${template.background}, ${template.accent}55)`
      : `linear-gradient(${ramp?.angle_deg ?? 135}deg, ${ramp?.start_color ?? template.background}, ${ramp?.end_color ?? template.accent})`;
  return (
    <AbsoluteFill
      style={{
        background,
        filter:
          settings?.replacement === "blurred_source"
            ? "blur(12px) saturate(1.1)"
            : undefined,
      }}
    />
  );
}

function usesLegacyBackgroundIsolation(input: MotionlyRenderProps) {
  const settings = input.effectsConfig?.background_removal;
  if (!settings?.enabled) return false;
  return !input.sourceVideo.has_alpha && !input.segmentationMatte?.src;
}

function segmentationSpillFilter(input: MotionlyRenderProps) {
  const settings = input.effectsConfig?.background_removal;
  if (!settings?.enabled) return undefined;
  if (!input.sourceVideo.has_alpha && !input.segmentationMatte?.src) return undefined;
  const spill = Math.max(0, Math.min(1, settings.spill_suppression ?? 0));
  if (spill <= 0) return undefined;
  const strength = (spill * 18).toFixed(1);
  return `saturate(${(1 - spill * 0.35).toFixed(3)}) hue-rotate(${strength}deg)`;
}

function subjectIsolationStyle(featherPx: number) {
  const blur = Math.max(0, Math.min(20, featherPx / 8));
  return {
    clipPath: "ellipse(38% 48% at 50% 50%)",
    boxShadow: `0 0 ${blur}px rgba(255,255,255,0.26)`,
  };
}

type CropState = { x: number; y: number; zoom: number };
type MotionState = {
  scale: number;
  xPct: number;
  yPct: number;
  translateX: number;
  translateY: number;
  rotationDeg: number;
  opacity: number;
  playbackRate: number;
  filter?: string;
};

function sourceMotionState({
  input,
  frame,
  fps,
  durationInFrames,
  baseScale,
  crop,
  activeClipId,
}: {
  input: MotionlyRenderProps;
  frame: number;
  fps: number;
  durationInFrames: number;
  baseScale: number;
  crop: CropState;
  activeClipId: string | null;
}) {
  const timeMs = (frame / fps) * 1000;
  const motion = input.effectsConfig?.motion;
  let state: MotionState = {
    scale: baseScale,
    xPct: crop.x * 100,
    yPct: crop.y * 100,
    translateX: 0,
    translateY: 0,
    rotationDeg: 0,
    opacity: 1,
    playbackRate: 1,
  };
  state = applyKenBurns(state, motion, frame, durationInFrames);
  state = applyMotionKeyframes(state, motion, "source", null, timeMs);
  state = applyMotionKeyframes(state, motion, "source", activeClipId, timeMs);
  state = applySpeedRamp(state, input, motion, timeMs, input.sourceVideo.duration_ms);
  state = applyBeatImpact(state, input, motion, timeMs, frame);
  state = applyClipMotionEffects(
    state,
    input,
    motion,
    activeClipId,
    timeMs,
    frame,
    durationInFrames,
    fps,
  );
  return clampMotionState(state);
}

function brollMotionState({
  input,
  asset,
  frame,
  fps,
  durationInFrames,
  baseScale,
  baseTranslateX,
}: {
  input: MotionlyRenderProps;
  asset: MotionlyRenderProps["assets"][number];
  frame: number;
  fps: number;
  durationInFrames: number;
  baseScale: number;
  baseTranslateX: number;
}) {
  const localMs = (frame / fps) * 1000;
  const globalMs = (asset.start_ms ?? 0) + localMs;
  const motion = input.effectsConfig?.motion;
  let state: MotionState = {
    scale: baseScale,
    xPct: 50,
    yPct: 50,
    translateX: baseTranslateX,
    translateY: 0,
    rotationDeg: 0,
    opacity: 1,
    playbackRate: 1,
  };
  state = applyMotionKeyframes(state, motion, "broll", asset.slot_id ?? asset.id, localMs);
  state = applyMotionKeyframes(state, motion, "broll", null, globalMs);
  state = applySpeedRamp(state, input, motion, globalMs, (durationInFrames / fps) * 1000);
  state = applyBeatImpact(state, input, motion, globalMs, frame);
  const brollClipId = asset.slot_id ? `broll-${asset.slot_id}` : null;
  state = applyClipMotionEffects(
    state,
    input,
    motion,
    brollClipId,
    globalMs,
    frame,
    durationInFrames,
    fps,
  );
  return clampMotionState(state);
}

function cropWithAutoReframe(
  input: MotionlyRenderProps,
  crop: CropState,
  outputAspect: string | undefined,
  sourceTimeMs: number,
): CropState {
  const settings = input.effectsConfig?.motion?.auto_reframe;
  if (!settings?.enabled) return crop;
  if ((outputAspect ?? "9:16") !== settings.target_aspect) return crop;
  const tracked = interpolateSubjectTrack(settings.subject_track ?? [], sourceTimeMs);
  const subjectX = clamp(tracked?.x ?? settings.subject_x_pct / 100, 0, 1);
  const subjectY = clamp(tracked?.y ?? settings.subject_y_pct / 100, 0, 1);
  const strength = clamp(1 - settings.smoothing * 0.25, 0.5, 1);
  return {
    ...crop,
    x: clamp(0.5 + (subjectX - 0.5) * strength, 0, 1),
    y: clamp(0.5 + (subjectY - 0.5) * strength, 0, 1),
    zoom: Math.max(1, crop.zoom),
  };
}

function interpolateSubjectTrack(
  track: NonNullable<MotionSettings["auto_reframe"]>["subject_track"],
  timestampMs: number,
) {
  if (!track?.length) return null;
  const afterIndex = track.findIndex((point) => point.timestamp_ms >= timestampMs);
  if (afterIndex <= 0) return track[Math.max(0, afterIndex)];
  if (afterIndex < 0) return track[track.length - 1];
  const before = track[afterIndex - 1];
  const after = track[afterIndex];
  const progress = clamp(
    (timestampMs - before.timestamp_ms) / Math.max(1, after.timestamp_ms - before.timestamp_ms),
    0,
    1,
  );
  return {
    ...before,
    x: before.x + (after.x - before.x) * progress,
    y: before.y + (after.y - before.y) * progress,
  };
}

function applyKenBurns(
  state: MotionState,
  motion: MotionSettings | undefined,
  frame: number,
  durationInFrames: number,
) {
  return applyKenBurnsSettings(state, motion?.ken_burns, frame, durationInFrames);
}

function applyKenBurnsSettings(
  state: MotionState,
  settings: MotionSettings["ken_burns"] | undefined,
  frame: number,
  durationInFrames: number,
) {
  if (!settings?.enabled) return state;
  const progress = ease(
    clamp(frame / Math.max(1, durationInFrames - 1), 0, 1),
    settings.easing,
  );
  const pan = settings.intensity * 38;
  const next = { ...state };
  if (settings.direction === "pull_out") {
    next.scale *= 1 + settings.intensity * (1 - progress);
  } else if (settings.direction === "pan_left") {
    next.xPct -= pan * progress;
    next.scale *= 1 + settings.intensity * 0.5;
  } else if (settings.direction === "pan_right") {
    next.xPct += pan * progress;
    next.scale *= 1 + settings.intensity * 0.5;
  } else if (settings.direction === "pan_up") {
    next.yPct -= pan * progress;
    next.scale *= 1 + settings.intensity * 0.5;
  } else if (settings.direction === "pan_down") {
    next.yPct += pan * progress;
    next.scale *= 1 + settings.intensity * 0.5;
  } else {
    next.scale *= 1 + settings.intensity * progress;
  }
  return next;
}

function applyMotionKeyframes(
  state: MotionState,
  motion: MotionSettings | undefined,
  target: "source" | "broll",
  clipId: string | null,
  timeMs: number,
) {
  const keyframes = matchingKeyframes(motion, target, clipId);
  if (!keyframes.length) return state;
  const keyState = interpolatedKeyframeState(keyframes, timeMs);
  return {
    ...state,
    scale: state.scale * keyState.scale,
    xPct: keyState.xPct,
    yPct: keyState.yPct,
    rotationDeg: state.rotationDeg + keyState.rotationDeg,
    opacity: state.opacity * keyState.opacity,
  };
}

function matchingKeyframes(
  motion: MotionSettings | undefined,
  target: "source" | "broll",
  clipId: string | null,
) {
  return [...(motion?.keyframes ?? [])]
    .filter((keyframe) => keyframe.target === target || keyframe.target === "all")
    .filter((keyframe) => {
      if (!keyframe.clip_id) return clipId === null;
      return keyframe.clip_id === clipId;
    })
    .sort((a, b) => a.at_ms - b.at_ms);
}

function interpolatedKeyframeState(keyframes: MotionKeyframe[], timeMs: number) {
  const base = keyframeToState({
    target: "source",
    clip_id: null,
    at_ms: 0,
    scale: 1,
    x_pct: 50,
    y_pct: 50,
    rotation_deg: 0,
    opacity: 1,
    easing: "linear",
  });
  const first = keyframes[0];
  if (timeMs <= first.at_ms) {
    if (first.at_ms === 0) return keyframeToState(first);
    return mixKeyframeState(base, keyframeToState(first), ease(timeMs / first.at_ms, first.easing));
  }
  const last = keyframes[keyframes.length - 1];
  if (timeMs >= last.at_ms) return keyframeToState(last);
  const nextIndex = keyframes.findIndex((keyframe) => keyframe.at_ms >= timeMs);
  const from = keyframes[Math.max(0, nextIndex - 1)];
  const to = keyframes[nextIndex];
  const progress = ease(
    (timeMs - from.at_ms) / Math.max(1, to.at_ms - from.at_ms),
    to.easing,
  );
  return mixKeyframeState(keyframeToState(from), keyframeToState(to), progress);
}

function keyframeToState(keyframe: MotionKeyframe) {
  return {
    scale: keyframe.scale,
    xPct: keyframe.x_pct,
    yPct: keyframe.y_pct,
    rotationDeg: keyframe.rotation_deg,
    opacity: keyframe.opacity,
  };
}

function mixKeyframeState(
  from: ReturnType<typeof keyframeToState>,
  to: ReturnType<typeof keyframeToState>,
  progress: number,
) {
  return {
    scale: lerp(from.scale, to.scale, progress),
    xPct: lerp(from.xPct, to.xPct, progress),
    yPct: lerp(from.yPct, to.yPct, progress),
    rotationDeg: lerp(from.rotationDeg, to.rotationDeg, progress),
    opacity: lerp(from.opacity, to.opacity, progress),
  };
}

function applySpeedRamp(
  state: MotionState,
  input: MotionlyRenderProps,
  motion: MotionSettings | undefined,
  timeMs: number,
  durationMs: number,
) {
  const settings = motion?.speed_ramp;
  if (!settings?.enabled) return state;
  const amount = speedRampAmount({
    input,
    timeMs,
    durationMs,
    rampInMs: settings.ramp_in_ms,
    rampOutMs: settings.ramp_out_ms,
    easing: settings.easing,
  });
  if (amount <= 0) return state;
  return {
    ...state,
    playbackRate: 1 + amount * (settings.peak_speed - 1),
    scale: state.scale * (1 + amount * 0.018),
    filter: `blur(${(amount * 1.4).toFixed(2)}px)`,
  };
}

function speedRampAmount({
  input,
  timeMs,
  durationMs,
  rampInMs,
  rampOutMs,
  easing,
}: {
  input: MotionlyRenderProps;
  timeMs: number;
  durationMs: number;
  rampInMs: number;
  rampOutMs: number;
  easing: MotionSettings["speed_ramp"]["easing"];
}) {
  const boundaries = [
    ...input.editPlan.cuts.map((cut) => cut.start_ms),
    ...input.editPlan.zoom_moments.map((moment) => moment.timestamp_ms),
  ].filter((boundary) => boundary > 0);
  let amount = 0;
  for (const boundary of boundaries) {
    const delta = timeMs - boundary;
    if (delta < 0 && Math.abs(delta) <= rampInMs) {
      amount = Math.max(amount, ease(1 - Math.abs(delta) / rampInMs, easing));
    }
    if (delta >= 0 && delta <= rampOutMs) {
      amount = Math.max(amount, ease(1 - delta / rampOutMs, easing));
    }
  }
  if (!boundaries.length) {
    if (timeMs <= rampInMs) amount = Math.max(amount, ease(timeMs / rampInMs, easing));
    const remaining = durationMs - timeMs;
    if (remaining <= rampOutMs) amount = Math.max(amount, ease(remaining / rampOutMs, easing));
  }
  return clamp(amount, 0, 1);
}

function applyBeatImpact(
  state: MotionState,
  input: MotionlyRenderProps,
  motion: MotionSettings | undefined,
  timeMs: number,
  frame: number,
) {
  const settings = motion?.beat_impact;
  const beats = input.musicSelection?.track?.beat_map ?? [];
  if (!settings?.enabled || !beats.length || settings.max_impacts <= 0) return state;
  const impactBeats = beats.slice(0, settings.max_impacts);
  const nearest = impactBeats.reduce(
    (current, beat) => (Math.abs(beat - timeMs) < Math.abs(current - timeMs) ? beat : current),
    impactBeats[0],
  );
  const distance = Math.abs(nearest - timeMs);
  const windowMs = 150;
  if (distance > windowMs) return state;
  const amount = ease(1 - distance / windowMs, "ease_out") * settings.intensity;
  return {
    ...state,
    scale: state.scale * (1 + (settings.zoom - 1) * amount * 2.4),
    translateX: state.translateX + Math.sin(frame * 1.7) * settings.shake_px * amount,
    translateY: state.translateY + Math.cos(frame * 2.1) * settings.shake_px * amount,
    rotationDeg: state.rotationDeg + Math.sin(frame * 0.9) * amount * 0.8,
  };
}

function applyClipMotionEffects(
  state: MotionState,
  input: MotionlyRenderProps,
  motion: MotionSettings | undefined,
  clipId: string | null,
  timeMs: number,
  frame: number,
  durationInFrames: number,
  fps: number,
): MotionState {
  if (!clipId) return state;
  const effects = motion?.clip_effects?.[clipId] ?? [];
  if (!effects.length) return state;
  const effectSet = new Set(effects);
  let next = { ...state };
  if (effectSet.has("ken_burns")) {
    next = applyKenBurnsSettings(next, motion?.ken_burns, frame, durationInFrames);
  }
  if (effectSet.has("zoom_punch")) {
    const nearest = input.editPlan.zoom_moments.find(
      (moment) => Math.abs(moment.timestamp_ms - timeMs) <= 220,
    );
    const punch = nearest ? Math.min(1.12, nearest.intensity) : 1;
    next = { ...next, scale: next.scale * punch };
  }
  if (effectSet.has("speed_ramp")) {
    const settings = motion?.speed_ramp;
    if (settings?.enabled) {
      const durationMs = (durationInFrames / fps) * 1000;
      const amount = clipSpeedRampAmount(timeMs, durationMs, settings);
      next = {
        ...next,
        playbackRate: next.playbackRate * (1 + amount * (settings.peak_speed - 1)),
        scale: next.scale * (1 + amount * 0.018),
        filter: [next.filter, `blur(${(amount * 1.4).toFixed(2)}px)`].filter(Boolean).join(" ") || undefined,
      };
    }
  }
  if (effectSet.has("beat_impact")) {
    const settings = motion?.beat_impact;
    const beats = input.musicSelection?.track?.beat_map ?? [];
    if (settings?.enabled && beats.length && settings.max_impacts > 0) {
      const nearest = beats.slice(0, settings.max_impacts).reduce(
        (current, beat) => (Math.abs(beat - timeMs) < Math.abs(current - timeMs) ? beat : current),
        beats[0],
      );
      const distance = Math.abs(nearest - timeMs);
      const windowMs = 150;
      if (distance <= windowMs) {
        const amount = ease(1 - distance / windowMs, "ease_out") * settings.intensity;
        next = {
          ...next,
          scale: next.scale * (1 + (settings.zoom - 1) * amount * 2.4),
          translateX: next.translateX + Math.sin(frame * 1.7) * settings.shake_px * amount,
          translateY: next.translateY + Math.cos(frame * 2.1) * settings.shake_px * amount,
          rotationDeg: next.rotationDeg + Math.sin(frame * 0.9) * amount * 0.8,
        };
      }
    }
  }
  if (effectSet.has("motion_blur")) {
    next = { ...next, filter: [next.filter, "blur(0.65px)"].filter(Boolean).join(" ") || undefined };
  }
  if (effectSet.has("flash")) {
    const flashAmount = pulseAroundBoundaries({
      timeMs,
      boundaries: input.editPlan.cuts.map((cut) => cut.start_ms).filter((start) => start > 0),
      fallbackDurationMs: (durationInFrames / fps) * 1000,
      rampInMs: 50,
      rampOutMs: 180,
      easing: "ease_out",
    });
    next = {
      ...next,
      filter: [next.filter, `brightness(${1 + flashAmount * 0.25})`].filter(Boolean).join(" ") || undefined,
    };
  }
  if (effectSet.has("glitch")) {
    const glitchAmount = pulseAroundBoundaries({
      timeMs,
      boundaries: input.editPlan.cuts.map((cut) => cut.start_ms).filter((start) => start > 0),
      fallbackDurationMs: (durationInFrames / fps) * 1000,
      rampInMs: 60,
      rampOutMs: 220,
      easing: "ease_out",
    });
    next = {
      ...next,
      translateX: next.translateX + Math.sin(timeMs * 0.12) * glitchAmount * 1.4,
      filter: [next.filter, `hue-rotate(${glitchAmount * 12}deg)`].filter(Boolean).join(" ") || undefined,
    };
  }
  return next;
}

function pulseAroundBoundaries({
  timeMs,
  boundaries,
  fallbackDurationMs,
  rampInMs,
  rampOutMs,
  easing,
}: {
  timeMs: number;
  boundaries: number[];
  fallbackDurationMs: number;
  rampInMs: number;
  rampOutMs: number;
  easing: MotionSettings["speed_ramp"]["easing"] | "linear";
}) {
  const activeBoundaries = boundaries.length ? boundaries : [0, fallbackDurationMs];
  let amount = 0;
  for (const boundary of activeBoundaries) {
    const delta = timeMs - boundary;
    if (delta < 0 && Math.abs(delta) <= rampInMs) {
      amount = Math.max(amount, ease(1 - Math.abs(delta) / rampInMs, easing));
    }
    if (delta >= 0 && delta <= rampOutMs) {
      amount = Math.max(amount, ease(1 - delta / rampOutMs, easing));
    }
  }
  return clamp(amount, 0, 1);
}

function clipSpeedRampAmount(
  timeMs: number,
  durationMs: number,
  settings: NonNullable<MotionSettings["speed_ramp"]>,
) {
  let amount = 0;
  if (timeMs <= settings.ramp_in_ms) {
    amount = Math.max(amount, ease(timeMs / settings.ramp_in_ms, settings.easing));
  }
  const remaining = durationMs - timeMs;
  if (remaining <= settings.ramp_out_ms) {
    amount = Math.max(amount, ease(remaining / settings.ramp_out_ms, settings.easing));
  }
  return clamp(amount, 0, 1);
}

function motionTransformCss(state: MotionState) {
  return [
    `translate(${state.translateX.toFixed(2)}px, ${state.translateY.toFixed(2)}px)`,
    `scale(${state.scale.toFixed(4)})`,
    `rotate(${state.rotationDeg.toFixed(3)}deg)`,
  ].join(" ");
}

function clampMotionState(state: MotionState): MotionState {
  return {
    ...state,
    scale: clamp(state.scale, 0.5, 3),
    xPct: clamp(state.xPct, 0, 100),
    yPct: clamp(state.yPct, 0, 100),
    rotationDeg: clamp(state.rotationDeg, -45, 45),
    opacity: clamp(state.opacity, 0, 1),
    playbackRate: clamp(state.playbackRate, 0.2, 4),
  };
}

function activeZoom(
  zoomMoments: MotionlyRenderProps["editPlan"]["zoom_moments"],
  frame: number,
  fps: number,
) {
  const timeMs = (frame / fps) * 1000;
  const match = zoomMoments.find(
    (moment) => Math.abs(moment.timestamp_ms - timeMs) <= 750,
  );
  if (!match) return 1;
  const distance = Math.abs(match.timestamp_ms - timeMs) / 750;
  return interpolate(1 - distance, [0, 1], [1, match.intensity], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
}

function gradeFilter(colorGrade: string, fallback: string) {
  const grade = colorGrade || fallback;
  if (grade.includes("gold") || grade.includes("warm"))
    return "contrast(1.08) saturate(1.08) sepia(0.12)";
  if (grade.includes("mono")) return "grayscale(0.92) contrast(1.14)";
  if (grade.includes("studio")) return "contrast(1.06) saturate(0.96)";
  return "contrast(1.04) saturate(1.04)";
}

function Vignette({ template }: { template: TemplateDefinition }) {
  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(180deg, ${template.background}88 0%, transparent 25%, transparent 68%, ${template.background}cc 100%)`,
      }}
    />
  );
}

function HookCard({
  input,
  template,
}: {
  input: MotionlyRenderProps;
  template: TemplateDefinition;
}) {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 12, 48, 60], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const firstCaption = input.captions[0]?.text ?? template.name;
  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "center",
        padding: 90,
        opacity,
      }}
    >
      <div
        style={{
          borderLeft: `8px solid ${template.accent}`,
          padding: "24px 30px",
          maxWidth: 780,
          background: "rgba(0,0,0,0.42)",
          color: template.foreground,
          fontFamily: template.fontFamily,
          fontSize: 54,
          fontWeight: 800,
          lineHeight: 1.05,
        }}
      >
        {firstCaption}
      </div>
    </AbsoluteFill>
  );
}

function OutroCard({ template }: { template: TemplateDefinition }) {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill
      style={{ justifyContent: "center", alignItems: "center", opacity }}
    >
      <div
        style={{
          color: template.foreground,
          fontFamily: template.fontFamily,
          fontSize: 62,
          fontWeight: 900,
          textAlign: "center",
          textShadow: "0 8px 26px rgba(0,0,0,0.72)",
        }}
      >
        Follow for the next one
      </div>
    </AbsoluteFill>
  );
}

function Waveform({ accent }: { accent: string }) {
  const frame = useCurrentFrame();
  const bars = Array.from({ length: 34 }, (_, index) => {
    const height = 24 + Math.abs(Math.sin(frame / 8 + index * 0.7)) * 92;
    return (
      <div
        key={index}
        style={{
          width: 10,
          height,
          borderRadius: 12,
          background: accent,
          opacity: 0.72,
        }}
      />
    );
  });
  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-start",
        alignItems: "center",
        paddingTop: 132,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {bars}
      </div>
    </AbsoluteFill>
  );
}

function ease(
  progress: number,
  easing: MotionSettings["ken_burns"]["easing"] | "linear",
) {
  const t = clamp(progress, 0, 1);
  if (easing === "ease_in") return t * t;
  if (easing === "ease_out") return 1 - Math.pow(1 - t, 2);
  if (easing === "spring") return clamp(1 - Math.cos(t * Math.PI * 1.5) * Math.pow(1 - t, 2), 0, 1);
  if (easing === "ease_in_out") return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  return t;
}

function lerp(from: number, to: number, progress: number) {
  return from + (to - from) * progress;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}
