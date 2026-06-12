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
import { buildApiHeaders } from "@/lib/client-api";
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
  const [segments, setSegments] = useState<CaptionSegment[]>(activeTrack?.segments ?? []);
  const [selectedSegmentIndex, setSelectedSegmentIndex] = useState(0);
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
      const response = await fetch(endpoint, {
        method: activeTrack ? "PATCH" : "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          preset,
          hide_filler_words: hideFillerWords,
          style,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Failed to update captions");
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
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/captions/${activeTrack.id}/segments`, {
        method: "PATCH",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ segments }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Caption segments could not be saved.");
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
    const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/plans/${activePlan.id}/caption-highlights`, {
      method: "PATCH",
      headers: {
        ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ caption_highlights }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error?.message ?? "Caption highlights could not be saved.");
    onPlanUpdated(payload.plan);
  }

  function applyTrack(track: CaptionTrackSummary) {
    setTracks((current) => [track, ...current.filter((item) => item.id !== track.id)]);
    setSegments(track.segments ?? []);
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
    <div className="space-y-6">
      <div className="pixel-panel pixel-corners space-y-6 p-4">
        <div className="space-y-2">
          <Label>Caption Preset</Label>
          <Select value={preset} onChange={(val) => setPreset(val as CaptionPresetKey)} options={presets} />
        </div>

        <label className="flex items-center justify-between gap-3 border-t border-border pt-4 text-xs font-bold uppercase tracking-widest text-muted-foreground/70">
          Hide filler words
          <input
            type="checkbox"
            checked={hideFillerWords}
            onChange={(event) => setHideFillerWords(event.target.checked)}
            className="size-4 accent-primary"
          />
        </label>

        {style ? (
          <div className="space-y-4 border-t border-border pt-4">
            <Label>Brand Kit Caption Style</Label>
            <div className="grid grid-cols-2 gap-3">
              <Input value={style.font_family} onChange={(event) => updateStyle({ font_family: event.target.value })} />
              <Input
                type="color"
                value={style.text_color}
                onChange={(event) => updateStyle({ text_color: event.target.value })}
                className="px-2"
              />
              <Input
                type="color"
                value={style.accent_color}
                onChange={(event) => updateStyle({ accent_color: event.target.value })}
                className="px-2"
              />
              <Select
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
          <div className="mb-4 flex items-center justify-between">
            <Label className="mb-0">Segments</Label>
            <span className="text-[10px] font-bold text-muted-foreground">{segments.length} editable</span>
          </div>
          {segments.length === 0 ? (
            <p className="text-xs text-muted-foreground">No caption track yet.</p>
          ) : (
            <div className="space-y-3">
              <div className="max-h-36 space-y-2 overflow-y-auto pr-2">
                {segments.map((segment, index) => (
                  <button
                    key={segment.id ?? index}
                    onClick={() => setSelectedSegmentIndex(index)}
                    className={cn(
                      "w-full border p-2 text-left text-xs transition",
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
                <div className="space-y-3 border border-border bg-muted/30 p-3">
                  <TextArea
                    value={selectedSegment.text}
                    onChange={(event) => updateSelectedText(event.target.value)}
                    className="min-h-20 text-xs"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <Input
                      type="number"
                      value={selectedSegment.start_ms}
                      onChange={(event) => updateSelectedSegment({ start_ms: Number(event.target.value) })}
                    />
                    <Input
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
                          className={cn(
                            "border px-2 py-1 text-[10px] font-bold uppercase tracking-wider",
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
        <Button onClick={updateCaptions} disabled={loading} className="h-12">
          {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <RefreshCw className="mr-2 size-4" />}
          {activeTrack ? "Save Style" : "Generate"}
        </Button>
        <Button onClick={saveSegments} disabled={loading || !activeTrack} className="h-12">
          {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
          Save Text
        </Button>
      </div>
      {message ? (
        <p className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
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

  return (
    <div
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
