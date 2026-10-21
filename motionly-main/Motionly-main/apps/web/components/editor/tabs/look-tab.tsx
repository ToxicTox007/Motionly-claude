"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Input, Label, Select, Slider, PanelSection } from "../ui";
import { Button } from "../../button";
import {
  AlertTriangle,
  Check,
  Loader2,
  Palette,
  Plus,
  Trash2,
} from "lucide-react";
import type {
  AfterEffectsPresetSummary,
  BackgroundRemovalSettings,
  EffectsConfigSummary,
  EffectsTextureSettings,
  GradientRampSettings,
  MotionEasing,
  MotionEffectKey,
  MotionKeyframe,
  MotionSettings,
  MotionTarget,
  TimelineClip,
  VisualPresetKey,
} from "@motionly/shared";
import { editorApiRequest, resolveQueuedLayer } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";
import { useEditorHistory } from "../history-context";
import type { PreviewLookState } from "../preview/VideoPreview";

interface LookTabProps {
  projectId: string;
  workspaceId: string;
  initialConfig: EffectsConfigSummary | null;
  suggestedPreset?: VisualPresetKey | null;
  selectedTimelineClip?: TimelineClip | null;
  onConfigUpdated: (config: EffectsConfigSummary) => void;
  onPreviewLookUpdated?: (state: PreviewLookState | null) => void;
}

const presets: { value: VisualPresetKey; label: string }[] = [
  { value: "vogue_luxury", label: "Editorial Contrast" },
  { value: "champagne_gold", label: "Warm Gold" },
  { value: "dubai_influencer", label: "Travel Pop" },
  { value: "old_money", label: "Muted Film" },
  { value: "clean_founder", label: "Crisp Neutral" },
  { value: "cinematic_hotel", label: "Cinematic Hotel" },
];

const motionEffects: Array<{ value: MotionEffectKey; label: string }> = [
  { value: "zoom_punch", label: "Zoom Punch" },
  { value: "ken_burns", label: "Ken Burns" },
  { value: "speed_ramp", label: "Speed Ramp" },
  { value: "beat_impact", label: "Beat Impact" },
  { value: "motion_blur", label: "Motion Blur" },
  { value: "flash", label: "Flash" },
  { value: "glitch", label: "Glitch" },
  { value: "broll_slide_in", label: "B-roll Slide" },
  { value: "gradient_ramp", label: "Gradient Ramp" },
];

const motionHeavyEffects: MotionEffectKey[] = [
  "zoom_punch",
  "ken_burns",
  "speed_ramp",
  "beat_impact",
  "motion_blur",
  "flash",
  "glitch",
];
const flashingEffects: MotionEffectKey[] = ["flash", "glitch"];

const defaultTexture: EffectsTextureSettings = {
  grain: 0.18,
  vignette: 0.16,
  light_leak: 0.08,
  anamorphic_flare: false,
};

const defaultBackground: BackgroundRemovalSettings = {
  enabled: false,
  apply_to_broll: true,
  feather_px: 18,
  spill_suppression: 0.45,
  replacement: "gradient_ramp",
};

const defaultGradient: GradientRampSettings = {
  enabled: false,
  start_color: "#0f172a",
  end_color: "#f59e0b",
  angle_deg: 135,
  opacity: 0.28,
  blend_mode: "soft-light",
};

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

const easingOptions: Array<{ value: MotionEasing; label: string }> = [
  { value: "ease_in_out", label: "Ease In/Out" },
  { value: "ease_in", label: "Ease In" },
  { value: "ease_out", label: "Ease Out" },
  { value: "linear", label: "Linear" },
  { value: "spring", label: "Spring" },
];

const motionTargetOptions: Array<{ value: MotionTarget; label: string }> = [
  { value: "source", label: "Source" },
  { value: "broll", label: "B-roll" },
  { value: "all", label: "All" },
];

const kenBurnsDirectionOptions = [
  { value: "push_in", label: "Push In" },
  { value: "pull_out", label: "Pull Out" },
  { value: "pan_left", label: "Pan Left" },
  { value: "pan_right", label: "Pan Right" },
  { value: "pan_up", label: "Pan Up" },
  { value: "pan_down", label: "Pan Down" },
];

function defaultMotionSettings(): MotionSettings {
  return {
    keyframes: [],
    ken_burns: {
      enabled: false,
      intensity: 0.08,
      direction: "push_in",
      easing: "ease_in_out",
    },
    speed_ramp: {
      enabled: false,
      ramp_in_ms: 280,
      ramp_out_ms: 320,
      peak_speed: 1.35,
      easing: "ease_in_out",
    },
    beat_impact: {
      enabled: false,
      intensity: 0.22,
      shake_px: 10,
      zoom: 1.035,
      max_impacts: 12,
    },
    auto_reframe: {
      enabled: false,
      target_aspect: "9:16",
      subject_x_pct: 50,
      subject_y_pct: 50,
      smoothing: 0.65,
    },
  };
}

