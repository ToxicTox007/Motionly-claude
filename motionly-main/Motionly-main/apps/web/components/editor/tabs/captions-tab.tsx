"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Select } from "../ui";
import { Button } from "../../button";
import { Loader2, RefreshCw } from "lucide-react";
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
  const router = useRouter();
  const [tracks, setTracks] = useState(initialTracks);
  const activeTrack = tracks[0] ?? null;
  const [preset, setPreset] = useState<CaptionPresetKey>(activeTrack?.preset ?? "luxury_minimal");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function updateCaptions() {
    setLoading(true);
    setMessage(null);
    try {
      const endpoint = activeTrack
        ? `${env.apiBaseUrl}/projects/${projectId}/captions/${activeTrack.id}`
        : `${env.apiBaseUrl}/projects/${projectId}/captions/generate`;
      const response = await fetch(endpoint, {
        method: activeTrack ? "PATCH" : "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          preset,
          hide_filler_words: activeTrack?.hide_filler_words ?? false,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error?.message ?? "Failed to update captions");
      }
      setTracks((current) => [
        payload.track,
        ...current.filter((track) => track.id !== payload.track.id),
      ]);
      setMessage("Caption style updated.");
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
          <Label>Caption Preset</Label>
          <Select
            value={preset}
            onChange={(val) => setPreset(val as CaptionPresetKey)}
            options={presets}
          />
        </div>

        <div className="border-t border-border pt-4">
          <div className="flex items-center justify-between mb-4">
             <Label className="mb-0">Transcription</Label>
             <span className="text-[10px] font-bold text-muted-foreground">Style preview</span>
          </div>
          <div className="space-y-2 max-h-48 overflow-y-auto pr-2">
             {activeTrack?.segments.slice(0, 5).map((segment, i) => (
               <div key={i} className="border border-border bg-muted/50 p-3 text-xs italic leading-relaxed">
                 &quot;{segment.text}&quot;
               </div>
             ))}
             {activeTrack?.segments.length ? null : (
               <p className="text-xs text-muted-foreground">No caption track yet.</p>
             )}
             {(activeTrack?.segments.length ?? 0) > 5 && (
               <p className="text-[10px] text-center text-muted-foreground">...</p>
             )}
          </div>
        </div>
      </div>

      <Button onClick={updateCaptions} disabled={loading} className="h-12 w-full">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
        {activeTrack ? "Update Captions" : "Generate Captions"}
      </Button>
      {message ? (
        <p className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}
