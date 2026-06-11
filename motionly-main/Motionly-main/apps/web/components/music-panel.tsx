"use client";

import type { MusicSelectionSummary, MusicTrackSummary } from "@motionly/shared";
import {
  Check,
  Disc3,
  Loader2,
  Music2,
  Play,
  RefreshCw,
  Volume2,
  WandSparkles,
  Zap,
} from "lucide-react";
import type { ReactNode } from "react";
import { useRef, useState } from "react";
import { Button } from "@/components/button";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

type MusicPanelProps = {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialSelection: MusicSelectionSummary | null;
  activePlanMusicMood?: string | null;
  hasActivePlan: boolean;
};

const DEFAULT_MUSIC_VOLUME = 0.25;
const DEFAULT_SFX_INTENSITY = 0.25;
const moods = ["uplifting ambient luxury", "clean confident", "subtle conversational"];

export function MusicPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialSelection,
  activePlanMusicMood,
  hasActivePlan,
}: MusicPanelProps) {
  const [selection, setSelection] = useState(initialSelection);
  const [mood, setMood] = useState(activePlanMusicMood ?? moods[0]);
  const [musicVolume, setMusicVolume] = useState(
    initialSelection?.music_volume ?? DEFAULT_MUSIC_VOLUME,
  );
  const [sfxIntensity, setSfxIntensity] = useState(
    initialSelection?.sfx_intensity ?? DEFAULT_SFX_INTENSITY,
  );
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [trackOptions, setTrackOptions] = useState<MusicTrackSummary[]>([]);
  const previewRef = useRef<HTMLAudioElement | null>(null);

  async function loadTracks(nextMood = mood) {
    setPhase("loading");
    setMessage("Loading licensed music options...");
    try {
      const params = new URLSearchParams({ mood: nextMood });
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/music/tracks?${params}`, {
        headers: await requestHeaders(workspaceId, userId, apiAccessToken, false),
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(payload.error?.message ?? "Music options could not be loaded.");
      setTrackOptions(payload.tracks ?? []);
      setPickerOpen(true);
      setPhase("ready");
      setMessage(null);
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  async function selectMusic({
    trackId,
    force = false,
    fromPlan = false,
    includeMusic = true,
  }: {
    trackId?: string;
    force?: boolean;
    fromPlan?: boolean;
    includeMusic?: boolean;
  }) {
    setPhase("loading");
    setMessage(
      includeMusic
        ? force
          ? "Finding a different sound design pass..."
          : "Saving music and beat cues..."
        : "Saving export without music...",
    );
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/music/select`, {
        method: "POST",
        headers: await requestHeaders(workspaceId, userId, apiAccessToken),
        body: JSON.stringify({
          music_track_id: trackId,
          mood: fromPlan ? null : mood,
          include_music: includeMusic,
          music_volume: includeMusic ? musicVolume : 0,
          sfx_intensity: includeMusic ? sfxIntensity : 0,
          rights_confirmed: true,
          force,
        }),
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(payload.error?.message ?? "Music could not be selected.");
      setSelection(payload.selection);
      if (!includeMusic) {
        setMusicVolume(0);
        setSfxIntensity(0);
      } else {
        setMusicVolume(payload.selection.music_volume);
        setSfxIntensity(payload.selection.sfx_intensity);
      }
      setPickerOpen(false);
      setPhase("ready");
      setMessage(payload.cached ? "Reused the matching music selection." : `Music ready: ${payload.job_id}`);
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  function previewSelected() {
    const src = playableAudioUrl(selection?.music_track);
    if (!src) return;
    previewRef.current?.pause();
    previewRef.current = new Audio(src);
    previewRef.current.volume = Math.min(1, Math.max(0, musicVolume));
    void previewRef.current.play();
  }

  const selectedTrack = selection?.music_track ?? null;
  const playablePreview = Boolean(playableAudioUrl(selectedTrack));
  const mixNeedsUpdate = Boolean(
    selection &&
      (Math.abs(selection.music_volume - musicVolume) > 0.001 ||
        Math.abs(selection.sfx_intensity - sfxIntensity) > 0.001),
  );

  return (
    <section className="rounded-lg border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Music2 className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-2xl font-bold">Music and SFX</h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {selectedTrack
              ? `${selectedTrack.title} / ${selectedTrack.bpm ?? "free"} BPM`
              : selection
                ? "Music muted for this project."
                : hasActivePlan
                  ? "Choose a track or use the AI plan's music mood."
                  : "Create an AI plan before selecting music."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" onClick={() => selectMusic({ force: true })} disabled={!selectedTrack || phase === "loading"}>
            <RefreshCw className="mr-2 size-4" aria-hidden="true" />
            Find new track
          </Button>
          <Button type="button" variant="secondary" onClick={() => loadTracks()} disabled={!hasActivePlan || phase === "loading"}>
            <Disc3 className="mr-2 size-4" aria-hidden="true" />
            Choose music
          </Button>
          <Button type="button" onClick={() => selectMusic({ fromPlan: true })} disabled={!hasActivePlan || phase === "loading"}>
            {phase === "loading" ? <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" /> : <WandSparkles className="mr-2 size-4" aria-hidden="true" />}
            Use AI plan mood
          </Button>
        </div>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {moods.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => {
                  setMood(item);
                  if (pickerOpen) void loadTracks(item);
                }}
                className={cn(
                  "rounded-lg border px-3 py-2 text-sm font-semibold transition",
                  mood === item ? "border-primary bg-primary text-primary-foreground" : "border-border bg-white hover:bg-muted",
                )}
                aria-pressed={mood === item}
              >
                {item}
              </button>
            ))}
          </div>

          {pickerOpen ? (
            <div className="grid gap-2 rounded-lg border border-border bg-white p-3">
              {trackOptions.map((track) => (
                <button
                  key={track.id}
                  type="button"
                  onClick={() => selectMusic({ trackId: track.id })}
                  className={cn(
                    "flex items-center justify-between gap-3 rounded-lg border p-3 text-left transition",
                    selectedTrack?.id === track.id ? "border-primary bg-muted" : "border-border hover:bg-muted",
                  )}
                >
                  <span>
                    <span className="block text-sm font-bold">{track.title}</span>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {track.license} / {track.vocals ? "vocals" : "instrumental"} / {track.beat_map.length} beats
                    </span>
                  </span>
                  {selectedTrack?.id === track.id ? <Check className="size-4 text-primary" aria-hidden="true" /> : null}
                </button>
              ))}
              <Button type="button" variant="secondary" onClick={() => selectMusic({ includeMusic: false })} disabled={phase === "loading"}>
                Export without music
              </Button>
            </div>
          ) : null}

          <SliderRow
            icon={<Volume2 className="size-4" aria-hidden="true" />}
            label="Music volume"
            value={musicVolume}
            onChange={setMusicVolume}
          />
          <SliderRow
            icon={<Zap className="size-4" aria-hidden="true" />}
            label="SFX intensity"
            value={sfxIntensity}
            onChange={setSfxIntensity}
          />

          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() =>
                selectMusic({
                  trackId: selectedTrack?.id,
                  includeMusic: Boolean(selectedTrack),
                })
              }
              disabled={!selection || phase === "loading"}
            >
              Save audio mix
            </Button>
            <Button type="button" variant="secondary" onClick={previewSelected} disabled={!playablePreview || phase === "loading"}>
              <Play className="mr-2 size-4" aria-hidden="true" />
              Preview
            </Button>
          </div>

          {mixNeedsUpdate ? (
            <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              Save mix to use these volume and SFX settings in the next export.
            </p>
          ) : null}

          {message ? (
            <p className={cn("rounded-lg border p-3 text-sm", phase === "error" ? "border-red-200 bg-red-50 text-red-800" : "border-border bg-muted text-muted-foreground")}>
              {message}
            </p>
          ) : null}
        </div>

        <div className="rounded-lg border border-border bg-white p-4">
          <p className="text-sm font-bold uppercase text-muted-foreground">Beat map</p>
          {selectedTrack ? (
            <>
              <p className="mt-3 text-lg font-bold">{selectedTrack.title}</p>
              <p className="mt-2 text-2xl font-bold">{selectedTrack.beat_map.length}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {selectedTrack.loopable ? "Loopable" : "Fixed"} / {selectedTrack.vocals ? "vocals" : "instrumental"}
              </p>
              <div className="mt-3 grid grid-cols-8 gap-1">
                {selectedTrack.beat_map.slice(0, 24).map((beat) => (
                  <span
                    key={beat}
                    className="h-2 rounded-full bg-primary"
                    title={`${Math.round(beat)}ms`}
                  />
                ))}
              </div>
              <p className="mt-3 text-xs text-muted-foreground">{selectedTrack.license}</p>
            </>
          ) : (
            <p className="mt-3 text-sm text-muted-foreground">
              Choose music or use the AI plan mood to build a beat map.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function SliderRow({
  icon,
  label,
  value,
  onChange,
}: {
  icon: ReactNode;
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="grid gap-2 rounded-lg border border-border bg-white p-3">
      <span className="flex items-center justify-between gap-3 text-sm font-semibold">
        <span className="flex items-center gap-2">
          {icon}
          {label}
        </span>
        <span>{Math.round(value * 100)}%</span>
      </span>
      <input
        type="range"
        min="0"
        max="1"
        step="0.05"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full accent-primary"
      />
    </label>
  );
}

function playableAudioUrl(track: MusicTrackSummary | null | undefined) {
  const metadata = track?.raw_metadata;
  const value = metadata?.audio_url ?? metadata?.download_url ?? metadata?.src;
  return typeof value === "string" && !value.startsWith("fixture://") ? value : null;
}

function requestHeaders(workspaceId: string, userId: string, apiAccessToken: string, json = true) {
  return buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken, json });
}

async function readJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}
