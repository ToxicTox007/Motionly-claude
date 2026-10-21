"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Label, Slider, PanelSection } from "../ui";
import { Button } from "../../button";
import { Check, Loader2, Music, Volume2 } from "lucide-react";
import type {
  MusicSelectionSummary,
  MusicTrackSummary,
} from "@motionly/shared";
import {
  apiFetch,
  editorApiRequest,
  resolveQueuedLayer,
} from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";
import type { PreviewAudioState } from "../preview/VideoPreview";

interface AudioTabProps {
  projectId: string;
  workspaceId: string;
  initialSelection: MusicSelectionSummary | null;
  activePlanMusicMood?: string | null;
  onSelectionUpdated: (selection: MusicSelectionSummary) => void;
  onPreviewAudioUpdated?: (state: PreviewAudioState | null) => void;
}

type StemResource = {
  id: string;
  status: string;
  revision: number;
  result: {
    active?: "vocals" | "no_vocals";
    stem_provider?: string | null;
    vocals?: { bucket?: string; path?: string } | null;
    no_vocals?: { bucket?: string; path?: string } | null;
  };
};

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
  const [previewTrackId, setPreviewTrackId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [stem, setStem] = useState<StemResource | null>(null);
  const [stemLoading, setStemLoading] = useState(false);
  const [stemBusy, setStemBusy] = useState(false);
  const selectedTrack =
    tracks.find((track) => track.id === selectedTrackId) ??
    initialSelection?.music_track ??
    null;

  async function previewTrack(trackId: string) {
    setMessage(null);
    setPreviewTrackId(trackId);
    try {
      const response = await apiFetch(
        `${env.apiBaseUrl}/projects/${projectId}/music/tracks/${trackId}/preview`,
        {},
        { workspaceId },
      );
      const payload = await response.json();
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Preview is unavailable for this track.",
        );
      setPreviewUrl(payload.preview_url ?? null);
    } catch (error) {
      console.error(error);
      setPreviewUrl(null);
      setPreviewTrackId(null);
      setMessage((error as Error).message);
    }
  }

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
        const response = await apiFetch(
          `${env.apiBaseUrl}/projects/${projectId}/music/tracks?${params}`,
          {},
          { workspaceId },
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
  }, [includeMusic, mood, projectId, workspaceId]);

  const loadStem = useCallback(async () => {
    setStemLoading(true);
    try {
      const data = await editorApiRequest<{ resources: StemResource[] }>(
        `${env.apiBaseUrl}/projects/${projectId}/phase5/resources?kind=stem_separation`,
        { workspaceId },
      );
      // The most recent READY separation is the one renders resolve from.
      const ready =
        data.resources?.find((item) => item.status === "ready") ?? null;
      setStem(ready);
    } catch (error) {
      console.error(error);
    } finally {
      setStemLoading(false);
    }
  }, [projectId, workspaceId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadStem();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadStem]);

  async function selectStem(active: "vocals" | "no_vocals") {
    if (!stem) return;
    setStemBusy(true);
    setMessage(null);
    try {
      const updated = await editorApiRequest<StemResource>(
        `${env.apiBaseUrl}/projects/${projectId}/phase5/stems/${stem.id}`,
        {
          method: "PATCH",
          workspaceId,
          friendlyError:
            "That stem could not be selected. Wait for separation to finish and try again.",
          body: { active, expected_revision: stem.revision },
        },
      );
      setStem(updated);
      setMessage(
        active === "no_vocals"
          ? "Using the instrumental (no vocals) stem for renders."
          : "Using the vocals stem for renders.",
      );
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setStemBusy(false);
    }
  }

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
        options: { workspaceId },
        load: () =>
          editorApiRequest<MusicSelectionSummary | null>(
            `${env.apiBaseUrl}/projects/${projectId}/music/selection`,
            { workspaceId },
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
    <div className="space-y-4">
      <h2 className="sr-only">Audio</h2>
      <div className="rounded-neu-lg bg-surface p-5 space-y-4 shadow-neu-raised sm:p-6">
        <PanelSection title="Basic" defaultOpen>
          <div className="flex items-center gap-3.5 rounded-neu-md bg-surface p-3.5 shadow-neu-raised-sm">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-neu bg-surface text-primary shadow-neu-inset-sm">
              <Music className="size-5" strokeWidth={1.75} />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-primary">
                Selected Track
              </p>
              <p className="truncate text-sm font-semibold text-foreground mt-0.5">
                {includeMusic
                  ? selectedTrack?.title || "No track selected"
                  : "No music"}
              </p>
            </div>
          </div>

          <label className="mt-3 flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-neu-md bg-surface px-4 py-3 text-sm font-semibold text-foreground shadow-neu-raised-sm select-none">
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
              className="neu-check cursor-pointer"
            />
          </label>
          <label className="mt-3 flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-neu-md bg-surface px-4 py-3 text-sm font-semibold text-foreground shadow-neu-raised-sm select-none">
            <span>Use this licensed catalog track for this export</span>
            <input
              type="checkbox"
              aria-label="Acknowledge licensed music terms"
              checked={rightsDecision}
              onChange={(event) => setRightsDecision(event.target.checked)}
              disabled={!includeMusic || loading}
              className="neu-check cursor-pointer disabled:cursor-not-allowed"
            />
          </label>
          <p className="text-xs font-medium text-muted-foreground leading-relaxed">
            Music is used only after Motionly verifies the catalog grant, scope,
            and expiry on the server.
          </p>
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
                    "inline-flex min-h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-4 py-1 text-xs font-semibold transition-shadow duration-200 disabled:cursor-not-allowed disabled:opacity-50",
                    selected
                      ? "bg-primary text-primary-foreground shadow-neu-glow"
                      : "bg-surface text-muted-foreground shadow-neu-raised-sm hover:text-foreground hover:shadow-neu-raised active:shadow-neu-pressed",
                  )}
                >
                  {selected ? (
                    <Check
                      className="size-3.5"
                      aria-hidden="true"
                      strokeWidth={1.75}
                    />
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
              <Loader2
                className="size-3.5 animate-spin text-primary"
                strokeWidth={1.75}
              />
            ) : null}
          </div>
          <div className="space-y-2">
            {!includeMusic ? (
              <p className="rounded-neu-md bg-surface p-3 text-xs font-medium text-muted-foreground text-center shadow-neu-inset-sm">
                Turn music on to choose a track.
              </p>
            ) : tracks.length === 0 && !tracksLoading ? (
              <p className="rounded-neu-md bg-surface p-3 text-xs font-medium text-muted-foreground text-center shadow-neu-inset-sm">
                No alternatives available for this mood.
              </p>
            ) : (
              tracks.map((track) => (
                <div key={track.id} className="space-y-2">
                  <button
                    onClick={() => {
                      setSelectedTrackId(track.id);
                      updateAudio({ trackId: track.id, include: true });
                    }}
                    disabled={loading || selectedTrackId === track.id}
                    aria-pressed={selectedTrackId === track.id}
                    className={cn(
                      "flex min-h-11 w-full items-center justify-between gap-3 rounded-neu-md p-3.5 text-left transition-shadow duration-200 disabled:cursor-not-allowed",
                      selectedTrackId === track.id
                        ? "bg-primary text-primary-foreground shadow-neu-glow"
                        : "bg-surface text-foreground shadow-neu-raised-sm hover:shadow-neu-raised active:shadow-neu-pressed",
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-xs font-semibold">
                        {track.title}
                      </span>
                      <span
                        className={cn(
                          "block text-xs mt-0.5 font-medium",
                          selectedTrackId === track.id
                            ? "text-primary-foreground"
                            : "text-muted-foreground",
                        )}
                      >
                        {track.mood_tags.join(", ") || track.license}
                      </span>
                    </span>
                    <span className="text-xs font-semibold">
                      {selectedTrackId === track.id ? (
                        <span className="inline-flex items-center gap-1">
                          <Check className="size-3" strokeWidth={1.75} />{" "}
                          Selected
                        </span>
                      ) : (
                        "Select"
                      )}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => previewTrack(track.id)}
                    disabled={loading}
                    aria-label={`Preview ${track.title}`}
                    className="inline-flex min-h-9 items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-xs font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Volume2 className="size-3" strokeWidth={1.75} /> Preview
                  </button>
                  {previewTrackId === track.id && previewUrl ? (
                    <audio
                      controls
                      autoPlay
                      src={previewUrl}
                      aria-label={`Preview player for ${track.title}`}
                      className="w-full"
                    >
                      <track kind="captions" />
                    </audio>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </PanelSection>

        <PanelSection title="Vocal Stems">
          <div className="mb-2 flex items-center justify-between gap-2">
            <Label className="mb-0">Source audio stem</Label>
            {stem?.result?.stem_provider === "fixture" ? (
              <span
                className="inline-flex items-center rounded-full bg-surface px-3 py-0.5 text-xs font-semibold text-foreground shadow-neu-raised-sm"
                aria-label="Test stems from the fixture separator"
              >
                Test stems
              </span>
            ) : null}
            {stemLoading ? (
              <Loader2
                className="size-3.5 animate-spin text-primary"
                strokeWidth={1.75}
              />
            ) : null}
          </div>
          {!stem ? (
            <p className="rounded-neu-md bg-surface p-3 text-xs font-medium text-muted-foreground text-center shadow-neu-inset-sm">
              Separate vocals and music in Intelligence &amp; Scale to choose a
              stem.
            </p>
          ) : (
            <div
              role="radiogroup"
              aria-label="Source audio stem used for renders"
              className="space-y-2"
            >
              {(
                [
                  ["vocals", "Keep vocals (full mix)"],
                  ["no_vocals", "Instrumental only (no vocals)"],
                ] as const
              ).map(([value, label]) => {
                const active = (stem.result.active ?? "vocals") === value;
                return (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    disabled={stemBusy || active}
                    onClick={() => void selectStem(value)}
                    className={cn(
                      "flex min-h-11 w-full items-center justify-between gap-3 rounded-neu-md p-3.5 text-left transition-shadow duration-200 disabled:cursor-not-allowed",
                      active
                        ? "bg-primary text-primary-foreground shadow-neu-glow"
                        : "bg-surface text-foreground shadow-neu-raised-sm hover:shadow-neu-raised active:shadow-neu-pressed",
                    )}
                  >
                    <span className="text-xs font-semibold">{label}</span>
                    <span className="text-xs font-semibold">
                      {active ? (
                        <span className="inline-flex items-center gap-1">
                          <Check className="size-3" strokeWidth={1.75} /> In use
                        </span>
                      ) : (
                        "Use"
                      )}
                    </span>
                  </button>
                );
              })}
              <p className="text-xs font-medium text-muted-foreground">
                The chosen stem is the audio renders use for this project.
              </p>
            </div>
          )}
        </PanelSection>
      </div>

      <button
        onClick={() => updateAudio()}
        disabled={loading}
        aria-busy={loading}
        className="w-full inline-flex items-center justify-center gap-2 h-11 rounded-neu bg-primary text-primary-foreground text-sm font-semibold shadow-neu-glow transition-shadow duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? (
          <Loader2
            className="size-4 animate-spin text-primary-foreground"
            strokeWidth={1.75}
          />
        ) : (
          <Volume2 className="size-4" strokeWidth={1.75} />
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
          className="rounded-neu-md bg-surface p-3.5 text-xs font-semibold text-foreground shadow-neu-inset-sm"
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}
