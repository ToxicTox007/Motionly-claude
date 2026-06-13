"use client";

import React, { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Slider } from "../ui";
import { Button } from "../../button";
import { Check, Loader2, Music, Volume2 } from "lucide-react";
import type { MusicSelectionSummary, MusicTrackSummary } from "@motionly/shared";
import { buildApiHeaders, editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";
import type { PreviewAudioState } from "../preview/VideoPreview";

interface AudioTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialSelection: MusicSelectionSummary | null;
  activePlanMusicMood?: string | null;
  onSelectionUpdated: (selection: MusicSelectionSummary) => void;
  onPreviewAudioUpdated?: (state: PreviewAudioState | null) => void;
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
  onPreviewAudioUpdated,
}: AudioTabProps) {
  const router = useRouter();
  const initialMood = useMemo(() => {
    const rawMood = initialSelection?.music_track?.raw_metadata?.mood;
    return typeof rawMood === "string" && rawMood.trim()
      ? rawMood
      : activePlanMusicMood || "uplifting ambient luxury";
  }, [activePlanMusicMood, initialSelection]);
  const [musicVolume, setMusicVolume] = useState(
    initialSelection?.music_track_id ? Math.round(initialSelection.music_volume * 100) || 25 : 25,
  );
  const [sfxIntensity, setSfxIntensity] = useState(
    initialSelection?.music_track_id ? Math.round(initialSelection.sfx_intensity * 100) || 20 : 20,
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
  const selectedTrack =
    tracks.find((track) => track.id === selectedTrackId) ?? initialSelection?.music_track ?? null;

  useEffect(() => {
    onPreviewAudioUpdated?.({
      includeMusic,
      musicVolume,
      sfxIntensity,
      mood,
      selectedTrackTitle: selectedTrack?.title ?? null,
    });
  }, [includeMusic, mood, musicVolume, onPreviewAudioUpdated, selectedTrack?.title, sfxIntensity]);

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
      const payload = await editorApiRequest<{ selection: MusicSelectionSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/music/select`,
        {
        method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "No production music track is available for that mood yet.",
          body: {
            music_track_id: nextInclude ? nextTrackId : null,
            music_volume: nextInclude ? Math.max(0.05, musicVolume / 100) : 0,
            sfx_intensity: nextInclude ? Math.max(0.05, sfxIntensity / 100) : 0,
            mood: nextMood,
            include_music: nextInclude,
            rights_confirmed: true,
            force: nextTrackId ? false : nextMood !== mood,
          },
        },
      );
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
    <div className="space-y-3 sm:space-y-4">
      <h2 className="sr-only">Audio</h2>
      <div className="pixel-panel space-y-3 p-3 sm:pixel-corners sm:p-4">
        <PanelSection title="Basic" defaultOpen>
        <div className="flex items-center gap-3 border border-primary/20 bg-primary/5 p-3">
          <div className="flex size-9 shrink-0 items-center justify-center border border-primary/20 bg-primary/10 text-primary">
            <Music className="size-5" />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-bold text-primary uppercase tracking-widest">Selected Track</p>
            <p className="truncate text-sm font-bold">
              {includeMusic
                ? selectedTrack?.title || "No track selected"
                : "No music"}
            </p>
          </div>
        </div>

        <label className="mt-3 flex min-h-11 cursor-pointer items-center justify-between gap-3 border border-border bg-muted/30 px-3 text-sm font-bold">
          Include Music
          <input
            type="checkbox"
            aria-label="Include music in export"
            checked={includeMusic}
            onChange={(event) => {
              setIncludeMusic(event.target.checked);
              updateAudio({ include: event.target.checked, trackId: selectedTrackId });
            }}
            className="size-6 accent-primary"
          />
        </label>
        </PanelSection>

        <PanelSection title="Mix" defaultOpen>
        <div className="space-y-3">
          <Slider label="Music Volume" value={musicVolume} onChange={setMusicVolume} />
          <Slider label="SFX Intensity" value={sfxIntensity} onChange={setSfxIntensity} />
        </div>
        </PanelSection>

        <PanelSection title="Mood" defaultOpen>
          <Label>Audio Mood</Label>
          <div className="flex gap-2 overflow-x-auto pb-1">
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
                  aria-pressed={selected}
                  className={cn(
                    "inline-flex min-h-10 shrink-0 items-center gap-1.5 whitespace-nowrap border px-3 py-1.5 text-xs font-bold transition disabled:cursor-not-allowed disabled:opacity-50",
                    selected
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border hover:border-primary/40 hover:bg-muted",
                  )}
                >
                  {selected ? <Check className="size-3.5" aria-hidden="true" /> : null}
                  {label}
                  {selected ? <span className="sr-only"> selected</span> : null}
                </button>
              );
            })}
          </div>
        </PanelSection>

        <PanelSection title="Advanced">
          <div className="mb-3 flex items-center justify-between">
            <Label className="mb-0">Track Alternatives</Label>
            {tracksLoading ? <Loader2 className="size-3.5 animate-spin text-primary" /> : null}
          </div>
          <div className="space-y-2">
            {!includeMusic ? (
              <p className="border border-dashed border-border p-2 text-xs text-muted-foreground">
                Turn music on to choose a track.
              </p>
            ) : tracks.length === 0 && !tracksLoading ? (
              <p className="border border-dashed border-border p-2 text-xs text-muted-foreground">
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
                  aria-pressed={selectedTrackId === track.id}
                  className={cn(
                    "flex min-h-11 w-full items-center justify-between gap-3 border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-70",
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
                    {selectedTrackId === track.id ? (
                      <span className="inline-flex items-center gap-1"><Check className="size-3" /> Selected</span>
                    ) : "Select"}
                  </span>
                </button>
              ))
            )}
          </div>
        </PanelSection>
      </div>

      <Button onClick={() => updateAudio()} disabled={loading} aria-busy={loading} className="h-12 w-full">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Volume2 className="mr-2 size-4" />}
        {loading ? "Saving, please wait" : "Save Audio Mix"}
      </Button>
      {message ? (
        <p role={message.toLowerCase().includes("could not") || message.toLowerCase().includes("unavailable") ? "alert" : "status"} aria-live="polite" className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function PanelSection({
  title,
  defaultOpen,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  return (
    <details className="group border border-border bg-surface/55" open={defaultOpen}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between px-3 text-sm font-black text-foreground">
        {title}
        <span className="text-xs text-muted-foreground transition group-open:rotate-90">›</span>
      </summary>
      <div className="border-t border-border p-3">{children}</div>
    </details>
  );
}
