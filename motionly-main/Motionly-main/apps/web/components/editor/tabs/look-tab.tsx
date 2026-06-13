"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Input, Label, Select, Slider } from "../ui";
import { Button } from "../../button";
import { AlertTriangle, Check, Loader2, Palette } from "lucide-react";
import type {
  BackgroundRemovalSettings,
  EffectsConfigSummary,
  EffectsTextureSettings,
  GradientRampSettings,
  MotionEffectKey,
  VisualPresetKey,
} from "@motionly/shared";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

interface LookTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialConfig: EffectsConfigSummary | null;
  suggestedPreset?: VisualPresetKey | null;
  onConfigUpdated: (config: EffectsConfigSummary) => void;
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
  { value: "speed_ramp", label: "Speed Ramp" },
  { value: "motion_blur", label: "Motion Blur" },
  { value: "flash", label: "Flash" },
  { value: "glitch", label: "Glitch" },
  { value: "broll_slide_in", label: "B-roll Slide" },
  { value: "gradient_ramp", label: "Gradient Ramp" },
];

const motionHeavyEffects: MotionEffectKey[] = ["zoom_punch", "speed_ramp", "motion_blur", "flash", "glitch"];
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

export function LookTab({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialConfig,
  suggestedPreset,
  onConfigUpdated,
}: LookTabProps) {
  const router = useRouter();
  const [preset, setPreset] = useState<VisualPresetKey>(
    initialConfig?.preset ?? suggestedPreset ?? "clean_founder",
  );
  const [intensity, setIntensity] = useState(
    initialConfig ? Math.round(initialConfig.lut_intensity * 100) : 80,
  );
  const [texture, setTexture] = useState<EffectsTextureSettings>(
    initialConfig?.texture ?? defaultTexture,
  );
  const [selectedMotionEffects, setSelectedMotionEffects] = useState<MotionEffectKey[]>(
    initialConfig?.motion_effects ?? [],
  );
  const [backgroundRemoval, setBackgroundRemoval] = useState<BackgroundRemovalSettings>(
    initialConfig?.background_removal ?? defaultBackground,
  );
  const [gradientRamp, setGradientRamp] = useState<GradientRampSettings>(
    initialConfig?.gradient_ramp ?? defaultGradient,
  );
  const [resetToDefaults, setResetToDefaults] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [premiumBlockedMessage, setPremiumBlockedMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(media.matches);
    const onChange = () => setReducedMotion(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  async function applyEffects() {
    setLoading(true);
    setMessage(null);
    setPremiumBlockedMessage(null);
    try {
      const payload = await editorApiRequest<{ config: EffectsConfigSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/effects`,
        {
        method: "PATCH",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Look settings could not be applied. Please try again.",
          body: {
            preset,
            lut_intensity: Math.max(50, intensity) / 100,
            texture,
            motion_effects: reducedMotion
              ? selectedMotionEffects.filter((effect) => !motionHeavyEffects.includes(effect))
              : selectedMotionEffects,
            background_removal: backgroundRemoval,
            gradient_ramp: gradientRamp,
            after_effects_presets: initialConfig?.after_effects_presets ?? [],
            reset_to_defaults: resetToDefaults,
            force: true,
          },
        },
      );
      onConfigUpdated(payload.config);
      setPreset(payload.config.preset);
      setIntensity(Math.round(payload.config.lut_intensity * 100));
      setTexture(payload.config.texture);
      setSelectedMotionEffects(payload.config.motion_effects);
      setBackgroundRemoval(payload.config.background_removal);
      setGradientRamp(payload.config.gradient_ramp);
      setResetToDefaults(false);
      setMessage("Look applied.");
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function toggleMotionEffect(effect: MotionEffectKey) {
    if (reducedMotion && motionHeavyEffects.includes(effect)) {
      setMessage("Reduced motion is on. Motion-heavy effects are disabled for accessibility.");
      return;
    }
    setSelectedMotionEffects((current) =>
      current.includes(effect)
        ? current.filter((item) => item !== effect)
        : [...current, effect],
    );
    if (effect === "gradient_ramp") {
      setGradientRamp((current) => ({ ...current, enabled: !current.enabled }));
    }
  }

  return (
    <div className="space-y-6">
      <h2 className="sr-only">Look</h2>
      <div className="pixel-panel pixel-corners space-y-6 p-4">
        <div className="space-y-2">
          <Label>Visual Style</Label>
          <Select
            id="visual-style"
            label="Visual Style"
            value={preset}
            onChange={(val) => setPreset(val as VisualPresetKey)}
            options={presets}
          />
        </div>

        <Slider label="LUT Intensity" value={intensity} min={50} max={100} onChange={setIntensity} />

        <div className="border-t border-border pt-4">
          <Label>Texture</Label>
          <Slider
            label="Grain"
            value={Math.round(texture.grain * 100)}
            onChange={(value) => setTexture((current) => ({ ...current, grain: value / 100 }))}
          />
          <Slider
            label="Vignette"
            value={Math.round(texture.vignette * 100)}
            onChange={(value) => setTexture((current) => ({ ...current, vignette: value / 100 }))}
          />
          <Slider
            label="Light Leak"
            value={Math.round(texture.light_leak * 100)}
            onChange={(value) => setTexture((current) => ({ ...current, light_leak: value / 100 }))}
          />
          {texture.light_leak > 0.2 ? (
            <p role="alert" className="mt-2 flex gap-2 border border-amber-300 bg-amber-50 p-2 text-xs font-bold text-amber-800">
              <AlertTriangle className="size-4 shrink-0" />
              Light leak can create flashing in fast edits. Lower it for sensitive viewers.
            </p>
          ) : null}
          <label className="mt-3 flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-bold text-muted-foreground">
            Anamorphic flare
            <input
              type="checkbox"
              aria-label="Enable anamorphic flare"
              checked={texture.anamorphic_flare}
              onChange={(event) =>
                setTexture((current) => ({ ...current, anamorphic_flare: event.target.checked }))
              }
              className="size-6 accent-primary"
            />
          </label>
        </div>

        <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 border border-border bg-muted/30 p-3 text-sm font-bold">
          Reduced motion
          <input
            type="checkbox"
            aria-label="Reduce motion and flashing effects"
            checked={reducedMotion}
            onChange={(event) => setReducedMotion(event.target.checked)}
            className="size-6 accent-primary"
          />
        </label>

        <div className="border-t border-border pt-4">
          <Label>Motion Effects</Label>
          <p className="mb-3 text-sm text-muted-foreground">
            Flashing and aggressive motion effects stay disabled when reduced motion is on.
          </p>
          <div className="flex flex-wrap gap-2">
            {motionEffects.map((effect) => {
              const selected = selectedMotionEffects.includes(effect.value);
              const disabled = reducedMotion && motionHeavyEffects.includes(effect.value);
              const flashing = flashingEffects.includes(effect.value);
              return (
                <button
                  key={effect.value}
                  onClick={() => toggleMotionEffect(effect.value)}
                  disabled={disabled}
                  aria-pressed={selected}
                  aria-describedby={flashing ? "motion-effects-warning" : undefined}
                  className={cn(
                    "inline-flex min-h-11 items-center gap-1.5 border px-3 py-2 text-xs font-bold transition disabled:cursor-not-allowed disabled:opacity-50",
                    selected
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border hover:border-primary/40 hover:bg-muted",
                  )}
                >
                  {selected ? <Check className="size-3.5" aria-hidden="true" /> : null}
                  {effect.label}
                  {selected ? <span className="sr-only"> selected</span> : null}
                </button>
              );
            })}
          </div>
          <p id="motion-effects-warning" className="mt-3 flex gap-2 border border-amber-300 bg-amber-50 p-2 text-xs font-bold text-amber-800">
            <AlertTriangle className="size-4 shrink-0" />
            Flash and Glitch can create flashing frames. Keep reduced motion on when accessibility is the priority.
          </p>
          {premiumBlockedMessage ? (
            <p className="mt-3 border border-destructive/30 bg-destructive/10 p-2 text-xs font-bold text-destructive">
              {premiumBlockedMessage}
            </p>
          ) : null}
        </div>

        <div className="border-t border-border pt-4">
          <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-bold text-muted-foreground">
            Background removal
            <input
              type="checkbox"
              aria-label="Enable background removal"
              checked={backgroundRemoval.enabled}
              onChange={(event) =>
                setBackgroundRemoval((current) => ({ ...current, enabled: event.target.checked }))
              }
              className="size-6 accent-primary"
            />
          </label>
          {backgroundRemoval.enabled ? (
            <div className="mt-4 space-y-4">
              <Slider
                label="Feather"
                value={backgroundRemoval.feather_px}
                min={0}
                max={80}
                onChange={(value) =>
                  setBackgroundRemoval((current) => ({ ...current, feather_px: value }))
                }
              />
              <Slider
                label="Spill Suppression"
                value={Math.round(backgroundRemoval.spill_suppression * 100)}
                onChange={(value) =>
                  setBackgroundRemoval((current) => ({ ...current, spill_suppression: value / 100 }))
                }
              />
            </div>
          ) : null}
        </div>

        <div className="border-t border-border pt-4">
          <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm font-bold text-muted-foreground">
            Gradient ramp
            <input
              type="checkbox"
              aria-label="Enable gradient ramp"
              checked={gradientRamp.enabled}
              onChange={(event) =>
                setGradientRamp((current) => ({ ...current, enabled: event.target.checked }))
              }
              className="size-6 accent-primary"
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
                    setGradientRamp((current) => ({ ...current, start_color: event.target.value }))
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
                    setGradientRamp((current) => ({ ...current, end_color: event.target.value }))
                  }
                  className="px-2"
                />
              </div>
              <div className="col-span-2">
                <Slider
                  label="Opacity"
                  value={Math.round(gradientRamp.opacity * 100)}
                  onChange={(value) =>
                    setGradientRamp((current) => ({ ...current, opacity: value / 100 }))
                  }
                />
              </div>
            </div>
          ) : null}
        </div>

        <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 border-t border-border pt-4 text-sm font-bold text-muted-foreground">
          Reset preset defaults on save
          <input
            type="checkbox"
            aria-label="Reset preset defaults on save"
            checked={resetToDefaults}
            onChange={(event) => setResetToDefaults(event.target.checked)}
            className="size-6 accent-primary"
          />
        </label>
      </div>

      <Button onClick={applyEffects} disabled={loading} aria-busy={loading} className="h-12 w-full">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Palette className="mr-2 size-4" />}
        {loading ? "Applying, please wait" : "Apply Look"}
      </Button>
      {message ? (
        <p role={message.toLowerCase().includes("could not") || message.toLowerCase().includes("disabled") ? "alert" : "status"} aria-live="polite" className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}
