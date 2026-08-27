"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Label, Slider, PanelSection } from "../ui";
import { Button } from "../../button";
import { Check, Loader2, Music, Volume2 } from "lucide-react";
import type {
  MusicSelectionSummary,
  MusicTrackSummary,
} from "@motionly/shared";
import {
  buildApiHeaders,
  editorApiRequest,
  resolveQueuedLayer,
} from "@/lib/client-api";
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
  const initialMood = useMemo(() => {
    const rawMood = initialSelection?.music_track?.raw_metadata?.mood;
    return typeof rawMood === "string" && rawMood.trim()
      ? rawMood
      : activePlanMusicMood || "uplifting ambient luxury";
  }, [activePlanMusicMood, initialSelection]);
  const [musicVolume, setMusicVolume] = useState(
    initialSelection?.music_track_id
      ? Math.round(initialSelection.music_volume * 100) || 25
      : 25,
  );
  const [sfxIntensity, setSfxIntensity] = useState(
    initialSelection?.music_track_id
      ? Math.round(initialSelection.sfx_intensity * 100) || 20
      : 20,
  );
  const [includeMusic, setIncludeMusic] = useState(
    initialSelection ? Boolean(initialSelection.music_track_id) : true,
  );
  const [rightsDecision, setRightsDecision] = useState(
    Boolean(initialSelection?.rights_confirmed),
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
    tracks.find((track) => track.id === selectedTrackId) ??
    initialSelection?.music_track ??
    null;

  useEffect(() => {
    onPreviewAudioUpdated?.({
      includeMusic,
      musicVolume,
      sfxIntensity,
      mood,
      selectedTrackTitle: selectedTrack?.title ?? null,
    });
  }, [
    includeMusic,
    mood,
    musicVolume,
    onPreviewAudioUpdated,
    selectedTrack?.title,
    sfxIntensity,
  ]);

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
        const response = await fetch(
          `${env.apiBaseUrl}/projects/${projectId}/music/tracks?${params}`,
          {
            headers: await buildApiHeaders({
              workspaceId,
              userId,
              accessToken: apiAccessToken,
            }),
          },
        );
        const payload = await response.json();
        if (!response.ok)
          throw new Error(
            payload.error?.message ?? "Music alternatives are unavailable.",
          );
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

  async function updateAudio(next?: {
    mood?: string;
    trackId?: string | null;
    include?: boolean;
  }) {
    const nextMood = next?.mood ?? mood;
    const nextTrackId = next?.trackId ?? selectedTrackId;
    const nextInclude = next?.include ?? includeMusic;
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{
        selection?: MusicSelectionSummary | null;
        status: "ready" | "queued";
        status_url?: string | null;
      }>(`${env.apiBaseUrl}/projects/${projectId}/music/select`, {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError:
          "No production music track is available for that mood yet.",
        body: {
          music_track_id: nextInclude ? nextTrackId : null,
          music_volume: nextInclude ? Math.max(0.05, musicVolume / 100) : 0,
          sfx_intensity: nextInclude ? Math.max(0.05, sfxIntensity / 100) : 0,
          mood: nextMood,
          include_music: nextInclude,
          rights_decision: nextInclude ? rightsDecision : false,
          force: nextTrackId ? false : nextMood !== mood,
        },
      });
      const selection = await resolveQueuedLayer({
        submission: payload,
        immediate: payload.selection,
        apiBaseUrl: env.apiBaseUrl,
        options: { workspaceId, userId, accessToken: apiAccessToken },
        load: () =>
          editorApiRequest<MusicSelectionSummary | null>(
            `${env.apiBaseUrl}/projects/${projectId}/music/selection`,
            { workspaceId, userId, accessToken: apiAccessToken },
          ),
        resourceLabel: "Music selection",
        onProgress: (progress) => setMessage(`Selecting music: ${progress}%`),
      });
      setMood(nextMood);
      setSelectedTrackId(selection.music_track_id ?? null);
      setIncludeMusic(Boolean(selection.music_track_id));
      onSelectionUpdated(selection);
      setMessage(
        selection.music_track?.title
          ? `Selected ${selection.music_track.title}.`
          : "Music disabled for this edit.",
      );
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
      <div className="card rounded-lg border-2 border-foreground p-4 sm:p-5 space-y-4 shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] bg-surface">
        <PanelSection title="Basic" defaultOpen>
          <div className="flex items-center gap-3.5 border-2 border-foreground bg-primary/10 rounded p-3.5 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
            <div className="flex size-9 shrink-0 items-center justify-center rounded border-2 border-foreground bg-primary/20 text-foreground">
              <Music className="size-5 text-foreground" strokeWidth={2} />
            </div>
            <div className="min-w-0">
              <p className="text-[10px] font-black text-primary uppercase tracking-widest font-mono">
                Selected Track
              </p>
              <p className="truncate text-sm font-bold mt-0.5">
                {includeMusic
                  ? selectedTrack?.title || "No track selected"
                  : "No music"}
              </p>
            </div>
          </div>

          <label className="mt-3 flex min-h-[44px] cursor-pointer items-center justify-between gap-3 rounded border-2 border-foreground bg-muted/20 px-3.5 text-xs font-bold select-none">
            Include Music
            <input
              type="checkbox"
              aria-label="Include music in export"
              checked={includeMusic}
              onChange={(event) => {
                setIncludeMusic(event.target.checked);
                updateAudio({
                  include: event.target.checked,
                  trackId: selectedTrackId,
                });
              }}
              className="size-4.5 rounded border-2 border-foreground text-primary focus:ring-primary/30 accent-primary cursor-pointer"
            />
          </label>
          <label className="mt-3 flex min-h-[44px] cursor-pointer items-center justify-between gap-3 rounded border-2 border-foreground bg-muted/20 px-3.5 text-xs font-bold select-none">
            <span>Use this licensed catalog track for this export</span>
            <input
              type="checkbox"
              aria-label="Acknowledge licensed music terms"
              checked={rightsDecision}
              onChange={(event) => setRightsDecision(event.target.checked)}
              disabled={!includeMusic || loading}
              className="size-4.5 rounded border-2 border-foreground text-primary focus:ring-primary/30 accent-primary cursor-pointer disabled:cursor-not-allowed"
            />
          </label>
          <p className="text-xs text-muted-foreground">Music is used only after Motionly verifies the catalog grant, scope, and expiry on the server.</p>
        </PanelSection>

        <PanelSection title="Mix" defaultOpen>
          <div className="space-y-3">
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
        </PanelSection>

        <PanelSection title="Mood" defaultOpen>
          <Label>Audio Mood</Label>
          <div className="flex gap-2 overflow-x-auto pb-1.5 scrollbar-none pt-1">
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
                    updateAudio({
                      mood: nextMood,
                      trackId: null,
                      include: true,
                    });
                  }}
                  disabled={loading}
                  aria-pressed={selected}
                  className={cn(
                    "inline-flex min-h-9 shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-1 rounded text-xs font-bold font-mono transition-all duration-[100ms] border-2 border-foreground disabled:cursor-not-allowed disabled:opacity-50",
                    selected
                      ? "bg-primary text-primary-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
                      : "bg-surface text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none",
                  )}
                >
                  {selected ? (
                    <Check className="size-3.5" aria-hidden="true" strokeWidth={2} />
                  ) : null}
                  {label}
                </button>
              );
            })}
          </div>
        </PanelSection>

        <PanelSection title="Advanced">
          <div className="mb-3 flex items-center justify-between">
            <Label className="mb-0">Track Alternatives</Label>
            {tracksLoading ? (
              <Loader2 className="size-3.5 animate-spin text-primary" strokeWidth={2} />
            ) : null}
          </div>
          <div className="space-y-2">
            {!includeMusic ? (
              <p className="border-2 border-dashed border-foreground rounded p-3 text-xs text-muted-foreground text-center font-mono">
                Turn music on to choose a track.
              </p>
            ) : tracks.length === 0 && !tracksLoading ? (
              <p className="border-2 border-dashed border-foreground rounded p-3 text-xs text-muted-foreground text-center font-mono">
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
                    "flex min-h-11 w-full items-center justify-between gap-3 border-2 border-foreground p-3.5 text-left transition-all duration-[100ms] rounded disabled:cursor-not-allowed disabled:opacity-70",
                    selectedTrackId === track.id
                      ? "bg-primary text-primary-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
                      : "bg-surface text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none",
                  )}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-bold">
                      {track.title}
                    </span>
                    <span className={cn("block text-[10px] mt-0.5 font-mono", selectedTrackId === track.id ? "text-primary-foreground/80" : "text-muted-foreground")}>
                      {track.mood_tags.join(", ") || track.license}
                    </span>
                  </span>
                  <span className="text-[10px] font-bold uppercase tracking-widest font-mono">
                    {selectedTrackId === track.id ? (
                      <span className="inline-flex items-center gap-1">
                        <Check className="size-3" strokeWidth={2} /> Selected
                      </span>
                    ) : (
                      "Select"
                    )}
                  </span>
                </button>
              ))
            )}
          </div>
        </PanelSection>
      </div>

      <button
        onClick={() => updateAudio()}
        disabled={loading}
        aria-busy={loading}
        className="w-full inline-flex items-center justify-center gap-2 h-11 rounded border-2 border-foreground bg-primary text-primary-foreground text-xs font-black hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none transition-all duration-[100ms] focus:outline-none"
      >
        {loading ? (
          <Loader2 className="size-4 animate-spin text-primary-foreground" strokeWidth={2} />
        ) : (
          <Volume2 className="size-4" strokeWidth={2} />
        )}
        <span>{loading ? "Saving, please wait" : "Save Audio Mix"}</span>
      </button>
      {message ? (
        <p
          role={
            message.toLowerCase().includes("could not") ||
            message.toLowerCase().includes("unavailable")
              ? "alert"
              : "status"
          }
          aria-live="polite"
          className="rounded border-2 border-foreground bg-muted p-3.5 text-xs font-bold text-foreground font-mono shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}
