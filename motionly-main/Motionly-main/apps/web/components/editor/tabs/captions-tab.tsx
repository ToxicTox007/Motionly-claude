"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Input, Label, Select, Slider, TextArea } from "../ui";
import { Button } from "../../button";
import { Loader2, RefreshCw, Save } from "lucide-react";
import type {
  CaptionHighlight,
  CaptionPresetKey,
  CaptionSegment,
  CaptionStyle,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  TranscriptWord,
} from "@motionly/shared";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

interface CaptionsTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialTracks: CaptionTrackSummary[];
  activePlan: EditPlanRecordSummary | null;
  onTrackUpdated: (track: CaptionTrackSummary) => void;
  onPlanUpdated: (plan: EditPlanRecordSummary) => void;
}

const presets: { value: CaptionPresetKey; label: string }[] = [
  { value: "luxury_minimal", label: "Minimal" },
  { value: "bold_influencer", label: "Bold & Loud" },
  { value: "vogue_white", label: "Editorial" },
  { value: "gold_premium", label: "Gold Accent" },
  { value: "founder_clean", label: "Clean Blue" },
];

const outputLanguages = [
  { value: "original", label: "Original" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "es", label: "Spanish" },
  { value: "pt", label: "Portuguese" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "ar", label: "Arabic" },
];

