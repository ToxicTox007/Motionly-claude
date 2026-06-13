"use client";

import React, { useCallback, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Loader2,
  Maximize2,
  Minus,
  Scissors,
  Trash2,
  ZoomIn,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMs, rulerTicks } from "@/lib/timeline";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { useTimeline } from "./useTimeline";
import { usePlaybackSync } from "./usePlaybackSync";
import type {
  BrollSlotSummary,
  CaptionSegment,
  CaptionTrackSummary,
  EditPlanCut,
  EditPlanRecordSummary,
  TimelineClip,
  TimelineState,
} from "@motionly/shared";

interface TimelinePanelProps {
  state: TimelineState;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  playheadMs: number;
  setPlayheadMs: (ms: number) => void;
  selectedClipId: string | null;
  onClipSelect: (clipId: string | null) => void;
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  activePlan: EditPlanRecordSummary | null;
  captionTrack: CaptionTrackSummary | null;
  onPlanUpdated: (plan: EditPlanRecordSummary) => void;
  onCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  onBrollSlotUpdated: (slot: BrollSlotSummary) => void;
}

const TRACK_HEIGHT = 44;
const RULER_HEIGHT = 30;
const LABEL_WIDTH = 64;
const MIN_CLIP_MS = 300;
const NUDGE_MS = 500;

