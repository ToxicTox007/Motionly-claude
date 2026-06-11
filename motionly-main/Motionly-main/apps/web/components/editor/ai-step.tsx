"use client";

import React, { useState } from "react";
import { Label, Select, TextArea, Slider } from "./ui";
import { Button } from "../button";
import { Sparkles, Loader2 } from "lucide-react";
import { env } from "@/lib/env";
import { buildApiHeaders } from "@/lib/client-api";
import type { BrollDensity, EditPlanRecordSummary, EditPlanStyleKey } from "@motionly/shared";

interface AIStepProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialPlans: EditPlanRecordSummary[];
  activePlanId: string | null;
  hasTranscript: boolean;
  onPlanGenerated: (plan: EditPlanRecordSummary) => void;
}

const styles: { value: EditPlanStyleKey; label: string }[] = [
  { value: "luxury", label: "Luxury (Warm & Premium)" },
  { value: "founder", label: "Founder (Clean & Pro)" },
  { value: "travel", label: "Travel (Vibrant & Immersive)" },
  { value: "fashion", label: "Fashion (Editorial & Dramatic)" },
  { value: "podcast", label: "Podcast (Speaker-focused)" },
];

export function AIStep({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialPlans,
  activePlanId,
  hasTranscript,
  onPlanGenerated,
}: AIStepProps) {
  const activePlan = initialPlans.find(p => p.id === activePlanId) || initialPlans[0];
  
  const [style, setStyle] = useState<EditPlanStyleKey>(activePlan?.style ?? "luxury");
  const [density, setDensity] = useState<BrollDensity>(activePlan?.broll_density ?? "medium");
  const [prompt, setPrompt] = useState(activePlan?.prompt ?? "");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const densityMap: Record<BrollDensity, number> = {
    none: 0,
    low: 33,
    medium: 66,
    high: 100
  };

  const reverseDensityMap = (val: number): BrollDensity => {
    if (val < 25) return "none";
    if (val < 50) return "low";
    if (val < 75) return "medium";
    return "high";
  };

  async function generatePlan() {
    setLoading(true);
    setMessage(null);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
        method: "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          prompt,
          style,
          broll_density: density,
          target_duration_s: 35, // Default for now
          force: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Failed to generate plan");
      onPlanGenerated(payload.plan);
      setMessage(`AI edit plan v${payload.plan.version} is ready.`);
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h3 className="text-xl font-black tracking-tight">AI Creative Direction</h3>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Our AI will analyze your video and plan the perfect edit based on your chosen style.
        </p>
      </div>

      <div className="pixel-panel pixel-corners space-y-6 p-4">
        <div className="space-y-2">
          <Label>Edit Style</Label>
          <Select
            value={style}
            onChange={(val) => setStyle(val as EditPlanStyleKey)}
            options={styles}
          />
        </div>

        <div className="space-y-2">
          <Slider
            label="B-Roll Intensity"
            value={densityMap[density]}
            onChange={(val) => setDensity(reverseDensityMap(val))}
          />
          <p className="text-[10px] text-muted-foreground text-right italic font-medium">
            {density === "none" ? "No extra media" : 
             density === "low" ? "Minimal inserts" : 
             density === "medium" ? "Balanced storytelling" : 
             "High-energy visual flow"}
          </p>
        </div>

        <div className="space-y-2">
          <Label>Custom Instructions (Optional)</Label>
          <TextArea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. Focus on the product demo, keep the tone humorous..."
            className="min-h-36 font-mono text-[13px]"
          />
        </div>
      </div>

      <Button 
        onClick={generatePlan} 
        disabled={loading || !hasTranscript}
        className="h-14 w-full bg-primary text-primary-foreground group"
      >
        {loading ? (
          <Loader2 className="mr-2 size-5 animate-spin" />
        ) : (
          <Sparkles className="mr-2 size-5 group-hover:rotate-12 transition-transform" />
        )}
        <span className="text-base font-bold">Generate AI Edit</span>
      </Button>
      
      {!hasTranscript && (
        <p className="border border-amber-200 bg-amber-50 p-3 text-center text-xs font-medium text-amber-700 shadow-soft">
          Waiting for transcription to complete...
        </p>
      )}
      {message ? (
        <p className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}
