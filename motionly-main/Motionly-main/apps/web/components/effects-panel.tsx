"use client";

import type {
  AfterEffectsPresetSummary,
  BackgroundRemovalSettings,
  EffectsConfigSummary,
  GradientRampSettings,
  MotionEffectKey,
  VisualPresetKey,
} from "@motionly/shared";
import type { CSSProperties } from "react";
import {
  Aperture,
  FileJson2,
  Loader2,
  RefreshCw,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/button";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

type EffectsPanelProps = {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialConfig: EffectsConfigSummary | null;
  suggestedPreset?: VisualPresetKey | null;
  hasActivePlan: boolean;
  sourceThumbnailUrl?: string | null;
  hasReadySource: boolean;
  hasBrollSlots: boolean;
};

const presets: Array<{ key: VisualPresetKey; name: string; tone: string }> = [
  { key: "vogue_luxury", name: "Editorial Contrast", tone: "High-fashion grade" },
  { key: "champagne_gold", name: "Warm Gold Grade", tone: "Premium warmth" },
  {
    key: "dubai_influencer",
    name: "Bright Travel Pop",
    tone: "Clean saturation",
  },
  { key: "old_money", name: "Muted Film Grade", tone: "Soft texture" },
  { key: "clean_founder", name: "Crisp Neutral Grade", tone: "Low texture" },
];

const motionEffects: Array<{ key: MotionEffectKey; label: string }> = [
  { key: "zoom_punch", label: "Zoom" },
  { key: "speed_ramp", label: "Ramp" },
  { key: "motion_blur", label: "Blur" },
  { key: "flash", label: "Flash" },
  { key: "glitch", label: "Glitch" },
  { key: "broll_slide_in", label: "B-roll slide" },
];

const afterEffectsPresetOptions: AfterEffectsPresetSummary[] = [
  {
    name: "Motionly Correction Controls",
    enabled: true,
    intensity: 0.55,
    expression_controls: {
      lutIntensity: 0.75,
      captionSafeArea: true,
    },
  },
  {
    name: "Fast Vertical Cleanup",
    enabled: true,
    intensity: 0.65,
    expression_controls: {
      motionBlurCompensation: true,
      edgeFeather: 18,
    },
  },
];

const defaultBackgroundRemoval: BackgroundRemovalSettings = {
  enabled: false,
  apply_to_broll: true,
  feather_px: 18,
  spill_suppression: 0.45,
  replacement: "gradient_ramp",
};

const defaultGradientRamp: GradientRampSettings = {
  enabled: false,
  start_color: "#0f172a",
  end_color: "#f59e0b",
  angle_deg: 135,
  opacity: 0.28,
  blend_mode: "soft-light",
};

const presetDefaults: Record<
  VisualPresetKey,
  {
    lutIntensity: number;
    texture: {
      grain: number;
      vignette: number;
      lightLeak: number;
      anamorphicFlare: boolean;
    };
    motionEffects: MotionEffectKey[];
    gradientRamp: GradientRampSettings;
  }
> = {
  vogue_luxury: {
    lutIntensity: 0.82,
    texture: {
      grain: 0.22,
      vignette: 0.2,
      lightLeak: 0.08,
      anamorphicFlare: false,
    },
    motionEffects: ["zoom_punch", "flash"],
    gradientRamp: {
      enabled: true,
      start_color: "#111827",
      end_color: "#f8fafc",
      angle_deg: 118,
      opacity: 0.24,
      blend_mode: "soft-light",
    },
  },
  champagne_gold: {
    lutIntensity: 0.76,
    texture: {
      grain: 0.16,
      vignette: 0.14,
      lightLeak: 0.12,
      anamorphicFlare: false,
    },
    motionEffects: ["zoom_punch", "broll_slide_in"],
    gradientRamp: {
      enabled: false,
      start_color: "#20120b",
      end_color: "#f5c76b",
      angle_deg: 135,
      opacity: 0.28,
      blend_mode: "soft-light",
    },
  },
  dubai_influencer: {
    lutIntensity: 0.8,
    texture: {
      grain: 0.12,
      vignette: 0.1,
      lightLeak: 0.18,
      anamorphicFlare: true,
    },
    motionEffects: ["speed_ramp", "flash", "broll_slide_in"],
    gradientRamp: {
      enabled: false,
      start_color: "#0284c7",
      end_color: "#f97316",
      angle_deg: 122,
      opacity: 0.22,
      blend_mode: "screen",
    },
  },
  old_money: {
    lutIntensity: 0.72,
    texture: {
      grain: 0.2,
      vignette: 0.18,
      lightLeak: 0.05,
      anamorphicFlare: false,
    },
    motionEffects: ["motion_blur"],
    gradientRamp: {
      enabled: true,
      start_color: "#1f2933",
      end_color: "#bfa46a",
      angle_deg: 146,
      opacity: 0.18,
      blend_mode: "soft-light",
    },
  },
  clean_founder: {
    lutIntensity: 0.62,
    texture: {
      grain: 0.06,
      vignette: 0.06,
      lightLeak: 0,
      anamorphicFlare: false,
    },
    motionEffects: ["zoom_punch"],
    gradientRamp: {
      enabled: false,
      start_color: "#0f172a",
      end_color: "#38bdf8",
      angle_deg: 90,
      opacity: 0.12,
      blend_mode: "normal",
    },
  },
  cinematic_hotel: {
    lutIntensity: 0.84,
    texture: {
      grain: 0.24,
      vignette: 0.24,
      lightLeak: 0.1,
      anamorphicFlare: true,
    },
    motionEffects: ["motion_blur", "flash"],
    gradientRamp: {
      enabled: true,
      start_color: "#111827",
      end_color: "#c08457",
      angle_deg: 152,
      opacity: 0.26,
      blend_mode: "soft-light",
    },
  },
};

export function EffectsPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialConfig,
  suggestedPreset,
  hasActivePlan,
  sourceThumbnailUrl,
  hasReadySource,
  hasBrollSlots,
}: EffectsPanelProps) {
  const [config, setConfig] = useState(initialConfig);
  const [planReady, setPlanReady] = useState(hasActivePlan);
  const [preset, setPreset] = useState<VisualPresetKey>(
    initialConfig?.preset ?? suggestedPreset ?? "champagne_gold",
  );
  const [lutIntensity, setLutIntensity] = useState(
    initialConfig?.lut_intensity ?? 0.75,
  );
  const [grain, setGrain] = useState(initialConfig?.texture.grain ?? 0.18);
  const [vignette, setVignette] = useState(
    initialConfig?.texture.vignette ?? 0.16,
  );
  const [lightLeak, setLightLeak] = useState(
    initialConfig?.texture.light_leak ?? 0.08,
  );
  const [anamorphicFlare, setAnamorphicFlare] = useState(
    initialConfig?.texture.anamorphic_flare ?? false,
  );
  const [selectedMotion, setSelectedMotion] = useState<MotionEffectKey[]>(
    normalizeMotionEffects(initialConfig?.motion_effects ?? ["zoom_punch"]),
  );
  const [backgroundRemoval, setBackgroundRemoval] =
    useState<BackgroundRemovalSettings>(
      initialConfig?.background_removal ?? defaultBackgroundRemoval,
    );
  const [gradientRamp, setGradientRamp] = useState<GradientRampSettings>(
    initialConfig?.gradient_ramp ?? defaultGradientRamp,
  );
  const [afterEffectsPresets, setAfterEffectsPresets] = useState<
    AfterEffectsPresetSummary[]
  >(
    initialConfig?.after_effects_presets?.length
      ? initialConfig.after_effects_presets
      : [afterEffectsPresetOptions[0]],
  );
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">(
    "idle",
  );
  const [message, setMessage] = useState<string | null>(null);
  const gradientColorsValid =
    isHexColor(gradientRamp.start_color) && isHexColor(gradientRamp.end_color);
  const gradientAngleValid =
    Number.isFinite(gradientRamp.angle_deg) &&
    gradientRamp.angle_deg >= 0 &&
    gradientRamp.angle_deg <= 360;
  const effectsInputValid = gradientColorsValid && gradientAngleValid;
  const backgroundRemovalSupported = hasReadySource;
  const effectiveBackgroundRemoval: BackgroundRemovalSettings = {
    ...backgroundRemoval,
    enabled: backgroundRemovalSupported && backgroundRemoval.enabled,
    apply_to_broll:
      backgroundRemovalSupported &&
      hasBrollSlots &&
      backgroundRemoval.apply_to_broll,
  };
  const liveRenderImpact = renderImpact({
    grain,
    vignette,
    lightLeak,
    anamorphicFlare,
    selectedMotion,
    backgroundRemoval: effectiveBackgroundRemoval,
    gradientRamp,
    afterEffectsPresets,
  });
  const liveEffectCap = effectCap(selectedMotion);
  const currentPreviewStyle = {
    filter: presetPreviewFilter(preset, lutIntensity),
  };
  const currentPreviewOverlay: CSSProperties | null = gradientRamp.enabled
    ? {
        background: `linear-gradient(${gradientRamp.angle_deg}deg, ${gradientRamp.start_color}, ${gradientRamp.end_color})`,
        opacity: Math.min(0.42, gradientRamp.opacity),
        mixBlendMode: gradientRamp.blend_mode,
      }
    : null;

  useEffect(() => {
    function handlePlanReady(event: Event) {
      const detail = (event as CustomEvent<{ planId?: string; version?: number; colorGrade?: string }>).detail ?? {};
      if (!detail.planId) return;
      setPlanReady(true);
      setConfig((current) => (current?.edit_plan_id === detail.planId ? current : null));
      setPreset(toVisualPreset(detail.colorGrade) ?? "champagne_gold");
      setMessage(`Plan v${detail.version ?? "current"} active for effects.`);
      setPhase("idle");
    }

    window.addEventListener("motionly:edit-plan-ready", handlePlanReady);
    return () => window.removeEventListener("motionly:edit-plan-ready", handlePlanReady);
  }, []);

  function applyPresetDefaults(nextPreset: VisualPresetKey) {
    const defaults = presetDefaults[nextPreset];
    setPreset(nextPreset);
    setLutIntensity(defaults.lutIntensity);
    setGrain(defaults.texture.grain);
    setVignette(defaults.texture.vignette);
    setLightLeak(defaults.texture.lightLeak);
    setAnamorphicFlare(defaults.texture.anamorphicFlare);
    setSelectedMotion(normalizeMotionEffects(defaults.motionEffects));
    setGradientRamp(defaults.gradientRamp);
    setConfig(null);
  }

  async function updateEffects(resetToDefaults = false, force = false) {
    setPhase("loading");
    setMessage(
      resetToDefaults
        ? "Resetting effects to the selected preset defaults..."
        : "Applying effects and color settings...",
    );
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/effects`,
        {
          method: "PATCH",
          headers: await requestHeaders(workspaceId, userId, apiAccessToken),
          body: JSON.stringify({
            preset,
            lut_intensity: lutIntensity,
            texture: {
              grain,
              vignette,
              light_leak: lightLeak,
              anamorphic_flare: anamorphicFlare,
            },
            motion_effects: selectedMotion,
            background_removal: effectiveBackgroundRemoval,
            gradient_ramp: {
              ...gradientRamp,
              enabled: gradientRamp.enabled,
            },
            after_effects_presets: afterEffectsPresets,
            reset_to_defaults: resetToDefaults,
            force,
          }),
        },
      );
      const payload = await readJson(response);
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Effects could not be updated.",
        );
      setConfig(payload.config);
      setGrain(payload.config.texture.grain);
      setVignette(payload.config.texture.vignette);
      setLightLeak(payload.config.texture.light_leak);
      setAnamorphicFlare(payload.config.texture.anamorphic_flare);
      setSelectedMotion(normalizeMotionEffects(payload.config.motion_effects));
      setBackgroundRemoval(payload.config.background_removal);
      setGradientRamp(payload.config.gradient_ramp);
      setAfterEffectsPresets(payload.config.after_effects_presets);
      setPhase("ready");
      setMessage(
        payload.cached
          ? "Reused the matching effects setup."
          : `Effects ready: ${payload.job_id}`,
      );
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Aperture className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-2xl font-bold">Effects and Color</h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {config
              ? `${presetLabel(config.preset)} / ${Math.round(config.lut_intensity * 100)}% LUT`
              : "Choose a visual signature for the next render"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={() => updateEffects(true, true)}
            disabled={!planReady || phase === "loading"}
          >
            <RefreshCw className="mr-2 size-4" aria-hidden="true" />
            Reset effect settings
          </Button>
          <Button
            type="button"
            onClick={() => updateEffects(false, false)}
            disabled={!planReady || phase === "loading" || !effectsInputValid}
            title={!planReady ? "Create an edit plan before applying effects." : !effectsInputValid ? "Fix gradient color or angle values before applying effects." : undefined}
          >
            {phase === "loading" ? (
              <Loader2
                className="mr-2 size-4 animate-spin"
                aria-hidden="true"
              />
            ) : (
              <WandSparkles className="mr-2 size-4" aria-hidden="true" />
            )}
            Apply effects
          </Button>
        </div>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {presets.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => applyPresetDefaults(item.key)}
                className={cn(
                  "min-h-24 rounded-lg border p-3 text-left transition",
                  preset === item.key
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-white hover:bg-muted",
                )}
                aria-pressed={preset === item.key}
              >
                <span className="block text-sm font-bold">{item.name}</span>
                <span
                  className={cn(
                    "mt-1 block text-xs",
                    preset === item.key
                      ? "text-primary-foreground/80"
                      : "text-muted-foreground",
                  )}
                >
                  {item.tone}
                </span>
              </button>
            ))}
          </div>

          <SliderRow
            label="LUT intensity"
            value={lutIntensity}
            min={0.5}
            onChange={setLutIntensity}
          />
          <SliderRow label="Film grain" value={grain} onChange={setGrain} />
          <SliderRow label="Vignette" value={vignette} onChange={setVignette} />
          <SliderRow
            label="Light leak"
            value={lightLeak}
            onChange={setLightLeak}
          />

          <label className="flex items-center justify-between gap-3 rounded-lg border border-border bg-white p-3 text-sm font-semibold">
            <span>Anamorphic flare</span>
            <input
              type="checkbox"
              checked={anamorphicFlare}
              onChange={(event) => setAnamorphicFlare(event.target.checked)}
              className="size-4 accent-primary"
            />
          </label>

          <div className="grid gap-3 lg:grid-cols-2">
            <label
              className={cn(
                "flex items-center justify-between gap-3 rounded-lg border border-border bg-white p-3 text-sm font-semibold",
                !backgroundRemovalSupported && "opacity-60",
              )}
            >
              <span>
                Background removal
                {!backgroundRemovalSupported ? (
                  <span className="block text-xs font-medium text-muted-foreground">
                    Requires a ready source
                  </span>
                ) : null}
              </span>
              <input
                type="checkbox"
                checked={effectiveBackgroundRemoval.enabled}
                disabled={!backgroundRemovalSupported}
                onChange={(event) =>
                  setBackgroundRemoval((current) => ({
                    ...current,
                    enabled: event.target.checked,
                  }))
                }
                className="size-4 accent-primary"
              />
            </label>
            <label
              className={cn(
                "flex items-center justify-between gap-3 rounded-lg border border-border bg-white p-3 text-sm font-semibold",
                (!backgroundRemovalSupported || !hasBrollSlots) && "opacity-60",
              )}
            >
              <span>
                B-roll background removal
                {!hasBrollSlots ? (
                  <span className="block text-xs font-medium text-muted-foreground">
                    Requires ready B-roll
                  </span>
                ) : null}
              </span>
              <input
                type="checkbox"
                checked={effectiveBackgroundRemoval.apply_to_broll}
                disabled={!backgroundRemovalSupported || !hasBrollSlots}
                onChange={(event) =>
                  setBackgroundRemoval((current) => ({
                    ...current,
                    apply_to_broll: event.target.checked,
                  }))
                }
                className="size-4 accent-primary"
              />
            </label>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <SliderRow
              label="Background feather"
              value={backgroundRemoval.feather_px / 80}
              disabled={!effectiveBackgroundRemoval.enabled}
              onChange={(value) =>
                setBackgroundRemoval((current) => ({
                  ...current,
                  feather_px: Math.round(value * 80),
                }))
              }
            />
            <SliderRow
              label="Spill suppression"
              value={backgroundRemoval.spill_suppression}
              disabled={!effectiveBackgroundRemoval.enabled}
              onChange={(value) =>
                setBackgroundRemoval((current) => ({
                  ...current,
                  spill_suppression: value,
                }))
              }
            />
          </div>

          <div className="rounded-lg border border-border bg-white p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm font-semibold">Gradient Ramp</p>
              <input
                type="checkbox"
                checked={gradientRamp.enabled}
                onChange={(event) => {
                  setGradientRamp((current) => ({
                    ...current,
                    enabled: event.target.checked,
                  }));
                }}
                className="size-4 accent-primary"
              />
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <ColorRow
                label="Start"
                value={gradientRamp.start_color}
                onChange={(value) =>
                  setGradientRamp((current) => ({
                    ...current,
                    start_color: value,
                  }))
                }
              />
              <ColorRow
                label="End"
                value={gradientRamp.end_color}
                onChange={(value) =>
                  setGradientRamp((current) => ({
                    ...current,
                    end_color: value,
                  }))
                }
              />
              <SliderRow
                label="Ramp opacity"
                value={gradientRamp.opacity}
                onChange={(value) =>
                  setGradientRamp((current) => ({ ...current, opacity: value }))
                }
              />
              <label className="grid gap-2 text-sm font-semibold">
                <span>Angle</span>
                <input
                  type="number"
                  min={0}
                  max={360}
                  value={gradientRamp.angle_deg}
                  onChange={(event) =>
                    setGradientRamp((current) => ({
                      ...current,
                      angle_deg: Number(event.target.value),
                    }))
                  }
                  className="rounded-lg border border-border bg-white px-3 py-2"
                />
              </label>
            </div>
            {!effectsInputValid ? (
              <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                Use 6-digit hex colors and an angle from 0 to 360 before applying effects.
              </p>
            ) : null}
          </div>

          <div className="flex flex-wrap gap-2">
            {motionEffects.map((effect) => {
              const active = selectedMotion.includes(effect.key);
              return (
                <button
                  key={effect.key}
                  type="button"
                  onClick={() =>
                    setSelectedMotion((current) =>
                      active
                        ? current.filter((item) => item !== effect.key)
                        : [...current, effect.key],
                    )
                  }
                  className={cn(
                    "rounded-lg border px-3 py-2 text-sm font-semibold transition",
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-white hover:bg-muted",
                  )}
                  aria-pressed={active}
                >
                  {effect.label}
                </button>
              );
            })}
          </div>

          <div className="rounded-lg border border-border bg-white p-3">
            <p className="text-sm font-semibold">After Effects presets</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {afterEffectsPresetOptions.map((presetOption) => {
                const active = afterEffectsPresets.some(
                  (item) => item.name === presetOption.name,
                );
                return (
                  <button
                    key={presetOption.name}
                    type="button"
                    onClick={() =>
                      setAfterEffectsPresets((current) =>
                        active
                          ? current.filter(
                              (item) => item.name !== presetOption.name,
                            )
                          : [...current, presetOption],
                      )
                    }
                    className={cn(
                      "rounded-lg border px-3 py-2 text-sm font-semibold transition",
                      active
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-white hover:bg-muted",
                    )}
                    aria-pressed={active}
                  >
                    {presetOption.name}
                  </button>
                );
              })}
            </div>
          </div>

          {message ? (
            <p
              className={cn(
                "rounded-lg border p-3 text-sm",
                phase === "error"
                  ? "border-red-200 bg-red-50 text-red-800"
                  : "border-border bg-muted text-muted-foreground",
              )}
            >
              {message}
            </p>
          ) : null}
        </div>

        <div className="rounded-lg border border-border bg-white p-4">
          <div className="flex items-center gap-2 text-sm font-bold uppercase text-muted-foreground">
            <Sparkles className="size-4" aria-hidden="true" />
            Render impact
          </div>
          <p className="mt-3 text-3xl font-bold capitalize">
            {liveRenderImpact}
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            {liveEffectCap} effects per 30s cap
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            Higher impact can increase render time; heavy effects are capped to protect caption readability.
          </p>
          <div className="mt-4 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <PreviewSwatch
              label="Before"
              thumbnailUrl={config?.preview_before_url ?? sourceThumbnailUrl}
            />
            <PreviewSwatch
              label="After"
              thumbnailUrl={config?.preview_after_url ?? sourceThumbnailUrl}
              imageStyle={currentPreviewStyle}
              overlayStyle={currentPreviewOverlay}
            />
          </div>
          {config?.after_effects_project_url ||
          config?.correction_manifest_url ? (
            <div className="mt-4 rounded-lg border border-border bg-muted p-3 text-sm text-muted-foreground">
              <div className="flex items-center gap-2 font-bold text-foreground">
                <FileJson2 className="size-4" aria-hidden="true" />
                After Effects handoff
              </div>
              <p className="mt-2 break-all">
                {config.after_effects_project_url ?? "AEP pending"}
              </p>
              <p className="mt-1 break-all">
                {config.correction_manifest_url ?? "Manifest pending"}
              </p>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function ColorRow({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid gap-2 text-sm font-semibold">
      <span>{label}</span>
      <span className="flex items-center gap-2">
        <input
          type="color"
          value={isHexColor(value) ? value : "#000000"}
          onChange={(event) => onChange(event.target.value)}
          className="h-10 w-12 rounded border border-border bg-white"
        />
        <input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-border bg-white px-3 py-2"
        />
      </span>
    </label>
  );
}

function isHexColor(value: string) {
  return /^#[0-9a-fA-F]{6}$/.test(value);
}

function toVisualPreset(colorGrade: string | null | undefined): VisualPresetKey | null {
  if (!colorGrade) return null;
  if (colorGrade.includes("champagne") || colorGrade.includes("gold")) return "champagne_gold";
  if (colorGrade.includes("founder") || colorGrade.includes("neutral") || colorGrade.includes("clean")) return "clean_founder";
  if (colorGrade.includes("travel") || colorGrade.includes("daylight")) return "dubai_influencer";
  if (colorGrade.includes("fashion") || colorGrade.includes("editorial")) return "vogue_luxury";
  if (colorGrade.includes("skin")) return "clean_founder";
  return null;
}

function SliderRow({
  label,
  value,
  min = 0,
  disabled = false,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  disabled?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label
      className={cn(
        "grid gap-2 rounded-lg border border-border bg-white p-3",
        disabled && "opacity-60",
      )}
    >
      <span className="flex items-center justify-between gap-3 text-sm font-semibold">
        <span>{label}</span>
        <span>{Math.round(value * 100)}%</span>
      </span>
      <input
        type="range"
        min={min}
        max="1"
        step="0.05"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full accent-primary"
      />
    </label>
  );
}

function PreviewSwatch({
  label,
  thumbnailUrl,
  imageStyle,
  overlayStyle,
}: {
  label: string;
  thumbnailUrl?: string | null;
  imageStyle?: CSSProperties;
  overlayStyle?: CSSProperties | null;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="relative h-20 overflow-hidden bg-slate-900">
        {thumbnailUrl ? (
          <img
            alt=""
            className="size-full object-cover"
            src={thumbnailUrl}
            style={imageStyle}
          />
        ) : (
          <div
            className="size-full bg-[linear-gradient(135deg,#1f2937,#94a3b8)]"
            style={imageStyle}
          />
        )}
        {overlayStyle ? (
          <div className="absolute inset-0" style={overlayStyle} />
        ) : null}
      </div>
      <p className="p-2 font-semibold">{label}</p>
    </div>
  );
}

function presetLabel(preset: VisualPresetKey) {
  return presets.find((item) => item.key === preset)?.name ?? preset;
}

function normalizeMotionEffects(effects: MotionEffectKey[]) {
  return effects.filter((effect) => effect !== "gradient_ramp");
}

function renderImpact({
  grain,
  vignette,
  lightLeak,
  anamorphicFlare,
  selectedMotion,
  backgroundRemoval,
  gradientRamp,
  afterEffectsPresets,
}: {
  grain: number;
  vignette: number;
  lightLeak: number;
  anamorphicFlare: boolean;
  selectedMotion: MotionEffectKey[];
  backgroundRemoval: BackgroundRemovalSettings;
  gradientRamp: GradientRampSettings;
  afterEffectsPresets: AfterEffectsPresetSummary[];
}) {
  const score =
    grain +
    vignette +
    lightLeak +
    (anamorphicFlare ? 0.3 : 0) +
    selectedMotion.length * 0.22 +
    (backgroundRemoval.enabled ? 0.55 : 0) +
    (backgroundRemoval.apply_to_broll ? 0.2 : 0) +
    (gradientRamp.enabled ? 0.18 : 0) +
    afterEffectsPresets.filter((preset) => preset.enabled).length * 0.2;
  if (score >= 2.2) return "high";
  if (score >= 1.1) return "medium";
  return "low";
}

function effectCap(selectedMotion: MotionEffectKey[]) {
  return Math.max(2, Math.min(6, 6 - Math.max(0, selectedMotion.length - 2)));
}

function presetPreviewFilter(preset: VisualPresetKey, intensity: number) {
  const amount = Math.max(0.5, Math.min(1, intensity));
  const filterByPreset: Record<VisualPresetKey, string> = {
    vogue_luxury: `contrast(${1 + amount * 0.16}) saturate(${1 - amount * 0.06}) brightness(${1 + amount * 0.04})`,
    champagne_gold: `sepia(${amount * 0.18}) saturate(${1 + amount * 0.16}) brightness(${1 + amount * 0.06})`,
    dubai_influencer: `saturate(${1 + amount * 0.24}) contrast(${1 + amount * 0.08}) brightness(${1 + amount * 0.05})`,
    old_money: `sepia(${amount * 0.14}) saturate(${1 - amount * 0.08}) contrast(${1 + amount * 0.12})`,
    clean_founder: `saturate(${1 - amount * 0.1}) brightness(${1 + amount * 0.08}) contrast(${1 + amount * 0.04})`,
    cinematic_hotel: `sepia(${amount * 0.12}) contrast(${1 + amount * 0.18}) saturate(${1 + amount * 0.08})`,
  };
  return filterByPreset[preset];
}

function requestHeaders(workspaceId: string, userId: string, apiAccessToken: string) {
  return buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken });
}

async function readJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}