export function LookTab({
  projectId,
  workspaceId,
  initialConfig,
  suggestedPreset,
  selectedTimelineClip,
  onConfigUpdated,
  onPreviewLookUpdated,
}: LookTabProps) {
  const { pushHistory } = useEditorHistory();
  const [preset, setPreset] = useState<VisualPresetKey>(
    initialConfig?.preset ?? suggestedPreset ?? "clean_founder",
  );
  const [hoverPreset, setHoverPreset] = useState<VisualPresetKey | null>(null);
  const [intensity, setIntensity] = useState(
    initialConfig ? Math.round(initialConfig.lut_intensity * 100) : 80,
  );
  const [texture, setTexture] = useState<EffectsTextureSettings>(
    initialConfig?.texture ?? defaultTexture,
  );
  const [selectedMotionEffects, setSelectedMotionEffects] = useState<
    MotionEffectKey[]
  >(() => {
    const effects = initialConfig?.motion_effects ?? [];
    return prefersReducedMotion()
      ? effects.filter((effect) => !motionHeavyEffects.includes(effect))
      : effects;
  });
  const [motion, setMotion] = useState<MotionSettings>(() => {
    const settings = initialConfig?.motion ?? defaultMotionSettings();
    return prefersReducedMotion()
      ? motionWithReducedMotion(settings)
      : settings;
  });
  const [backgroundRemoval, setBackgroundRemoval] =
    useState<BackgroundRemovalSettings>(
      initialConfig?.background_removal ?? defaultBackground,
    );
  const [gradientRamp, setGradientRamp] = useState<GradientRampSettings>(
    initialConfig?.gradient_ramp ?? defaultGradient,
  );
  const [resetToDefaults, setResetToDefaults] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(prefersReducedMotion);
  const [premiumBlockedMessage, setPremiumBlockedMessage] = useState<
    string | null
  >(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const previewPreset = hoverPreset ?? preset;

  const applyReducedMotionPreference = useCallback((next: boolean) => {
    setReducedMotion(next);
    if (!next) return;
    setSelectedMotionEffects((current) =>
      current.filter((effect) => !motionHeavyEffects.includes(effect)),
    );
    setMotion((current) => motionWithReducedMotion(current));
    setTexture((current) =>
      current.light_leak > 0.2 ? { ...current, light_leak: 0.08 } : current,
    );
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => applyReducedMotionPreference(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [applyReducedMotionPreference]);

  useEffect(() => {
    onPreviewLookUpdated?.({
      preset: previewPreset,
      intensity,
      grain: texture.grain,
      vignette: texture.vignette,
      lightLeak: texture.light_leak,
      gradientRamp: {
        enabled: gradientRamp.enabled,
        startColor: gradientRamp.start_color,
        endColor: gradientRamp.end_color,
        opacity: gradientRamp.opacity,
        angleDeg: gradientRamp.angle_deg,
      },
      motionEffects: reducedMotion
        ? selectedMotionEffects.filter(
            (effect) => !motionHeavyEffects.includes(effect),
          )
        : selectedMotionEffects,
      motion: reducedMotion ? motionWithReducedMotion(motion) : motion,
      backgroundRemoval: {
        enabled: backgroundRemoval.enabled,
        featherPx: backgroundRemoval.feather_px,
      },
      mattePreview: null,
      selectedSourceClipId:
        selectedTimelineClip?.sourceRef.kind === "cut"
          ? selectedTimelineClip.id
          : null,
      selectedSourceClipStartMs:
        selectedTimelineClip?.sourceRef.kind === "cut"
          ? selectedTimelineClip.startMs
          : null,
      selectedSourceClipEndMs:
        selectedTimelineClip?.sourceRef.kind === "cut"
          ? selectedTimelineClip.endMs
          : null,
      zoomMoments: null,
      reducedMotion,
    });
  }, [
    gradientRamp.angle_deg,
    gradientRamp.enabled,
    gradientRamp.end_color,
    gradientRamp.opacity,
    gradientRamp.start_color,
    intensity,
    onPreviewLookUpdated,
    previewPreset,
    backgroundRemoval.enabled,
    backgroundRemoval.feather_px,
    reducedMotion,
    selectedMotionEffects,
    selectedTimelineClip,
    motion,
    texture.grain,
    texture.light_leak,
    texture.vignette,
  ]);

  type EffectsBody = {
    preset: VisualPresetKey;
    lut_intensity: number;
    texture: EffectsTextureSettings;
    motion_effects: MotionEffectKey[];
    motion: MotionSettings;
    background_removal: BackgroundRemovalSettings;
    gradient_ramp: GradientRampSettings;
    after_effects_presets: AfterEffectsPresetSummary[];
    reset_to_defaults: boolean;
    force: true;
  };

  function buildEffectsBody(): EffectsBody {
    const effectiveMotionEffects = reducedMotion
      ? selectedMotionEffects.filter(
          (effect) => !motionHeavyEffects.includes(effect),
        )
      : selectedMotionEffects;
    const effectiveMotion = reducedMotion
      ? motionWithReducedMotion(motion)
      : motion;
    return {
      preset,
      lut_intensity: Math.max(50, intensity) / 100,
      texture,
      motion_effects: effectiveMotionEffects,
      motion: effectiveMotion,
      background_removal: backgroundRemoval,
      gradient_ramp: gradientRamp,
      after_effects_presets: initialConfig?.after_effects_presets ?? [],
      reset_to_defaults: resetToDefaults,
      force: true,
    };
  }

  async function applyEffectsBody(body: EffectsBody) {
    setLoading(true);
    setMessage(null);
    setPremiumBlockedMessage(null);
    try {
      const payload = await editorApiRequest<{
        config?: EffectsConfigSummary | null;
        status: "ready" | "queued";
        status_url?: string | null;
      }>(`${env.apiBaseUrl}/projects/${projectId}/effects`, {
        method: "PATCH",
        workspaceId,
        friendlyError: "Look settings could not be applied. Please try again.",
        body,
      });
      const config = await resolveQueuedLayer({
        submission: payload,
        immediate: payload.config,
        apiBaseUrl: env.apiBaseUrl,
        options: { workspaceId },
        load: () =>
          editorApiRequest<EffectsConfigSummary | null>(
            `${env.apiBaseUrl}/projects/${projectId}/effects`,
            { workspaceId },
          ),
        resourceLabel: "Look settings",
        onProgress: (progress) => setMessage(`Applying look: ${progress}%`),
      });
      onConfigUpdated(config);
      setPreset(config.preset);
      setIntensity(Math.round(config.lut_intensity * 100));
      setTexture(config.texture);
      setSelectedMotionEffects(config.motion_effects);
      setMotion(config.motion ?? defaultMotionSettings());
      setBackgroundRemoval(config.background_removal);
      setGradientRamp(config.gradient_ramp);
      setResetToDefaults(false);
      setMessage("Look applied.");
      return config;
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
      throw error;
    } finally {
      setLoading(false);
    }
  }

  async function applyEffects() {
    const previousBody = buildEffectsBody();
    try {
      const config = await applyEffectsBody(previousBody);
      const nextBody: EffectsBody = {
        preset: config.preset,
        lut_intensity: config.lut_intensity,
        texture: config.texture,
        motion_effects: config.motion_effects,
        motion: config.motion,
        background_removal: config.background_removal,
        gradient_ramp: config.gradient_ramp,
        after_effects_presets: config.after_effects_presets,
        reset_to_defaults: false,
        force: true,
      };
      pushHistory({
        id: `look-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label: "Look",
        undo: () =>
          applyEffectsBody(previousBody).then(() => "Look settings restored."),
        redo: () =>
          applyEffectsBody(nextBody).then(() => "Look settings reapplied."),
      });
    } catch {
      // Error already surfaced by applyEffectsBody.
    }
  }

  function toggleMotionEffect(effect: MotionEffectKey) {
    if (reducedMotion && motionHeavyEffects.includes(effect)) {
      setMessage(
        "Reduced motion is on. Motion-heavy effects are disabled for accessibility.",
      );
      return;
    }
    setSelectedMotionEffects((current) => {
      const enabled = !current.includes(effect);
      setMotion((currentMotion) =>
        motionWithEffectEnabled(currentMotion, effect, enabled),
      );
      return enabled
        ? [...current, effect]
        : current.filter((item) => item !== effect);
    });
    if (effect === "gradient_ramp") {
      setGradientRamp((current) => ({ ...current, enabled: !current.enabled }));
    }
  }

  function setMotionEffectEnabled(effect: MotionEffectKey, enabled: boolean) {
    if (reducedMotion && enabled && motionHeavyEffects.includes(effect)) {
      setMessage(
        "Reduced motion is on. Motion-heavy effects are disabled for accessibility.",
      );
      return;
    }
    setMotion((current) => motionWithEffectEnabled(current, effect, enabled));
    setSelectedMotionEffects((current) =>
      enabled
        ? current.includes(effect)
          ? current
          : [...current, effect]
        : current.filter((item) => item !== effect),
    );
  }

  function updateMotionKeyframe(index: number, patch: Partial<MotionKeyframe>) {
    setMotion((current) => ({
      ...current,
      keyframes: current.keyframes.map((keyframe, keyframeIndex) =>
        keyframeIndex === index ? { ...keyframe, ...patch } : keyframe,
      ),
    }));
  }

  function addMotionKeyframe(targetedClip?: TimelineClip | null) {
    const clipTarget = targetedClip
      ? motionTargetForTimelineClip(targetedClip)
      : null;
    setMotion((current) => ({
      ...current,
      keyframes: [
        ...current.keyframes,
        {
          target: clipTarget?.target ?? "source",
          clip_id: clipTarget?.clipId ?? null,
          at_ms:
            clipTarget?.atMs ??
            (current.keyframes.length
              ? current.keyframes[current.keyframes.length - 1].at_ms + 600
              : 0),
          scale: 1.06,
          x_pct: 50,
          y_pct: 50,
          rotation_deg: 0,
          opacity: 1,
          easing: "ease_in_out",
        } satisfies MotionKeyframe,
      ].slice(0, 48),
    }));
  }

  function removeMotionKeyframe(index: number) {
    setMotion((current) => ({
      ...current,
      keyframes: current.keyframes.filter(
        (_, keyframeIndex) => keyframeIndex !== index,
      ),
    }));
  }

  function selectedClipIdForMotion(): string | null {
    if (!selectedTimelineClip) return null;
    if (selectedTimelineClip.sourceRef.kind === "cut")
      return selectedTimelineClip.id;
    if (
      selectedTimelineClip.sourceRef.kind === "broll_slot" &&
      selectedTimelineClip.sourceRef.id
    ) {
      return selectedTimelineClip.id;
    }
    return null;
  }

  function selectedClipEffects(): MotionEffectKey[] {
    const clipId = selectedClipIdForMotion();
    if (!clipId) return [];
    return motion.clip_effects?.[clipId] ?? [];
  }

  function toggleClipMotionEffect(effect: MotionEffectKey) {
    const clipId = selectedClipIdForMotion();
    if (!clipId) return;
    if (reducedMotion && motionHeavyEffects.includes(effect)) {
      setMessage(
        "Reduced motion is on. Motion-heavy effects are disabled for accessibility.",
      );
      return;
    }
    setMotion((current) => {
      const currentEffects = current.clip_effects?.[clipId] ?? [];
      const enabled = !currentEffects.includes(effect);
      const nextEffects = enabled
        ? [...currentEffects, effect]
        : currentEffects.filter((item) => item !== effect);
      return {
        ...current,
        clip_effects: {
          ...current.clip_effects,
          [clipId]: nextEffects,
        },
      };
    });
  }

  function clearClipMotionEffects() {
    const clipId = selectedClipIdForMotion();
    if (!clipId) return;
    setMotion((current) => {
      const next = { ...current, clip_effects: { ...current.clip_effects } };
      delete next.clip_effects[clipId];
      return next;
    });
  }

  return (
    <div className="space-y-4">
      <h2 className="sr-only">Look</h2>
      <div className="rounded-neu-lg bg-surface p-5 space-y-4 shadow-neu-raised sm:p-6">
        <PanelSection title="Color" defaultOpen>
          <div className="space-y-2">
            <Label>Visual Style</Label>
            <Select
              id="visual-style"
              label="Visual Style"
              value={preset}
              onChange={(val) => setPreset(val as VisualPresetKey)}
              options={presets}
            />
            <div className="mt-2 grid grid-cols-2 gap-2">
              {presets.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  aria-pressed={preset === item.value}
                  onMouseEnter={() => setHoverPreset(item.value)}
                  onMouseLeave={() => setHoverPreset(null)}
                  onFocus={() => setHoverPreset(item.value)}
                  onBlur={() => setHoverPreset(null)}
                  onClick={() => setPreset(item.value)}
                  className={cn(
                    "min-h-11 rounded-neu-md px-3 py-2 text-left text-xs font-semibold transition-shadow duration-200",
                    preset === item.value
                      ? "bg-primary text-primary-foreground shadow-neu-glow"
                      : "bg-surface text-foreground shadow-neu-raised-sm hover:shadow-neu-raised active:shadow-neu-pressed",
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <Slider
            label="LUT Intensity"
            value={intensity}
            min={50}
            max={100}
            onChange={setIntensity}
          />
        </PanelSection>

        <PanelSection title="Texture">
          <Label>Texture</Label>
          <Slider
            label="Grain"
            value={Math.round(texture.grain * 100)}
            onChange={(value) =>
              setTexture((current) => ({ ...current, grain: value / 100 }))
            }
          />
          <Slider
            label="Vignette"
            value={Math.round(texture.vignette * 100)}
            onChange={(value) =>
              setTexture((current) => ({ ...current, vignette: value / 100 }))
            }
          />
          <Slider
            label="Light Leak"
            value={Math.round(texture.light_leak * 100)}
            onChange={(value) =>
              setTexture((current) => ({
                ...current,
                light_leak: reducedMotion
                  ? Math.min(value / 100, 0.2)
                  : value / 100,
              }))
            }
          />
          {texture.light_leak > 0.2 ? (
            <p
              role="alert"
              className="mt-2 inline-flex items-center gap-1.5 rounded-neu-md bg-warning/10 px-3 py-2 text-xs font-semibold text-warning shadow-neu-inset-sm"
            >
              <AlertTriangle className="size-3.5 shrink-0" strokeWidth={1.75} />
              Flashing risk: lower Light Leak.
            </p>
          ) : null}
          <label className="mt-3 flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-neu-md bg-surface px-4 py-3 text-sm font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed select-none">
            Anamorphic flare
            <input
              type="checkbox"
              aria-label="Enable anamorphic flare"
              checked={texture.anamorphic_flare}
              onChange={(event) =>
                setTexture((current) => ({
                  ...current,
                  anamorphic_flare: event.target.checked,
                }))
              }
              className="neu-check cursor-pointer"
            />
          </label>
        </PanelSection>

        <PanelSection title="Motion" defaultOpen>
          <div className="mt-3">
            <Label>Motion Effects</Label>
            <p className="mb-2.5 text-xs font-medium text-muted-foreground leading-relaxed">
              Reduced Motion removes flashing and aggressive effects
              automatically.
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              {motionEffects.map((effect) => {
                const selected = selectedMotionEffects.includes(effect.value);
                const disabled =
                  reducedMotion && motionHeavyEffects.includes(effect.value);
                const flashing = flashingEffects.includes(effect.value);
                return (
                  <button
                    key={effect.value}
                    onClick={() => toggleMotionEffect(effect.value)}
                    disabled={disabled}
                    aria-pressed={selected}
                    aria-describedby={
                      flashing ? "motion-effects-warning" : undefined
                    }
                    className={cn(
                      "inline-flex min-h-9 items-center gap-1.5 rounded-full px-4 py-1 text-xs font-semibold transition-shadow duration-200 disabled:cursor-not-allowed",
                      selected
                        ? "bg-primary text-primary-foreground shadow-neu-glow"
                        : disabled
                          ? "bg-muted text-muted-foreground shadow-neu-inset-sm"
                          : "bg-surface text-muted-foreground shadow-neu-raised-sm hover:text-foreground hover:shadow-neu-raised active:shadow-neu-pressed",
                    )}
                  >
                    {selected ? (
                      <Check
                        className="size-3.5"
                        aria-hidden="true"
                        strokeWidth={1.75}
                      />
                    ) : disabled ? (
                      <AlertTriangle
                        className="size-3.5"
                        aria-hidden="true"
                        strokeWidth={1.75}
                      />
                    ) : null}
                    <span>{effect.label}</span>
                    <span className="text-[10px] opacity-75 font-normal">
                      ({disabled ? "Disabled" : selected ? "Selected" : "Off"})
                    </span>
                  </button>
                );
              })}
            </div>
            <p
              id="motion-effects-warning"
              className="mt-2 inline-flex items-center gap-1.5 rounded-neu-md bg-warning/10 px-3 py-2 text-xs font-semibold text-warning shadow-neu-inset-sm"
            >
              <AlertTriangle className="size-3.5 shrink-0" />
              Flash/Glitch are risky for sensitive viewers.
            </p>
            <div className="mt-4 space-y-4 border-t border-hairline pt-4">
              <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-semibold text-foreground">
                Ken Burns
                <input
                  type="checkbox"
                  aria-label="Enable Ken Burns motion"
                  checked={motion.ken_burns.enabled}
                  onChange={(event) =>
                    setMotionEffectEnabled("ken_burns", event.target.checked)
                  }
                  className="neu-check"
                />
              </label>
              {motion.ken_burns.enabled ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Select
                    label="Ken Burns Direction"
                    value={motion.ken_burns.direction}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        ken_burns: {
                          ...current.ken_burns,
                          direction:
                            value as MotionSettings["ken_burns"]["direction"],
                        },
                      }))
                    }
                    options={kenBurnsDirectionOptions}
                  />
                  <Select
                    label="Ken Burns Easing"
                    value={motion.ken_burns.easing}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        ken_burns: {
                          ...current.ken_burns,
                          easing: value as MotionEasing,
                        },
                      }))
                    }
                    options={easingOptions}
                  />
                  <div className="sm:col-span-2">
                    <Slider
                      label="Ken Burns Intensity"
                      value={Math.round(motion.ken_burns.intensity * 100)}
                      min={0}
                      max={35}
                      onChange={(value) =>
                        setMotion((current) => ({
                          ...current,
                          ken_burns: {
                            ...current.ken_burns,
                            intensity: value / 100,
                          },
                        }))
                      }
                    />
                  </div>
                </div>
              ) : null}

              <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-bold text-muted-foreground">
                Speed ramp
                <input
                  type="checkbox"
                  aria-label="Enable speed ramp"
                  checked={motion.speed_ramp.enabled}
                  onChange={(event) =>
                    setMotionEffectEnabled("speed_ramp", event.target.checked)
                  }
                  className="size-6 accent-primary"
                />
              </label>
              {motion.speed_ramp.enabled ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <Label>Ramp In MS</Label>
                    <Input
                      type="number"
                      min={80}
                      max={1200}
                      value={motion.speed_ramp.ramp_in_ms}
                      onChange={(event) =>
                        setMotion((current) => ({
                          ...current,
                          speed_ramp: {
                            ...current.speed_ramp,
                            ramp_in_ms: numberFromInput(
                              event.target.value,
                              280,
                            ),
                          },
                        }))
                      }
                    />
                  </div>
                  <div>
                    <Label>Ramp Out MS</Label>
                    <Input
                      type="number"
                      min={80}
                      max={1600}
                      value={motion.speed_ramp.ramp_out_ms}
                      onChange={(event) =>
                        setMotion((current) => ({
                          ...current,
                          speed_ramp: {
                            ...current.speed_ramp,
                            ramp_out_ms: numberFromInput(
                              event.target.value,
                              320,
                            ),
                          },
                        }))
                      }
                    />
                  </div>
                  <Select
                    label="Speed Easing"
                    value={motion.speed_ramp.easing}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        speed_ramp: {
                          ...current.speed_ramp,
                          easing: value as MotionEasing,
                        },
                      }))
                    }
                    options={easingOptions}
                  />
                  <Slider
                    label="Peak Speed"
                    value={Math.round(motion.speed_ramp.peak_speed * 100)}
                    min={100}
                    max={300}
                    step={5}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        speed_ramp: {
                          ...current.speed_ramp,
                          peak_speed: value / 100,
                        },
                      }))
                    }
                  />
                </div>
              ) : null}

              <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-bold text-muted-foreground">
                Beat impact
                <input
                  type="checkbox"
                  aria-label="Enable beat impact"
                  checked={motion.beat_impact.enabled}
                  onChange={(event) =>
                    setMotionEffectEnabled("beat_impact", event.target.checked)
                  }
                  className="size-6 accent-primary"
                />
              </label>
              {motion.beat_impact.enabled ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Slider
                    label="Impact Intensity"
                    value={Math.round(motion.beat_impact.intensity * 100)}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        beat_impact: {
                          ...current.beat_impact,
                          intensity: value / 100,
                        },
                      }))
                    }
                  />
                  <div>
                    <Label>Shake PX</Label>
                    <Input
                      type="number"
                      min={0}
                      max={48}
                      value={motion.beat_impact.shake_px}
                      onChange={(event) =>
                        setMotion((current) => ({
                          ...current,
                          beat_impact: {
                            ...current.beat_impact,
                            shake_px: numberFromInput(event.target.value, 10),
                          },
                        }))
                      }
                    />
                  </div>
                  <Slider
                    label="Impact Zoom"
                    value={Math.round((motion.beat_impact.zoom - 1) * 500)}
                    min={0}
                    max={100}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        beat_impact: {
                          ...current.beat_impact,
                          zoom: 1 + value / 500,
                        },
                      }))
                    }
                  />
                  <div>
                    <Label>Max Impacts</Label>
                    <Input
                      type="number"
                      min={0}
                      max={48}
                      value={motion.beat_impact.max_impacts}
                      onChange={(event) =>
                        setMotion((current) => ({
                          ...current,
                          beat_impact: {
                            ...current.beat_impact,
                            max_impacts: numberFromInput(
                              event.target.value,
                              12,
                            ),
                          },
                        }))
                      }
                    />
                  </div>
                </div>
              ) : null}

              <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-bold text-muted-foreground">
                Auto-reframe 9:16
                <input
                  type="checkbox"
                  aria-label="Enable auto reframe"
                  checked={motion.auto_reframe.enabled}
                  onChange={(event) =>
                    setMotion((current) => ({
                      ...current,
                      auto_reframe: {
                        ...current.auto_reframe,
                        enabled: event.target.checked,
                      },
                    }))
                  }
                  className="size-6 accent-primary"
                />
              </label>
              {motion.auto_reframe.enabled ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Select
                    label="Target Aspect"
                    value={motion.auto_reframe.target_aspect}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        auto_reframe: {
                          ...current.auto_reframe,
                          target_aspect:
                            value as MotionSettings["auto_reframe"]["target_aspect"],
                        },
                      }))
                    }
                    options={[
                      { value: "9:16", label: "9:16" },
                      { value: "1:1", label: "1:1" },
                      { value: "4:5", label: "4:5" },
                      { value: "16:9", label: "16:9" },
                    ]}
                  />
                  <Slider
                    label="Reframe Smoothing"
                    value={Math.round(motion.auto_reframe.smoothing * 100)}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        auto_reframe: {
                          ...current.auto_reframe,
                          smoothing: value / 100,
                        },
                      }))
                    }
                  />
                  <Slider
                    label="Subject X"
                    value={Math.round(motion.auto_reframe.subject_x_pct)}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        auto_reframe: {
                          ...current.auto_reframe,
                          subject_x_pct: value,
                        },
                      }))
                    }
                  />
                  <Slider
                    label="Subject Y"
                    value={Math.round(motion.auto_reframe.subject_y_pct)}
                    onChange={(value) =>
                      setMotion((current) => ({
                        ...current,
                        auto_reframe: {
                          ...current.auto_reframe,
                          subject_y_pct: value,
                        },
                      }))
                    }
                  />
                </div>
              ) : null}

              <div className="space-y-3 border-t border-hairline pt-4">
                <div className="flex items-center justify-between gap-3">
                  <Label className="mb-0">Motion Keyframes</Label>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => addMotionKeyframe()}
                    className="min-h-10 px-3"
                  >
                    <Plus className="mr-2 size-4" />
                    Add
                  </Button>
                </div>
                {selectedTimelineClip &&
                ["cut", "broll_slot"].includes(
                  selectedTimelineClip.sourceRef.kind,
                ) ? (
                  <div className="space-y-3 rounded-neu-md bg-surface p-3 shadow-neu-inset-sm">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="text-xs font-semibold text-primary">
                          Selected clip motion
                        </p>
                        <p className="mt-1 text-xs font-semibold text-muted-foreground">
                          {selectedTimelineClip.label}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => addMotionKeyframe(selectedTimelineClip)}
                        className="min-h-10 px-3"
                      >
                        <Plus className="mr-2 size-4" />
                        Add keyframe
                      </Button>
                    </div>
                    <div>
                      <p className="mb-2 text-[11px] font-semibold text-muted-foreground">
                        Apply effects to this clip only
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {motionEffects.map((effect) => {
                          const clipEffects = selectedClipEffects();
                          const selected = clipEffects.includes(effect.value);
                          const disabled =
                            reducedMotion &&
                            motionHeavyEffects.includes(effect.value);
                          return (
                            <button
                              key={effect.value}
                              type="button"
                              onClick={() =>
                                toggleClipMotionEffect(effect.value)
                              }
                              disabled={disabled}
                              aria-pressed={selected}
                              className={cn(
                                "inline-flex min-h-9 items-center gap-1.5 rounded-full px-3 py-1 text-[10px] font-semibold transition-shadow duration-200 disabled:cursor-not-allowed",
                                selected
                                  ? "bg-primary text-primary-foreground shadow-neu-glow"
                                  : disabled
                                    ? "bg-muted text-muted-foreground shadow-neu-inset-sm"
                                    : "bg-surface text-muted-foreground shadow-neu-raised-sm hover:text-foreground hover:shadow-neu-raised active:shadow-neu-pressed",
                              )}
                            >
                              {selected ? (
                                <Check className="size-3" aria-hidden="true" />
                              ) : null}
                              {effect.label}
                            </button>
                          );
                        })}
                      </div>
                      {selectedClipEffects().length > 0 ? (
                        <button
                          type="button"
                          onClick={clearClipMotionEffects}
                          className="mt-2 text-[11px] font-semibold text-danger hover:underline"
                        >
                          Clear clip effects
                        </button>
                      ) : null}
                    </div>
                  </div>
                ) : null}
                {motion.keyframes.length ? (
                  <div className="space-y-3">
                    {motion.keyframes.map((keyframe, index) => (
                      <div
                        key={`${keyframe.target}-${index}`}
                        className="rounded-neu-md bg-surface p-3 shadow-neu-inset-sm"
                      >
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <span className="text-xs font-semibold text-muted-foreground">
                            Keyframe {index + 1}
                          </span>
                          <button
                            type="button"
                            aria-label={`Remove keyframe ${index + 1}`}
                            onClick={() => removeMotionKeyframe(index)}
                            className="inline-flex min-h-9 items-center justify-center rounded-neu bg-surface px-2 text-muted-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:text-danger hover:shadow-neu-raised active:shadow-neu-pressed"
                          >
                            <Trash2 className="size-4" />
                          </button>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                          <Select
                            label={`Target ${index + 1}`}
                            value={keyframe.target}
                            onChange={(value) =>
                              updateMotionKeyframe(index, {
                                target: value as MotionTarget,
                              })
                            }
                            options={motionTargetOptions}
                          />
                          <div>
                            <Label>Clip ID</Label>
                            <Input
                              value={keyframe.clip_id ?? ""}
                              onChange={(event) =>
                                updateMotionKeyframe(index, {
                                  clip_id: event.target.value.trim() || null,
                                })
                              }
                            />
                          </div>
                          <div>
                            <Label>At MS</Label>
                            <Input
                              type="number"
                              min={0}
                              value={keyframe.at_ms}
                              onChange={(event) =>
                                updateMotionKeyframe(index, {
                                  at_ms: numberFromInput(event.target.value, 0),
                                })
                              }
                            />
                          </div>
                          <div>
                            <Label>Scale</Label>
                            <Input
                              type="number"
                              min={0.5}
                              max={3}
                              step={0.01}
                              value={keyframe.scale}
                              onChange={(event) =>
                                updateMotionKeyframe(index, {
                                  scale: numberFromInput(event.target.value, 1),
                                })
                              }
                            />
                          </div>
                          <Slider
                            label={`X Position ${index + 1}`}
                            value={Math.round(keyframe.x_pct)}
                            onChange={(value) =>
                              updateMotionKeyframe(index, { x_pct: value })
                            }
                          />
                          <Slider
                            label={`Y Position ${index + 1}`}
                            value={Math.round(keyframe.y_pct)}
                            onChange={(value) =>
                              updateMotionKeyframe(index, { y_pct: value })
                            }
                          />
                          <div>
                            <Label>Rotation Deg</Label>
                            <Input
                              type="number"
                              min={-45}
                              max={45}
                              step={0.1}
                              value={keyframe.rotation_deg}
                              onChange={(event) =>
                                updateMotionKeyframe(index, {
                                  rotation_deg: numberFromInput(
                                    event.target.value,
                                    0,
                                  ),
                                })
                              }
                            />
                          </div>
                          <Select
                            label={`Easing ${index + 1}`}
                            value={keyframe.easing}
                            onChange={(value) =>
                              updateMotionKeyframe(index, {
                                easing: value as MotionEasing,
                              })
                            }
                            options={easingOptions}
                          />
                          <div className="sm:col-span-2">
                            <Slider
                              label={`Opacity ${index + 1}`}
                              value={Math.round(keyframe.opacity * 100)}
                              onChange={(value) =>
                                updateMotionKeyframe(index, {
                                  opacity: value / 100,
                                })
                              }
                            />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="rounded-neu-md bg-surface p-3 text-xs font-medium text-muted-foreground shadow-neu-inset-sm">
                    No keyframes.
                  </p>
                )}
              </div>
            </div>
            {premiumBlockedMessage ? (
              <p className="mt-3 rounded-neu-md bg-danger/10 p-2 text-xs font-semibold text-danger shadow-neu-inset-sm">
                {premiumBlockedMessage}
              </p>
            ) : null}
          </div>
        </PanelSection>

        <PanelSection title="Accessibility" defaultOpen>
          <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-neu-md bg-surface p-3 text-sm font-semibold text-foreground shadow-neu-raised-sm">
            <span className="inline-flex items-center gap-2">
              {reducedMotion ? <Check className="size-4 text-primary" /> : null}
              Reduced motion
            </span>
            <span className="inline-flex items-center gap-3">
              <span className="text-xs font-medium text-muted-foreground">
                {reducedMotion ? "On" : "Off"}
              </span>
              <input
                type="checkbox"
                aria-label="Reduce motion and flashing effects"
                checked={reducedMotion}
                onChange={(event) =>
                  applyReducedMotionPreference(event.target.checked)
                }
                className="neu-check"
              />
            </span>
          </label>
        </PanelSection>

        <PanelSection title="Advanced">
          <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-semibold text-foreground">
            Subject segmentation
            <input
              type="checkbox"
              aria-label="Enable subject segmentation"
              checked={backgroundRemoval.enabled}
              onChange={(event) =>
                setBackgroundRemoval((current) => ({
                  ...current,
                  enabled: event.target.checked,
                }))
              }
              className="neu-check cursor-pointer"
            />
          </label>
          {backgroundRemoval.enabled ? (
            <div className="mt-4 space-y-4">
              <p className="text-xs font-medium text-muted-foreground">
                Uses lightweight MediaPipe subject segmentation after visual
                analysis completes.
              </p>
              <p className="rounded-neu-md bg-surface p-2 text-[10px] font-semibold text-foreground leading-normal shadow-neu-inset-sm">
                ⚠️ Preview runs a lightweight browser model. The final
                server-rendered export utilizes a high-precision neural
                segmentation model.
              </p>
              <Slider
                label="Feather"
                value={backgroundRemoval.feather_px}
                min={0}
                max={80}
                suffix="px"
                onChange={(value) =>
                  setBackgroundRemoval((current) => ({
                    ...current,
                    feather_px: value,
                  }))
                }
              />
              <Slider
                label="Spill Suppression"
                value={Math.round(backgroundRemoval.spill_suppression * 100)}
                onChange={(value) =>
                  setBackgroundRemoval((current) => ({
                    ...current,
                    spill_suppression: value / 100,
                  }))
                }
              />
            </div>
          ) : null}
        </PanelSection>

        <PanelSection title="Gradient">
          <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-neu-md bg-surface px-4 py-3 text-sm font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed select-none">
            Gradient ramp
            <input
              type="checkbox"
              aria-label="Enable gradient ramp"
              checked={gradientRamp.enabled}
              onChange={(event) =>
                setGradientRamp((current) => ({
                  ...current,
                  enabled: event.target.checked,
                }))
              }
              className="neu-check cursor-pointer"
            />
          </label>
          {gradientRamp.enabled ? (
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <Label>Start</Label>
                <Input
                  type="color"
                  aria-label="Gradient start color"
                  value={gradientRamp.start_color}
                  onChange={(event) =>
                    setGradientRamp((current) => ({
                      ...current,
                      start_color: event.target.value,
                    }))
                  }
                  className="px-2"
                />
              </div>
              <div>
                <Label>End</Label>
                <Input
                  type="color"
                  aria-label="Gradient end color"
                  value={gradientRamp.end_color}
                  onChange={(event) =>
                    setGradientRamp((current) => ({
                      ...current,
                      end_color: event.target.value,
                    }))
                  }
                  className="px-2"
                />
              </div>
              <div className="col-span-2">
                <Slider
                  label="Opacity"
                  value={Math.round(gradientRamp.opacity * 100)}
                  onChange={(value) =>
                    setGradientRamp((current) => ({
                      ...current,
                      opacity: value / 100,
                    }))
                  }
                />
              </div>
            </div>
          ) : null}
        </PanelSection>

        <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-neu-md bg-surface px-4 py-3 text-sm font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed select-none">
          Reset preset defaults on save
          <input
            type="checkbox"
            aria-label="Reset preset defaults on save"
            checked={resetToDefaults}
            onChange={(event) => setResetToDefaults(event.target.checked)}
            className="neu-check cursor-pointer"
          />
        </label>
      </div>

      <button
        onClick={applyEffects}
        disabled={loading}
        aria-busy={loading}
        className="w-full inline-flex items-center justify-center gap-2 h-11 rounded-neu bg-primary text-primary-foreground text-sm font-semibold shadow-neu-glow transition-shadow duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? (
          <Loader2
            className="size-4 animate-spin text-primary-foreground"
            strokeWidth={1.75}
          />
        ) : (
          <Palette
            className="size-4 text-primary-foreground"
            strokeWidth={1.75}
          />
        )}
        <span>{loading ? "Applying, please wait" : "Apply Look"}</span>
      </button>
      {message ? (
        <p
          role={
            message.toLowerCase().includes("could not") ||
            message.toLowerCase().includes("disabled")
              ? "alert"
              : "status"
          }
          aria-live="polite"
          className="rounded-neu-md bg-surface p-3.5 text-xs font-semibold text-foreground shadow-neu-inset-sm"
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}

function motionWithEffectEnabled(
  motion: MotionSettings,
  effect: MotionEffectKey,
  enabled: boolean,
): MotionSettings {
  if (effect === "ken_burns") {
    return { ...motion, ken_burns: { ...motion.ken_burns, enabled } };
  }
  if (effect === "speed_ramp") {
    return { ...motion, speed_ramp: { ...motion.speed_ramp, enabled } };
  }
  if (effect === "beat_impact") {
    return { ...motion, beat_impact: { ...motion.beat_impact, enabled } };
  }
  return motion;
}

function motionWithReducedMotion(motion: MotionSettings): MotionSettings {
  return {
    ...motion,
    keyframes: [],
    ken_burns: { ...motion.ken_burns, enabled: false },
    speed_ramp: { ...motion.speed_ramp, enabled: false },
    beat_impact: { ...motion.beat_impact, enabled: false },
  };
}

function numberFromInput(value: string, fallback: number) {
  const next = Number(value);
  return Number.isFinite(next) ? next : fallback;
}

function motionTargetForTimelineClip(clip: TimelineClip): {
  target: MotionTarget;
  clipId: string | null;
  atMs: number;
} | null {
  if (clip.sourceRef.kind === "cut") {
    return { target: "source", clipId: clip.id, atMs: clip.startMs };
  }
  if (clip.sourceRef.kind === "broll_slot" && clip.sourceRef.id) {
    return { target: "broll", clipId: clip.sourceRef.id, atMs: 0 };
  }
  return null;
}
