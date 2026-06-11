"use client";

import React, { useState } from "react";
import { Label, Slider } from "../ui";
import { Button } from "../../button";
import { Music, Loader2, Volume2, Zap } from "lucide-react";
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
  const [musicVolume, setMusicVolume] = useState(initialSelection ? initialSelection.music_volume * 100 : 25);
  const [sfxIntensity, setSfxIntensity] = useState(initialSelection ? initialSelection.sfx_intensity * 100 : 25);
  const [loading, setLoading] = useState(false);

  async function updateAudio() {
    setLoading(true);
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
      if (!response.ok) throw new Error("Failed to update audio");
      alert("Audio mix updated!");
    } catch (error) {
      console.error(error);
      alert("Failed to update audio.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      <div className="space-y-8">
        <div className="p-4 rounded-xl bg-primary/5 border border-primary/10 flex items-center gap-4">
           <div className="size-10 rounded-lg bg-primary/10 flex items-center justify-center text-primary">
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

        <div className="pt-4 border-t border-border">
          <Label>Audio Mood</Label>
          <div className="flex flex-wrap gap-2">
            {["Luxury", "Energetic", "Minimal", "Corporate"].map(mood => (
              <button 
                key={mood}
                className="px-3 py-1.5 rounded-lg border border-border text-[10px] font-bold uppercase tracking-wider hover:bg-muted transition"
              >
                {mood}
              </button>
            ))}
          </div>
        </div>
      </div>

      <Button onClick={updateAudio} disabled={loading} className="w-full h-12 rounded-xl">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Volume2 className="mr-2 size-4" />}
        Save Audio Mix
      </Button>
    </div>
  );
}