export function TimelinePanel({
  state,
  videoRef,
  playheadMs,
  setPlayheadMs,
  selectedClipId,
  onClipSelect,
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  activePlan,
  captionTrack,
  onPlanUpdated,
  onCaptionTrackUpdated,
  onBrollSlotUpdated,
}: TimelinePanelProps) {
  const router = useRouter();
  const { totalDurationMs, tracks } = state;
  const scrollRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const { zoomLevel, zoomIn, zoomOut, fitToContent, seekTo } = useTimeline({
    totalDurationMs,
    videoRef,
  });

  usePlaybackSync(videoRef, setPlayheadMs);

  const selectedClip = useMemo(
    () => tracks.flatMap((track) => track.clips).find((clip) => clip.id === selectedClipId) ?? null,
    [selectedClipId, tracks],
  );

  const pct = useCallback(
    (ms: number) => `${((ms / Math.max(totalDurationMs, 1)) * 100).toFixed(4)}%`,
    [totalDurationMs],
  );

  const msFromPointer = useCallback(
    (clientX: number) => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return 0;
      const relX = Math.min(Math.max(clientX - rect.left, 0), rect.width);
      return Math.round((relX / rect.width) * totalDurationMs);
    },
    [totalDurationMs],
  );

  function handleRulerPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    const ms = msFromPointer(e.clientX);
    seekTo(ms);
    setPlayheadMs(ms);
  }

  function handleTrackAreaClick() {
    onClipSelect(null);
  }

  async function request(path: string, body: object) {
    return editorApiRequest<Record<string, any>>(`${env.apiBaseUrl}${path}`, {
      method: "PATCH",
      workspaceId,
      userId,
      accessToken: apiAccessToken,
      friendlyError: "Timeline changes could not be saved. Please try again.",
      body: body as Record<string, unknown>,
    });
  }

  async function saveCuts(cuts: EditPlanCut[]) {
    if (!activePlan) throw new Error("Generate an AI edit first.");
    const payload = await request(`/projects/${projectId}/plans/${activePlan.id}/cuts`, { cuts });
    onPlanUpdated(payload.plan);
    return "Video cuts saved.";
  }

  async function saveCaptionSegments(segments: CaptionSegment[]) {
    if (!captionTrack) throw new Error("Generate captions first.");
    const payload = await request(`/projects/${projectId}/captions/${captionTrack.id}/segments`, {
      segments,
    });
    onCaptionTrackUpdated(payload.track);
    return "Caption timing saved.";
  }

  async function saveBrollRegion(slotId: string, startMs: number, endMs: number, disabled?: boolean) {
    const payload = await request(`/projects/${projectId}/broll/${slotId}/region`, {
      start_ms: startMs,
      end_ms: endMs,
      disabled,
    });
    onBrollSlotUpdated(payload.slot);
    return disabled ? "B-roll slot disabled." : "B-roll timing saved.";
  }

  async function runTimelineAction(action: () => Promise<string>) {
    setSaving(true);
    setMessage(null);
    try {
      const nextMessage = await action();
      setMessage(nextMessage);
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function cutsForClipUpdate(clip: TimelineClip, startMs: number, endMs: number) {
    const cuts = [...(activePlan?.plan.cuts ?? [])];
    const index = clip.sourceRef.index ?? -1;
    if (index < 0 || !cuts[index]) return null;
    cuts[index] = { ...cuts[index], start_ms: startMs, end_ms: endMs };
    return cuts;
  }

  function captionSegmentsForClipUpdate(clip: TimelineClip, startMs: number, endMs: number) {
    const segments = [...(captionTrack?.segments ?? [])];
    const index = captionSegmentIndex(clip, segments);
    if (index < 0) return null;
    segments[index] = { ...segments[index], start_ms: startMs, end_ms: endMs };
    return segments;
  }

  function commitTrim(clip: TimelineClip, side: "left" | "right", nextMs: number) {
    const nextStart = side === "left" ? Math.min(nextMs, clip.endMs - MIN_CLIP_MS) : clip.startMs;
    const nextEnd = side === "right" ? Math.max(nextMs, clip.startMs + MIN_CLIP_MS) : clip.endMs;
    if (clip.sourceRef.kind === "cut") {
      const cuts = cutsForClipUpdate(clip, nextStart, nextEnd);
      if (cuts) runTimelineAction(() => saveCuts(cuts));
      return;
    }
    if (clip.sourceRef.kind === "caption_segment") {
      const segments = captionSegmentsForClipUpdate(clip, nextStart, nextEnd);
      if (segments) runTimelineAction(() => saveCaptionSegments(segments));
      return;
    }
    if (clip.sourceRef.kind === "broll_slot" && clip.sourceRef.id) {
      runTimelineAction(() => saveBrollRegion(clip.sourceRef.id!, nextStart, nextEnd));
    }
  }

  function commitRegionMove(clip: TimelineClip, nextStartMs: number) {
    if (clip.sourceRef.kind !== "broll_slot" || !clip.sourceRef.id) {
      setMessage(
        clip.sourceRef.kind === "music"
          ? "Music region dragging is coming soon. Edit music in Audio."
          : "Drag-to-reorder is not available for this track yet.",
      );
      return;
    }
    const duration = clip.endMs - clip.startMs;
    const startMs = Math.max(0, Math.min(totalDurationMs - duration, nextStartMs));
    runTimelineAction(() => saveBrollRegion(clip.sourceRef.id!, startMs, startMs + duration));
  }

  function splitSelectedClip() {
    if (!selectedClip) return;
    runTimelineAction(async () => {
      if (selectedClip.sourceRef.kind === "music") throw new Error("Cannot split music yet. Edit in Audio.");
      if (playheadMs <= selectedClip.startMs + MIN_CLIP_MS || playheadMs >= selectedClip.endMs - MIN_CLIP_MS) {
        throw new Error("Move the playhead inside the selected clip to split.");
      }
      if (selectedClip.sourceRef.kind === "cut") {
        const cuts = [...(activePlan?.plan.cuts ?? [])];
        const index = selectedClip.sourceRef.index ?? -1;
        const current = cuts[index];
        if (!current) throw new Error("Select a generated video clip.");
        cuts.splice(
          index,
          1,
          { ...current, end_ms: playheadMs },
          { ...current, start_ms: playheadMs, reason: `${current.reason} split` },
        );
        return saveCuts(cuts);
      }
      if (selectedClip.sourceRef.kind === "caption_segment") {
        const segments = [...(captionTrack?.segments ?? [])];
        const index = captionSegmentIndex(selectedClip, segments);
        const current = segments[index];
        if (!current) throw new Error("Select a caption segment.");
        segments.splice(
          index,
          1,
          { ...current, end_ms: playheadMs },
          { ...current, id: undefined, start_ms: playheadMs },
        );
        return saveCaptionSegments(segments);
      }
      throw new Error("Cannot split this clip yet.");
    });
  }

  function deleteSelectedClip() {
    if (!selectedClip) return;
    if (!window.confirm(`Delete ${selectedClip.label}?`)) return;
    runTimelineAction(async () => {
      if (selectedClip.sourceRef.kind === "music") throw new Error("Cannot delete music here. Edit in Audio.");
      if (selectedClip.sourceRef.kind === "cut") {
        const cuts = [...(activePlan?.plan.cuts ?? [])];
        const index = selectedClip.sourceRef.index ?? -1;
        if (cuts.length <= 1) throw new Error("At least one video clip must remain.");
        if (!cuts[index]) throw new Error("Select a generated video clip.");
        cuts.splice(index, 1);
        onClipSelect(null);
        return saveCuts(cuts);
      }
      if (selectedClip.sourceRef.kind === "caption_segment") {
        const segments = [...(captionTrack?.segments ?? [])];
        const index = captionSegmentIndex(selectedClip, segments);
        if (segments.length <= 1) throw new Error("At least one caption segment must remain.");
        if (!segments[index]) throw new Error("Select a caption segment.");
        segments.splice(index, 1);
        onClipSelect(null);
        return saveCaptionSegments(segments);
      }
      if (selectedClip.sourceRef.kind === "broll_slot" && selectedClip.sourceRef.id) {
        onClipSelect(null);
        return saveBrollRegion(
          selectedClip.sourceRef.id,
          selectedClip.startMs,
          selectedClip.endMs,
          true,
        );
      }
      throw new Error("Cannot delete this clip yet.");
    });
  }

  function moveSelectedClip(direction: -1 | 1) {
    if (!selectedClip) return;
    runTimelineAction(async () => {
      if (selectedClip.sourceRef.kind === "music") throw new Error("Cannot reorder music. Edit in Audio.");
      if (selectedClip.sourceRef.kind === "cut") {
        const cuts = [...(activePlan?.plan.cuts ?? [])];
        const index = selectedClip.sourceRef.index ?? -1;
        const nextIndex = index + direction;
        if (!cuts[index] || !cuts[nextIndex]) throw new Error("No adjacent video clip to move.");
        [cuts[index], cuts[nextIndex]] = [cuts[nextIndex], cuts[index]];
        return saveCuts(cuts);
      }
      if (selectedClip.sourceRef.kind === "caption_segment") {
        const segments = [...(captionTrack?.segments ?? [])];
        const index = captionSegmentIndex(selectedClip, segments);
        const nextIndex = index + direction;
        if (!segments[index] || !segments[nextIndex]) throw new Error("No adjacent caption to move.");
        [segments[index], segments[nextIndex]] = [segments[nextIndex], segments[index]];
        return saveCaptionSegments(segments);
      }
      if (selectedClip.sourceRef.kind === "broll_slot" && selectedClip.sourceRef.id) {
        const duration = selectedClip.endMs - selectedClip.startMs;
        const nextStart = Math.max(0, Math.min(totalDurationMs - duration, selectedClip.startMs + direction * NUDGE_MS));
        return saveBrollRegion(selectedClip.sourceRef.id, nextStart, nextStart + duration);
      }
      throw new Error("Cannot move this clip yet.");
    });
  }

  function updateSelectedTiming(startMs: number, endMs: number) {
    if (!selectedClip) return;
    runTimelineAction(async () => {
      if (endMs <= startMs) throw new Error("End time must be after start time.");
      if (selectedClip.sourceRef.kind === "cut") {
        const cuts = cutsForClipUpdate(selectedClip, startMs, endMs);
        if (!cuts) throw new Error("Select a generated video clip.");
        return saveCuts(cuts);
      }
      if (selectedClip.sourceRef.kind === "caption_segment") {
        const segments = captionSegmentsForClipUpdate(selectedClip, startMs, endMs);
        if (!segments) throw new Error("Select a caption segment.");
        return saveCaptionSegments(segments);
      }
      if (selectedClip.sourceRef.kind === "broll_slot" && selectedClip.sourceRef.id) {
        return saveBrollRegion(selectedClip.sourceRef.id, startMs, endMs);
      }
      throw new Error("Timing for this clip is edited in Audio.");
    });
  }

  const ticks = rulerTicks(totalDurationMs);
  const selectedCopy = selectedClip
    ? `${selectedClip.label} ${formatMs(selectedClip.startMs)}-${formatMs(selectedClip.endMs)}`
    : "Select a clip";

  return (
    <div className="flex h-full min-h-0 select-none flex-col bg-[hsl(var(--surface))]">
      <h2 id="timeline-heading" className="sr-only">Timeline</h2>
      <div className="pixel-grid-fine flex min-h-12 shrink-0 items-center gap-2 border-b border-border px-2 sm:px-3">
        <span className="font-mono text-xs text-muted-foreground tabular-nums" aria-live="polite">
          {formatMs(playheadMs)} <span className="opacity-40">/</span> {formatMs(totalDurationMs)}
        </span>
        <span className="hidden truncate text-[10px] font-bold text-muted-foreground sm:block">
          {saving ? "Saving timeline..." : message ?? selectedCopy}
        </span>
        <div className="ml-auto flex items-center gap-1 overflow-x-auto">
          {saving ? <Loader2 className="mr-1 size-3 animate-spin text-primary" /> : null}
          <TimelineButton onClick={splitSelectedClip} title={selectedClip ? "Split selected clip at playhead" : "Select a clip"} ariaLabel="Split selected clip at playhead" disabled={!selectedClip || saving}>
            <Scissors className="size-3" />
          </TimelineButton>
          <TimelineButton onClick={deleteSelectedClip} title={selectedClip ? "Delete selected clip" : "Select a clip"} ariaLabel="Delete selected clip" disabled={!selectedClip || saving}>
            <Trash2 className="size-3" />
          </TimelineButton>
          <TimelineButton onClick={() => moveSelectedClip(-1)} title={selectedClip ? "Move left" : "Select a clip"} ariaLabel="Move selected clip earlier" disabled={!selectedClip || saving}>
            <ArrowLeft className="size-3" />
          </TimelineButton>
          <TimelineButton onClick={() => moveSelectedClip(1)} title={selectedClip ? "Move right" : "Select a clip"} ariaLabel="Move selected clip later" disabled={!selectedClip || saving}>
            <ArrowRight className="size-3" />
          </TimelineButton>
          <span className="mx-1 h-4 w-px bg-border" />
          <TimelineButton onClick={zoomOut} title="Zoom out" ariaLabel="Zoom timeline out">
            <Minus className="size-3" />
          </TimelineButton>
          <TimelineButton onClick={fitToContent} title="Fit to content" ariaLabel="Fit timeline to content">
            <Maximize2 className="size-3" />
          </TimelineButton>
          <TimelineButton onClick={zoomIn} title="Zoom in" ariaLabel="Zoom timeline in">
            <ZoomIn className="size-3" />
          </TimelineButton>
          {zoomLevel !== 1 && (
            <span className="ml-1 text-[9px] font-bold text-primary tabular-nums">
              {zoomLevel.toFixed(1)}x
            </span>
          )}
        </div>
      </div>
      {selectedClip ? (
        <form
          className="flex flex-wrap items-end gap-2 border-b border-border bg-muted/35 p-2"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            updateSelectedTiming(Number(form.get("start_ms")), Number(form.get("end_ms")));
          }}
        >
          <label className="text-sm font-bold text-muted-foreground">
            Start ms
            <input name="start_ms" type="number" defaultValue={selectedClip.startMs} className="pixel-input ml-2 min-h-11 w-28 px-2" />
          </label>
          <label className="text-sm font-bold text-muted-foreground">
            End ms
            <input name="end_ms" type="number" defaultValue={selectedClip.endMs} className="pixel-input ml-2 min-h-11 w-28 px-2" />
          </label>
          <span className="text-sm text-muted-foreground">
            Duration {formatMs(selectedClip.endMs - selectedClip.startMs)}
          </span>
          <ButtonLike type="submit" disabled={saving}>Save Timing</ButtonLike>
        </form>
      ) : null}

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div
          className="flex flex-shrink-0 flex-col border-r border-border bg-[hsl(var(--surface))]"
          style={{ width: LABEL_WIDTH }}
        >
          <div style={{ height: RULER_HEIGHT }} />
          {tracks.map((track) => (
            <div key={track.kind} className="flex items-center justify-end pr-2" style={{ height: TRACK_HEIGHT }}>
              <span className="text-[9px] font-bold uppercase tracking-widest text-muted-foreground/70">
                {track.label}
              </span>
            </div>
          ))}
        </div>

        <div className="relative flex-1 overflow-x-auto overflow-y-hidden" ref={scrollRef}>
          <div
            ref={canvasRef}
            className="relative"
            style={{
              width: `${Math.round(zoomLevel * 100)}%`,
              minWidth: "100%",
            }}
            onClick={handleTrackAreaClick}
          >
            <div
              className="pixel-grid-fine relative cursor-pointer bg-[hsl(var(--background))]"
              style={{ height: RULER_HEIGHT }}
              onPointerDown={handleRulerPointerDown}
            >
              {ticks.map((tick) => (
                <div
                  key={tick.ms}
                  className="absolute top-0 flex flex-col items-center"
                  style={{ left: pct(tick.ms), transform: "translateX(-50%)" }}
                >
                  <span className="pt-1 font-mono text-[8px] leading-none text-muted-foreground/50">
                    {tick.label}
                  </span>
                  <div className="mt-0.5 h-2 w-px bg-border" />
                </div>
              ))}
            </div>

            {tracks.map((track, trackIdx) => (
              <div
                key={track.kind}
                role="list"
                aria-label={`${track.label} timeline track`}
                className={cn(
                  "relative border-b border-border/60",
                  trackIdx % 2 === 0 ? "bg-[hsl(var(--background))]" : "bg-[hsl(var(--surface))]",
                )}
                style={{ height: TRACK_HEIGHT }}
              >
                {track.clips.map((clip) => (
                  <ClipBlock
                    key={clip.id}
                    clip={clip}
                    pct={pct}
                    selected={clip.id === selectedClipId}
                    onSelect={onClipSelect}
                    onTrim={commitTrim}
                    onMove={commitRegionMove}
                    msFromPointer={msFromPointer}
                    trimDisabled={clip.sourceRef.kind === "music"}
                    moveDisabled={clip.sourceRef.kind !== "broll_slot"}
                  />
                ))}
              </div>
            ))}

            <Playhead pct={pct(playheadMs)} totalHeight={RULER_HEIGHT + tracks.length * TRACK_HEIGHT} />
          </div>
        </div>
      </div>
    </div>
  );
}

