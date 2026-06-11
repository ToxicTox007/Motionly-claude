import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { CaptionSegment, TranscriptWord } from "@motionly/shared";
import type { TemplateDefinition } from "../types";

type CaptionsProps = {
  segments: CaptionSegment[];
  words: TranscriptWord[];
  template: TemplateDefinition;
};

export function AnimatedCaptions({ segments, words, template }: CaptionsProps) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;
  const segment = segments.find((item) => timeMs >= item.start_ms && timeMs <= item.end_ms);
  const activeWord = words.find((word) => timeMs >= word.start_ms && timeMs <= word.end_ms);
  const entrance = spring({ frame, fps, config: { damping: 18, stiffness: 120 } });
  const y = interpolate(entrance, [0, 1], [64, 0]);
  const pulse =
    template.caption_animation === "pulse"
      ? 1 + Math.sin(frame / 4) * 0.025
      : 1;

  if (!segment) return null;

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        padding: "0 76px 180px",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          maxWidth: 840,
          transform: `translateY(${y}px) scale(${pulse})`,
          textAlign: "center",
          color: template.foreground,
          fontFamily: template.fontFamily,
          fontSize: 76,
          fontWeight: 900,
          lineHeight: 1.05,
          textShadow: "0 6px 24px rgba(0,0,0,0.72)",
          textTransform: "uppercase",
        }}
      >
        {segment.words.map((word, index) => {
          const isActive = activeWord?.index === word.index;
          return (
            <React.Fragment key={`${word.index}-${word.start_ms}`}>
              <span
                style={{
                  color: isActive ? template.accent : template.foreground,
                  filter:
                    isActive && template.caption_animation === "gold_shimmer"
                      ? "drop-shadow(0 0 14px rgba(214,179,95,0.8))"
                      : undefined,
                  transform: isActive ? "scale(1.06)" : "scale(1)",
                  display: "inline-block",
                  transition: "color 80ms linear",
                }}
              >
                {word.text}
              </span>
              {index < segment.words.length - 1 ? " " : null}
            </React.Fragment>
          );
        })}
      </div>
    </AbsoluteFill>
  );
}
