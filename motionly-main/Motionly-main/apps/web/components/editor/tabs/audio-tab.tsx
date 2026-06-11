"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Slider } from "../ui";
import { Button } from "../../button";
import { Music, Loader2, Volume2 } from "lucide-react";
import type { MusicSelectionSummary } from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";

interface AudioTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialSelection: MusicSelectionSummary | null;
  activePlanMusicMood?: string | null;
}

export function AudioTab({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialSelection,
  activePlanMusicMood,
}: AudioTabProps) {
  const router = useRouter();
  const [musicVolume, setMusicVolume] = useState(initialSelection ? initialSelection.music_volume * 100 : 25);
  const [sfxIntensity, setSfxIntensity] = useState(initialSelection ? initialSelection.sfx_intensity * 100 : 25);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function updateAudio() {
    setLoading(true);
    setMessage(null);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/music/select`, {
        method: "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          music_volume: musicVolume / 100,
          sfx_intensity: sfxIntensity / 100,
          mood: activePlanMusicMood || "uplifting ambient luxury",
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Failed to update audio");
      setMessage(payload.selection?.music_track?.title ? `Selected ${payload.selection.music_track.title}.` : "Audio mix updated.");
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
      <div className="pixel-panel pixel-corners space-y-8 p-4">
        <div className="flex items-center gap-4 border border-primary/20 bg-primary/5 p-4">
           <div className="flex size-10 items-center justify-center border border-primary/20 bg-primary/10 text-primary">
              <Music className="size-5" />
           </div>
           <div>
              <p className="text-xs font-bold text-primary uppercase tracking-widest">Selected Track</p>
              <p className="text-sm font-bold truncate">
                {initialSelection?.music_track?.title || "AI Recommended Track"}
              </p>
           </div>
        </div>

        <div className="space-y-6">
          <Slider
            label="Music Volume"
            value={musicVolume}
            onChange={setMusicVolume}
          />

          <Slider
            label="SFX Intensity"
            value={sfxIntensity}
            onChange={setSfxIntensity}
          />
        </div>

        <div className="border-t border-border pt-4">
          <Label>Audio Mood</Label>
          <div className="flex flex-wrap gap-2">
            {["Luxury", "Energetic", "Minimal", "Corporate"].map(mood => (
              <button 
                key={mood}
                className="border border-border px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider transition hover:border-primary/40 hover:bg-muted"
              >
                {mood}
              </button>
            ))}
          </div>
        </div>
      </div>

      <Button onClick={updateAudio} disabled={loading} className="h-12 w-full">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Volume2 className="mr-2 size-4" />}
        Save Audio Mix
      </Button>
      {message ? (
        <p className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}
