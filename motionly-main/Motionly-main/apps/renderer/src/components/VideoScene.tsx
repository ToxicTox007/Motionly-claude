import {
  AbsoluteFill,
  interpolate,
  OffthreadVideo,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { MotionlyRenderProps, TemplateDefinition } from "../types";

type VideoSceneProps = {
  input: MotionlyRenderProps;
  template: TemplateDefinition;
};

export function VideoScene({ input, template }: VideoSceneProps) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const zoom = activeZoom(input.editPlan.zoom_moments, frame, fps);
  const introFrames = template.intro ? Math.min(60, durationInFrames / 5) : 0;
  const outroFrames = template.outro ? Math.min(72, durationInFrames / 5) : 0;
  const outroStart = Math.max(0, durationInFrames - outroFrames);
  const backgroundRemoval = input.effectsConfig?.background_removal;
  const framing = input.exportSettings.framing;
  const crop = framing?.crop ?? { x: 0.5, y: 0.5, zoom: 1 };
  const framingZoom =
    framing?.fit_mode === "custom" ? Math.max(1, Math.min(3, crop.zoom)) : 1;
  const activeZoomScale = zoom * framingZoom;
  const sourceStyle = {
    width: "100%",
    height: "100%",
    objectFit: framing?.fit_mode === "fit" ? "contain" as const : "cover" as const,
    objectPosition: `${Math.round(crop.x * 100)}% ${Math.round(crop.y * 100)}%`,
    transform: `scale(${activeZoomScale})`,
    transformOrigin: `${Math.round(crop.x * 100)}% ${Math.round(crop.y * 100)}%`,
    filter: gradeFilter(
      input.editPlan.color_grade,
      template.default_color_grade,
    ),
    ...(backgroundRemoval?.enabled
      ? subjectIsolationStyle(backgroundRemoval.feather_px)
      : {}),
  };

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
      <OffthreadVideo src={input.sourceVideo.src} muted={false} style={sourceStyle} />
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
  const isolateBroll = Boolean(
    input.effectsConfig?.background_removal.enabled &&
    input.effectsConfig.background_removal.apply_to_broll,
  );
  return (
    <OffthreadVideo
      src={asset.src}
      muted
      style={{
        width: "100%",
        height: "100%",
        objectFit: "cover",
        opacity,
        transform: `translateX(${slideX}px) scale(${scale})`,
        filter: "contrast(1.04) saturate(1.05)",
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

function subjectIsolationStyle(featherPx: number) {
  const blur = Math.max(0, Math.min(20, featherPx / 8));
  return {
    clipPath: "ellipse(38% 48% at 50% 50%)",
    boxShadow: `0 0 ${blur}px rgba(255,255,255,0.26)`,
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
