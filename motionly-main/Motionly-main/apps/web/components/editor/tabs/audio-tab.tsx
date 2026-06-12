"use client";

import React, { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Slider } from "../ui";
import { Button } from "../../button";
import { Loader2, Music, Volume2 } from "lucide-react";
import type { MusicSelectionSummary, MusicTrackSummary } from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

interface AudioTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialSelection: MusicSelectionSummary | null;
  activePlanMusicMood?: string | null;
  onSelectionUpdated: (selection: MusicSelectionSummary) => void;
}

const moods = [
  "Luxury",
  "Energetic",
  "Minimal",
  "Corporate",
  "Cinematic",
  "Uplifting",
];

export function AudioTab({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialSelection,
  activePlanMusicMood,
  onSelectionUpdated,
}: AudioTabProps) {
  const router = useRouter();
  const initialMood = useMemo(() => {
    const rawMood = initialSelection?.music_track?.raw_metadata?.mood;
    return typeof rawMood === "string" && rawMood.trim()
      ? rawMood
      : activePlanMusicMood || "uplifting ambient luxury";
  }, [activePlanMusicMood, initialSelection]);
  const [musicVolume, setMusicVolume] = useState(
    initialSelection ? Math.round(initialSelection.music_volume * 100) : 25,
  );
  const [sfxIntensity, setSfxIntensity] = useState(
    initialSelection ? Math.round(initialSelection.sfx_intensity * 100) : 25,
  );
  const [includeMusic, setIncludeMusic] = useState(
    initialSelection ? Boolean(initialSelection.music_track_id) : true,
  );
  const [mood, setMood] = useState(initialMood);
  const [tracks, setTracks] = useState<MusicTrackSummary[]>([]);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(
    initialSelection?.music_track_id ?? null,
  );
  const [tracksLoading, setTracksLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let canceled = false;
    async function loadTracks() {
      if (!includeMusic) {
        setTracks([]);
        return;
      }
      setTracksLoading(true);
      try {
        const params = new URLSearchParams({ mood });
        const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/music/tracks?${params}`, {
          headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error?.message ?? "Music alternatives are unavailable.");
        if (!canceled) setTracks(payload.tracks ?? []);
      } catch (error) {
        console.error(error);
        if (!canceled) setMessage((error as Error).message);
      } finally {
        if (!canceled) setTracksLoading(false);
      }
    }
    loadTracks();
    return () => {
      canceled = true;
    };
  }, [apiAccessToken, includeMusic, mood, projectId, userId, workspaceId]);

  async function updateAudio(next?: { mood?: string; trackId?: string | null; include?: boolean }) {
    const nextMood = next?.mood ?? mood;
    const nextTrackId = next?.trackId ?? selectedTrackId;
    const nextInclude = next?.include ?? includeMusic;
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
          music_track_id: nextInclude ? nextTrackId : null,
          music_volume: musicVolume / 100,
          sfx_intensity: sfxIntensity / 100,
          mood: nextMood,
          include_music: nextInclude,
          rights_confirmed: true,
          force: nextTrackId ? false : nextMood !== mood,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Failed to update audio");
      setMood(nextMood);
      setSelectedTrackId(payload.selection?.music_track_id ?? null);
      setIncludeMusic(Boolean(payload.selection?.music_track_id));
      onSelectionUpdated(payload.selection);
      setMessage(
        payload.selection?.music_track?.title
          ? `Selected ${payload.selection.music_track.title}.`
          : "Music disabled for this edit.",
      );
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
          <div className="min-w-0">
            <p className="text-xs font-bold text-primary uppercase tracking-widest">Selected Track</p>
            <p className="truncate text-sm font-bold">
              {includeMusic
                ? initialSelection?.music_track?.title || "AI Recommended Track"
                : "No music"}
            </p>
          </div>
        </div>

        <label className="flex items-center justify-between gap-3 border border-border bg-muted/30 p-3 text-xs font-bold uppercase tracking-widest">
          Include Music
          <input
            type="checkbox"
            checked={includeMusic}
            onChange={(event) => {
              setIncludeMusic(event.target.checked);
              updateAudio({ include: event.target.checked, trackId: selectedTrackId });
            }}
            className="size-4 accent-primary"
          />
        </label>

        <div className="space-y-6">
          <Slider label="Music Volume" value={musicVolume} onChange={setMusicVolume} />
          <Slider label="SFX Intensity" value={sfxIntensity} onChange={setSfxIntensity} />
        </div>

        <div className="border-t border-border pt-4">
          <Label>Audio Mood</Label>
          <div className="flex flex-wrap gap-2">
            {moods.map((label) => {
              const value = label.toLowerCase();
              const selected = mood.toLowerCase().includes(value);
              return (
                <button
                  key={label}
                  onClick={() => {
                    const nextMood = `${value} music`;
                    setMood(nextMood);
                    setSelectedTrackId(null);
                    updateAudio({ mood: nextMood, trackId: null, include: true });
                  }}
                  disabled={loading}
                  className={cn(
                    "border px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider transition disabled:cursor-not-allowed disabled:opacity-50",
                    selected
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border hover:border-primary/40 hover:bg-muted",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="border-t border-border pt-4">
          <div className="mb-3 flex items-center justify-between">
            <Label className="mb-0">Track Alternatives</Label>
            {tracksLoading ? <Loader2 className="size-3.5 animate-spin text-primary" /> : null}
          </div>
          <div className="space-y-2">
            {!includeMusic ? (
              <p className="border border-dashed border-border p-3 text-xs text-muted-foreground">
                Turn music on to choose a track.
              </p>
            ) : tracks.length === 0 && !tracksLoading ? (
              <p className="border border-dashed border-border p-3 text-xs text-muted-foreground">
                No alternatives available for this mood.
              </p>
            ) : (
              tracks.map((track) => (
                <button
                  key={track.id}
                  onClick={() => {
                    setSelectedTrackId(track.id);
                    updateAudio({ trackId: track.id, include: true });
                  }}
                  disabled={loading || selectedTrackId === track.id}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-70",
                    selectedTrackId === track.id
                      ? "border-primary bg-primary/10"
                      : "border-border hover:border-primary/40 hover:bg-muted",
                  )}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-bold">{track.title}</span>
                    <span className="block text-[10px] text-muted-foreground">
                      {track.mood_tags.join(", ") || track.license}
                    </span>
                  </span>
                  <span className="text-[10px] font-bold uppercase tracking-widest">
                    {selectedTrackId === track.id ? "Selected" : "Select"}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      </div>

      <Button onClick={() => updateAudio()} disabled={loading} className="h-12 w-full">
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
