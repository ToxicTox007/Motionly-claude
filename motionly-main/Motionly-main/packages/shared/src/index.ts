export type WorkspaceRole = "owner" | "admin" | "member";
export type WorkspaceMemberStatus = "active" | "invited" | "disabled";
export type ProjectStatus = "draft" | "ready" | "failed";
export type UploadStatus =
  | "created"
  | "uploading"
  | "uploaded"
  | "processing"
  | "ready"
  | "failed";
export type LanguageHint =
  | "auto"
  | "en"
  | "hi"
  | "es"
  | "pt"
  | "fr"
  | "de"
  | "ar";
export type TranscriptLanguage = Exclude<LanguageHint, "auto"> | "unknown";
export type TranscriptStatus =
  | "queued"
  | "processing"
  | "ready"
  | "failed"
  | "captions_unavailable";
export type VisualManifestStatus = "queued" | "processing" | "ready" | "failed";
export type CameraMotion =
  | "static"
  | "pan"
  | "tilt"
  | "push_in"
  | "handheld"
  | "unknown";
export type SceneType =
  | "talking_head"
  | "screen_recording"
  | "product"
  | "landscape"
  | "crowd"
  | "text_heavy"
  | "abstract"
  | "unknown";
export type RenderJobStatus =
  | "queued"
  | "preparing"
  | "rendering"
  | "uploading"
  | "complete"
  | "failed"
  | "canceled";
export type ExportFormat = "mp4";
export type EditPlanStyleKey =
  | "motivational"
  | "emotional"
  | "cinematic"
  | "educational"
  | "luxury"
  | "storytelling"
  | "dramatic"
  | "fast_paced"
  | "clean_minimal"
  | "creator_influencer"
  | "founder"
  | "travel"
  | "fashion"
  | "podcast";
export type RenderTemplateId =
  | "luxury_reel_v1"
  | "founder_reel_v1"
  | "travel_reel_v1"
  | "podcast_reel_v1"
  | "fashion_reel_v1"
  | "talking_head_reel_v1"
  | "product_promo_reel_v1";
export type RenderEngine = "ffmpeg_basic" | "remotion_premium";
export type BrollDensity = "none" | "low" | "medium" | "high";
export type EditPlanStatus = "ready" | "failed";
export type BrollSlotStatus = "ready" | "disabled" | "failed";
export type StockProviderKey = "fixture" | "pexels" | "pixabay";
export type MusicProviderKey = "fixture" | "licensed_catalog";
export type MusicSelectionStatus = "ready" | "failed";
export type VisualPresetKey =
  | "vogue_luxury"
  | "champagne_gold"
  | "dubai_influencer"
  | "old_money"
  | "clean_founder"
  | "cinematic_hotel";
export type MotionEffectKey =
  | "zoom_punch"
  | "speed_ramp"
  | "motion_blur"
  | "flash"
  | "glitch"
  | "broll_slide_in"
  | "gradient_ramp";
export type EffectsStatus = "ready" | "failed";
export type RegenerationLayer =
  | "transcript"
  | "captions"
  | "plan"
  | "cuts"
  | "broll"
  | "music"
  | "effects"
  | "render";
export type RegenerationAction =
  | "make_punchier"
  | "more_cinematic"
  | "less_broll"
  | "change_music";
export type DurationPreset = "short" | "medium" | "long";
export type PlatformPreset = "instagram" | "tiktok" | "shorts";
export type ExportResolution = "720p" | "1080p" | "4k";
export type OutputAspect = "original" | "9:16" | "1:1" | "4:5" | "16:9";
export type ExportFitMode = "fit" | "fill" | "custom";
export type BillingPlanKey = "free" | "creator" | "pro" | "agency";
export type SubscriptionStatus =
  | "none"
  | "trialing"
  | "active"
  | "past_due"
  | "canceled"
  | "unpaid"
  | "incomplete";
export type StockOrientation = "portrait" | "landscape" | "square";
export type CaptionPresetKey =
  | "luxury_minimal"
  | "bold_influencer"
  | "vogue_white"
  | "gold_premium"
  | "founder_clean";

export interface ProjectSummary {
  id: string;
  workspaceId: string;
  title: string;
  status: ProjectStatus;
  updatedAt: string;
}

