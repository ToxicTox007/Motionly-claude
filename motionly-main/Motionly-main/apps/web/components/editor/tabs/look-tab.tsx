"use client";

import React, { useState } from "react";
import { Label, Select, Slider } from "../ui";
import { Button } from "../../button";
import { Palette, Sparkles, Loader2 } from "lucide-react";
import type { VisualPresetKey, EffectsConfigSummary } from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";

interface LookTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialConfig: EffectsConfigSummary | null;
  suggestedPreset?: VisualPresetKey | null;
}

const presets: { value: VisualPresetKey; label: string }[] = [
  { value: "vogue_luxury", label: "Editorial Contrast" },
  { value: "champagne_gold", label: "Warm Gold" },
  { value: "dubai_influencer", label: "Travel Pop" },
  { value: "old_money", label: "Muted Film" },
  { value: "clean_founder", label: "Crisp Neutral" },
];

export function LookTab({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialConfig,
  suggestedPreset,
}: LookTabProps) {
  const [preset, setPreset] = useState<VisualPresetKey>(initialConfig?.preset ?? suggestedPreset ?? "clean_founder");
  const [intensity, setIntensity] = useState(initialConfig ? initialConfig.lut_intensity * 100 : 80);
  const [loading, setLoading] = useState(false);

  async function applyEffects() {
    setLoading(true);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/effects`, {
        method: "PATCH",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          preset,
          lut_intensity: intensity / 100,
        }),
      });
      if (!response.ok) throw new Error("Failed to apply effects");
      alert("Look updated!");
    } catch (error) {
      console.error(error);
      alert("Failed to update look.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      <div className="space-y-6">
        <div className="space-y-2">
          <Label>Visual Style</Label>
          <Select
            value={preset}
            onChange={(val) => setPreset(val as VisualPresetKey)}
            options={presets}
          />
        </div>

        <Slider
          label="Effect Intensity"
          value={intensity}
          onChange={setIntensity}
        />

        <div className="pt-4 border-t border-border">
          <div className="flex items-center justify-between mb-4">
            <Label className="mb-0">Advanced FX</Label>
            <span className="text-[10px] font-bold text-muted-foreground bg-muted px-1.5 py-0.5 rounded">Coming Soon</span>
          </div>
          <div className="grid grid-cols-2 gap-3 opacity-50 grayscale pointer-events-none">
             {["Background Blur", "Grain Texture", "Motion Blur", "Light Leaks"].map(fx => (
               <div key={fx} className="flex items-center gap-2 rounded-lg border border-border p-2 text-[10px] font-bold">
                 <div className="size-2 rounded-full bg-border" />
                 {fx}
               </div>
             ))}
          </div>
        </div>
      </div>

      <Button onClick={applyEffects} disabled={loading} className="w-full h-12 rounded-xl">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Palette className="mr-2 size-4" />}
        Apply Look
      </Button>
    </div>
  );
}
