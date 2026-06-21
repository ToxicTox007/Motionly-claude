"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDroppable } from "@dnd-kit/core";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Loader2,
  Maximize2,
  Minus,
  Scissors,
  Trash2,
  Redo2,
  Undo2,
  ZoomIn,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { formatMs, rulerTicks } from "@/lib/timeline";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { useTimeline } from "./useTimeline";
import { usePlaybackSync } from "./usePlaybackSync";
import { useEditorHistory, type HistoryEntry } from "../history-context";
import type {
  BrollSlotSummary,
  CaptionSegment,
  CaptionTrackSummary,
  EditPlanCut,
  EditPlanRecordSummary,
  MusicRegion,
  MusicSelectionSummary,
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
  musicSelection: MusicSelectionSummary | null;
  onPlanUpdated: (plan: EditPlanRecordSummary) => void;
  onCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  onBrollSlotUpdated: (slot: BrollSlotSummary) => void;
  onMusicSelectionUpdated: (selection: MusicSelectionSummary) => void;
}

type ReorderDirection = -1 | 1;

const TRACK_HEIGHT = 44;
const RULER_HEIGHT = 30;
const LABEL_WIDTH = 64;
const MIN_CLIP_MS = 300;
const NUDGE_MS = 500;
const REORDER_THRESHOLD_MS = 120;

