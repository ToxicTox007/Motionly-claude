"use client";

import React, { useState } from "react";
import { Label, Select } from "../ui";
import { Button } from "../../button";
import { Type, Loader2, RefreshCw } from "lucide-react";
import type { CaptionTrackSummary, CaptionPresetKey } from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";

interface CaptionsTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialTracks: CaptionTrackSummary[];
}

const presets: { value: CaptionPresetKey; label: string }[] = [
  { value: "luxury_minimal", label: "Minimal" },
  { value: "bold_influencer", label: "Bold & Loud" },
  { value: "vogue_white", label: "Editorial" },
  { value: "gold_premium", label: "Gold Accent" },
  { value: "founder_clean", label: "Clean Blue" },
];

export function CaptionsTab({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialTracks,
}: CaptionsTabProps) {
  const [preset, setPreset] = useState<CaptionPresetKey>(initialTracks[0]?.preset ?? "luxury_minimal");
  const [loading, setLoading] = useState(false);

  async function updateCaptions() {
    setLoading(true);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/captions/tracks`, {
        method: "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          preset,
        }),
      });
      if (!response.ok) throw new Error("Failed to update captions");
      alert("Caption style updated!");
    } catch (error) {
      console.error(error);
      alert("Failed to update caption style.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      <div className="space-y-6">
        <div className="space-y-2">
          <Label>Caption Preset</Label>
          <Select
            value={preset}
            onChange={(val) => setPreset(val as CaptionPresetKey)}
            options={presets}
          />
        </div>

        <div className="pt-4 border-t border-border">
          <div className="flex items-center justify-between mb-4">
             <Label className="mb-0">Transcription</Label>
             <button className="text-[10px] font-bold text-primary hover:underline">Edit Text</button>
          </div>
          <div className="space-y-2 max-h-48 overflow-y-auto pr-2">
             {initialTracks[0]?.segments.slice(0, 5).map((segment, i) => (
               <div key={i} className="p-3 rounded-lg bg-muted/50 border border-border text-xs leading-relaxed italic">
                 "{segment.text}"
               </div>
             ))}
             {initialTracks[0]?.segments.length > 5 && (
               <p className="text-[10px] text-center text-muted-foreground">...</p>
             )}
          </div>
        </div>
      </div>

      <Button onClick={updateCaptions} disabled={loading} className="w-full h-12 rounded-xl">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
        Update Captions
      </Button>
    </div>
  );
}
