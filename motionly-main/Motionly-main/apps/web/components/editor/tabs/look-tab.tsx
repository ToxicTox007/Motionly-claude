"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Input, Label, Select, Slider } from "../ui";
import { Button } from "../../button";
import { Loader2, Palette } from "lucide-react";
import type {
  BackgroundRemovalSettings,
  EffectsConfigSummary,
  EffectsTextureSettings,
  GradientRampSettings,
  MotionEffectKey,
  VisualPresetKey,
} from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
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
  const [premiumBlockedMessage, setPremiumBlockedMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function applyEffects() {
    setLoading(true);
    setMessage(null);
    setPremiumBlockedMessage(null);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/effects`, {
        method: "PATCH",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          preset,
          lut_intensity: Math.max(50, intensity) / 100,
          texture,
          motion_effects: selectedMotionEffects,
          background_removal: backgroundRemoval,
          gradient_ramp: gradientRamp,
          after_effects_presets: initialConfig?.after_effects_presets ?? [],
          reset_to_defaults: resetToDefaults,
          force: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        const errorMessage = payload.error?.message ?? "Failed to apply effects";
        if (response.status === 402) setPremiumBlockedMessage(errorMessage);
        throw new Error(errorMessage);
      }
      onConfigUpdated(payload.config);
      setPreset(payload.config.preset);
      setIntensity(Math.round(payload.config.lut_intensity * 100));
      setTexture(payload.config.texture);
      setSelectedMotionEffects(payload.config.motion_effects);
      setBackgroundRemoval(payload.config.background_removal);
      setGradientRamp(payload.config.gradient_ramp);
      setResetToDefaults(false);
      setMessage("Look queued and saved.");
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function toggleMotionEffect(effect: MotionEffectKey) {
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
      <div className="pixel-panel pixel-corners space-y-6 p-4">
        <div className="space-y-2">
          <Label>Visual Style</Label>
          <Select
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
          <label className="mt-3 flex items-center justify-between gap-3 text-xs font-bold uppercase tracking-widest text-muted-foreground/70">
            Anamorphic flare
            <input
              type="checkbox"
              checked={texture.anamorphic_flare}
              onChange={(event) =>
                setTexture((current) => ({ ...current, anamorphic_flare: event.target.checked }))
              }
              className="size-4 accent-primary"
            />
          </label>
        </div>

        <div className="border-t border-border pt-4">
          <Label>Motion Effects</Label>
          <div className="flex flex-wrap gap-2">
            {motionEffects.map((effect) => {
              const selected = selectedMotionEffects.includes(effect.value);
              return (
                <button
                  key={effect.value}
                  onClick={() => toggleMotionEffect(effect.value)}
                  className={cn(
                    "border px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider transition",
                    selected
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border hover:border-primary/40 hover:bg-muted",
                  )}
                >
                  {effect.label}
                </button>
              );
            })}
          </div>
          {premiumBlockedMessage ? (
            <p className="mt-3 border border-destructive/30 bg-destructive/10 p-2 text-xs font-bold text-destructive">
              {premiumBlockedMessage}
            </p>
          ) : null}
        </div>

        <div className="border-t border-border pt-4">
          <label className="flex items-center justify-between gap-3 text-xs font-bold uppercase tracking-widest text-muted-foreground/70">
            Background removal
            <input
              type="checkbox"
              checked={backgroundRemoval.enabled}
              onChange={(event) =>
                setBackgroundRemoval((current) => ({ ...current, enabled: event.target.checked }))
              }
              className="size-4 accent-primary"
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
          <label className="flex items-center justify-between gap-3 text-xs font-bold uppercase tracking-widest text-muted-foreground/70">
            Gradient ramp
            <input
              type="checkbox"
              checked={gradientRamp.enabled}
              onChange={(event) =>
                setGradientRamp((current) => ({ ...current, enabled: event.target.checked }))
              }
              className="size-4 accent-primary"
            />
          </label>
          {gradientRamp.enabled ? (
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <Label>Start</Label>
                <Input
                  type="color"
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

        <label className="flex items-center justify-between gap-3 border-t border-border pt-4 text-xs font-bold uppercase tracking-widest text-muted-foreground/70">
          Reset preset defaults on save
          <input
            type="checkbox"
            checked={resetToDefaults}
            onChange={(event) => setResetToDefaults(event.target.checked)}
            className="size-4 accent-primary"
          />
        </label>
      </div>

      <Button onClick={applyEffects} disabled={loading} className="h-12 w-full">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Palette className="mr-2 size-4" />}
        Apply Look
      </Button>
      {message ? (
        <p className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}
