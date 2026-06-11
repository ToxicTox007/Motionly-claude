"use client";

import type {
  CaptionPresetKey,
  CaptionSegment,
  CaptionStyle,
  TranscriptWord,
} from "@motionly/shared";
import {
  Captions,
  EyeOff,
  Languages,
  Loader2,
  RefreshCw,
  Wand2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/button";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

type Preset = {
  key: CaptionPresetKey;
  name: string;
  style: CaptionStyle;
};

type Track = {
  id: string;
  preset: CaptionPresetKey;
  style: CaptionStyle;
  hide_filler_words: boolean;
  segments: CaptionSegment[];
};

type Transcript = {
  id: string;
  status: string;
  language: "en" | "hi" | "unknown";
  words: TranscriptWord[];
  error_message?: string | null;
} | null;

type CaptionStudioProps = {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialTranscript: Transcript;
  initialPresets: Preset[];
  initialTracks: Track[];
  hasReadySource: boolean;
  sourceStatus?: string | null;
  sourceVideoUrl?: string | null;
  thumbnailUrl?: string | null;
};

const fallbackPresets: Preset[] = [
  {
    key: "luxury_minimal",
    name: "Minimal captions",
    style: {
      font_family: "Inter",
      font_size_px: 42,
      font_weight: 600,
      text_case: "normal",
      text_color: "#f8fafc",
      accent_color: "#14b8a6",
      stroke_color: "#0f172a",
      stroke_width_px: 1,
      shadow: "0 8px 24px rgba(15, 23, 42, 0.45)",
      safe_area_bottom_pct: 18,
      placement: "lower",
      animation: "fade_up",
      max_lines: 2,
      contrast_guardrail: "Keep captions readable over bright footage.",
    },
  },
  {
    key: "bold_influencer",
    name: "Bold Influencer",
    style: {
      font_family: "Inter",
      font_size_px: 50,
      font_weight: 900,
      text_case: "uppercase",
      text_color: "#ffffff",
      accent_color: "#ef4444",
      stroke_color: "#111827",
      stroke_width_px: 3,
      shadow: "0 5px 0 rgba(17, 24, 39, 0.9)",
      safe_area_bottom_pct: 15,
      placement: "lower",
      animation: "pop",
      max_lines: 2,
      contrast_guardrail: "Always stroke text over motion-heavy clips.",
    },
  },
  {
    key: "vogue_white",
    name: "Editorial white",
    style: {
      font_family: "Georgia",
      font_size_px: 44,
      font_weight: 700,
      text_case: "title",
      text_color: "#ffffff",
      accent_color: "#d1d5db",
      stroke_color: "#020617",
      stroke_width_px: 1,
      shadow: "0 12px 28px rgba(2, 6, 23, 0.4)",
      safe_area_bottom_pct: 20,
      placement: "middle",
      animation: "type_on",
      max_lines: 2,
      contrast_guardrail:
        "Use center placement only when face-safe area is clear.",
    },
  },
  {
    key: "gold_premium",
    name: "Gold accent",
    style: {
      font_family: "Inter",
      font_size_px: 46,
      font_weight: 800,
      text_case: "uppercase",
      text_color: "#fff7ed",
      accent_color: "#d6a13d",
      stroke_color: "#21180b",
      stroke_width_px: 2,
      shadow: "0 0 20px rgba(214, 161, 61, 0.42)",
      safe_area_bottom_pct: 16,
      placement: "lower",
      animation: "glow",
      max_lines: 2,
      contrast_guardrail: "Keep gold as an accent when footage is warm.",
    },
  },
  {
    key: "founder_clean",
    name: "Clean captions",
    style: {
      font_family: "Inter",
      font_size_px: 40,
      font_weight: 700,
      text_case: "normal",
      text_color: "#e5f3ff",
      accent_color: "#2563eb",
      stroke_color: "#0f172a",
      stroke_width_px: 1,
      shadow: "0 10px 26px rgba(15, 23, 42, 0.35)",
      safe_area_bottom_pct: 17,
      placement: "lower",
      animation: "slide_up",
      max_lines: 2,
      contrast_guardrail: "Use sentence case and avoid more than two lines.",
    },
  },
];

export function CaptionStudio({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialTranscript,
  initialPresets,
  initialTracks,
  hasReadySource,
  sourceStatus,
  sourceVideoUrl,
  thumbnailUrl,
}: CaptionStudioProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [transcript, setTranscript] = useState<Transcript>(initialTranscript);
  const [presets] = useState<Preset[]>(
    initialPresets.length ? initialPresets : fallbackPresets,
  );
  const [tracks, setTracks] = useState<Track[]>(initialTracks);
  const [activeTrackId, setActiveTrackId] = useState<string | null>(
    initialTracks[0]?.id ?? null,
  );
  const [selectedPreset, setSelectedPreset] = useState<CaptionPresetKey>(
    initialTracks[0]?.preset ?? presets[0]?.key ?? "luxury_minimal",
  );
  const [hideFillers, setHideFillers] = useState(
    initialTracks[0]?.hide_filler_words ?? false,
  );
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">(
    "idle",
  );
  const [message, setMessage] = useState<string | null>(null);
  const [playheadMs, setPlayheadMs] = useState(0);

  const selectedTrack =
    tracks.find((track) => track.id === activeTrackId) ??
    tracks[0] ??
    null;
  const selectedPresetData =
    presets.find((preset) => preset.key === selectedPreset) ?? presets[0];
  const previewStyle =
    selectedTrack?.preset === selectedPreset
      ? selectedTrack.style
      : selectedPresetData.style;
  const activeSegment = selectedTrack?.segments.find(
    (segment) => playheadMs >= segment.start_ms && playheadMs <= segment.end_ms,
  );
  const activeWord = transcript?.words.find(
    (word) => playheadMs >= word.start_ms && playheadMs <= word.end_ms,
  );
  const sourceBlockedMessage = hasReadySource
    ? null
    : sourceStatus
      ? `Source video is ${sourceStatus}. Captions unlock after preprocessing finishes.`
      : "Upload a valid source video before generating captions.";
  const durationMs = useMemo(() => {
    const wordEnd = transcript?.words.at(-1)?.end_ms ?? 6000;
    const segmentEnd = selectedTrack?.segments.at(-1)?.end_ms ?? wordEnd;
    return Math.max(wordEnd, segmentEnd, 6000);
  }, [selectedTrack, transcript]);
  const timedWordCount = transcript?.words.filter(
    (word) => word.end_ms > word.start_ms,
  ).length;
  const activeLanguage = transcript?.language ?? "unknown";

  useEffect(() => {
    if (!selectedTrack) return;
    setSelectedPreset(selectedTrack.preset);
    setHideFillers(selectedTrack.hide_filler_words);
  }, [selectedTrack]);

  function setPreviewTime(nextMs: number) {
    const bounded = Math.max(0, Math.min(nextMs, durationMs));
    setPlayheadMs(bounded);
    if (videoRef.current) {
      videoRef.current.currentTime = bounded / 1000;
    }
  }

  async function requestTranscript(
    languageHint: "auto" | "en" | "hi" = "auto",
    force = false,
  ) {
    setPhase("loading");
    setMessage("Preparing word-level transcript...");
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/transcriptions`,
        {
          method: "POST",
          headers: await requestHeaders(workspaceId, userId, apiAccessToken),
          body: JSON.stringify({ language_hint: languageHint, force }),
        },
      );
      const payload = await readJson(response);
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Transcript could not be generated.",
        );
      const transcriptResponse = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/transcript`,
        {
          headers: await requestHeaders(
            workspaceId,
            userId,
            apiAccessToken,
            false,
          ),
        },
      );
      const transcriptPayload = await readJson(transcriptResponse);
      if (!transcriptResponse.ok)
        throw new Error(transcriptPayload.error?.message ?? payload.message);
      setTranscript(transcriptPayload);
      setTracks([]);
      setActiveTrackId(null);
      window.dispatchEvent(
        new CustomEvent("motionly:caption-track-ready", {
          detail: { trackId: null, includeCaptions: false },
        }),
      );
      setPhase("ready");
      setMessage(
        transcriptPayload.language === languageHint || languageHint === "auto"
          ? payload.message
          : `${payload.message} Provider returned ${languageLabel(transcriptPayload.language)}.`,
      );
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  async function saveCaptionSettings(
    preset: CaptionPresetKey = selectedPreset,
    hideFillerWords = hideFillers,
  ) {
    setPhase("loading");
    setMessage(
      selectedTrack ? "Saving caption style..." : "Generating caption track...",
    );
    try {
      const endpoint = selectedTrack
        ? `${env.apiBaseUrl}/projects/${projectId}/captions/${selectedTrack.id}`
        : `${env.apiBaseUrl}/projects/${projectId}/captions/generate`;
      const response = await fetch(endpoint, {
        method: selectedTrack ? "PATCH" : "POST",
        headers: await requestHeaders(workspaceId, userId, apiAccessToken),
        body: JSON.stringify({
          transcript_id: selectedTrack ? undefined : transcript?.id,
          preset,
          hide_filler_words: hideFillerWords,
        }),
      });
      const payload = await readJson(response);
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Captions could not be generated.",
        );
      setTracks((current) => [
        payload.track,
        ...current.filter((track) => track.id !== payload.track.id),
      ]);
      setActiveTrackId(payload.track.id);
      setSelectedPreset(payload.track.preset);
      setHideFillers(payload.track.hide_filler_words);
      window.dispatchEvent(
        new CustomEvent("motionly:caption-track-ready", {
          detail: { trackId: payload.track.id, includeCaptions: true },
        }),
      );
      setPhase("ready");
      setMessage(`Caption style saved: ${payload.job_id}`);
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  function handlePresetClick(preset: CaptionPresetKey) {
    setSelectedPreset(preset);
    if (transcript?.words.length) void saveCaptionSettings(preset, hideFillers);
  }

  function handleHideFillers(nextValue: boolean) {
    setHideFillers(nextValue);
    if (transcript?.words.length)
      void saveCaptionSettings(selectedPreset, nextValue);
  }

  return (
    <section className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="relative mx-auto aspect-[9/16] max-h-[620px] overflow-hidden rounded-lg bg-slate-950">
          {sourceVideoUrl ? (
            <video
              ref={videoRef}
              className="absolute inset-0 size-full object-contain"
              muted
              playsInline
              preload="metadata"
              poster={thumbnailUrl ?? undefined}
              src={sourceVideoUrl}
              onTimeUpdate={(event) =>
                setPlayheadMs(Math.round(event.currentTarget.currentTime * 1000))
              }
            />
          ) : (
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_20%,rgba(20,184,166,0.24),transparent_34%),linear-gradient(160deg,#111827,#020617_60%,#0f172a)]" />
          )}
          <div className="absolute left-4 right-4 top-4 flex items-center justify-between text-xs font-semibold text-white/70">
            <span>Caption preview</span>
            <span>{formatTime(playheadMs)}</span>
          </div>
          {activeSegment ? (
            <PreviewCaption segment={activeSegment} style={previewStyle} />
          ) : (
            <div className="absolute inset-x-6 bottom-[16%] rounded-lg border border-white/10 bg-black/35 p-3 text-center text-sm font-semibold text-white/70">
              {selectedTrack
                ? "No caption at this timestamp"
                : "Generate captions to preview timed text"}
            </div>
          )}
        </div>
        <input
          className="mt-4 w-full accent-teal-600"
          type="range"
          min={0}
          max={durationMs}
          step={80}
          value={playheadMs}
          onChange={(event) => setPreviewTime(Number(event.target.value))}
          aria-label="Caption preview timeline"
        />
        <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
          <span>{formatTime(0)}</span>
          <span>{formatTime(durationMs)}</span>
        </div>
      </div>

      <div className="space-y-5">
        <div className="rounded-lg border border-border bg-surface p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-bold">Captions</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                {transcript?.status === "ready"
                  ? `${timedWordCount ?? 0} timed words / ${languageLabel(activeLanguage)}`
                  : transcript?.status === "captions_unavailable"
                    ? "Captions unavailable"
                  : "Transcript not generated"}
              </p>
            </div>
            <Button
              type="button"
              onClick={() => requestTranscript("auto", Boolean(transcript))}
              disabled={!hasReadySource || phase === "loading"}
            >
              {phase === "loading" ? (
                <Loader2
                  className="mr-2 size-4 animate-spin"
                  aria-hidden="true"
                />
              ) : (
                <Wand2 className="mr-2 size-4" aria-hidden="true" />
              )}
              {transcript ? "Regenerate transcript" : "Transcribe"}
            </Button>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              type="button"
              variant={activeLanguage === "en" ? "primary" : "secondary"}
              onClick={() => requestTranscript("en", true)}
              disabled={!hasReadySource || phase === "loading"}
              aria-pressed={activeLanguage === "en"}
            >
              <Languages className="mr-2 size-4" aria-hidden="true" />
              English
            </Button>
            <Button
              type="button"
              variant={activeLanguage === "hi" ? "primary" : "secondary"}
              onClick={() => requestTranscript("hi", true)}
              disabled={!hasReadySource || phase === "loading"}
              aria-pressed={activeLanguage === "hi"}
            >
              <Languages className="mr-2 size-4" aria-hidden="true" />
              Hindi
            </Button>
          </div>
          {sourceBlockedMessage ? (
            <p className="mt-4 rounded-lg border border-border bg-muted p-3 text-sm text-muted-foreground">
              {sourceBlockedMessage}
            </p>
          ) : null}
          {message ? (
            <p
              className={cn(
                "mt-4 rounded-lg border p-3 text-sm",
                phase === "error"
                  ? "border-red-200 bg-red-50 text-red-800"
                  : "border-border bg-muted text-muted-foreground",
              )}
            >
              {message}
            </p>
          ) : null}
        </div>

        <div className="rounded-lg border border-border bg-surface p-5">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-bold uppercase text-muted-foreground">
              Preset
            </h3>
            <label className="inline-flex items-center gap-2 text-sm font-semibold">
              <input
                type="checkbox"
                checked={hideFillers}
                onChange={(event) => handleHideFillers(event.target.checked)}
                className="size-4 accent-teal-600"
              />
              Hide fillers
            </label>
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {presets.map((preset) => (
              <button
                key={preset.key}
                type="button"
                onClick={() => handlePresetClick(preset.key)}
                aria-pressed={selectedPreset === preset.key}
                className={cn(
                  "rounded-lg border p-3 text-left text-sm font-semibold transition",
                  selectedPreset === preset.key
                    ? "border-teal-600 bg-teal-50 text-teal-950"
                    : "border-border bg-white hover:bg-muted",
                )}
              >
                <span
                  className="mr-2 inline-block size-3 rounded-full"
                  style={{ backgroundColor: preset.style.accent_color }}
                />
                {preset.name}
              </button>
            ))}
          </div>
          <Button
            type="button"
            className="mt-4 w-full"
            onClick={() => saveCaptionSettings()}
            disabled={
              !hasReadySource ||
              !transcript?.words.length ||
              phase === "loading"
            }
          >
            <RefreshCw className="mr-2 size-4" aria-hidden="true" />
            {selectedTrack ? "Save caption style" : "Generate captions"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="mt-2 w-full"
            onClick={() =>
              window.dispatchEvent(
                new CustomEvent("motionly:caption-track-ready", {
                  detail: {
                    trackId: selectedTrack?.id ?? null,
                    includeCaptions: false,
                  },
                }),
              )
            }
            disabled={!selectedTrack || phase === "loading"}
          >
            <EyeOff className="mr-2 size-4" aria-hidden="true" />
            Hide captions in export
          </Button>
        </div>

        <div className="rounded-lg border border-border bg-surface p-5">
          <div className="flex items-center gap-2">
            <Captions className="size-4 text-primary" aria-hidden="true" />
            <h3 className="text-sm font-bold uppercase text-muted-foreground">
              Transcript
            </h3>
          </div>
          <div className="mt-4 max-h-64 overflow-auto text-sm leading-7">
            {transcript?.words.length ? (
              transcript.words.map((word) => (
                <span
                  key={`${word.index}-${word.start_ms}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => setPreviewTime(word.start_ms)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setPreviewTime(word.start_ms);
                    }
                  }}
                  className={cn(
                    "mr-1 rounded px-1.5 py-0.5 transition hover:bg-muted",
                    activeWord?.index === word.index
                      ? "bg-teal-100 font-bold text-teal-950"
                      : "text-foreground",
                  )}
                >
                  {word.text}
                </span>
              ))
            ) : (
              <p className="text-muted-foreground">
                Generate a transcript to inspect word timings.
              </p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function PreviewCaption({
  segment,
  style,
}: {
  segment: CaptionSegment;
  style: CaptionStyle;
}) {
  const text =
    style.text_case === "uppercase" ? segment.text.toUpperCase() : segment.text;
  return (
    <div
      className={cn(
        "absolute left-6 right-6 text-center leading-tight",
        style.placement === "middle"
          ? "top-1/2 -translate-y-1/2"
          : "bottom-[16%]",
      )}
      style={{
        color: style.text_color,
        fontFamily: style.font_family,
        fontSize: `clamp(28px, ${Math.round(style.font_size_px / 9)}vw, ${style.font_size_px}px)`,
        fontWeight: style.font_weight,
        textShadow: style.shadow ?? undefined,
        WebkitTextStroke: style.stroke_width_px
          ? `${style.stroke_width_px}px ${style.stroke_color}`
          : undefined,
      }}
    >
      {text}
    </div>
  );
}

function requestHeaders(
  workspaceId: string,
  userId: string,
  apiAccessToken: string,
  json = true,
) {
  return buildApiHeaders({
    workspaceId,
    userId,
    accessToken: apiAccessToken,
    json,
  });
}

async function readJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function formatTime(ms: number) {
  const totalSeconds = Math.floor(ms / 1000);
  return `${Math.floor(totalSeconds / 60)}:${String(totalSeconds % 60).padStart(2, "0")}`;
}

function languageLabel(language: "en" | "hi" | "unknown") {
  if (language === "en") return "English";
  if (language === "hi") return "Hindi";
  return "Language unknown";
}
