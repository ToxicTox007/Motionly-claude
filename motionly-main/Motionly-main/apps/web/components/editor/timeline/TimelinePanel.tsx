"use client";

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useDroppable, useDndContext } from "@dnd-kit/core";
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
import { Button } from "@/components/button";
import { Modal, Tooltip } from "@/components/brutal-ui";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";
import { useToast } from "@/components/toast";
import { formatMs, outputMsToSourceMs, rulerTicks } from "@/lib/timeline";
import { editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useTimeline } from "./useTimeline";
import { usePlaybackSync } from "./usePlaybackSync";
import { useTimelineKeyboardShortcuts } from "./useTimelineKeyboardShortcuts";
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
  activePlan: EditPlanRecordSummary | null;
  captionTrack: CaptionTrackSummary | null;
  musicSelection: MusicSelectionSummary | null;
  brollSlots: BrollSlotSummary[];
  onPlanUpdated: (plan: EditPlanRecordSummary) => void;
  onCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  onBrollSlotUpdated: (slot: BrollSlotSummary) => void;
  onMusicSelectionUpdated: (selection: MusicSelectionSummary) => void;
  getExpectedRevision?: () => number | undefined;
  onConflictDetected?: (error: unknown, source: string) => boolean;
}

type ReorderDirection = -1 | 1;

