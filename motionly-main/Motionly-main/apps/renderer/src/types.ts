import type {
  CaptionSegment,
  EditPlan,
  RenderSpecs,
  RenderTemplateId,
  RenderTemplateMetadata,
  TranscriptWord,
  MusicMixSettings,
  EffectsConfigSummary,
} from "@motionly/shared";

export type RendererAsset = {
  id: string;
  kind: "video" | "image" | "audio";
  src: string;
  start_ms?: number;
  end_ms?: number;
  slot_id?: string;
  provider?: string;
  license?: string;
  crossfade_ms?: number;
  score?: number;
};

export type MotionlyRenderProps = {
  sourceVideo: {
    src: string;
    width: number;
    height: number;
    duration_ms: number;
  };
  editPlan: EditPlan;
  captions: CaptionSegment[];
  words: TranscriptWord[];
  assets: RendererAsset[];
  templateId: RenderTemplateId;
  exportSettings: RenderSpecs;
  musicSelection?: {
    selection_id?: string | null;
    music_track_id?: string | null;
    music_volume?: number;
    sfx_intensity?: number;
    track?: import("@motionly/shared").MusicTrackSummary | null;
    mix?: MusicMixSettings;
  };
  effectsConfig?: EffectsConfigSummary | null;
};

export type TemplateDefinition = RenderTemplateMetadata & {
  accent: string;
  background: string;
  foreground: string;
  fontFamily: string;
};