type BrollRegionSnapshot = {
  startMs: number;
  endMs: number;
  disabled?: boolean;
};

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
  musicSelection,
  onPlanUpdated,
  onCaptionTrackUpdated,
  onBrollSlotUpdated,
  onMusicSelectionUpdated,
}: TimelinePanelProps) {
  const router = useRouter();
  const { totalDurationMs, tracks } = state;
  const scrollRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { pushHistory, undo, redo, canUndo, canRedo, currentLabel } = useEditorHistory();

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

  const request = useCallback(async (path: string, body: object) => {
    return editorApiRequest<Record<string, any>>(`${env.apiBaseUrl}${path}`, {
      method: "PATCH",
      workspaceId,
      userId,
      accessToken: apiAccessToken,
      friendlyError: "Timeline changes could not be saved. Please try again.",
      body: body as Record<string, unknown>,
    });
  }, [apiAccessToken, userId, workspaceId]);

  const saveCuts = useCallback(async (cuts: EditPlanCut[]) => {
    if (!activePlan) throw new Error("Generate an AI edit first.");
    const payload = await request(`/projects/${projectId}/plans/${activePlan.id}/cuts`, { cuts });
    onPlanUpdated(payload.plan);
    return "Video cuts saved.";
  }, [activePlan, onPlanUpdated, projectId, request]);

  const saveCaptionSegments = useCallback(async (segments: CaptionSegment[]) => {
    if (!captionTrack) throw new Error("Generate captions first.");
    const payload = await request(`/projects/${projectId}/captions/${captionTrack.id}/segments`, {
      segments,
    });
    onCaptionTrackUpdated(payload.track);
    return "Caption timing saved.";
  }, [captionTrack, onCaptionTrackUpdated, projectId, request]);

  const saveBrollRegion = useCallback(async (
    slotId: string,
    startMs: number,
    endMs: number,
    disabled?: boolean,
  ) => {
    const payload = await request(`/projects/${projectId}/broll/${slotId}/region`, {
      start_ms: startMs,
      end_ms: endMs,
      disabled,
    });
    onBrollSlotUpdated(payload.slot);
    return disabled ? "B-roll slot disabled." : "B-roll timing saved.";
  }, [onBrollSlotUpdated, projectId, request]);

  const saveMusicRegions = useCallback(async (regions: MusicRegion[]) => {
    if (!musicSelection) throw new Error("Select music first.");
    const payload = (await request(`/projects/${projectId}/music/regions`, { regions })) as MusicSelectionSummary;
    onMusicSelectionUpdated(payload);
    return "Music regions saved.";
  }, [musicSelection, onMusicSelectionUpdated, projectId, request]);

  const runTimelineAction = useCallback(async (
    action: () => Promise<string>,
    historyEntry?: HistoryEntry,
  ) => {
    setSaving(true);
    setMessage(null);
    try {
      const nextMessage = await action();
      if (historyEntry) pushHistory(historyEntry);
      setMessage(nextMessage);
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setSaving(false);
    }
  }, [pushHistory, router]);



  function cloneCuts(cuts: EditPlanCut[]) {
    return cuts.map((cut) => ({ ...cut }));
  }

  function cloneSegments(segments: CaptionSegment[]) {
    return segments.map((segment) => ({
      ...segment,
      words: segment.words.map((word) => ({ ...word })),
      highlight_words: [...(segment.highlight_words ?? [])],
    }));
  }

  function commitCuts(nextCuts: EditPlanCut[], label: string) {
    const before = cloneCuts(activePlan?.plan.cuts ?? []);
    const after = cloneCuts(nextCuts);
    runTimelineAction(
      () => saveCuts(after),
      {
        id: `cuts-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: () => saveCuts(before),
        redo: () => saveCuts(after),
      },
    );
  }

  function commitCaptionSegments(nextSegments: CaptionSegment[], label: string) {
    const before = cloneSegments(captionTrack?.segments ?? []);
    const after = cloneSegments(nextSegments);
    runTimelineAction(
      () => saveCaptionSegments(after),
      {
        id: `captions-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: () => saveCaptionSegments(before),
        redo: () => saveCaptionSegments(after),
      },
    );
  }

  function commitBrollRegion(
    slotId: string,
    before: BrollRegionSnapshot,
    after: BrollRegionSnapshot,
    label: string,
  ) {
    runTimelineAction(
      () => saveBrollRegion(slotId, after.startMs, after.endMs, after.disabled),
      {
        id: `broll-${slotId}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: () => saveBrollRegion(slotId, before.startMs, before.endMs, before.disabled),
        redo: () => saveBrollRegion(slotId, after.startMs, after.endMs, after.disabled),
      },
    );
  }

  function cloneMusicRegions(regions: MusicRegion[]) {
    return regions.map((region) => ({ ...region }));
  }

  function commitMusicRegions(nextRegions: MusicRegion[], label: string) {
    const before = cloneMusicRegions(musicSelection?.regions ?? []);
    const after = cloneMusicRegions(nextRegions);
    runTimelineAction(
      () => saveMusicRegions(after),
      {
        id: `music-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: () => saveMusicRegions(before),
        redo: () => saveMusicRegions(after),
      },
    );
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

  function musicRegionsWithUpdate(regionId: string, startMs: number, endMs: number) {
    const regions = [...(musicSelection?.regions ?? [])];
    const index = regions.findIndex((region) => region.id === regionId);
    if (index < 0) return null;
    regions[index] = { ...regions[index], start_ms: startMs, end_ms: endMs };
    return regions;
  }

  function commitTrim(clip: TimelineClip, side: "left" | "right", nextMs: number) {
    const nextStart = side === "left" ? Math.min(nextMs, clip.endMs - MIN_CLIP_MS) : clip.startMs;
    const nextEnd = side === "right" ? Math.max(nextMs, clip.startMs + MIN_CLIP_MS) : clip.endMs;
    if (clip.sourceRef.kind === "cut") {
      const cuts = cutsForClipUpdate(clip, nextStart, nextEnd);
      if (cuts) commitCuts(cuts, `Trim ${clip.label}`);
      return;
    }
    if (clip.sourceRef.kind === "caption_segment") {
      const segments = captionSegmentsForClipUpdate(clip, nextStart, nextEnd);
      if (segments) commitCaptionSegments(segments, `Trim ${clip.label}`);
      return;
    }
    if (clip.sourceRef.kind === "broll_slot" && clip.sourceRef.id) {
      commitBrollRegion(
        clip.sourceRef.id,
        { startMs: clip.startMs, endMs: clip.endMs },
        { startMs: nextStart, endMs: nextEnd },
        `Trim ${clip.label}`,
      );
      return;
    }
    if (clip.sourceRef.kind === "music" && clip.sourceRef.regionId) {
      const regions = musicRegionsWithUpdate(clip.sourceRef.regionId, nextStart, nextEnd);
      if (regions) commitMusicRegions(regions, `Trim ${clip.label}`);
    }
  }

  function commitRegionMove(clip: TimelineClip, nextStartMs: number) {
    const duration = clip.endMs - clip.startMs;
    const startMs = Math.max(0, Math.min(totalDurationMs - duration, nextStartMs));
    if (clip.sourceRef.kind === "broll_slot" && clip.sourceRef.id) {
      commitBrollRegion(
        clip.sourceRef.id,
        { startMs: clip.startMs, endMs: clip.endMs },
        { startMs, endMs: startMs + duration },
        `Move ${clip.label}`,
      );
      return;
    }
    if (clip.sourceRef.kind === "music" && clip.sourceRef.regionId) {
      const regions = musicRegionsWithUpdate(clip.sourceRef.regionId, startMs, startMs + duration);
      if (regions) commitMusicRegions(regions, `Move ${clip.label}`);
      return;
    }
    setMessage("Drag-to-reorder is not available for this track yet.");
  }

  function commitReorder(clip: TimelineClip, direction: ReorderDirection) {
    if (clip.sourceRef.kind === "cut") {
      const cuts = [...(activePlan?.plan.cuts ?? [])];
      const index = clip.sourceRef.index ?? -1;
      const nextIndex = index + direction;
      if (!cuts[index] || !cuts[nextIndex]) {
        setMessage("No adjacent video clip to reorder.");
        return;
      }
      [cuts[index], cuts[nextIndex]] = [cuts[nextIndex], cuts[index]];
      commitCuts(cuts, `Reorder ${clip.label}`);
      return;
    }
    if (clip.sourceRef.kind === "caption_segment") {
      const segments = [...(captionTrack?.segments ?? [])];
      const index = captionSegmentIndex(clip, segments);
      const nextIndex = index + direction;
      if (!segments[index] || !segments[nextIndex]) {
        setMessage("No adjacent caption to reorder.");
        return;
      }
      [segments[index], segments[nextIndex]] = [segments[nextIndex], segments[index]];
      commitCaptionSegments(segments, `Reorder ${clip.label}`);
      return;
    }
    setMessage("Cannot reorder this clip type yet.");
  }

  function splitSelectedClip() {
    if (!selectedClip) return;
    if (playheadMs <= selectedClip.startMs + MIN_CLIP_MS || playheadMs >= selectedClip.endMs - MIN_CLIP_MS) {
      setMessage("Move the playhead inside the selected clip to split.");
      return;
    }
    if (selectedClip.sourceRef.kind === "music" && selectedClip.sourceRef.regionId) {
      const regions = [...(musicSelection?.regions ?? [])];
      const index = regions.findIndex((region) => region.id === selectedClip.sourceRef.regionId);
      const current = regions[index];
      if (!current) {
        setMessage("Select a music region.");
        return;
      }
      regions.splice(
        index,
        1,
        { ...current, end_ms: playheadMs },
        { ...current, id: `${current.id}-split-${Date.now()}`, start_ms: playheadMs },
      );
      commitMusicRegions(regions, `Split ${selectedClip.label}`);
      return;
    }
    if (selectedClip.sourceRef.kind === "cut") {
      const cuts = [...(activePlan?.plan.cuts ?? [])];
      const index = selectedClip.sourceRef.index ?? -1;
      const current = cuts[index];
      if (!current) {
        setMessage("Select a generated video clip.");
        return;
      }
      cuts.splice(
        index,
        1,
        { ...current, end_ms: playheadMs },
        { ...current, start_ms: playheadMs, reason: `${current.reason} split` },
      );
      commitCuts(cuts, `Split ${selectedClip.label}`);
      return;
    }
    if (selectedClip.sourceRef.kind === "caption_segment") {
      const segments = [...(captionTrack?.segments ?? [])];
      const index = captionSegmentIndex(selectedClip, segments);
      const current = segments[index];
      if (!current) {
        setMessage("Select a caption segment.");
        return;
      }
      segments.splice(
        index,
        1,
        { ...current, end_ms: playheadMs },
        { ...current, id: undefined, start_ms: playheadMs },
      );
      commitCaptionSegments(segments, `Split ${selectedClip.label}`);
      return;
    }
    setMessage("Cannot split this clip yet.");
  }

  function deleteSelectedClip() {
    if (!selectedClip) return;
    if (!window.confirm(`Delete ${selectedClip.label}?`)) return;
    if (selectedClip.sourceRef.kind === "music" && selectedClip.sourceRef.regionId) {
      const regions = [...(musicSelection?.regions ?? [])];
      const index = regions.findIndex((region) => region.id === selectedClip.sourceRef.regionId);
      if (regions.length <= 1) {
        setMessage("At least one music region must remain.");
        return;
      }
      if (!regions[index]) {
        setMessage("Select a music region.");
        return;
      }
      regions.splice(index, 1);
      onClipSelect(null);
      commitMusicRegions(regions, `Delete ${selectedClip.label}`);
      return;
    }
      if (selectedClip.sourceRef.kind === "cut") {
        const cuts = [...(activePlan?.plan.cuts ?? [])];
        const index = selectedClip.sourceRef.index ?? -1;
        if (cuts.length <= 1) {
          setMessage("At least one video clip must remain.");
          return;
        }
        if (!cuts[index]) {
          setMessage("Select a generated video clip.");
          return;
        }
        cuts.splice(index, 1);
        onClipSelect(null);
        commitCuts(cuts, `Delete ${selectedClip.label}`);
        return;
      }
      if (selectedClip.sourceRef.kind === "caption_segment") {
        const segments = [...(captionTrack?.segments ?? [])];
        const index = captionSegmentIndex(selectedClip, segments);
        if (segments.length <= 1) {
          setMessage("At least one caption segment must remain.");
          return;
        }
        if (!segments[index]) {
          setMessage("Select a caption segment.");
          return;
        }
        segments.splice(index, 1);
        onClipSelect(null);
        commitCaptionSegments(segments, `Delete ${selectedClip.label}`);
        return;
      }
      if (selectedClip.sourceRef.kind === "broll_slot" && selectedClip.sourceRef.id) {
        onClipSelect(null);
        commitBrollRegion(
          selectedClip.sourceRef.id,
          { startMs: selectedClip.startMs, endMs: selectedClip.endMs },
          { startMs: selectedClip.startMs, endMs: selectedClip.endMs, disabled: true },
          `Delete ${selectedClip.label}`,
        );
        return;
      }
      setMessage("Cannot delete this clip yet.");
  }

  function moveSelectedClip(direction: -1 | 1) {
    if (!selectedClip) return;
      if (selectedClip.sourceRef.kind === "cut") {
        const cuts = [...(activePlan?.plan.cuts ?? [])];
        const index = selectedClip.sourceRef.index ?? -1;
        const nextIndex = index + direction;
        if (!cuts[index] || !cuts[nextIndex]) {
          setMessage("No adjacent video clip to move.");
          return;
        }
        [cuts[index], cuts[nextIndex]] = [cuts[nextIndex], cuts[index]];
        commitCuts(cuts, `Reorder ${selectedClip.label}`);
        return;
      }
      if (selectedClip.sourceRef.kind === "caption_segment") {
        const segments = [...(captionTrack?.segments ?? [])];
        const index = captionSegmentIndex(selectedClip, segments);
        const nextIndex = index + direction;
        if (!segments[index] || !segments[nextIndex]) {
          setMessage("No adjacent caption to move.");
          return;
        }
        [segments[index], segments[nextIndex]] = [segments[nextIndex], segments[index]];
        commitCaptionSegments(segments, `Reorder ${selectedClip.label}`);
        return;
      }
      if (selectedClip.sourceRef.kind === "broll_slot" && selectedClip.sourceRef.id) {
        const duration = selectedClip.endMs - selectedClip.startMs;
        const nextStart = Math.max(0, Math.min(totalDurationMs - duration, selectedClip.startMs + direction * NUDGE_MS));
        commitBrollRegion(
          selectedClip.sourceRef.id,
          { startMs: selectedClip.startMs, endMs: selectedClip.endMs },
          { startMs: nextStart, endMs: nextStart + duration },
          `Nudge ${selectedClip.label}`,
        );
        return;
      }
      if (selectedClip.sourceRef.kind === "music" && selectedClip.sourceRef.regionId) {
        const duration = selectedClip.endMs - selectedClip.startMs;
        const nextStart = Math.max(0, Math.min(totalDurationMs - duration, selectedClip.startMs + direction * NUDGE_MS));
        const regions = musicRegionsWithUpdate(selectedClip.sourceRef.regionId, nextStart, nextStart + duration);
        if (regions) commitMusicRegions(regions, `Nudge ${selectedClip.label}`);
        return;
      }
      setMessage("Cannot move this clip yet.");
  }

  function updateSelectedTiming(startMs: number, endMs: number) {
    if (!selectedClip) return;
    if (endMs <= startMs) {
      setMessage("End time must be after start time.");
      return;
    }
      if (selectedClip.sourceRef.kind === "cut") {
        const cuts = cutsForClipUpdate(selectedClip, startMs, endMs);
        if (!cuts) {
          setMessage("Select a generated video clip.");
          return;
        }
        commitCuts(cuts, `Retiming ${selectedClip.label}`);
        return;
      }
      if (selectedClip.sourceRef.kind === "caption_segment") {
        const segments = captionSegmentsForClipUpdate(selectedClip, startMs, endMs);
        if (!segments) {
          setMessage("Select a caption segment.");
          return;
        }
        commitCaptionSegments(segments, `Retiming ${selectedClip.label}`);
        return;
      }
      if (selectedClip.sourceRef.kind === "broll_slot" && selectedClip.sourceRef.id) {
        commitBrollRegion(
          selectedClip.sourceRef.id,
          { startMs: selectedClip.startMs, endMs: selectedClip.endMs },
          { startMs, endMs },
          `Retiming ${selectedClip.label}`,
        );
        return;
      }
      if (selectedClip.sourceRef.kind === "music" && selectedClip.sourceRef.regionId) {
        const regions = musicRegionsWithUpdate(selectedClip.sourceRef.regionId, startMs, endMs);
        if (regions) commitMusicRegions(regions, `Retiming ${selectedClip.label}`);
        return;
      }
      setMessage("Timing for this clip is edited in Audio.");
  }

  const seekBy = useCallback((deltaMs: number) => {
    const nextMs = Math.max(0, Math.min(totalDurationMs, playheadMs + deltaMs));
    seekTo(nextMs);
    setPlayheadMs(nextMs);
  }, [playheadMs, seekTo, setPlayheadMs, totalDurationMs]);

  const togglePlayback = useCallback((mode: "pause" | "forward" | "backward") => {
    const video = videoRef.current;
    if (!video) return;
    if (mode === "pause") {
      video.pause();
      video.playbackRate = 1;
      return;
    }
    if (mode === "backward") {
      seekBy(-1000);
      return;
    }
    video.playbackRate = video.paused ? 1 : Math.min(4, video.playbackRate * 2 || 1);
    void video.play();
  }, [seekBy, videoRef]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const isTyping = ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "");
      if (isTyping) return;
      if (event.key.toLowerCase() === "j") {
        event.preventDefault();
        togglePlayback("backward");
      } else if (event.key.toLowerCase() === "k") {
        event.preventDefault();
        togglePlayback("pause");
      } else if (event.key.toLowerCase() === "l") {
        event.preventDefault();
        togglePlayback("forward");
      } else if (event.key === "[") {
        event.preventDefault();
        if (selectedClip) commitTrim(selectedClip, "left", playheadMs);
      } else if (event.key === "]") {
        event.preventDefault();
        if (selectedClip) commitTrim(selectedClip, "right", playheadMs);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // commitTrim intentionally comes from render scope so bracket trim shortcuts use current clip state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playheadMs, selectedClip, togglePlayback]);

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
          {saving ? "Saving timeline..." : message ?? currentLabel ?? selectedCopy}
        </span>
        <div className="ml-auto flex items-center gap-1 overflow-x-auto">
          {saving ? <Loader2 className="mr-1 size-3 animate-spin text-primary" /> : null}
          <TimelineButton onClick={() => { if (canUndo && !saving) void undo(); }} title="Undo" ariaLabel="Undo" disabled={!canUndo || saving}>
            <Undo2 className="size-3" />
          </TimelineButton>
          <TimelineButton onClick={() => { if (canRedo && !saving) void redo(); }} title="Redo" ariaLabel="Redo" disabled={!canRedo || saving}>
            <Redo2 className="size-3" />
          </TimelineButton>
          <span className="mx-1 h-4 w-px bg-border" />
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

      <MobileTimelineView
        tracks={tracks}
        totalDurationMs={totalDurationMs}
        playheadMs={playheadMs}
        selectedClipId={selectedClipId}
        onClipSelect={onClipSelect}
        seekTo={(ms) => {
          seekTo(ms);
          setPlayheadMs(ms);
        }}
      />

      <div className="hidden min-h-0 flex-1 overflow-hidden md:flex">
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
              <DroppableTrack
                key={track.kind}
                id={`track:${track.kind}`}
                data={{ zone: "timeline", trackKind: track.kind, totalDurationMs }}
                role="list"
                aria-label={`${track.label} timeline track`}
                className={cn(
                  "relative border-b border-border/60",
                  trackIdx % 2 === 0 ? "bg-[hsl(var(--background))]" : "bg-[hsl(var(--surface))]",
                )}
                style={{ height: TRACK_HEIGHT }}
              >
                {track.kind === "video" ? track.clips.slice(0, -1).map((clip, index) => (
                  <TransitionDropZone key={`transition-zone-${clip.id}`} index={index} left={pct(clip.endMs)} />
                )) : null}
                {track.clips.map((clip) => (
                  <ClipBlock
                    key={clip.id}
                    clip={clip}
                    pct={pct}
                    selected={clip.id === selectedClipId}
                    onSelect={onClipSelect}
                    onTrim={commitTrim}
                    onMove={commitRegionMove}
                    onReorder={commitReorder}
                    msFromPointer={msFromPointer}
                    trimDisabled={!["cut", "caption_segment", "broll_slot", "music"].includes(clip.sourceRef.kind)}
                    moveDisabled={!["broll_slot", "music"].includes(clip.sourceRef.kind)}
                    reorderDisabled={!["cut", "caption_segment"].includes(clip.sourceRef.kind)}
                  />
                ))}
              </DroppableTrack>
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

function MobileTimelineView({
  tracks,
  totalDurationMs,
  playheadMs,
  selectedClipId,
  onClipSelect,
  seekTo,
}: {
  tracks: TimelineState["tracks"];
  totalDurationMs: number;
  playheadMs: number;
  selectedClipId: string | null;
  onClipSelect: (clipId: string | null) => void;
  seekTo: (ms: number) => void;
}) {
  const mobileTracks = tracks.filter((track) =>
    ["video", "captions", "broll", "audio"].includes(track.kind),
  );
  const minWidth = Math.max(760, Math.round(totalDurationMs / 60));
  const pct = (ms: number) => `${((ms / Math.max(totalDurationMs, 1)) * 100).toFixed(4)}%`;

  return (
    <div className="min-h-0 flex-1 overflow-hidden md:hidden">
      <div className="h-full overflow-x-auto overflow-y-auto overscroll-x-contain">
        <div className="relative min-h-full" style={{ minWidth }}>
          <button
            type="button"
            className="pixel-grid-fine sticky top-0 z-10 h-12 w-full cursor-pointer border-b border-border bg-background text-left"
            aria-label="Timeline ruler. Tap to seek."
            onPointerDown={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              const nextMs = Math.round(
                ((event.clientX - rect.left) / Math.max(rect.width, 1)) * totalDurationMs,
              );
              seekTo(Math.max(0, Math.min(totalDurationMs, nextMs)));
            }}
          >
            {rulerTicks(totalDurationMs).map((tick) => (
              <span
                key={tick.ms}
                className="absolute top-2 font-mono text-[10px] font-semibold text-muted-foreground"
                style={{ left: pct(tick.ms), transform: "translateX(-50%)" }}
              >
                {tick.label}
              </span>
            ))}
          </button>

          <div
            className="pointer-events-none absolute top-0 z-20 h-full w-px bg-red-500/80"
            style={{ left: pct(playheadMs) }}
          />

          {mobileTracks.map((track) => (
            <div key={track.kind} className="grid grid-cols-[82px_minmax(0,1fr)] border-b border-border bg-surface">
              <div className="sticky left-0 z-10 flex min-h-16 items-center border-r border-border bg-surface px-2">
                <span className="whitespace-nowrap text-[11px] font-black uppercase tracking-wide text-muted-foreground">
                  {track.label}
                </span>
              </div>
              <div className="relative min-h-16 bg-background">
                {track.clips.length === 0 ? (
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-xs font-semibold text-muted-foreground">
                    Empty
                  </span>
                ) : (
                  track.clips.map((clip) => (
                    <button
                      key={clip.id}
                      type="button"
                      aria-pressed={clip.id === selectedClipId}
                      aria-label={`${track.label} clip ${clip.label}. Starts ${formatMs(clip.startMs)}, ends ${formatMs(clip.endMs)}.`}
                      title={`${clip.label} | ${formatMs(clip.startMs)} - ${formatMs(clip.endMs)}`}
                      onClick={() => onClipSelect(clip.id)}
                      className={cn(
                        "absolute top-2 flex min-h-11 min-w-16 items-center overflow-hidden border border-white/20 px-2 text-left text-[11px] font-bold leading-tight text-white shadow-[2px_2px_0_rgba(0,0,0,0.18)]",
                        clip.colorClass,
                        clip.disabled && "opacity-35",
                        clip.id === selectedClipId
                          ? "ring-2 ring-white"
                          : "ring-1 ring-white/10",
                      )}
                      style={{
                        left: pct(clip.startMs),
                        width: `max(4rem, ${pct(Math.max(clip.endMs - clip.startMs, 250))})`,
                      }}
                    >
                      <span className="truncate whitespace-nowrap">{clip.label}</span>
                    </button>
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

interface ClipBlockProps {
  clip: TimelineClip;
  pct: (ms: number) => string;
  selected: boolean;
  onSelect: (id: string) => void;
  onTrim: (clip: TimelineClip, side: "left" | "right", nextMs: number) => void;
  onMove: (clip: TimelineClip, nextStartMs: number) => void;
  onReorder: (clip: TimelineClip, direction: ReorderDirection) => void;
  msFromPointer: (clientX: number) => number;
  trimDisabled: boolean;
  moveDisabled: boolean;
  reorderDisabled: boolean;
}

function DroppableTrack({ id, data, className, children, ...props }: React.HTMLAttributes<HTMLDivElement> & { id: string; data: Record<string, unknown> }) {
  const { setNodeRef, isOver } = useDroppable({ id, data });
  return (
    <div ref={setNodeRef} {...props} className={cn(className, isOver && "bg-primary/10 ring-1 ring-inset ring-primary/70")}>
      {children}
      {isOver ? <span className="pointer-events-none absolute bottom-0 top-0 z-30 w-0.5 bg-primary" style={{ left: "50%" }} /> : null}
    </div>
  );
}

function TransitionDropZone({ index, left }: { index: number; left: string }) {
  const { setNodeRef, isOver } = useDroppable({
    id: `transition:${index}`,
    data: { zone: "transition", afterClipIndex: index },
  });
  return (
    <div ref={setNodeRef} className={cn("absolute bottom-0 top-0 z-20 w-5 -translate-x-1/2", isOver && "bg-primary/30")} style={{ left }} aria-label={`Transition drop zone after clip ${index + 1}`}>
      <span className={cn("absolute bottom-1 top-1 left-1/2 w-px -translate-x-1/2 border-l border-dashed border-cyan-400/50", isOver && "w-1 border-0 bg-primary")} />
    </div>
  );
}

function ClipBlock({
  clip,
  pct,
  selected,
  onSelect,
  onTrim,
  onMove,
  onReorder,
  msFromPointer,
  trimDisabled,
  moveDisabled,
  reorderDisabled,
}: ClipBlockProps) {
  const widthMs = Math.max(clip.endMs - clip.startMs, 0);
  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `clip:${clip.id}`,
    data: { zone: "clip", clip },
  });

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
    const startPointerMs = msFromPointer(event.clientX);
    const startClipMs = clip.startMs;
    const handlePointerUp = (nextEvent: PointerEvent) => {
      window.removeEventListener("pointerup", handlePointerUp);
      const deltaMs = msFromPointer(nextEvent.clientX) - startPointerMs;
      if (Math.abs(deltaMs) < 80) return;
      if (!moveDisabled) {
        onMove(clip, startClipMs + deltaMs);
        return;
      }
      if (!reorderDisabled) {
        if (deltaMs > REORDER_THRESHOLD_MS) {
          onReorder(clip, 1);
        } else if (deltaMs < -REORDER_THRESHOLD_MS) {
          onReorder(clip, -1);
        }
      }
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
    } else if (event.key === "ArrowLeft" && !reorderDisabled && moveDisabled) {
      event.preventDefault();
      onReorder(clip, -1);
    } else if (event.key === "ArrowRight" && !reorderDisabled && moveDisabled) {
      event.preventDefault();
      onReorder(clip, 1);
    }
  }

  return (
    <div
      ref={setDropRef}
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
        isOver && "z-20 ring-2 ring-primary brightness-125",
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
      title={`${clip.label} | ${formatMs(clip.startMs)} - ${formatMs(clip.endMs)}${
        moveDisabled
          ? reorderDisabled
            ? " | Use toolbar actions"
            : " | Drag left/right to reorder"
          : " | Drag to move"
      }`}
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