export interface UploadedVideoSummary {
  id: string;
  projectId: string;
  workspaceId: string;
  originalFilename: string;
  status: UploadStatus;
  durationMs?: number | null;
  width?: number | null;
  height?: number | null;
  thumbnailPath?: string | null;
  warnings: string[];
  updatedAt: string;
}

export interface TranscriptWord {
  id?: string | null;
  index: number;
  text: string;
  start_ms: number;
  end_ms: number;
  confidence?: number | null;
}

export interface AudioIntelligenceSummary {
  schema_version: number;
  keywords: Array<{ text: string; score: number }>;
  style_signals: {
    predicted_style: string;
    tone: string;
    pace: string;
    style_scores: Record<string, number>;
  };
  speech_metrics: {
    word_count: number;
    duration_ms?: number | null;
    words_per_minute?: number | null;
  };
  confidence: {
    average_word_confidence?: number | null;
    low_confidence_word_count: number;
    word_confidence_available: boolean;
  };
  speaker_diarization: {
    provider: string;
    speaker_count: number;
    confidence: number;
    segments: Array<{
      speaker: string;
      start_ms: number;
      end_ms: number;
      segment_index: number;
    }>;
  };
  emotion_pacing: {
    aggregate_emotion: string;
    pause_count: number;
    windows: Array<{
      start_ms: number;
      end_ms: number;
      emotion: string;
      intensity: number;
      word_count: number;
    }>;
  };
  sound_classification: {
    provider: string;
    background_sound: string;
    labels: Array<{ label: string; confidence: number }>;
  };
}

export interface TranscriptSummary {
  id: string;
  status: TranscriptStatus;
  language: TranscriptLanguage;
  provider: string;
  model: string;
  confidence?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  progress_pct: number;
  words: TranscriptWord[];
  raw_payload?: {
    audio_intelligence?: AudioIntelligenceSummary;
    [key: string]: unknown;
  };
  created_at?: string;
  updated_at?: string;
}

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface DominantColor {
  hex: string;
  rgb: [number, number, number];
  percentage: number;
}

export interface ObjectDetection {
  label: string;
  confidence: number;
  box: BoundingBox;
}

export interface FaceDetection {
  confidence: number;
  box: BoundingBox;
  landmarks: Record<string, [number, number]>;
}

export interface FrameSample {
  timestamp_ms: number;
  width: number;
  height: number;
  scene_change_score: number;
  embedding: number[];
  caption: string;
  dominant_colors: DominantColor[];
  objects: ObjectDetection[];
  faces: FaceDetection[];
  camera_motion: CameraMotion;
  scene_type: SceneType;
  visual_importance_score: number;
}

export interface SceneSegment {
  index: number;
  start_ms: number;
  end_ms: number;
  representative_timestamp_ms: number;
  caption: string;
  embedding: number[];
  dominant_colors: DominantColor[];
  objects: ObjectDetection[];
  faces: FaceDetection[];
  camera_motion: CameraMotion;
  scene_type: SceneType;
  visual_importance_score: number;
}

export interface VideoManifest {
  schema_version: number;
  duration_ms?: number | null;
  sample_rate_fps: number;
  frame_count: number;
  provider: string;
  model_versions: Record<string, string>;
  frame_samples: FrameSample[];
  scenes: SceneSegment[];
  summary: Record<string, unknown>;
}

