import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type {
  CaptionSegment,
  CaptionStyle,
  TranscriptWord,
} from "@motionly/shared";
import {
  millisecondsToFrameEnd,
  millisecondsToFrameStart,
} from "@motionly/shared";
import type { TemplateDefinition } from "../types";

type CaptionsProps = {
  segments: CaptionSegment[];
  words: TranscriptWord[];
  style?: CaptionStyle | null;
  template: TemplateDefinition;
};

export function AnimatedCaptions({
  segments,
  words,
  style,
  template,
}: CaptionsProps) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;
  const segment = segments.find(
    (item) => timeMs >= item.start_ms && timeMs <= item.end_ms,
  );
  if (!segment) return null;

  const segmentStartFrame = millisecondsToFrameStart(segment.start_ms, fps);
  const localFrame = Math.max(0, frame - segmentStartFrame);
  const remainingFrames = millisecondsToFrameEnd(segment.end_ms - timeMs, fps);
  const activeWord = (segment.words.length ? segment.words : words).find(
    (word) => timeMs >= word.start_ms && timeMs <= word.end_ms,
  );
  const entrance = spring({
    frame: localFrame,
    fps,
    config: { damping: 18, stiffness: 140, mass: 0.72 },
  });
  const mode = style?.mode ?? "word_pop";
  const placement = captionPlacement(style, mode);
  const animation = captionAnimation({
    mode,
    animation: style?.animation ?? "fade_up",
    entrance,
    frame,
    localFrame,
    remainingFrames,
  });
  const textColor = style?.text_color ?? template.foreground;
  const accentColor = style?.accent_color ?? template.accent;
  const fontSize = style?.font_size_px
    ? Math.round(style.font_size_px * 1.45)
    : 76;
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
          maxWidth:
            mode === "creator_ticker"
              ? 960
              : mode === "impact_stamp"
                ? 1000
                : 840,
          width:
            mode === "impact_stamp" || mode === "creator_ticker"
              ? "88%"
              : undefined,
          ...(placement.customPosition
            ? {
                position: "absolute",
                left: placement.customPosition.left,
                top: placement.customPosition.top,
                transform: `translate(-50%, -50%) ${animation.transform}`,
              }
            : {
                transform: animation.transform,
              }),
          opacity: animation.opacity,
          textAlign: mode === "creator_ticker" ? "left" : "center",
          color: textColor,
          fontFamily: style?.font_family ?? template.fontFamily,
          fontSize,
          fontWeight,
          lineHeight: mode === "minimal_line" ? 1.18 : 1.05,
          letterSpacing: style?.letter_spacing_px,
          backgroundColor: captionBackground(style),
          border:
            style?.border_color && style.background_opacity > 0
              ? `2px solid ${style.border_color}`
              : undefined,
          borderRadius:
            mode === "creator_ticker" ? 999 : (style?.border_radius_px ?? 0),
          padding:
            style?.background_opacity && style.background_opacity > 0
              ? `${style.padding_y_px}px ${style.padding_x_px}px`
              : undefined,
          boxShadow:
            mode === "minimal_line" ? undefined : (style?.shadow ?? undefined),
          textShadow: style?.shadow ?? "0 6px 24px rgba(0,0,0,0.72)",
          WebkitTextStroke:
            style?.stroke_width_px && style.stroke_color
              ? `${style.stroke_width_px}px ${style.stroke_color}`
              : undefined,
          textTransform,
        }}
      >
        {mode === "educational_card" ? (
          <span style={stepBadgeStyle(accentColor)}>
            STEP {segment.index + 1}
          </span>
        ) : null}
        {renderWords({
          segment,
          activeWord,
          mode,
          textColor,
          accentColor,
          goldShimmer: template.caption_animation === "gold_shimmer",
        })}
      </div>
    </AbsoluteFill>
  );
}