function captionSegmentIndex(clip: TimelineClip, segments: CaptionSegment[]) {
  if (clip.sourceRef.id) {
    const byId = segments.findIndex((segment) => segment.id === clip.sourceRef.id);
    if (byId >= 0) return byId;
  }
  return clip.sourceRef.index ?? -1;
}

interface ClipBlockProps {
  clip: TimelineClip;
  pct: (ms: number) => string;
  selected: boolean;
  onSelect: (id: string) => void;
  onTrim: (clip: TimelineClip, side: "left" | "right", nextMs: number) => void;
  onMove: (clip: TimelineClip, nextStartMs: number) => void;
  msFromPointer: (clientX: number) => number;
  trimDisabled: boolean;
  moveDisabled: boolean;
}

function ClipBlock({
  clip,
  pct,
  selected,
  onSelect,
  onTrim,
  onMove,
  msFromPointer,
  trimDisabled,
  moveDisabled,
}: ClipBlockProps) {
  const widthMs = Math.max(clip.endMs - clip.startMs, 0);

  function startTrim(side: "left" | "right", event: React.PointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    if (trimDisabled) return;
    const handlePointerUp = (nextEvent: PointerEvent) => {
      window.removeEventListener("pointerup", handlePointerUp);
      onTrim(clip, side, msFromPointer(nextEvent.clientX));
    };
    window.addEventListener("pointerup", handlePointerUp);
  }

  function startMove(event: React.PointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    onSelect(clip.id);
    if (moveDisabled) return;
    const startPointerMs = msFromPointer(event.clientX);
    const startClipMs = clip.startMs;
    const handlePointerUp = (nextEvent: PointerEvent) => {
      window.removeEventListener("pointerup", handlePointerUp);
      const deltaMs = msFromPointer(nextEvent.clientX) - startPointerMs;
      if (Math.abs(deltaMs) < 80) return;
      onMove(clip, startClipMs + deltaMs);
    };
    window.addEventListener("pointerup", handlePointerUp);
  }
  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(clip.id);
    } else if (event.key === "ArrowLeft" && !moveDisabled) {
      event.preventDefault();
      onMove(clip, clip.startMs - 500);
    } else if (event.key === "ArrowRight" && !moveDisabled) {
      event.preventDefault();
      onMove(clip, clip.startMs + 500);
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${clip.trackKind} clip ${clip.label}. Starts ${formatMs(clip.startMs)}, ends ${formatMs(clip.endMs)}, duration ${formatMs(widthMs)}.`}
      className={cn(
        "group absolute bottom-1 top-1 cursor-pointer overflow-hidden border border-white/20",
        "flex items-center px-2 text-[10px] font-bold leading-none text-white/90",
        "shadow-[2px_2px_0_rgba(0,0,0,0.2)] ring-inset transition-all duration-100",
        clip.colorClass,
        clip.disabled && "opacity-30",
        selected
          ? "ring-2 ring-white shadow-[0_0_0_1px_white,4px_4px_0_rgba(0,0,0,0.22)]"
          : "ring-1 ring-white/10 hover:-translate-y-0.5 hover:brightness-110",
      )}
      style={{
        left: pct(clip.startMs),
        width: pct(widthMs),
      }}
      onPointerDown={startMove}
      onKeyDown={onKeyDown}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(clip.id);
      }}
      title={`${clip.label} | ${formatMs(clip.startMs)} - ${formatMs(clip.endMs)}${moveDisabled ? " | Use toolbar actions" : " | Drag to move"}`}
    >
      <TrimHandle side="left" disabled={trimDisabled} onPointerDown={(event) => startTrim("left", event)} />
      <span className="flex-1 truncate">{clip.label}</span>
      <TrimHandle side="right" disabled={trimDisabled} onPointerDown={(event) => startTrim("right", event)} />
    </div>
  );
}

function TrimHandle({
  side,
  disabled,
  onPointerDown,
}: {
  side: "left" | "right";
  disabled: boolean;
  onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      className={cn(
        "absolute bottom-0 top-0 z-10 flex w-1.5 items-center justify-center transition",
        disabled ? "cursor-not-allowed opacity-0" : "cursor-ew-resize bg-white/20 opacity-0 hover:bg-white/50 hover:opacity-100 group-hover:opacity-100",
        side === "left" ? "left-0" : "right-0",
      )}
      title={disabled ? "Edit in Audio" : "Trim clip"}
      onPointerDown={onPointerDown}
      onClick={(event) => event.stopPropagation()}
    />
  );
}

function Playhead({ pct, totalHeight }: { pct: string; totalHeight: number }) {
  return (
    <div
      className="pointer-events-none absolute top-0 z-20 flex flex-col items-center"
      style={{ left: pct, transform: "translateX(-50%)" }}
    >
      <div className="h-2 w-2 rotate-45 bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.8)]" />
      <div
        className="w-px bg-red-500/80 shadow-[0_0_3px_rgba(239,68,68,0.5)]"
        style={{ height: totalHeight - 8 }}
      />
    </div>
  );
}

function TimelineButton({
  onClick,
  title,
  ariaLabel,
  disabled,
  children,
}: {
  onClick: () => void;
  title: string;
  ariaLabel: string;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={ariaLabel}
      disabled={disabled}
      className="flex min-h-11 min-w-11 shrink-0 items-center justify-center border border-transparent text-muted-foreground transition hover:border-primary/30 hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-transparent disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}

function ButtonLike({
  type,
  disabled,
  children,
}: {
  type: "submit";
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      className="pixel-button min-h-11 px-3 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-60"
    >
      {children}
    </button>
  );
}
