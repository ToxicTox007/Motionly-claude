import { Composition } from "remotion";
import { MotionlyPremiumComposition } from "./Composition";
import type { MotionlyRenderProps } from "./types";
import { outputDurationMs } from "./timeline";

const defaultRenderProps: MotionlyRenderProps = {
  sourceVideo: {
    src: "",
    width: 1080,
    height: 1920,
    duration_ms: 30000,
  },
  editPlan: {
    schema_version: 1,
    style: "luxury",
    target_duration_s: 30,
    cuts: [],
    caption_highlights: [],
    broll_requests: [],
    zoom_moments: [],
    music_mood: "none",
    effects: [],
    color_grade: "clean_founder",
    risk_flags: [],
    manual_elements: [],
  },
  captions: [],
  words: [],
  assets: [],
  templateId: "luxury_reel_v1",
  exportSettings: {
    width: 1080,
    height: 1920,
    fps: 30,
    video_codec: "h264",
    audio_codec: "aac",
    pixel_format: "yuv420p",
    format: "mp4",
  },
};

export function RemotionRoot() {
  return (
    <Composition
      id="MotionlyPremium"
      component={MotionlyPremiumComposition}
      durationInFrames={durationInFrames(defaultRenderProps)}
      fps={defaultRenderProps.exportSettings.fps}
      width={defaultRenderProps.exportSettings.width}
      height={defaultRenderProps.exportSettings.height}
      defaultProps={defaultRenderProps}
      calculateMetadata={({ props }) => ({
        durationInFrames: durationInFrames(props),
        fps: props.exportSettings.fps,
        width: props.exportSettings.width,
        height: props.exportSettings.height,
      })}
    />
  );
}

function durationInFrames(props: MotionlyRenderProps) {
  return Math.max(
    1,
    Math.ceil((outputDurationMs(props) / 1000) * props.exportSettings.fps),
  );
}