function renderWords({
  segment,
  activeWord,
  mode,
  textColor,
  accentColor,
  goldShimmer,
}: {
  segment: CaptionSegment;
  activeWord?: TranscriptWord;
  mode: string;
  textColor: string;
  accentColor: string;
  goldShimmer: boolean;
}) {
  const displayWords = segment.words.length
    ? segment.words
    : segment.text.split(/\s+/).map((text, index) => ({
        index,
        text,
        start_ms: segment.start_ms,
        end_ms: segment.end_ms,
      }));
  return displayWords.map((word, index) => {
    const isActive = activeWord?.index === word.index;
    const normalized = word.text.replace(/[^\w'-]/g, "");
    const isHighlighted =
      isActive || segment.highlight_words?.includes(normalized);
    return (
      <React.Fragment key={`${word.index}-${word.start_ms}-${index}`}>
        <span
          style={wordStyle({
            mode,
            isActive: Boolean(isActive),
            isHighlighted: Boolean(isHighlighted),
            textColor,
            accentColor,
            goldShimmer,
          })}
        >
          {word.text}
        </span>
        {index < displayWords.length - 1 ? " " : null}
      </React.Fragment>
    );
  });
}

function captionPlacement(style?: CaptionStyle | null, mode = "word_pop") {
  if (style?.position_x_pct != null && style.position_y_pct != null) {
    return {
      justifyContent: "flex-start" as const,
      padding: "0",
      customPosition: {
        left: `${Math.max(12, Math.min(88, style.position_x_pct))}%`,
        top: `${Math.max(12, Math.min(82, style.position_y_pct))}%`,
      },
    };
  }
  if (style?.placement === "upper") {
    return { justifyContent: "flex-start" as const, padding: "190px 76px 0" };
  }
  if (style?.placement === "middle") {
    return { justifyContent: "center" as const, padding: "0 76px" };
  }
  const bottomPct = style?.safe_area_bottom_pct ?? 20;
  const bottomPadding =
    mode === "creator_ticker"
      ? Math.max(120, bottomPct * 8)
      : Math.max(180, Math.round(bottomPct * 10));
  return {
    justifyContent: "flex-end" as const,
    padding: `0 76px ${bottomPadding}px`,
  };
}

function captionAnimation({
  mode,
  animation,
  entrance,
  frame,
  localFrame,
  remainingFrames,
}: {
  mode: string;
  animation: string;
  entrance: number;
  frame: number;
  localFrame: number;
  remainingFrames: number;
}) {
  const exitOpacity =
    remainingFrames < 8 ? interpolate(remainingFrames, [0, 8], [0, 1]) : 1;
  if (animation === "impact_stamp" || mode === "impact_stamp") {
    const shake =
      localFrame < 10 ? Math.sin(frame * 1.7) * (10 - localFrame) * 0.9 : 0;
    const scale = interpolate(entrance, [0, 0.72, 1], [1.28, 0.96, 1]);
    return {
      opacity: exitOpacity,
      transform: `translateX(${shake}px) scale(${scale})`,
    };
  }
  if (animation === "ticker" || mode === "creator_ticker") {
    const y = interpolate(entrance, [0, 1], [48, 0]);
    return {
      opacity: exitOpacity,
      transform: `translateY(${y}px)`,
    };
  }
  if (animation === "minimal_fade" || mode === "minimal_line") {
    const opacity = Math.min(
      exitOpacity,
      interpolate(localFrame, [0, 18], [0, 1], { extrapolateRight: "clamp" }),
    );
    const y = interpolate(localFrame, [0, 18], [16, 0], {
      extrapolateRight: "clamp",
    });
    return {
      opacity,
      transform: `translateY(${y}px)`,
    };
  }
  const y = interpolate(entrance, [0, 1], [64, 0]);
  const pulse = animation === "pop" ? 1 + Math.sin(frame / 4) * 0.025 : 1;
  return {
    opacity: exitOpacity,
    transform: `translateY(${y}px) scale(${pulse})`,
  };
}

function wordStyle({
  mode,
  isActive,
  isHighlighted,
  textColor,
  accentColor,
  goldShimmer,
}: {
  mode: string;
  isActive: boolean;
  isHighlighted: boolean;
  textColor: string;
  accentColor: string;
  goldShimmer: boolean;
}): React.CSSProperties {
  const activeColor = isHighlighted ? accentColor : textColor;
  if (mode === "creator_ticker" && isHighlighted) {
    return {
      color: "#07111f",
      backgroundColor: accentColor,
      borderRadius: 999,
      padding: "2px 10px",
      display: "inline-block",
      transform: isActive ? "scale(1.08)" : "scale(1)",
    };
  }
  if (mode === "minimal_line" && isHighlighted) {
    return {
      color: accentColor,
      borderBottom: `2px solid ${accentColor}`,
      paddingBottom: 2,
      display: "inline-block",
    };
  }
  return {
    color: activeColor,
    filter:
      isHighlighted && goldShimmer
        ? "drop-shadow(0 0 14px rgba(214,179,95,0.8))"
        : undefined,
    transform: isActive ? "scale(1.06)" : "scale(1)",
    display: "inline-block",
    transition: "color 80ms linear",
  };
}

function stepBadgeStyle(accentColor: string): React.CSSProperties {
  return {
    display: "inline-block",
    marginRight: 14,
    padding: "5px 10px",
    borderRadius: 999,
    backgroundColor: accentColor,
    color: "#ffffff",
    fontSize: 24,
    fontWeight: 900,
    letterSpacing: 1,
    verticalAlign: "middle",
  };
}

function captionBackground(style?: CaptionStyle | null) {
  if (!style?.background_color || style.background_opacity <= 0)
    return undefined;
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
