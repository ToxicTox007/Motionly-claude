import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { CaptionSegment, CaptionStyle, TranscriptWord } from "@motionly/shared";
import type { TemplateDefinition } from "../types";

type CaptionsProps = {
  segments: CaptionSegment[];
  words: TranscriptWord[];
  style?: CaptionStyle | null;
  template: TemplateDefinition;
};

export function AnimatedCaptions({ segments, words, style, template }: CaptionsProps) {
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
  const placement = captionPlacement(style);
  const textColor = style?.text_color ?? template.foreground;
  const accentColor = style?.accent_color ?? template.accent;
  const fontSize = style?.font_size_px ? Math.round(style.font_size_px * 1.45) : 76;
  const fontWeight = style?.font_weight ?? 900;
  const textTransform =
    style?.text_case === "uppercase"
      ? "uppercase"
      : style?.text_case === "title"
        ? "capitalize"
        : "none";

  return (
    <AbsoluteFill
      style={{
        justifyContent: placement.justifyContent,
        alignItems: "center",
        padding: placement.padding,
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          maxWidth: 840,
          ...(placement.customPosition
            ? {
                position: "absolute",
                left: placement.customPosition.left,
                top: placement.customPosition.top,
                transform: `translate(-50%, -50%) translateY(${y}px) scale(${pulse})`,
              }
            : {
                transform: `translateY(${y}px) scale(${pulse})`,
              }),
          textAlign: "center",
          color: textColor,
          fontFamily: style?.font_family ?? template.fontFamily,
          fontSize,
          fontWeight,
          lineHeight: 1.05,
          textShadow: style?.shadow ?? "0 6px 24px rgba(0,0,0,0.72)",
          WebkitTextStroke:
            style?.stroke_width_px && style.stroke_color
              ? `${style.stroke_width_px}px ${style.stroke_color}`
              : undefined,
          textTransform,
        }}
      >
        {segment.words.map((word, index) => {
          const isActive = activeWord?.index === word.index;
          const isHighlighted = isActive || segment.highlight_words?.includes(word.text);
          return (
            <React.Fragment key={`${word.index}-${word.start_ms}`}>
              <span
                style={{
                  color: isHighlighted ? accentColor : textColor,
                  filter:
                    isHighlighted && template.caption_animation === "gold_shimmer"
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

function captionPlacement(style?: CaptionStyle | null) {
  if (style?.position_x_pct != null && style.position_y_pct != null) {
    return {
      justifyContent: "flex-start" as const,
      padding: "0",
      customPosition: {
        left: `${style.position_x_pct}%`,
        top: `${style.position_y_pct}%`,
      },
    };
  }
  if (style?.placement === "upper") {
    return { justifyContent: "flex-start" as const, padding: "190px 76px 0" };
  }
  if (style?.placement === "middle") {
    return { justifyContent: "center" as const, padding: "0 76px" };
  }
  const bottomPct = style?.safe_area_bottom_pct ?? 12;
  return {
    justifyContent: "flex-end" as const,
    padding: `0 76px ${Math.max(120, Math.round(bottomPct * 10))}px`,
  };
}
