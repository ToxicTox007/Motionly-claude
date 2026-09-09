import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  OffthreadVideo,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { CSSProperties } from "react";
import {
  durationMsToFrameCount,
  millisecondsToFrameStart,
} from "@motionly/shared";
import { manualVisualBackground, safeRendererMediaUrl } from "../manual-media-policy";
import type { ManualElement } from "@motionly/shared";

export function ManualElements({
  elements = [],
}: {
  elements?: ManualElement[];
}) {
  const { fps } = useVideoConfig();
  return (
    <>
      {elements.map((element) => {
        const from = millisecondsToFrameStart(element.start_ms, fps);
        const duration = durationMsToFrameCount(
          element.end_ms - element.start_ms,
          fps,
        );
        const audioSrc = safeRendererMediaUrl(
          element.properties.assetUrl ?? element.properties.src,
          { allowDataAudio: true },
        );
        if (
          (element.type === "music" || element.type === "sfx") &&
          audioSrc
        ) {
          const volume = Math.max(
            0,
            Math.min(1, Number(element.properties.volume ?? 80) / 100),
          );
          return (
            <Sequence key={element.id} from={from} durationInFrames={duration}>
              <Audio src={audioSrc} volume={volume} />
            </Sequence>
          );
        }
        return (
          <Sequence key={element.id} from={from} durationInFrames={duration}>
            <ManualVisual element={element} durationInFrames={duration} />
          </Sequence>
        );
      })}
    </>
  );
}

function ManualVisual({
  element,
  durationInFrames,
}: {
  element: ManualElement;
  durationInFrames: number;
}) {
  const frame = useCurrentFrame();
  const fade = interpolate(
    frame,
    [
      0,
      Math.min(8, durationInFrames / 3),
      Math.max(9, durationInFrames - 8),
      durationInFrames,
    ],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const properties = element.properties ?? {};
  const visualSrc = safeRendererMediaUrl(
    properties.assetUrl ?? properties.src,
  );
  if (element.type === "broll" && visualSrc) {
    return (
      <AbsoluteFill style={{ opacity: fade * element.opacity }}>
        <OffthreadVideo
          src={visualSrc}
          muted
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </AbsoluteFill>
    );
  }
  if (element.type === "graphic" && visualSrc) {
    const objectFit = properties.objectFit === "cover" ? "cover" : "contain";
    return (
      <AbsoluteFill style={{ pointerEvents: "none" }}>
        <Img
          src={visualSrc}
          style={{
            position: "absolute",
            left: `${element.x_pct}%`,
            top: `${element.y_pct}%`,
            width: `${element.width_pct}%`,
            height: `${element.height_pct}%`,
            transform: `translate(-50%,-50%) rotate(${element.rotation_deg}deg)`,
            opacity: fade * element.opacity,
            objectFit,
          }}
        />
      </AbsoluteFill>
    );
  }
  if (element.type === "transition") {
    const transition = String(properties.transition ?? "dissolve");
    const progress = frame / Math.max(1, durationInFrames - 1);
    const isFade = transition.includes("fade");
    return (
      <AbsoluteFill
        style={{
          pointerEvents: "none",
          opacity: isFade
            ? 1 - Math.abs(progress * 2 - 1)
            : 0.42 * (1 - progress),
          background: isFade
            ? "#000"
            : `linear-gradient(90deg, transparent ${Math.max(0, progress * 100 - 25)}%, rgba(255,255,255,.35) ${progress * 100}%, transparent ${Math.min(100, progress * 100 + 25)}%)`,
          mixBlendMode: isFade ? "normal" : "screen",
        }}
      />
    );
  }
  if (
    !(["text", "caption", "overlay", "shape", "graphic"] as string[]).includes(
      element.type,
    )
  )
    return null;
  const shape = String(properties.shape ?? "");
  const background = manualVisualBackground(properties, element.type);
  const style: CSSProperties = {
    position: "absolute",
    left: `${element.x_pct}%`,
    top: `${element.y_pct}%`,
    width: `${element.width_pct}%`,
    height: `${element.height_pct}%`,
    transform: `translate(-50%,-50%) rotate(${element.rotation_deg}deg)`,
    opacity: fade * element.opacity,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    textAlign: "center",
    overflow: "hidden",
    padding: 8,
    color: String(properties.color ?? "#fff"),
    background,
    borderRadius:
      shape === "circle" ? "9999px" : Number(properties.borderRadius ?? 4),
    fontSize: Number(
      properties.fontSize ?? (element.type === "caption" ? 48 : 36),
    ),
    fontWeight: Number(properties.fontWeight ?? 800),
    lineHeight: 1.05,
  };
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={style}>
        {element.type === "shape" && !["arrow", "line"].includes(shape)
          ? null
          : String(properties.content ?? element.name)}
      </div>
    </AbsoluteFill>
  );
}