export function CaptionsTab({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialTracks,
  activePlan,
  onTrackUpdated,
  onPlanUpdated,
}: CaptionsTabProps) {
  const router = useRouter();
  const [tracks, setTracks] = useState(initialTracks);
  const activeTrack = tracks[0] ?? null;
  const [preset, setPreset] = useState<CaptionPresetKey>(activeTrack?.preset ?? "luxury_minimal");
  const [style, setStyle] = useState<CaptionStyle | null>(activeTrack?.style ?? null);
  const [hideFillerWords, setHideFillerWords] = useState(activeTrack?.hide_filler_words ?? false);
  const [outputLanguage, setOutputLanguage] = useState("original");
  const [segments, setSegments] = useState<CaptionSegment[]>(activeTrack?.segments ?? []);
  const [selectedSegmentIndex, setSelectedSegmentIndex] = useState(0);
  const [transcriptText, setTranscriptText] = useState(
    (activeTrack?.segments ?? []).map((segment) => segment.text).join(" "),
  );
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const selectedSegment = segments[selectedSegmentIndex] ?? null;

  async function updateCaptions() {
    setLoading(true);
    setMessage(null);
    try {
      const endpoint = activeTrack
        ? `${env.apiBaseUrl}/projects/${projectId}/captions/${activeTrack.id}`
        : `${env.apiBaseUrl}/projects/${projectId}/captions/generate`;
      const payload = await editorApiRequest<{ track: CaptionTrackSummary }>(endpoint, {
        method: activeTrack ? "PATCH" : "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "Caption settings could not be saved. Please try again.",
        body: {
          preset,
          hide_filler_words: hideFillerWords,
          style,
          output_language: outputLanguage,
        },
      });
      applyTrack(payload.track);
      setMessage("Caption style updated.");
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function saveSegments() {
    if (!activeTrack) {
      setMessage("Generate captions before editing segments.");
      return;
    }
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ track: CaptionTrackSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/captions/${activeTrack.id}/segments`,
        {
        method: "PATCH",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Caption text could not be saved. Please try again.",
          body: { segments },
        },
      );
      applyTrack(payload.track);
      await savePlanHighlights(payload.track.segments);
      setMessage("Caption edits saved.");
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function savePlanHighlights(nextSegments: CaptionSegment[]) {
    if (!activePlan) return;
    const caption_highlights: CaptionHighlight[] = nextSegments.flatMap((segment) =>
      (segment.highlight_words ?? []).slice(0, 4).map((word) => ({
        timestamp_ms: segment.start_ms,
        word,
        effect: "color_pop" as const,
      })),
    ).slice(0, 12);
    const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
      `${env.apiBaseUrl}/projects/${projectId}/plans/${activePlan.id}/caption-highlights`,
      {
      method: "PATCH",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "Caption highlights could not be saved.",
        body: { caption_highlights },
      },
    );
    onPlanUpdated(payload.plan);
  }

  function applyTrack(track: CaptionTrackSummary) {
    setTracks((current) => [track, ...current.filter((item) => item.id !== track.id)]);
    setSegments(track.segments ?? []);
    setTranscriptText((track.segments ?? []).map((segment) => segment.text).join(" "));
    setStyle(track.style);
    onTrackUpdated(track);
  }

  function updateSelectedSegment(update: Partial<CaptionSegment>) {
    setSegments((current) =>
      current.map((segment, index) =>
        index === selectedSegmentIndex ? { ...segment, ...update } : segment,
      ),
    );
  }

  function updateSelectedText(text: string) {
    if (!selectedSegment) return;
    updateSelectedSegment({
      text,
      words: wordsForText(selectedSegment, text),
      line_count: text.length > 28 ? 2 : 1,
    });
  }

  function applyTranscriptText() {
    const words = transcriptText.split(/\s+/).filter(Boolean);
    if (!words.length || !segments.length) return;
    const wordsPerSegment = Math.max(1, Math.ceil(words.length / segments.length));
    setSegments((current) =>
      current.map((segment, index) => {
        const text = words.slice(index * wordsPerSegment, (index + 1) * wordsPerSegment).join(" ");
        return text
          ? {
              ...segment,
              text,
              words: wordsForText(segment, text),
              line_count: text.length > 28 ? 2 : 1,
            }
          : segment;
      }),
    );
    setMessage("Transcript text applied to caption segments. Save text to persist it.");
  }

  function updateHighlight(word: string) {
    if (!selectedSegment) return;
    const normalized = word.trim();
    if (!normalized) return;
    const current = selectedSegment.highlight_words ?? [];
    updateSelectedSegment({
      highlight_words: current.includes(normalized)
        ? current.filter((item) => item !== normalized)
        : [...current, normalized].slice(0, 8),
    });
  }

  function updateStyle(update: Partial<CaptionStyle>) {
    setStyle((current) => current ? { ...current, ...update } : current);
  }

  return (
    <div className="space-y-4 sm:space-y-6" aria-live="polite">
      <div className="pixel-panel space-y-4 p-3 sm:pixel-corners sm:space-y-6 sm:p-4">
        <div className="space-y-2">
          <Label htmlFor="caption-preset">Caption Preset</Label>
          <Select id="caption-preset" label="Caption Preset" value={preset} onChange={(val) => setPreset(val as CaptionPresetKey)} options={presets} />
        </div>

        <label className="flex min-h-11 items-center justify-between gap-3 border-t border-border pt-4 text-sm font-bold text-muted-foreground">
          Hide filler words
          <input
            type="checkbox"
            aria-label="Hide filler words"
            checked={hideFillerWords}
            onChange={(event) => setHideFillerWords(event.target.checked)}
            className="size-6 accent-primary"
          />
        </label>

        <div className="space-y-2 border-t border-border pt-4">
          <Label htmlFor="caption-output-language">Caption Language</Label>
          <Select id="caption-output-language" label="Caption Language" value={outputLanguage} onChange={setOutputLanguage} options={outputLanguages} />
        </div>

        {style ? (
          <div className="space-y-4 border-t border-border pt-4">
            <Label>Brand Kit Caption Style</Label>
            <div className="grid grid-cols-2 gap-2 sm:gap-3">
              <Input aria-label="Caption font family" value={style.font_family} onChange={(event) => updateStyle({ font_family: event.target.value })} />
              <Input
                aria-label="Caption text color"
                type="color"
                value={style.text_color}
                onChange={(event) => updateStyle({ text_color: event.target.value })}
                className="px-2"
              />
              <Input
                aria-label="Caption accent color"
                type="color"
                value={style.accent_color}
                onChange={(event) => updateStyle({ accent_color: event.target.value })}
                className="px-2"
              />
              <Select
                label="Caption placement"
                value={style.placement}
                onChange={(value) => updateStyle({ placement: value as CaptionStyle["placement"] })}
                options={[
                  { value: "lower", label: "Lower" },
                  { value: "middle", label: "Middle" },
                  { value: "upper", label: "Upper" },
                ]}
              />
            </div>
            <Slider label="Font Size" value={style.font_size_px} min={24} max={80} onChange={(value) => updateStyle({ font_size_px: value })} />
            <PlacementPad style={style} onChange={updateStyle} />
          </div>
        ) : null}

        <div className="border-t border-border pt-4">
          <Label htmlFor="full-transcript">Full Transcript</Label>
          <TextArea
            id="full-transcript"
            value={transcriptText}
            onChange={(event) => setTranscriptText(event.target.value)}
            className="min-h-32"
            aria-describedby="full-transcript-help"
          />
          <p id="full-transcript-help" className="mt-2 text-sm text-muted-foreground">
            Edit the full speech text, apply it to segments, then save text.
          </p>
          <Button type="button" variant="secondary" onClick={applyTranscriptText} disabled={!segments.length} className="mt-3 min-h-11 w-full">
            Apply Transcript To Segments
          </Button>
        </div>

        <div className="border-t border-border pt-4">
          <div className="mb-4 flex items-center justify-between">
            <Label className="mb-0">Segments</Label>
            <span className="text-[10px] font-bold text-muted-foreground">{segments.length} editable</span>
          </div>
          {segments.length === 0 ? (
            <p className="text-xs text-muted-foreground">No caption track yet.</p>
          ) : (
            <div className="space-y-3">
              <div className="max-h-48 space-y-2 overflow-y-auto pr-1 sm:max-h-36 sm:pr-2">
                {segments.map((segment, index) => (
                  <button
                    key={segment.id ?? index}
                    onClick={() => setSelectedSegmentIndex(index)}
                    aria-current={selectedSegmentIndex === index ? "true" : undefined}
                    aria-label={`Caption segment ${index + 1}, ${segment.text}, starts at ${segment.start_ms} milliseconds, ends at ${segment.end_ms} milliseconds`}
                    className={cn(
                      "min-h-11 w-full border p-2 text-left text-sm transition sm:p-2",
                      selectedSegmentIndex === index
                        ? "border-primary bg-primary/10"
                        : "border-border bg-muted/40 hover:border-primary/40",
                    )}
                  >
                    <span className="block truncate font-bold">{segment.text}</span>
                    <span className="text-[10px] text-muted-foreground">{segment.start_ms} - {segment.end_ms} ms</span>
                  </button>
                ))}
              </div>

              {selectedSegment ? (
                <div className="space-y-3 border border-border bg-muted/30 p-2.5 sm:p-3">
                  <TextArea
                    aria-label="Selected caption segment text"
                    value={selectedSegment.text}
                    onChange={(event) => updateSelectedText(event.target.value)}
                    className="min-h-20 text-xs"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <Input
                      aria-label="Selected caption start time in milliseconds"
                      type="number"
                      value={selectedSegment.start_ms}
                      onChange={(event) => updateSelectedSegment({ start_ms: Number(event.target.value) })}
                    />
                    <Input
                      aria-label="Selected caption end time in milliseconds"
                      type="number"
                      value={selectedSegment.end_ms}
                      onChange={(event) => updateSelectedSegment({ end_ms: Number(event.target.value) })}
                    />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {uniqueWords(selectedSegment.text).map((word) => {
                      const selected = selectedSegment.highlight_words?.includes(word);
                      return (
                        <button
                          key={word}
                          onClick={() => updateHighlight(word)}
                          aria-pressed={selected}
                          className={cn(
                            "min-h-11 border px-3 py-2 text-xs font-bold",
                            selected ? "border-primary bg-primary/10 text-primary" : "border-border",
                          )}
                        >
                          {word}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Button onClick={updateCaptions} disabled={loading} aria-busy={loading} aria-describedby={message ? "caption-message" : undefined} className="h-11 sm:h-12">
          {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
          {activeTrack ? "Save Style" : "Generate"}
        </Button>
        <Button onClick={saveSegments} disabled={loading || !activeTrack} aria-busy={loading} aria-describedby={message ? "caption-message" : undefined} className="h-11 sm:h-12">
          {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
          Save Text
        </Button>
      </div>
      {message ? (
        <p id="caption-message" role={/failed|could not|try/i.test(message) ? "alert" : "status"} className="border border-border bg-muted p-3 text-sm font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function PlacementPad({
  style,
  onChange,
}: {
  style: CaptionStyle;
  onChange: (update: Partial<CaptionStyle>) => void;
}) {
  function updateFromPointer(event: React.PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = Math.round(((event.clientX - rect.left) / rect.width) * 100);
    const y = Math.round(((event.clientY - rect.top) / rect.height) * 100);
    onChange({
      position_x_pct: Math.max(0, Math.min(100, x)),
      position_y_pct: Math.max(0, Math.min(100, y)),
      placement: y < 33 ? "upper" : y > 66 ? "lower" : "middle",
    });
  }
  function updateFromKeyboard(event: React.KeyboardEvent<HTMLDivElement>) {
    const x = style.position_x_pct ?? 50;
    const y = style.position_y_pct ?? (style.placement === "upper" ? 18 : style.placement === "middle" ? 50 : 82);
    const step = event.shiftKey ? 10 : 2;
    let nextX = x;
    let nextY = y;
    if (event.key === "ArrowLeft") nextX -= step;
    else if (event.key === "ArrowRight") nextX += step;
    else if (event.key === "ArrowUp") nextY -= step;
    else if (event.key === "ArrowDown") nextY += step;
    else if (event.key === "Home") {
      nextX = 50;
      nextY = 82;
    } else {
      return;
    }
    event.preventDefault();
    const clampedY = Math.max(12, Math.min(82, nextY));
    onChange({
      position_x_pct: Math.max(12, Math.min(88, nextX)),
      position_y_pct: clampedY,
      placement: clampedY < 33 ? "upper" : clampedY > 66 ? "lower" : "middle",
    });
  }

  return (
    <div
      role="slider"
      tabIndex={0}
      aria-label="Caption placement"
      aria-valuetext={`X ${style.position_x_pct ?? 50} percent, Y ${style.position_y_pct ?? (style.placement === "upper" ? 18 : style.placement === "middle" ? 50 : 82)} percent`}
      onKeyDown={updateFromKeyboard}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        updateFromPointer(event);
      }}
      onPointerMove={(event) => {
        if (event.buttons === 1) updateFromPointer(event);
      }}
      className="relative aspect-[9/16] max-h-48 cursor-crosshair border border-border bg-black"
      title="Click or drag to place captions"
    >
      <div className="absolute inset-[10%] border border-dashed border-white/20" />
      <div
        className="absolute h-4 w-16 -translate-x-1/2 -translate-y-1/2 border border-primary bg-primary/20"
        style={{
          left: `${style.position_x_pct ?? 50}%`,
          top: `${style.position_y_pct ?? (style.placement === "upper" ? 18 : style.placement === "middle" ? 50 : 82)}%`,
        }}
      />
    </div>
  );
}

function wordsForText(segment: CaptionSegment, text: string): TranscriptWord[] {
  const tokens = uniqueWords(text, false);
  if (!tokens.length) return [];
  const duration = Math.max(tokens.length, segment.end_ms - segment.start_ms);
  const step = Math.max(1, Math.floor(duration / tokens.length));
  return tokens.map((token, index) => ({
    index,
    text: token,
    start_ms: segment.start_ms + index * step,
    end_ms: index === tokens.length - 1 ? segment.end_ms : segment.start_ms + (index + 1) * step,
    confidence: null,
  }));
}

function uniqueWords(text: string, dedupe = true) {
  const words = text
    .split(/\s+/)
    .map((word) => word.replace(/[^\w'-]/g, "").trim())
    .filter(Boolean);
  return dedupe ? Array.from(new Set(words)) : words;
}
