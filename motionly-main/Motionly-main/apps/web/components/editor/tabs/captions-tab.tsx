"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Input, Label, Select, Slider, TextArea, PanelSection } from "../ui";
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
  onPreviewTrackUpdated?: (track: CaptionTrackSummary | null) => void;
  onPlanUpdated: (plan: EditPlanRecordSummary) => void;
}

const presets: { value: CaptionPresetKey; label: string }[] = [
  { value: "luxury_minimal", label: "Minimal" },
  { value: "bold_influencer", label: "Bold & Loud" },
  { value: "vogue_white", label: "Editorial" },
  { value: "gold_premium", label: "Gold Accent" },
  { value: "founder_clean", label: "Clean Blue" },
  { value: "impact_stamp", label: "Impact Stamp" },
  { value: "minimal_line", label: "Minimal Line" },
  { value: "educational_card", label: "Education Card" },
  { value: "creator_ticker", label: "Creator Ticker" },
];

const animations: { value: CaptionStyle["animation"]; label: string }[] = [
  { value: "fade_up", label: "Fade Up" },
  { value: "pop", label: "Pop" },
  { value: "type_on", label: "Type On" },
  { value: "glow", label: "Glow" },
  { value: "slide_up", label: "Slide Up" },
  { value: "impact_stamp", label: "Impact Stamp" },
  { value: "minimal_fade", label: "Minimal Fade" },
  { value: "edu_step", label: "Step Card" },
  { value: "ticker", label: "Ticker" },
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
  onPreviewTrackUpdated,
  onPlanUpdated,
}: CaptionsTabProps) {
  const router = useRouter();
  const [tracks, setTracks] = useState(initialTracks);
  const activeTrack = tracks[0] ?? null;
  const [preset, setPreset] = useState<CaptionPresetKey>(
    activeTrack?.preset ?? "luxury_minimal",
  );
  const [style, setStyle] = useState<CaptionStyle | null>(
    activeTrack?.style ?? null,
  );
  const [hideFillerWords, setHideFillerWords] = useState(
    activeTrack?.hide_filler_words ?? false,
  );
  const [outputLanguage, setOutputLanguage] = useState("original");
  const [segments, setSegments] = useState<CaptionSegment[]>(
    activeTrack?.segments ?? [],
  );
  const [selectedSegmentIndex, setSelectedSegmentIndex] = useState(0);
  const [transcriptText, setTranscriptText] = useState(
    (activeTrack?.segments ?? []).map((segment) => segment.text).join(" "),
  );
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const selectedSegment = segments[selectedSegmentIndex] ?? null;

  useEffect(() => {
    if (!activeTrack) {
      onPreviewTrackUpdated?.(null);
      return;
    }
    onPreviewTrackUpdated?.({
      ...activeTrack,
      preset,
      style: style ?? activeTrack.style,
      hide_filler_words: hideFillerWords,
      segments,
    });
  }, [
    activeTrack,
    hideFillerWords,
    onPreviewTrackUpdated,
    preset,
    segments,
    style,
  ]);

  async function updateCaptions() {
    setLoading(true);
    setMessage(null);
    try {
      const endpoint = activeTrack
        ? `${env.apiBaseUrl}/projects/${projectId}/captions/${activeTrack.id}`
        : `${env.apiBaseUrl}/projects/${projectId}/captions/generate`;
      const payload = await editorApiRequest<{ track: CaptionTrackSummary }>(
        endpoint,
        {
          method: activeTrack ? "PATCH" : "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError:
            "Caption settings could not be saved. Please try again.",
          body: {
            preset,
            hide_filler_words: hideFillerWords,
            style,
            output_language: outputLanguage,
          },
        },
      );
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
    const caption_highlights: CaptionHighlight[] = nextSegments
      .flatMap((segment) =>
        (segment.highlight_words ?? []).slice(0, 4).map((word) => ({
          timestamp_ms: segment.start_ms,
          word,
          effect: "color_pop" as const,
        })),
      )
      .slice(0, 12);
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
    setTracks((current) => [
      track,
      ...current.filter((item) => item.id !== track.id),
    ]);
    setSegments(track.segments ?? []);
    setTranscriptText(
      (track.segments ?? []).map((segment) => segment.text).join(" "),
    );
    setStyle(track.style);
    onPreviewTrackUpdated?.(track);
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
    const wordsPerSegment = Math.max(
      1,
      Math.ceil(words.length / segments.length),
    );
    setSegments((current) =>
      current.map((segment, index) => {
        const text = words
          .slice(index * wordsPerSegment, (index + 1) * wordsPerSegment)
          .join(" ");
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
    setMessage(
      "Transcript text applied to caption segments. Save text to persist it.",
    );
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
    setStyle((current) => (current ? { ...current, ...update } : current));
  }

  function handlePresetChange(value: string) {
    const nextPreset = value as CaptionPresetKey;
    setPreset(nextPreset);
    setStyle((current) =>
      current ? applyPresetDefaults(current, nextPreset) : current,
    );
  }

  return (
    <div className="space-y-3 sm:space-y-4" aria-live="polite">
      <div className="rounded-md border-2 border-foreground shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] p-4 sm:p-5 space-y-4 bg-surface">
        <PanelSection title="Basic" defaultOpen>
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="caption-preset" className="mb-0">
                Caption Preset
              </Label>
              <Select
                id="caption-preset"
                label="Caption Preset"
                value={preset}
                onChange={handlePresetChange}
                options={presets}
                className="border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] dark:focus:shadow-[2px_2px_0px_#fff] transition-all font-mono text-xs"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="caption-output-language" className="mb-0">
                Caption Language
              </Label>
              <Select
                id="caption-output-language"
                label="Caption Language"
                value={outputLanguage}
                onChange={setOutputLanguage}
                options={outputLanguages}
                className="border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] dark:focus:shadow-[2px_2px_0px_#fff] transition-all font-mono text-xs"
              />
            </div>

            <label className="flex min-h-[44px] cursor-pointer items-center justify-between gap-3 rounded-md border-2 border-foreground bg-surface px-3.5 text-xs font-bold font-mono text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] select-none">
              Hide filler words
              <input
                type="checkbox"
                aria-label="Hide filler words"
                checked={hideFillerWords}
                onChange={(event) => setHideFillerWords(event.target.checked)}
                className="size-4.5 rounded border-2 border-foreground text-primary focus:ring-primary/30 accent-primary"
              />
            </label>
          </div>
        </PanelSection>

        {style ? (
          <PanelSection title="Advanced">
            <div className="grid grid-cols-2 gap-2 sm:gap-3">
              <Select
                label="Caption mode"
                value={style.mode}
                onChange={(value) =>
                  updateStyle({ mode: value as CaptionStyle["mode"] })
                }
                options={[
                  { value: "word_pop", label: "Word Pop" },
                  { value: "impact_stamp", label: "Impact Stamp" },
                  { value: "minimal_line", label: "Minimal Line" },
                  { value: "educational_card", label: "Education Card" },
                  { value: "creator_ticker", label: "Creator Ticker" },
                ]}
              />
              <Select
                label="Caption animation"
                value={style.animation}
                onChange={(value) =>
                  updateStyle({ animation: value as CaptionStyle["animation"] })
                }
                options={animations}
              />
              <Input
                aria-label="Caption font family"
                value={style.font_family}
                onChange={(event) =>
                  updateStyle({ font_family: event.target.value })
                }
              />
              <Input
                aria-label="Caption text color"
                type="color"
                value={style.text_color}
                onChange={(event) =>
                  updateStyle({ text_color: event.target.value })
                }
                className="px-2"
              />
              <Input
                aria-label="Caption accent color"
                type="color"
                value={style.accent_color}
                onChange={(event) =>
                  updateStyle({ accent_color: event.target.value })
                }
                className="px-2"
              />
              <Input
                aria-label="Caption background color"
                type="color"
                value={style.background_color ?? "#000000"}
                onChange={(event) =>
                  updateStyle({ background_color: event.target.value })
                }
                className="px-2"
              />
              <Select
                label="Caption placement"
                value={style.placement}
                onChange={(value) =>
                  updateStyle({ placement: value as CaptionStyle["placement"] })
                }
                options={[
                  { value: "lower", label: "Lower" },
                  { value: "middle", label: "Middle" },
                  { value: "upper", label: "Upper" },
                ]}
              />
            </div>
            <Slider
              label="Font Size"
              value={style.font_size_px}
              min={24}
              max={80}
              onChange={(value) => updateStyle({ font_size_px: value })}
            />
            <Slider
              label="Background Opacity"
              value={Math.round((style.background_opacity ?? 0) * 100)}
              min={0}
              max={100}
              onChange={(value) =>
                updateStyle({ background_opacity: value / 100 })
              }
            />
            <Slider
              label="Corner Radius"
              value={style.border_radius_px ?? 0}
              min={0}
              max={64}
              onChange={(value) => updateStyle({ border_radius_px: value })}
            />
            <Slider
              label="Letter Spacing"
              value={Math.round(style.letter_spacing_px ?? 0)}
              min={0}
              max={12}
              onChange={(value) => updateStyle({ letter_spacing_px: value })}
            />
            <PlacementPad style={style} onChange={updateStyle} />
          </PanelSection>
        ) : null}

        <PanelSection title="Transcript">
          <Label htmlFor="full-transcript" className="mb-1">
            Full Transcript
          </Label>
          <TextArea
            id="full-transcript"
            value={transcriptText}
            onChange={(event) => setTranscriptText(event.target.value)}
            className="min-h-24 px-3 py-2 text-sm focus:outline-none"
            aria-describedby="full-transcript-help"
          />
          <p
            id="full-transcript-help"
            className="mt-1.5 text-xs text-muted-foreground"
          >
            Edit the full speech text, apply it to segments, then save text.
          </p>
          <Button
            type="button"
            variant="secondary"
            onClick={applyTranscriptText}
            disabled={!segments.length}
            className="mt-3.5 h-11 w-full text-xs font-bold border-2 border-foreground hover:shadow-[2px_2px_0px_#000]"
          >
            Apply Transcript To Segments
          </Button>
        </PanelSection>

        <PanelSection title="Segments" defaultOpen>
          <div className="mb-2.5 flex items-center justify-between">
            <Label className="mb-0">Segments</Label>
            <span className="text-[10px] font-black uppercase text-foreground bg-surface border-2 border-foreground px-2.5 py-0.5 rounded shadow-[1px_1px_0px_#000] font-mono select-none">
              {segments.length} editable
            </span>
          </div>
          {segments.length === 0 ? (
            <p className="text-xs text-muted-foreground font-mono">
              No caption track yet.
            </p>
          ) : (
            <div className="space-y-3">
              <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-thin">
                {segments.map((segment, index) => (
                  <button
                    key={segment.id ?? index}
                    onClick={() => setSelectedSegmentIndex(index)}
                    aria-current={
                      selectedSegmentIndex === index ? "true" : undefined
                    }
                    aria-label={`Caption segment ${index + 1}, ${segment.text}, starts at ${segment.start_ms} milliseconds, ends at ${segment.end_ms} milliseconds`}
                    className={cn(
                      "min-h-11 w-40 shrink-0 border-2 border-foreground px-3 py-2 text-left text-xs transition-all duration-[100ms] rounded",
                      selectedSegmentIndex === index
                        ? "bg-primary text-primary-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
                        : "bg-surface text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[1px_1px_0px_#000] dark:hover:shadow-[1px_1px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none",
                    )}
                  >
                    <span className="block truncate font-bold">
                      {segment.text}
                    </span>
                    <span className={cn("text-[10px] mt-0.5 block font-mono", selectedSegmentIndex === index ? "text-primary-foreground/80" : "text-muted-foreground")}>
                      {segment.start_ms} - {segment.end_ms} ms
                    </span>
                  </button>
                ))}
              </div>

              {selectedSegment ? (
                <div className="space-y-3 border-2 border-foreground bg-surface p-4 rounded shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                  <TextArea
                    aria-label="Selected caption segment text"
                    value={selectedSegment.text}
                    onChange={(event) => updateSelectedText(event.target.value)}
                    className="min-h-16 px-3 py-2 text-sm focus:outline-none"
                  />
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <label className="font-bold text-foreground">
                      Start (ms)
                      <input
                        aria-label="Selected caption start time in milliseconds"
                        type="number"
                        value={selectedSegment.start_ms}
                        onChange={(event) =>
                          updateSelectedSegment({
                            start_ms: Number(event.target.value),
                          })
                        }
                        className="mt-1.5 min-h-[40px] w-full px-3 font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all"
                      />
                    </label>
                    <label className="font-bold text-foreground">
                      End (ms)
                      <input
                        aria-label="Selected caption end time in milliseconds"
                        type="number"
                        value={selectedSegment.end_ms}
                        onChange={(event) =>
                          updateSelectedSegment({
                            end_ms: Number(event.target.value),
                          })
                        }
                        className="mt-1.5 min-h-[40px] w-full px-3 font-bold border-2 border-foreground bg-surface rounded focus:outline-none focus:shadow-[2px_2px_0px_#000] transition-all"
                      />
                    </label>
                  </div>
                  <div className="flex gap-2 overflow-x-auto pb-1.5 scrollbar-none pt-1">
                    {uniqueWords(selectedSegment.text).map((word) => {
                      const selected =
                        selectedSegment.highlight_words?.includes(word);
                      return (
                        <button
                          key={word}
                          onClick={() => updateHighlight(word)}
                          aria-pressed={selected}
                          className={cn(
                            "min-h-[32px] shrink-0 whitespace-nowrap border-2 border-foreground px-3 py-1 text-xs font-bold font-mono rounded transition-all duration-[100ms]",
                            selected
                              ? "bg-primary text-primary-foreground shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]"
                              : "bg-surface text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[1px_1px_0px_#000] dark:hover:shadow-[1px_1px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none",
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
        </PanelSection>
      </div>

      <div className="sticky bottom-0 z-10 grid grid-cols-2 gap-2.5 bg-background/90 py-3.5 border-t-2 border-foreground backdrop-blur-md mt-4">
        <button
          onClick={updateCaptions}
          disabled={loading}
          aria-busy={loading}
          aria-describedby={message ? "caption-message" : undefined}
          className="w-full inline-flex items-center justify-center gap-2 h-11 rounded border-2 border-foreground bg-primary text-primary-foreground text-xs font-black hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none transition-all duration-[100ms] focus:outline-none"
        >
          {loading ? (
            <Loader2 className="size-4 animate-spin text-primary-foreground" strokeWidth={2} />
          ) : (
            <RefreshCw className="size-4" strokeWidth={2} />
          )}
          <span>{activeTrack ? "Save Style" : "Generate"}</span>
        </button>
        <button
          onClick={saveSegments}
          disabled={loading || !activeTrack}
          aria-busy={loading}
          aria-describedby={message ? "caption-message" : undefined}
          className="h-11 px-2 text-xs font-black rounded border-2 border-foreground bg-primary text-primary-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none transition-all duration-[100ms]"
        >
          {loading ? (
            <Loader2 className="mr-2 size-4 animate-spin text-primary-foreground" strokeWidth={2} />
          ) : (
            <Save className="mr-2 size-4" strokeWidth={2} />
          )}
          <span>Save Text</span>
        </button>
      </div>
      {message ? (
        <p
          id="caption-message"
          role={/failed|could not|try/i.test(message) ? "alert" : "status"}
          className="rounded border-2 border-foreground bg-muted p-3.5 text-xs font-bold text-foreground font-mono shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
        >
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
    const y =
      style.position_y_pct ??
      (style.placement === "upper"
        ? 18
        : style.placement === "middle"
          ? 50
          : 82);
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
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(
        style.position_y_pct ??
          (style.placement === "upper"
            ? 18
            : style.placement === "middle"
              ? 50
              : 82),
      )}
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
    end_ms:
      index === tokens.length - 1
        ? segment.end_ms
        : segment.start_ms + (index + 1) * step,
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

function applyPresetDefaults(
  style: CaptionStyle,
  preset: CaptionPresetKey,
): CaptionStyle {
  const base = {
    ...style,
    mode: modeForPreset(preset),
    animation: animationForPreset(preset),
  };
  switch (preset) {
    case "impact_stamp":
      return {
        ...base,
        text_case: "uppercase",
        font_weight: 900,
        font_size_px: 58,
        accent_color: "#ef4444",
        background_color: "#111827",
        background_opacity: 0.72,
        border_color: "#ef4444",
        border_radius_px: 2,
        letter_spacing_px: 2,
        placement: "middle",
      };
    case "minimal_line":
      return {
        ...base,
        text_case: "normal",
        font_weight: 500,
        font_size_px: 38,
        accent_color: "#94a3b8",
        background_color: null,
        background_opacity: 0,
        border_radius_px: 0,
        letter_spacing_px: 0,
        placement: "lower",
      };
    case "educational_card":
      return {
        ...base,
        text_case: "normal",
        font_weight: 800,
        font_size_px: 40,
        text_color: "#0f172a",
        accent_color: "#2563eb",
        background_color: "#f8fafc",
        background_opacity: 0.94,
        border_color: "#bfdbfe",
        border_radius_px: 14,
        placement: "lower",
      };
    case "creator_ticker":
      return {
        ...base,
        font_weight: 900,
        font_size_px: 44,
        accent_color: "#22c55e",
        background_color: "#111827",
        background_opacity: 0.84,
        border_color: "#22c55e",
        border_radius_px: 64,
        placement: "lower",
      };
    default:
      return base;
  }
}

function modeForPreset(preset: CaptionPresetKey): CaptionStyle["mode"] {
  if (preset === "impact_stamp") return "impact_stamp";
  if (preset === "minimal_line") return "minimal_line";
  if (preset === "educational_card") return "educational_card";
  if (preset === "creator_ticker") return "creator_ticker";
  return "word_pop";
}

function animationForPreset(
  preset: CaptionPresetKey,
): CaptionStyle["animation"] {
  if (preset === "impact_stamp") return "impact_stamp";
  if (preset === "minimal_line") return "minimal_fade";
  if (preset === "educational_card") return "edu_step";
  if (preset === "creator_ticker") return "ticker";
  if (preset === "bold_influencer") return "pop";
  if (preset === "vogue_white") return "type_on";
  if (preset === "gold_premium") return "glow";
  if (preset === "founder_clean") return "slide_up";
  return "fade_up";
}