const TRACK_HEIGHT = 56;
const RULER_HEIGHT = 36;
const LABEL_WIDTH = 112;
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
  activePlan,
  captionTrack,
  musicSelection,
  brollSlots,
  onPlanUpdated,
  onCaptionTrackUpdated,
  onBrollSlotUpdated,
  onMusicSelectionUpdated,
  getExpectedRevision,
  onConflictDetected,
}: TimelinePanelProps) {
  const { toast } = useToast();
  const { totalDurationMs, tracks } = state;
  const timelineClips = activePlan?.plan.timeline?.clips;
  const sourceClips = useMemo(() => timelineClips ?? [], [timelineClips]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { pushHistory, undo, redo, canUndo, canRedo, currentLabel } =
    useEditorHistory();

  const [snapMarkerMs, setSnapMarkerMs] = useState<number | null>(null);
  const [dragPreview, setDragPreview] = useState<{
    clipId: string;
    startMs: number;
    endMs: number;
  } | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const deleteModalRef = useFocusTrap(deleteConfirmOpen, () =>
    setDeleteConfirmOpen(false),
  );
  const [visibleRange, setVisibleRange] = useState<{
    startMs: number;
    endMs: number;
  }>({
    startMs: 0,
    endMs: 10000,
  });
  const requestQueue = useRef<Promise<any>>(Promise.resolve());

  const getSnappedMs = useCallback(
    (targetMs: number, ignoreClipId?: string) => {
      const snapThresholdMs = 150;
      let closestMs = targetMs;
      let minDiff = snapThresholdMs;

      const playheadDiff = Math.abs(targetMs - playheadMs);
      if (playheadDiff < minDiff) {
        minDiff = playheadDiff;
        closestMs = playheadMs;
      }

      for (const track of tracks) {
        for (const c of track.clips) {
          if (c.id === ignoreClipId) continue;

          const startDiff = Math.abs(targetMs - c.startMs);
          if (startDiff < minDiff) {
            minDiff = startDiff;
            closestMs = c.startMs;
          }

          const endDiff = Math.abs(targetMs - c.endMs);
          if (endDiff < minDiff) {
            minDiff = endDiff;
            closestMs = c.endMs;
          }
        }
      }

      return closestMs;
    },
    [playheadMs, tracks],
  );

  const { zoomLevel, zoomIn, zoomOut, fitToContent, seekTo } = useTimeline({
    totalDurationMs,
    videoRef,
    sourceClips,
  });

  useEffect(() => {
    const scrollEl = scrollRef.current;
    const canvasEl = canvasRef.current;
    if (!scrollEl || !canvasEl) return;

    const updateRange = () => {
      const scrollLeft = scrollEl.scrollLeft;
      const clientWidth = scrollEl.clientWidth;
      const totalWidth = canvasEl.clientWidth || 1;

      const pxToMs = totalDurationMs / totalWidth;
      const startMs = scrollLeft * pxToMs;
      const endMs = (scrollLeft + clientWidth) * pxToMs;

      const bufferMs = Math.max(2000, clientWidth * pxToMs * 0.25);

      setVisibleRange({
        startMs: Math.max(0, startMs - bufferMs),
        endMs: Math.min(totalDurationMs, endMs + bufferMs),
      });
    };

    updateRange();

    let frameId: number | null = null;
    const onScroll = () => {
      if (frameId !== null) return;
      frameId = requestAnimationFrame(() => {
        updateRange();
        frameId = null;
      });
    };

    scrollEl.addEventListener("scroll", onScroll);
    window.addEventListener("resize", onScroll);

    return () => {
      if (frameId !== null) cancelAnimationFrame(frameId);
      scrollEl.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [totalDurationMs, zoomLevel]);

  const isClipVisible = useCallback(
    (clip: TimelineClip) => {
      if (clip.id === selectedClipId) return true;
      return (
        clip.endMs >= visibleRange.startMs && clip.startMs <= visibleRange.endMs
      );
    },
    [selectedClipId, visibleRange],
  );

  usePlaybackSync(videoRef, setPlayheadMs, sourceClips, playheadMs);

  const selectedClip = useMemo(
    () =>
      tracks
        .flatMap((track) => track.clips)
        .find((clip) => clip.id === selectedClipId) ?? null,
    [selectedClipId, tracks],
  );

  const pct = useCallback(
    (ms: number) =>
      `${((ms / Math.max(totalDurationMs, 1)) * 100).toFixed(4)}%`,
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

  const request = useCallback(
    async (path: string, body: object) => {
      const expectedRev = getExpectedRevision?.();
      return editorApiRequest<Record<string, any>>(`${env.apiBaseUrl}${path}`, {
        method: "PATCH",
        workspaceId,
        friendlyError: "Timeline changes could not be saved. Please try again.",
        body: {
          ...(body as Record<string, unknown>),
          ...(expectedRev != null ? { expected_revision: expectedRev } : {}),
        },
      });
    },
    [getExpectedRevision, workspaceId],
  );

  const saveCuts = useCallback(
    async (cuts: EditPlanCut[]) => {
      if (!activePlan) throw new Error("Generate an AI edit first.");
      const payload = await request(
        `/projects/${projectId}/plans/${activePlan.id}/cuts`,
        { cuts },
      );
      onPlanUpdated(payload.plan);
      return "Video cuts saved.";
    },
    [activePlan, onPlanUpdated, projectId, request],
  );

  const saveCaptionSegments = useCallback(
    async (segments: CaptionSegment[]) => {
      if (!captionTrack) throw new Error("Generate captions first.");
      const payload = await request(
        `/projects/${projectId}/captions/${captionTrack.id}/segments`,
        {
          segments,
        },
      );
      onCaptionTrackUpdated(payload.track);
      return "Caption timing saved.";
    },
    [captionTrack, onCaptionTrackUpdated, projectId, request],
  );

  const saveBrollRegion = useCallback(
    async (
      slotId: string,
      startMs: number,
      endMs: number,
      disabled?: boolean,
    ) => {
      const payload = await request(
        `/projects/${projectId}/broll/${slotId}/region`,
        {
          start_ms: outputMsToSourceMs(startMs, sourceClips),
          end_ms: outputMsToSourceMs(endMs, sourceClips, "end"),
          disabled,
        },
      );
      onBrollSlotUpdated(payload.slot);
      return disabled ? "B-roll slot disabled." : "B-roll timing saved.";
    },
    [onBrollSlotUpdated, projectId, request, sourceClips],
  );

  const saveMusicRegions = useCallback(
    async (regions: MusicRegion[]) => {
      if (!musicSelection) throw new Error("Select music first.");
      const payload = (await request(`/projects/${projectId}/music/regions`, {
        regions,
      })) as MusicSelectionSummary;
      onMusicSelectionUpdated(payload);
      return "Music regions saved.";
    },
    [musicSelection, onMusicSelectionUpdated, projectId, request],
  );

  const runTimelineAction = useCallback(
    async (
      action: () => Promise<string>,
      historyEntry?: HistoryEntry,
      rollback?: () => void,
    ) => {
      setSaving(true);
      setMessage(null);

      requestQueue.current = requestQueue.current
        .then(async () => {
          try {
            const nextMessage = await action();
            if (historyEntry) pushHistory(historyEntry);
            setMessage(nextMessage);
          } catch (error) {
            if (onConflictDetected?.(error, "timeline")) return;
            console.error(error);
            const errMsg = (error as Error).message;
            setMessage(errMsg);
            if (rollback) rollback();
            toast({
              title: "Save Failed",
              description:
                "Your timeline changes could not be saved to the server. Reverted to last saved state.",
              variant: "error",
            });
          }
        })
        .finally(() => {
          setSaving(false);
        });

      await requestQueue.current;
    },
    [onConflictDetected, pushHistory, toast],
  );

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

    // Optimistic Update
    if (activePlan) {
      onPlanUpdated({
        ...activePlan,
        plan: {
          ...activePlan.plan,
          cuts: after,
        },
      });
    }

    runTimelineAction(
      () => saveCuts(after),
      {
        id: `cuts-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: () => saveCuts(before),
        redo: () => saveCuts(after),
        undoAction: historyAction(
          `/projects/${projectId}/plans/${activePlan!.id}/cuts`,
          { cuts: before },
          "plan",
          "Video cuts restored.",
        ),
        redoAction: historyAction(
          `/projects/${projectId}/plans/${activePlan!.id}/cuts`,
          { cuts: after },
          "plan",
          "Video cuts reapplied.",
        ),
      },
      () => {
        // Rollback
        if (activePlan) {
          onPlanUpdated({
            ...activePlan,
            plan: {
              ...activePlan.plan,
              cuts: before,
            },
          });
        }
      },
    );
  }

  function commitCaptionSegments(
    nextSegments: CaptionSegment[],
    label: string,
  ) {
    const before = cloneSegments(captionTrack?.segments ?? []);
    const after = cloneSegments(nextSegments);

    // Optimistic Update
    if (captionTrack) {
      onCaptionTrackUpdated({
        ...captionTrack,
        segments: after,
      });
    }

    runTimelineAction(
      () => saveCaptionSegments(after),
      {
        id: `captions-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: () => saveCaptionSegments(before),
        redo: () => saveCaptionSegments(after),
        undoAction: historyAction(
          `/projects/${projectId}/captions/${captionTrack!.id}/segments`,
          { segments: before },
          "caption",
          "Caption timing restored.",
        ),
        redoAction: historyAction(
          `/projects/${projectId}/captions/${captionTrack!.id}/segments`,
          { segments: after },
          "caption",
          "Caption timing reapplied.",
        ),
      },
      () => {
        // Rollback
        if (captionTrack) {
          onCaptionTrackUpdated({
            ...captionTrack,
            segments: before,
          });
        }
      },
    );
  }

  function commitBrollRegion(
    slotId: string,
    before: BrollRegionSnapshot,
    after: BrollRegionSnapshot,
    label: string,
  ) {
    const currentSlot = brollSlots.find((s) => s.id === slotId);
    const optimisticSlot = currentSlot
      ? {
          ...currentSlot,
          start_ms: outputMsToSourceMs(after.startMs, sourceClips),
          end_ms: outputMsToSourceMs(after.endMs, sourceClips, "end"),
          disabled: after.disabled,
        }
      : {
          id: slotId,
          start_ms: outputMsToSourceMs(after.startMs, sourceClips),
          end_ms: outputMsToSourceMs(after.endMs, sourceClips, "end"),
          disabled: after.disabled,
        };

    // Optimistic Update
    onBrollSlotUpdated(optimisticSlot as BrollSlotSummary);

    runTimelineAction(
      () => saveBrollRegion(slotId, after.startMs, after.endMs, after.disabled),
      {
        id: `broll-${slotId}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: () =>
          saveBrollRegion(
            slotId,
            before.startMs,
            before.endMs,
            before.disabled,
          ),
        redo: () =>
          saveBrollRegion(slotId, after.startMs, after.endMs, after.disabled),
        undoAction: historyAction(
          `/projects/${projectId}/broll/${slotId}/region`,
          brollHistoryBody(before, sourceClips),
          "broll",
          "B-roll timing restored.",
        ),
        redoAction: historyAction(
          `/projects/${projectId}/broll/${slotId}/region`,
          brollHistoryBody(after, sourceClips),
          "broll",
          "B-roll timing reapplied.",
        ),
      },
      () => {
        // Rollback
        const originalSlot = currentSlot
          ? {
              ...currentSlot,
              start_ms: outputMsToSourceMs(before.startMs, sourceClips),
              end_ms: outputMsToSourceMs(before.endMs, sourceClips, "end"),
              disabled: before.disabled,
            }
          : {
              id: slotId,
              start_ms: outputMsToSourceMs(before.startMs, sourceClips),
              end_ms: outputMsToSourceMs(before.endMs, sourceClips, "end"),
              disabled: before.disabled,
            };
        onBrollSlotUpdated(originalSlot as BrollSlotSummary);
      },
    );
  }

  function cloneMusicRegions(regions: MusicRegion[]) {
    return regions.map((region) => ({ ...region }));
  }

  function commitMusicRegions(nextRegions: MusicRegion[], label: string) {
    const before = cloneMusicRegions(musicSelection?.regions ?? []);
    const after = cloneMusicRegions(nextRegions);

    // Optimistic Update
    if (musicSelection) {
      onMusicSelectionUpdated({
        ...musicSelection,
        regions: after,
      });
    }

    runTimelineAction(
      () => saveMusicRegions(after),
      {
        id: `music-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: () => saveMusicRegions(before),
        redo: () => saveMusicRegions(after),
        undoAction: historyAction(
          `/projects/${projectId}/music/regions`,
          { regions: before },
          "music",
          "Music regions restored.",
        ),
        redoAction: historyAction(
          `/projects/${projectId}/music/regions`,
          { regions: after },
          "music",
          "Music regions reapplied.",
        ),
      },
      () => {
        // Rollback
        if (musicSelection) {
          onMusicSelectionUpdated({
            ...musicSelection,
            regions: before,
          });
        }
      },
    );
  }

  function cutsForClipUpdate(
    clip: TimelineClip,
    startMs: number,
    endMs: number,
  ) {
    const cuts = [...(activePlan?.plan.cuts ?? [])];
    const index = clip.sourceRef.index ?? -1;
    if (index < 0 || !cuts[index]) return null;
    const timelineClip = sourceClips[index];
    cuts[index] = {
      ...cuts[index],
      start_ms: timelineClip
        ? timelineClip.source_start_ms + startMs - clip.startMs
        : startMs,
      end_ms: timelineClip
        ? timelineClip.source_end_ms - (clip.endMs - endMs)
        : endMs,
    };
    return cuts;
  }

  function captionSegmentsForClipUpdate(
    clip: TimelineClip,
    startMs: number,
    endMs: number,
  ) {
    const segments = [...(captionTrack?.segments ?? [])];
    const index = captionSegmentIndex(clip, segments);
    if (index < 0) return null;
    segments[index] = {
      ...segments[index],
      start_ms: outputMsToSourceMs(startMs, sourceClips),
      end_ms: outputMsToSourceMs(endMs, sourceClips, "end"),
    };
    return segments;
  }

  function musicRegionsWithUpdate(
    regionId: string,
    startMs: number,
    endMs: number,
  ) {
    const regions = [...(musicSelection?.regions ?? [])];
    const index = regions.findIndex((region) => region.id === regionId);
    if (index < 0) return null;
    regions[index] = { ...regions[index], start_ms: startMs, end_ms: endMs };
    return regions;
  }

  function commitTrim(
    clip: TimelineClip,
    side: "left" | "right",
    nextMs: number,
  ) {
    const nextStart =
      side === "left"
        ? Math.min(nextMs, clip.endMs - MIN_CLIP_MS)
        : clip.startMs;
    const nextEnd =
      side === "right"
        ? Math.max(nextMs, clip.startMs + MIN_CLIP_MS)
        : clip.endMs;
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
      const regions = musicRegionsWithUpdate(
        clip.sourceRef.regionId,
        nextStart,
        nextEnd,
      );
      if (regions) commitMusicRegions(regions, `Trim ${clip.label}`);
    }
  }

  function commitRegionMove(clip: TimelineClip, nextStartMs: number) {
    const duration = clip.endMs - clip.startMs;
    const startMs = Math.max(
      0,
      Math.min(totalDurationMs - duration, nextStartMs),
    );
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
      const regions = musicRegionsWithUpdate(
        clip.sourceRef.regionId,
        startMs,
        startMs + duration,
      );
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
      [segments[index], segments[nextIndex]] = [
        segments[nextIndex],
        segments[index],
      ];
      commitCaptionSegments(segments, `Reorder ${clip.label}`);
      return;
    }
    setMessage("Cannot reorder this clip type yet.");
  }

  function splitSelectedClip() {
    if (!selectedClip) return;
    if (
      playheadMs <= selectedClip.startMs + MIN_CLIP_MS ||
      playheadMs >= selectedClip.endMs - MIN_CLIP_MS
    ) {
      setMessage("Move the playhead inside the selected clip to split.");
      return;
    }
    if (
      selectedClip.sourceRef.kind === "music" &&
      selectedClip.sourceRef.regionId
    ) {
      const regions = [...(musicSelection?.regions ?? [])];
      const index = regions.findIndex(
        (region) => region.id === selectedClip.sourceRef.regionId,
      );
      const current = regions[index];
      if (!current) {
        setMessage("Select a music region.");
        return;
      }
      regions.splice(
        index,
        1,
        { ...current, end_ms: playheadMs },
        {
          ...current,
          id: `${current.id}-split-${Date.now()}`,
          start_ms: playheadMs,
        },
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
      const splitSourceMs = outputMsToSourceMs(playheadMs, sourceClips);
      cuts.splice(
        index,
        1,
        { ...current, end_ms: splitSourceMs },
        {
          ...current,
          start_ms: splitSourceMs,
          reason: `${current.reason} split`,
        },
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
      const splitSourceMs = outputMsToSourceMs(playheadMs, sourceClips);
      segments.splice(
        index,
        1,
        { ...current, end_ms: splitSourceMs },
        { ...current, id: undefined, start_ms: splitSourceMs },
      );
      commitCaptionSegments(segments, `Split ${selectedClip.label}`);
      return;
    }
    setMessage("Cannot split this clip yet.");
  }

  function requestDeleteSelectedClip() {
    if (!selectedClip) return;
    setDeleteConfirmOpen(true);
  }

  function deleteSelectedClip() {
    if (!selectedClip) return;
    setDeleteConfirmOpen(false);
    if (
      selectedClip.sourceRef.kind === "music" &&
      selectedClip.sourceRef.regionId
    ) {
      const regions = [...(musicSelection?.regions ?? [])];
      const index = regions.findIndex(
        (region) => region.id === selectedClip.sourceRef.regionId,
      );
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
    if (
      selectedClip.sourceRef.kind === "broll_slot" &&
      selectedClip.sourceRef.id
    ) {
      onClipSelect(null);
      commitBrollRegion(
        selectedClip.sourceRef.id,
        { startMs: selectedClip.startMs, endMs: selectedClip.endMs },
        {
          startMs: selectedClip.startMs,
          endMs: selectedClip.endMs,
          disabled: true,
        },
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
      [segments[index], segments[nextIndex]] = [
        segments[nextIndex],
        segments[index],
      ];
      commitCaptionSegments(segments, `Reorder ${selectedClip.label}`);
      return;
    }
    if (
      selectedClip.sourceRef.kind === "broll_slot" &&
      selectedClip.sourceRef.id
    ) {
      const duration = selectedClip.endMs - selectedClip.startMs;
      const nextStart = Math.max(
        0,
        Math.min(
          totalDurationMs - duration,
          selectedClip.startMs + direction * NUDGE_MS,
        ),
      );
      commitBrollRegion(
        selectedClip.sourceRef.id,
        { startMs: selectedClip.startMs, endMs: selectedClip.endMs },
        { startMs: nextStart, endMs: nextStart + duration },
        `Nudge ${selectedClip.label}`,
      );
      return;
    }
    if (
      selectedClip.sourceRef.kind === "music" &&
      selectedClip.sourceRef.regionId
    ) {
      const duration = selectedClip.endMs - selectedClip.startMs;
      const nextStart = Math.max(
        0,
        Math.min(
          totalDurationMs - duration,
          selectedClip.startMs + direction * NUDGE_MS,
        ),
      );
      const regions = musicRegionsWithUpdate(
        selectedClip.sourceRef.regionId,
        nextStart,
        nextStart + duration,
      );
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
      const segments = captionSegmentsForClipUpdate(
        selectedClip,
        startMs,
        endMs,
      );
      if (!segments) {
        setMessage("Select a caption segment.");
        return;
      }
      commitCaptionSegments(segments, `Retiming ${selectedClip.label}`);
      return;
    }
    if (
      selectedClip.sourceRef.kind === "broll_slot" &&
      selectedClip.sourceRef.id
    ) {
      commitBrollRegion(
        selectedClip.sourceRef.id,
        { startMs: selectedClip.startMs, endMs: selectedClip.endMs },
        { startMs, endMs },
        `Retiming ${selectedClip.label}`,
      );
      return;
    }
    if (
      selectedClip.sourceRef.kind === "music" &&
      selectedClip.sourceRef.regionId
    ) {
      const regions = musicRegionsWithUpdate(
        selectedClip.sourceRef.regionId,
        startMs,
        endMs,
      );
      if (regions)
        commitMusicRegions(regions, `Retiming ${selectedClip.label}`);
      return;
    }
    setMessage("Timing for this clip is edited in Audio.");
  }

  const seekBy = useCallback(
    (deltaMs: number) => {
      const nextMs = Math.max(
        0,
        Math.min(totalDurationMs, playheadMs + deltaMs),
      );
      seekTo(nextMs);
      setPlayheadMs(nextMs);
    },
    [playheadMs, seekTo, setPlayheadMs, totalDurationMs],
  );

  const togglePlayback = useCallback(
    (mode: "pause" | "forward" | "backward") => {
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
      video.playbackRate = video.paused
        ? 1
        : Math.min(4, video.playbackRate * 2 || 1);
      void video.play();
    },
    [seekBy, videoRef],
  );

  useTimelineKeyboardShortcuts({
    playheadMs,
    selectedClip,
    togglePlayback,
    trimSelectedClip: commitTrim,
  });

  const ticks = useMemo(() => rulerTicks(totalDurationMs), [totalDurationMs]);
  const selectedCopy = selectedClip
    ? `${selectedClip.label} ${formatMs(selectedClip.startMs)}-${formatMs(selectedClip.endMs)}`
    : "Select a clip";

  return (
    <div className="flex h-full min-h-0 select-none flex-col bg-surface">
      <h2 id="timeline-heading" className="sr-only">
        Timeline
      </h2>
      <div className="pixel-grid-fine flex min-h-14 shrink-0 items-center gap-2 border-b-2 border-foreground bg-background px-2 sm:px-3">
        <span
          className="rounded border-2 border-foreground bg-surface px-2 py-1 font-mono text-xs font-black text-primary tabular-nums shadow-brutal-sm"
          aria-live="polite"
        >
          {formatMs(playheadMs)} <span className="opacity-40">/</span>{" "}
          {formatMs(totalDurationMs)}
        </span>
        <span className="hidden truncate text-[11px] font-black uppercase text-muted-foreground sm:block">
          {saving
            ? "Saving timeline..."
            : (message ?? currentLabel ?? selectedCopy)}
        </span>
        <div className="ml-auto flex items-center gap-1 overflow-x-auto">
          {saving ? (
            <Loader2 className="mr-1 size-3 animate-spin text-primary" />
          ) : null}
          <TimelineButton
            onClick={() => {
              if (canUndo && !saving) void undo();
            }}
            title="Undo"
            ariaLabel="Undo"
            disabled={!canUndo || saving}
          >
            <Undo2 className="size-3" />
          </TimelineButton>
          <TimelineButton
            onClick={() => {
              if (canRedo && !saving) void redo();
            }}
            title="Redo"
            ariaLabel="Redo"
            disabled={!canRedo || saving}
          >
            <Redo2 className="size-3" />
          </TimelineButton>
          <span className="mx-1 h-8 w-0.5 bg-foreground" />
          <TimelineButton
            onClick={splitSelectedClip}
            title={
              selectedClip ? "Split selected clip at playhead" : "Select a clip"
            }
            ariaLabel="Split selected clip at playhead"
            disabled={!selectedClip || saving}
          >
            <Scissors className="size-3" />
          </TimelineButton>
          <TimelineButton
            onClick={requestDeleteSelectedClip}
            title={selectedClip ? "Delete selected clip" : "Select a clip"}
            ariaLabel="Delete selected clip"
            disabled={!selectedClip || saving}
          >
            <Trash2 className="size-3" />
          </TimelineButton>
          <TimelineButton
            onClick={() => moveSelectedClip(-1)}
            title={selectedClip ? "Move left" : "Select a clip"}
            ariaLabel="Move selected clip earlier"
            disabled={!selectedClip || saving}
          >
            <ArrowLeft className="size-3" />
          </TimelineButton>
          <TimelineButton
            onClick={() => moveSelectedClip(1)}
            title={selectedClip ? "Move right" : "Select a clip"}
            ariaLabel="Move selected clip later"
            disabled={!selectedClip || saving}
          >
            <ArrowRight className="size-3" />
          </TimelineButton>
          <span className="mx-1 h-8 w-0.5 bg-foreground" />
          <TimelineButton
            onClick={zoomOut}
            title="Zoom out"
            ariaLabel="Zoom timeline out"
          >
            <Minus className="size-3" />
          </TimelineButton>
          <TimelineButton
            onClick={fitToContent}
            title="Fit to content"
            ariaLabel="Fit timeline to content"
          >
            <Maximize2 className="size-3" />
          </TimelineButton>
          <TimelineButton
            onClick={zoomIn}
            title="Zoom in"
            ariaLabel="Zoom timeline in"
          >
            <ZoomIn className="size-3" />
          </TimelineButton>
          {zoomLevel !== 1 && (
            <span className="ml-1 rounded border-2 border-foreground bg-primary px-1.5 py-1 font-mono text-[9px] font-black text-primary-foreground tabular-nums">
              {zoomLevel.toFixed(1)}x
            </span>
          )}
        </div>
      </div>
      {selectedClip ? (
        <form
          className="flex flex-wrap items-end gap-2 border-b-2 border-foreground bg-surface-elevated p-2"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            updateSelectedTiming(
              Number(form.get("start_ms")),
              Number(form.get("end_ms")),
            );
          }}
        >
          <span className="self-center rounded border-2 border-foreground bg-background px-2 py-1 font-mono text-[10px] font-black uppercase text-primary shadow-brutal-sm">
            Selected
          </span>
          <label className="text-sm font-black uppercase text-muted-foreground">
            Start ms
            <input
              name="start_ms"
              type="number"
              defaultValue={selectedClip.startMs}
              className="pixel-input ml-2 min-h-11 w-28 px-2"
            />
          </label>
          <label className="text-sm font-black uppercase text-muted-foreground">
            End ms
            <input
              name="end_ms"
              type="number"
              defaultValue={selectedClip.endMs}
              className="pixel-input ml-2 min-h-11 w-28 px-2"
            />
          </label>
          <span className="rounded border-2 border-foreground bg-background px-2 py-2 font-mono text-xs font-black uppercase text-muted-foreground">
            Duration {formatMs(selectedClip.endMs - selectedClip.startMs)}
          </span>
          <ButtonLike type="submit" disabled={saving}>
            Save Timing
          </ButtonLike>
        </form>
      ) : null}

      <TimelineMinimap
        tracks={tracks}
        totalDurationMs={totalDurationMs}
        playheadMs={playheadMs}
        pct={pct}
        onSeek={(ms) => {
          seekTo(ms);
          setPlayheadMs(ms);
        }}
      />

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
          className="flex flex-shrink-0 flex-col border-r-2 border-foreground bg-surface"
          style={{ width: LABEL_WIDTH }}
        >
          <div
            className="border-b-2 border-foreground bg-background"
            style={{ height: RULER_HEIGHT }}
          />
          {tracks.map((track) => (
            <div
              key={track.kind}
              className={cn(
                "flex items-center justify-between gap-2 border-b border-foreground/40 px-2",
                trackToneClass(track.kind),
              )}
              style={{ height: TRACK_HEIGHT }}
            >
              <PixelIcon name={trackIconName(track.kind)} className="size-4" />
              <span className="truncate font-mono text-[10px] font-black uppercase">
                {track.label}
              </span>
            </div>
          ))}
        </div>

        <div
          className="relative flex-1 overflow-x-auto overflow-y-hidden"
          ref={scrollRef}
        >
          <div
            ref={canvasRef}
            className="relative"
            style={{
              width: `${Math.round(zoomLevel * 100)}%`,
              minWidth: "100%",
            }}
            onClick={handleTrackAreaClick}
          >
            <TimelineRuler
              ticks={ticks}
              pct={pct}
              onPointerDown={handleRulerPointerDown}
            />

            {tracks.map((track, trackIdx) => (
              <DroppableTrack
                key={track.kind}
                id={`track:${track.kind}`}
                data={{
                  zone: "timeline",
                  trackKind: track.kind,
                  totalDurationMs,
                }}
                role="list"
                aria-label={`${track.label} timeline track`}
                className={cn(
                  "relative border-b border-foreground/50",
                  trackIdx % 2 === 0
                    ? "bg-background"
                    : "bg-surface-elevated/60",
                )}
                style={{ height: TRACK_HEIGHT }}
              >
                {track.kind === "video"
                  ? track.clips
                      .slice(0, -1)
                      .map((clip, index) => (
                        <TransitionDropZone
                          key={`transition-zone-${clip.id}`}
                          index={index}
                          left={pct(clip.endMs)}
                        />
                      ))
                  : null}
                {track.clips.map((clip) =>
                  isClipVisible(clip) ? (
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
                      trimDisabled={
                        ![
                          "cut",
                          "caption_segment",
                          "broll_slot",
                          "music",
                        ].includes(clip.sourceRef.kind)
                      }
                      moveDisabled={
                        !["broll_slot", "music"].includes(clip.sourceRef.kind)
                      }
                      reorderDisabled={
                        !["cut", "caption_segment"].includes(
                          clip.sourceRef.kind,
                        )
                      }
                      getSnappedMs={getSnappedMs}
                      setSnapMarkerMs={setSnapMarkerMs}
                      setDragPreview={setDragPreview}
                    />
                  ) : null,
                )}
              </DroppableTrack>
            ))}

            {snapMarkerMs !== null && (
              <div
                className="pointer-events-none absolute bottom-0 top-0 z-30 border-l-4 border-dashed border-primary"
                style={{ left: pct(snapMarkerMs) }}
              >
                <span className="absolute left-2 top-1 rounded border-2 border-foreground bg-primary px-1.5 py-0.5 font-mono text-[9px] font-black uppercase text-primary-foreground shadow-brutal-sm">
                  Snap {formatMs(snapMarkerMs)}
                </span>
              </div>
            )}

            {dragPreview ? (
              <div
                className="pointer-events-none absolute z-40 rounded border-2 border-foreground bg-primary/35 shadow-brutal-sm"
                style={{
                  left: pct(dragPreview.startMs),
                  top: RULER_HEIGHT,
                  height: tracks.length * TRACK_HEIGHT,
                  width: `max(3rem, ${pct(
                    Math.max(dragPreview.endMs - dragPreview.startMs, 250),
                  )})`,
                }}
              />
            ) : null}

            <Playhead
              pct={pct(playheadMs)}
              totalHeight={RULER_HEIGHT + tracks.length * TRACK_HEIGHT}
            />
          </div>
        </div>
      </div>
      <Modal
        open={deleteConfirmOpen}
        title="Delete clip"
        description={
          selectedClip
            ? `${selectedClip.label} will be removed from this timeline track.`
            : "Select a clip to delete."
        }
        onClose={() => setDeleteConfirmOpen(false)}
        contentRef={deleteModalRef}
        footer={
          <>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setDeleteConfirmOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={!selectedClip || saving}
              onClick={deleteSelectedClip}
            >
              Delete clip
            </Button>
          </>
        }
      >
        <p className="text-sm font-bold text-muted-foreground">
          This uses the existing timeline save endpoint and can be restored
          through timeline history when the server save succeeds.
        </p>
      </Modal>
    </div>
  );
}

function historyAction(
  path: string,
  body: Record<string, unknown>,
  resultKind: "plan" | "caption" | "broll" | "music",
  successLabel: string,
) {
  return { path, body, resultKind, successLabel } as const;
}

function brollHistoryBody(
  snapshot: BrollRegionSnapshot,
  sourceClips: NonNullable<EditPlanRecordSummary["plan"]["timeline"]>["clips"],
) {
  return {
    start_ms: outputMsToSourceMs(snapshot.startMs, sourceClips),
    end_ms: outputMsToSourceMs(snapshot.endMs, sourceClips, "end"),
    disabled: snapshot.disabled,
  };
}

function captionSegmentIndex(clip: TimelineClip, segments: CaptionSegment[]) {
  if (clip.sourceRef.id) {
    const byId = segments.findIndex(
      (segment) => segment.id === clip.sourceRef.id,
    );
    if (byId >= 0) return byId;
  }
  return clip.sourceRef.index ?? -1;
}

function trackIconName(kind: string): PixelIconName {
  if (kind === "captions") return "captions";
  if (kind === "broll") return "broll";
  if (kind === "audio" || kind === "music") return "music";
  return "play";
}

function trackToneClass(kind: string) {
  if (kind === "captions") return "bg-accent text-accent-foreground";
  if (kind === "broll") return "bg-info text-ink";
  if (kind === "audio" || kind === "music") return "bg-success text-white";
  return "bg-primary text-primary-foreground";
}

const TimelineMinimap = React.memo(function TimelineMinimap({
  tracks,
  totalDurationMs,
  playheadMs,
  pct,
  onSeek,
}: {
  tracks: TimelineState["tracks"];
  totalDurationMs: number;
  playheadMs: number;
  pct: (ms: number) => string;
  onSeek: (ms: number) => void;
}) {
  const clips = tracks.flatMap((track) =>
    track.clips.map((clip) => ({ clip, trackKind: track.kind })),
  );

  return (
    <button
      type="button"
      aria-label="Timeline overview. Click to seek."
      className="relative hidden h-10 shrink-0 border-b-2 border-foreground bg-background text-left md:block"
      onPointerDown={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const nextMs = Math.round(
          ((event.clientX - rect.left) / Math.max(rect.width, 1)) *
            totalDurationMs,
        );
        onSeek(Math.max(0, Math.min(totalDurationMs, nextMs)));
      }}
    >
      <span className="absolute left-2 top-1/2 z-10 -translate-y-1/2 rounded border-2 border-foreground bg-surface px-2 py-0.5 font-mono text-[9px] font-black uppercase text-muted-foreground">
        Map
      </span>
      <div className="absolute inset-y-2 left-[4.5rem] right-3 border-2 border-foreground bg-muted">
        {clips.map(({ clip }, index) => (
          <span
            key={`${clip.id}-${index}`}
            className={cn(
              "absolute top-0 h-full border-r border-foreground/40 opacity-80",
              clip.colorClass,
              clip.disabled && "opacity-20",
            )}
            style={{
              left: pct(clip.startMs),
              width: `max(2px, ${pct(Math.max(clip.endMs - clip.startMs, 250))})`,
            }}
          />
        ))}
        <span
          className="absolute bottom-[-4px] top-[-4px] z-20 w-1 bg-danger"
          style={{ left: pct(playheadMs), transform: "translateX(-50%)" }}
        />
      </div>
    </button>
  );
});

const MobileTimelineView = React.memo(
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
    const pct = (ms: number) =>
      `${((ms / Math.max(totalDurationMs, 1)) * 100).toFixed(4)}%`;
    const scrollRef = React.useRef<HTMLDivElement | null>(null);
    const [visibleRange, setVisibleRange] = React.useState({
      startMs: 0,
      endMs: Math.min(totalDurationMs, 10000),
    });
    const isMobileClipVisible = React.useCallback(
      (clip: TimelineClip) =>
        clip.id === selectedClipId ||
        (clip.endMs >= visibleRange.startMs &&
          clip.startMs <= visibleRange.endMs),
      [selectedClipId, visibleRange],
    );

    React.useEffect(() => {
      const scrollEl = scrollRef.current;
      if (!scrollEl) return;

      const updateRange = () => {
        const pxToMs = totalDurationMs / Math.max(minWidth, 1);
        const startMs = scrollEl.scrollLeft * pxToMs;
        const endMs = (scrollEl.scrollLeft + scrollEl.clientWidth) * pxToMs;
        const bufferMs = Math.max(2000, scrollEl.clientWidth * pxToMs * 0.35);
        setVisibleRange({
          startMs: Math.max(0, startMs - bufferMs),
          endMs: Math.min(totalDurationMs, endMs + bufferMs),
        });
      };

      updateRange();
      let frameId: number | null = null;
      const onScroll = () => {
        if (frameId !== null) return;
        frameId = window.requestAnimationFrame(() => {
          updateRange();
          frameId = null;
        });
      };

      scrollEl.addEventListener("scroll", onScroll);
      window.addEventListener("resize", onScroll);
      return () => {
        if (frameId !== null) window.cancelAnimationFrame(frameId);
        scrollEl.removeEventListener("scroll", onScroll);
        window.removeEventListener("resize", onScroll);
      };
    }, [minWidth, totalDurationMs]);

    return (
      <div className="min-h-0 flex-1 overflow-hidden md:hidden">
        <div
          ref={scrollRef}
          className="h-full overflow-x-auto overflow-y-auto overscroll-x-contain"
        >
          <div className="relative min-h-full" style={{ minWidth }}>
            <button
              type="button"
              className="pixel-grid-fine sticky top-0 z-10 h-12 w-full cursor-pointer border-b border-border bg-background text-left"
              aria-label="Timeline ruler. Tap to seek."
              onPointerDown={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                const nextMs = Math.round(
                  ((event.clientX - rect.left) / Math.max(rect.width, 1)) *
                    totalDurationMs,
                );
                seekTo(nextMs);
              }}
            >
              <div
                className="absolute top-0 bottom-0 z-20 w-px bg-red-500"
                style={{ left: pct(playheadMs) }}
              />
              {mobileTracks.map((track) => (
                <div
                  key={track.kind}
                  className="absolute bottom-0 top-0 w-px border-l border-dashed border-border"
                  style={{ left: pct(playheadMs) }}
                />
              ))}
            </button>

            {mobileTracks.map((track) => (
              <div
                key={track.kind}
                className="relative border-b border-border/60"
                style={{ height: 60 }}
              >
                <div
                  className={cn(
                    "sticky left-2 top-1/2 z-20 inline-flex -translate-y-1/2 items-center gap-1 rounded border-2 border-foreground px-1.5 py-0.5 text-[9px] font-black uppercase shadow-brutal-sm select-none",
                    trackToneClass(track.kind),
                  )}
                >
                  <PixelIcon
                    name={trackIconName(track.kind)}
                    className="size-3"
                  />
                  <span>{track.label}</span>
                </div>
                <div className="relative h-full w-full">
                  {track.clips.length === 0 ? (
                    <div className="flex h-full items-center justify-center text-[10px] text-muted-foreground">
                      Empty
                    </div>
                  ) : (
                    track.clips.map((clip) =>
                      isMobileClipVisible(clip) ? (
                        <button
                          key={clip.id}
                          type="button"
                          aria-pressed={clip.id === selectedClipId}
                          aria-label={`${track.label} clip ${clip.label}. Starts ${formatMs(clip.startMs)}, ends ${formatMs(clip.endMs)}.`}
                          title={`${clip.label} | ${formatMs(clip.startMs)} - ${formatMs(clip.endMs)}`}
                          onClick={() => onClipSelect(clip.id)}
                          className={cn(
                            "absolute top-2 flex min-h-11 min-w-16 items-center overflow-hidden border-2 border-foreground px-2 text-left text-[11px] font-black leading-tight text-white shadow-brutal-sm",
                            clip.colorClass,
                            clip.disabled && "opacity-35",
                            clip.id === selectedClipId
                              ? "ring-2 ring-primary"
                              : "ring-0",
                          )}
                          style={{
                            left: pct(clip.startMs),
                            width: `max(4rem, ${pct(Math.max(clip.endMs - clip.startMs, 250))})`,
                          }}
                        >
                          <span className="truncate whitespace-nowrap">
                            {clip.label}
                          </span>
                        </button>
                      ) : null,
                    )
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  },
  (prev, next) => {
    return (
      prev.playheadMs === next.playheadMs &&
      prev.selectedClipId === next.selectedClipId &&
      prev.tracks === next.tracks &&
      prev.totalDurationMs === next.totalDurationMs
    );
  },
);

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
  getSnappedMs: (targetMs: number, ignoreClipId?: string) => number;
  setSnapMarkerMs: (ms: number | null) => void;
  setDragPreview: (
    preview: { clipId: string; startMs: number; endMs: number } | null,
  ) => void;
}

function DroppableTrack({
  id,
  data,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  id: string;
  data: Record<string, unknown>;
}) {
  const { setNodeRef, isOver } = useDroppable({ id, data });
  const { active } = useDndContext();

  const activeData = active?.data?.current;
  const isDragging = !!active;
  const isDraggingPalette = activeData?.kind === "palette";
  const paletteElement = activeData?.element;

  const trackKind = data.trackKind as string;
  const elemType = paletteElement?.elementType as string;

  let isValid = true;
  if (isDraggingPalette && elemType) {
    if (trackKind === "broll" && elemType !== "broll") isValid = false;
    if (trackKind === "music" && elemType !== "music") isValid = false;
    if (trackKind === "video" && elemType !== "video" && elemType !== "broll")
      isValid = false;
  }

  return (
    <div
      ref={setNodeRef}
      {...props}
      className={cn(
        className,
        "transition-all duration-150 relative",
        isDragging && isValid && "border-dashed border-primary/30 bg-primary/5",
        isDragging && !isValid && "opacity-35 cursor-not-allowed bg-muted/10",
        isOver &&
          isValid &&
          "bg-primary/15 ring-2 ring-inset ring-primary/80 border-solid",
        isOver &&
          !isValid &&
          "bg-danger/10 ring-2 ring-inset ring-danger/40 border-solid cursor-not-allowed",
      )}
    >
      {children}
      {isOver && isValid ? (
        <span
          className="pointer-events-none absolute bottom-0 top-0 z-30 w-1 bg-primary animate-pulse"
          style={{ left: "50%" }}
        />
      ) : null}
    </div>
  );
}

function TransitionDropZone({ index, left }: { index: number; left: string }) {
  const { setNodeRef, isOver } = useDroppable({
    id: `transition:${index}`,
    data: { zone: "transition", afterClipIndex: index },
  });
  return (
    <div
      ref={setNodeRef}
      className={cn(
        "absolute bottom-0 top-0 z-30 w-6 -translate-x-1/2 transition-colors cursor-pointer flex items-center justify-center",
        isOver
          ? "bg-primary/30 border-x border-primary"
          : "bg-transparent hover:bg-primary/5",
      )}
      style={{ left }}
      aria-label={`Transition drop zone after clip ${index + 1}`}
    >
      <div
        className={cn("w-1 h-3/4 bg-primary/20", isOver && "bg-primary/80")}
      />
    </div>
  );
}

const ClipBlock = React.memo(
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
    getSnappedMs,
    setSnapMarkerMs,
    setDragPreview,
  }: ClipBlockProps) {
    const widthMs = Math.max(clip.endMs - clip.startMs, 0);
    const { setNodeRef: setDropRef, isOver } = useDroppable({
      id: `clip:${clip.id}`,
      data: { zone: "clip", clip },
    });

    function startTrim(
      side: "left" | "right",
      event: React.PointerEvent<HTMLDivElement>,
    ) {
      event.stopPropagation();
      if (trimDisabled) return;

      const handlePointerMove = (moveEvent: PointerEvent) => {
        const nextMs = msFromPointer(moveEvent.clientX);
        const snapped = getSnappedMs(nextMs, clip.id);
        const previewMs = Math.abs(snapped - nextMs) <= 150 ? snapped : nextMs;
        setDragPreview({
          clipId: clip.id,
          startMs:
            side === "left"
              ? Math.min(previewMs, clip.endMs - MIN_CLIP_MS)
              : clip.startMs,
          endMs:
            side === "right"
              ? Math.max(previewMs, clip.startMs + MIN_CLIP_MS)
              : clip.endMs,
        });
        if (Math.abs(snapped - nextMs) <= 150) {
          setSnapMarkerMs(snapped);
        } else {
          setSnapMarkerMs(null);
        }
      };

      const handlePointerUp = (nextEvent: PointerEvent) => {
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerUp);
        setSnapMarkerMs(null);
        setDragPreview(null);

        const nextMs = msFromPointer(nextEvent.clientX);
        const finalMs = getSnappedMs(nextMs, clip.id);
        onTrim(clip, side, finalMs);
      };

      window.addEventListener("pointermove", handlePointerMove);
      window.addEventListener("pointerup", handlePointerUp);
    }

    function startMove(event: React.PointerEvent<HTMLDivElement>) {
      event.stopPropagation();
      onSelect(clip.id);
      const startPointerMs = msFromPointer(event.clientX);
      const startClipMs = clip.startMs;

      const handlePointerMove = (moveEvent: PointerEvent) => {
        const deltaMs = msFromPointer(moveEvent.clientX) - startPointerMs;
        const nextMs = startClipMs + deltaMs;
        const snapped = getSnappedMs(nextMs, clip.id);
        const previewStart =
          Math.abs(snapped - nextMs) <= 150 ? snapped : nextMs;
        setDragPreview({
          clipId: clip.id,
          startMs: previewStart,
          endMs: previewStart + (clip.endMs - clip.startMs),
        });
        if (Math.abs(snapped - nextMs) <= 150) {
          setSnapMarkerMs(snapped);
        } else {
          setSnapMarkerMs(null);
        }
      };

      const handlePointerUp = (nextEvent: PointerEvent) => {
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerUp);
        setSnapMarkerMs(null);
        setDragPreview(null);

        const deltaMs = msFromPointer(nextEvent.clientX) - startPointerMs;
        if (Math.abs(deltaMs) < 80) return;

        const nextMs = startClipMs + deltaMs;
        const finalMs = getSnappedMs(nextMs, clip.id);

        if (!moveDisabled) {
          onMove(clip, finalMs);
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

      window.addEventListener("pointermove", handlePointerMove);
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
      } else if (
        event.key === "ArrowLeft" &&
        !reorderDisabled &&
        moveDisabled
      ) {
        event.preventDefault();
        onReorder(clip, -1);
      } else if (
        event.key === "ArrowRight" &&
        !reorderDisabled &&
        moveDisabled
      ) {
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
          "group absolute bottom-1 top-1 cursor-pointer overflow-hidden rounded border-2 border-foreground",
          "flex items-center gap-2 px-2 text-[10px] font-black leading-none text-white",
          "shadow-brutal-sm ring-inset transition-all duration-100",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 focus-visible:ring-offset-black",
          clip.colorClass,
          clip.disabled && "opacity-30",
          selected
            ? "z-30 ring-2 ring-primary shadow-brutal"
            : "ring-0 hover:-translate-y-0.5 hover:brightness-110",
          isOver && "z-20 ring-2 ring-primary brightness-125",
        )}
        style={{
          left: pct(clip.startMs),
          width: `max(3.75rem, ${pct(widthMs)})`,
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
        <TrimHandle
          side="left"
          selected={selected}
          disabled={trimDisabled}
          onPointerDown={(event) => startTrim("left", event)}
        />
        <span className="min-w-0 flex-1 truncate">{clip.label}</span>
        <span className="hidden shrink-0 rounded border border-foreground bg-black/40 px-1 font-mono text-[9px] sm:inline">
          {formatMs(widthMs)}
        </span>
        <TrimHandle
          side="right"
          selected={selected}
          disabled={trimDisabled}
          onPointerDown={(event) => startTrim("right", event)}
        />
      </div>
    );
  },
  (prev, next) => {
    return (
      prev.selected === next.selected &&
      prev.clip.id === next.clip.id &&
      prev.clip.startMs === next.clip.startMs &&
      prev.clip.endMs === next.clip.endMs &&
      prev.clip.label === next.clip.label &&
      prev.trimDisabled === next.trimDisabled &&
      prev.moveDisabled === next.moveDisabled &&
      prev.reorderDisabled === next.reorderDisabled
    );
  },
);

function TrimHandle({
  side,
  selected,
  disabled,
  onPointerDown,
}: {
  side: "left" | "right";
  selected: boolean;
  disabled: boolean;
  onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      className={cn(
        "absolute bottom-0 top-0 z-20 flex w-5 items-center justify-center transition",
        disabled
          ? "cursor-not-allowed opacity-0 pointer-events-none"
          : cn(
              "cursor-ew-resize opacity-70 hover:opacity-100 group-hover:opacity-100 active:opacity-100",
              selected ? "opacity-100" : "sm:opacity-0",
            ),
        side === "left" ? "left-0 -translate-x-2" : "right-0 translate-x-2",
      )}
      title={disabled ? "Edit in Audio" : "Trim clip"}
      onPointerDown={onPointerDown}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="h-7 w-2 border-2 border-foreground bg-white shadow-brutal-sm" />
    </div>
  );
}

const Playhead = React.memo(function Playhead({
  pct,
  totalHeight,
}: {
  pct: string;
  totalHeight: number;
}) {
  return (
    <div
      className="pointer-events-none absolute top-0 z-20 flex flex-col items-center"
      style={{ left: pct, transform: "translateX(-50%)" }}
    >
      <div className="h-3 w-3 rotate-45 border-2 border-foreground bg-danger" />
      <div className="w-1 bg-danger" style={{ height: totalHeight - 8 }} />
    </div>
  );
});

const TimelineRuler = React.memo(function TimelineRuler({
  ticks,
  pct,
  onPointerDown,
}: {
  ticks: Array<{ ms: number; label: string }>;
  pct: (ms: number) => string;
  onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
}) {
  const minorTicks = ticks.flatMap((tick, index) => {
    const next = ticks[index + 1];
    if (!next) return [];
    const gap = next.ms - tick.ms;
    return [tick.ms + gap * 0.25, tick.ms + gap * 0.5, tick.ms + gap * 0.75];
  });

  return (
    <div
      className="pixel-grid-fine relative cursor-pointer border-b-2 border-foreground bg-background"
      style={{ height: RULER_HEIGHT }}
      onPointerDown={onPointerDown}
    >
      {minorTicks.map((ms) => (
        <div
          key={`minor-${ms}`}
          className="absolute bottom-0 h-2 w-px bg-foreground/50"
          style={{ left: pct(ms), transform: "translateX(-50%)" }}
        />
      ))}
      {ticks.map((tick) => (
        <div
          key={tick.ms}
          className="absolute top-0 flex flex-col items-center"
          style={{ left: pct(tick.ms), transform: "translateX(-50%)" }}
        >
          <span className="bg-surface px-1 pt-1 font-mono text-[9px] font-black leading-none text-primary">
            {tick.label}
          </span>
          <div className="mt-0.5 h-4 w-0.5 bg-foreground" />
        </div>
      ))}
    </div>
  );
});

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
    <Tooltip label={title}>
      <button
        onClick={onClick}
        title={title}
        aria-label={ariaLabel}
        disabled={disabled}
        className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded border-2 border-foreground bg-surface text-muted-foreground shadow-brutal-sm transition hover:-translate-x-0.5 hover:-translate-y-0.5 hover:bg-surface-elevated hover:text-foreground hover:shadow-brutal active:translate-x-0 active:translate-y-0 active:shadow-none disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-x-0 disabled:hover:translate-y-0 disabled:hover:bg-surface disabled:hover:shadow-brutal-sm"
      >
        {children}
      </button>
    </Tooltip>
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
