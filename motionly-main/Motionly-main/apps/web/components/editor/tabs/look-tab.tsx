"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Select, Slider } from "../ui";
import { Button } from "../../button";
import { Palette, Loader2 } from "lucide-react";
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
  const router = useRouter();
  const [preset, setPreset] = useState<VisualPresetKey>(initialConfig?.preset ?? suggestedPreset ?? "clean_founder");
  const [intensity, setIntensity] = useState(initialConfig ? initialConfig.lut_intensity * 100 : 80);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function applyEffects() {
    setLoading(true);
    setMessage(null);
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
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Failed to apply effects");
      setMessage("Look queued and saved.");
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
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

        <Slider
          label="Effect Intensity"
          value={intensity}
          onChange={setIntensity}
        />
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
