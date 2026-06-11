"use client";

import React, { useState } from "react";
import { Card, Label, Select, TextArea, Slider } from "./ui";
import { Button } from "../button";
import { Sparkles, Loader2, Wand2 } from "lucide-react";
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
    } catch (error) {
      console.error(error);
      alert("Failed to generate AI plan. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8 animate-in fade-in slide-in-from-right-4 duration-500">
      <div className="space-y-2">
        <h3 className="text-xl font-bold tracking-tight">AI Creative Direction</h3>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Our AI will analyze your video and plan the perfect edit based on your chosen style.
        </p>
      </div>

      <div className="space-y-6">
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
          />
        </div>
      </div>

      <Button 
        onClick={generatePlan} 
        disabled={loading || !hasTranscript}
        className="w-full h-14 rounded-2xl bg-primary text-primary-foreground shadow-xl hover:scale-[1.02] active:scale-[0.98] transition-all duration-200 group"
      >
        {loading ? (
          <Loader2 className="mr-2 size-5 animate-spin" />
        ) : (
          <Sparkles className="mr-2 size-5 group-hover:rotate-12 transition-transform" />
        )}
        <span className="text-base font-bold">Generate AI Edit</span>
      </Button>
      
      {!hasTranscript && (
        <p className="text-xs text-center text-amber-600 font-medium bg-amber-50 p-3 rounded-lg border border-amber-100">
          Waiting for transcription to complete...
        </p>
      )}
    </div>
  );
}