export interface VideoManifestSummary {
  id: string;
  workspace_id: string;
  project_id: string;
  uploaded_video_id: string;
  version: number;
  schema_version: number;
  status: VisualManifestStatus;
  provider: string;
  model: string;
  manifest?: VideoManifest | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CaptionStyle {
  font_family: string;
  font_size_px: number;
  font_weight: number;
  text_case: "normal" | "uppercase" | "title";
  text_color: string;
  accent_color: string;
  stroke_color?: string | null;
  stroke_width_px: number;
  shadow?: string | null;
  safe_area_bottom_pct: number;
  position_x_pct?: number | null;
  position_y_pct?: number | null;
  placement: "lower" | "middle" | "upper";
  animation: "fade_up" | "pop" | "type_on" | "glow" | "slide_up";
  max_lines: number;
  contrast_guardrail: string;
}

export interface CaptionPreset {
  key: CaptionPresetKey;
  name: string;
  style: CaptionStyle;
}

export interface CaptionSegment {
  id?: string | null;
  index: number;
  start_ms: number;
  end_ms: number;
  text: string;
  words: TranscriptWord[];
  line_count: number;
  highlight_words?: string[];
}

export interface CaptionTrackSummary {
  id: string;
  preset: CaptionPresetKey;
  schema_version: number;
  style: CaptionStyle;
  hide_filler_words: boolean;
  segments: CaptionSegment[];
}

export interface CutSegment {
  start_ms: number;
  end_ms: number;
  reason: string;
}

export interface EditPlanCut {
  start_ms: number;
  end_ms: number;
  reason: string;
}

export interface CaptionHighlight {
  timestamp_ms: number;
  word: string;
  effect: "pulse" | "underline" | "color_pop" | "scale" | "none";
}

export interface BrollRequest {
  start_ms: number;
  end_ms: number;
  keywords: string[];
  priority: number;
  semantic_query?: string | null;
  semantic_embedding?: number[];
  source_scene_index?: number | null;
  source_scene_type?: string | null;
  shot_type?: string;
  placement_rule?: string;
  avoid_face_overlap?: boolean;
}

export interface StockVideoAssetSummary {
  id: string;
  workspace_id: string;
  project_id: string;
  provider: StockProviderKey;
  provider_asset_id: string;
  title: string;
  thumbnail_url?: string | null;
  preview_video_url?: string | null;
  download_url?: string | null;
  license: string;
  width: number;
  height: number;
  duration_ms: number;
  orientation: StockOrientation;
  score: number;
  raw_metadata: Record<string, unknown>;
  created_at: string;
}

export interface BrollSlotSummary {
  id: string;
  workspace_id: string;
  project_id: string;
  edit_plan_id: string;
  version: number;
  schema_version: number;
  status: BrollSlotStatus;
  start_ms: number;
  end_ms: number;
  keywords: string[];
  intent: string;
  density: string;
  priority: number;
  selected_asset_id?: string | null;
  candidate_asset_ids: string[];
  crossfade_ms: number;
  provider: StockProviderKey;
  score: number;
  error_code?: string | null;
  error_message?: string | null;
  metadata?: Record<string, unknown>;
  selected_asset?: StockVideoAssetSummary | null;
  candidates: StockVideoAssetSummary[];
  created_at: string;
  updated_at: string;
}

export interface MusicSelectionSummary {
  id: string;
  workspace_id: string;
  project_id: string;
  edit_plan_id: string;
  music_track_id?: string | null;
  music_volume: number;
  sfx_intensity: number;
  rights_confirmed: boolean;
  status: MusicSelectionStatus;
  cache_key?: string | null;
  created_at: string;
  updated_at: string;
  music_track?: MusicTrackSummary | null;
}

export interface MusicTrackSummary {
  id: string;
  workspace_id: string;
  project_id: string;
  provider: MusicProviderKey;
  provider_track_id: string;
  title: string;
  license: string;
  duration_ms: number;
  mood_tags: string[];
  bpm?: number | null;
  energy?: number | null;
  vocals: boolean;
  loopable: boolean;
  beat_map: number[];
  raw_metadata: Record<string, unknown>;
  created_at: string;
}

export interface MusicMixSettings {
  ducking: {
    enabled: boolean;
    speech_attenuation_db: number;
    attack_ms: number;
    release_ms: number;
  };
  loudness_target_lufs: number;
  beat_snap_tolerance_ms: number;
}

export interface EffectsTextureSettings {
  grain: number;
  vignette: number;
  light_leak: number;
  anamorphic_flare: boolean;
}

export interface BackgroundRemovalSettings {
  enabled: boolean;
  apply_to_broll: boolean;
  feather_px: number;
  spill_suppression: number;
  replacement: "transparent" | "gradient_ramp" | "blurred_source";
}

export interface GradientRampSettings {
  enabled: boolean;
  start_color: string;
  end_color: string;
  angle_deg: number;
  opacity: number;
  blend_mode: "screen" | "overlay" | "soft-light" | "normal";
}

export interface AfterEffectsPresetSummary {
  name: string;
  enabled: boolean;
  intensity: number;
  expression_controls: Record<string, number | string | boolean>;
}

export interface EffectsConfigSummary {
  id: string;
  workspace_id: string;
  project_id: string;
  edit_plan_id?: string | null;
  version: number;
  schema_version: number;
  status: EffectsStatus;
  preset: VisualPresetKey;
  lut_intensity: number;
  texture: EffectsTextureSettings;
  motion_effects: MotionEffectKey[];
  background_removal: BackgroundRemovalSettings;
  gradient_ramp: GradientRampSettings;
  after_effects_presets: AfterEffectsPresetSummary[];
  after_effects_project_url?: string | null;
  correction_manifest_url?: string | null;
  effect_count_limit_per_30s: number;
  render_time_impact: "low" | "medium" | "high";
  preview_before_url?: string | null;
  preview_after_url?: string | null;
  cache_key?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ZoomMoment {
  timestamp_ms: number;
  intensity: number;
  reason: string;
}

export interface StyleClassification {
  predicted_style: EditPlanStyleKey;
  effective_style: EditPlanStyleKey;
  requested_style?: EditPlanStyleKey | null;
  confidence: number;
  scores: Record<string, number>;
  signals: string[];
  source: "audio_visual_classifier";
}

export interface EditPlan {
  schema_version: number;
  style: EditPlanStyleKey;
  target_duration_s: number;
  cuts: EditPlanCut[];
  caption_highlights: CaptionHighlight[];
  broll_requests: BrollRequest[];
  zoom_moments: ZoomMoment[];
  music_mood: string;
  effects: string[];
  color_grade: string;
  risk_flags: string[];
}

export interface EditPlanSummary {
  duration_s: number;
  cut_count: number;
  broll_count: number;
  zoom_count: number;
  music_mood: string;
  effects: string[];
  color_grade: string;
  risk_flags: string[];
  source_topic?: string;
  direction_notes?: string[];
  broll_density?: BrollDensity;
  visual_preset?: VisualPresetKey;
  hook?: string;
  style_classification?: StyleClassification | null;
}

export interface EditPlanRecordSummary {
  id: string;
  project_id: string;
  transcript_id: string;
  version: number;
  schema_version: number;
  status: EditPlanStatus;
  is_active: boolean;
  style: EditPlanStyleKey;
  target_duration_s: number;
  broll_density: BrollDensity;
  prompt: string;
  feedback?: string | null;
  provider: string;
  model: string;
  cache_key: string;
  schema_hash: string;
  latency_ms?: number | null;
  cost_estimate_usd?: number | null;
  validation_errors: string[];
  summary: EditPlanSummary;
  plan: EditPlan;
  created_at: string;
  updated_at: string;
}

export interface RenderSpecs {
  width: number;
  height: number;
  fps: number;
  video_codec: string;
  audio_codec: string;
  pixel_format: string;
  format: ExportFormat;
}

export interface ExportCropSettings {
  x: number;
  y: number;
  zoom: number;
}

export interface ExportFraming {
  output_aspect: OutputAspect;
  fit_mode: ExportFitMode;
  crop: ExportCropSettings;
  padding_color: string;
}

export interface RenderTemplateMetadata {
  id: RenderTemplateId;
  name: string;
  style: EditPlanStyleKey;
  caption_animation: "karaoke" | "slide_up" | "pulse" | "gold_shimmer";
  supported_effects: string[];
  default_color_grade: string;
  intro: boolean;
  outro: boolean;
}

export interface PremiumRenderManifest {
  schema_version: number;
  source_video_id: string;
  caption_track_id: string;
  edit_plan_id?: string | null;
  music_selection_id?: string | null;
  effects_config_id?: string | null;
  template_id: RenderTemplateId;
  broll_selection_version?: number | null;
  render_engine: RenderEngine;
  renderer_version: string;
  props_hash: string;
  include_captions: boolean;
  include_broll: boolean;
  export_settings: {
    platform: PlatformPreset;
    requested_resolution: ExportResolution;
    resolution: ExportResolution;
    duration_preset: DurationPreset;
    fps: number;
    format: ExportFormat;
    watermark: boolean;
    framing?: ExportFraming;
  };
  unsupported_effects: string[];
  downgraded_effects: string[];
}

export interface RenderJobEventSummary {
  id: string;
  render_job_id: string;
  event_type: string;
  from_status?: RenderJobStatus | null;
  to_status: RenderJobStatus;
  progress_pct: number;
  message: string;
  error_code?: string | null;
  created_at: string;
}

export interface RenderJobSummary {
  id: string;
  status: RenderJobStatus;
  uploaded_video_id: string;
  caption_track_id: string;
  planner_version: string;
  manifest?: Record<string, any>;
  keep_segments: CutSegment[];
  specs: RenderSpecs;
  template_id?: RenderTemplateId | null;
  render_engine?: RenderEngine | null;
  renderer_version?: string | null;
  props_hash?: string | null;
  artifact_path?: string | null;
  benchmark_ms?: number | null;
  logs?: string[];
  progress_pct: number;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  events: RenderJobEventSummary[];
}

export interface ExportSummary {
  id: string;
  project_id: string;
  render_job_id: string;
  version: number;
  storage_bucket: string;
  storage_path: string;
  format: ExportFormat;
  width: number;
  height: number;
  fps: number;
  duration_ms?: number | null;
  file_size_bytes?: number | null;
  thumbnail_url?: string | null;
  settings_summary?: Record<string, unknown>;
  restored_from_export_id?: string | null;
  created_at: string;
}

export interface UserControlSettings {
  caption_style?: string | null;
  broll_density?: number | null;
  music_volume?: number | null;
  sfx_intensity?: number | null;
  effects_intensity?: number | null;
  duration_preset?: DurationPreset | null;
}

export interface ExportOptions {
  resolution: ExportResolution;
  format: ExportFormat;
  platform: PlatformPreset;
  framing?: ExportFraming;
}

export interface RegenerateResponseSummary {
  status: "queued";
  job_id: string;
  affected_layers: RegenerationLayer[];
  preserved_layers: RegenerationLayer[];
  creates_new_version: boolean;
  message: string;
}

export interface PlanEntitlements {
  monthly_renders: number;
  watermark: boolean;
  max_resolution: string;
  broll: boolean;
  priority_queue: boolean;
  premium_effects: boolean;
  seats: number;
  brand_kits: boolean;
  bulk_uploads: boolean;
  api_access: boolean;
}

export interface BillingPlanDefinition {
  key: BillingPlanKey;
  name: string;
  price_cents: number;
  currency: string;
  interval: "month";
  entitlements: PlanEntitlements;
  features: string[];
}

export interface SubscriptionSummary {
  workspace_id: string;
  plan: BillingPlanKey;
  status: SubscriptionStatus;
  stripe_customer_id?: string | null;
  stripe_subscription_id?: string | null;
  stripe_price_id?: string | null;
  current_period_start?: string | null;
  current_period_end?: string | null;
  cancel_at_period_end: boolean;
  payment_issue: boolean;
  updated_at: string;
}

export interface UsageSummary {
  workspace_id: string;
  period_start: string;
  period_end: string;
  renders_used: number;
  renders_limit: number;
  remaining_renders: number;
  percent_used: number;
}

export interface BillingStatusSummary {
  subscription: SubscriptionSummary;
  current_plan: BillingPlanDefinition;
  plans: BillingPlanDefinition[];
  usage: UsageSummary;
  can_render: boolean;
  entitlements: PlanEntitlements;
}

// ---------------------------------------------------------------------------
// Timeline editor types
// ---------------------------------------------------------------------------

export type TimelineTrackKind = "video" | "broll" | "caption" | "music" | "zoom";

export interface TimelineClip {
  id: string;
  trackKind: TimelineTrackKind;
  startMs: number;
  endMs: number;
  label: string;
  /** Tailwind bg-* class string, e.g. "bg-teal-500/80" */
  colorClass: string;
  disabled: boolean;
  sourceRef: {
    kind: "cut" | "caption_segment" | "broll_slot" | "music" | "zoom_marker";
    index?: number; // positional index in array (cuts)
    id?: string;    // UUID (segments, slots)
  };
}

export interface TimelineTrack {
  kind: TimelineTrackKind;
  label: string;
  clips: TimelineClip[];
  muted: boolean;
  locked: boolean;
}

export interface TimelineState {
  totalDurationMs: number;
  tracks: TimelineTrack[];
}
