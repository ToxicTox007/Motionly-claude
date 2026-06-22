"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  pointerWithin,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
  type CollisionDetection,
} from "@dnd-kit/core";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Sparkles, Type, Layers, Music2, Palette, Download,
  Film, Loader2, ChevronLeft, History, Network,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { buildApiHeaders, editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { buildTimelineState } from "@/lib/timeline";
import { EditorLayout } from "./editor-layout";
import { ThemeMenu } from "./theme-menu";
import { UploadPanel } from "./upload-panel";
import { AIStep } from "./editor/ai-step";
import { CaptionsTab } from "./editor/tabs/captions-tab";
import { ElementPalette } from "./editor/dnd/ElementPalette";
import { type PaletteElement } from "./editor/dnd/element-catalog";
import { PropertiesInspector } from "./editor/PropertiesInspector";
import { VersionHistoryPanel } from "./editor/VersionHistoryPanel";
import { ScalePanel } from "./editor/ScalePanel";
import {
  EditorHistoryProvider,
  useEditorHistory,
} from "./editor/history-context";
import {
  VideoPreview,
  type PreviewAudioState,
  type PreviewBrollState,
  type PreviewExportState,
  type PreviewLookState,
} from "./editor/preview/VideoPreview";
import { TimelinePanel } from "./editor/timeline/TimelinePanel";
import type {
  BrollDensity,
  BrollSlotSummary,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  EffectsConfigSummary,
  ExportSummary,
  ExportResolution,
  MusicSelectionSummary,
  ManualElement,
  MotionEffectKey,
  RenderJobSummary,
  TimelineClip,
  TimelineState,
  TranscriptSummary,
  UploadedVideoSummary,
  VisualPresetKey,
  VisualAnalysisStatusSummary,
} from "@motionly/shared";

// ── Props (unchanged from old editor — project page needs no changes) ─────────

interface EditorClientProps {
  projectId:       string;
  workspaceId:     string;
  userId:          string;
  apiAccessToken:  string;
  title:           string;
  upload:          UploadedVideoSummary | null;
  uploadAssetUrls: { source_video_url: string | null; thumbnail_url: string | null };
  transcript:      TranscriptSummary | null;
  captionTracks:   CaptionTrackSummary[];
  editPlans:       EditPlanRecordSummary[];
  activePlanId:    string | null;
  brollSlots:      BrollSlotSummary[];
  musicSelection:  MusicSelectionSummary | null;
  effectsConfig:   EffectsConfigSummary | null;
  renderJobs:      RenderJobSummary[];
  exports:         ExportSummary[];
  maxExportResolution: ExportResolution;
  visualStatus: VisualAnalysisStatusSummary;
}

type InspectorTab = "ai" | "captions" | "broll" | "audio" | "look" | "history" | "scale" | "export";

const BrollTab = dynamic(
  () => import("./editor/tabs/broll-tab").then((mod) => mod.BrollTab),
  { loading: () => <InspectorLoading /> },
);
const AudioTab = dynamic(
  () => import("./editor/tabs/audio-tab").then((mod) => mod.AudioTab),
  { loading: () => <InspectorLoading /> },
);
const LookTab = dynamic(
  () => import("./editor/tabs/look-tab").then((mod) => mod.LookTab),
  { loading: () => <InspectorLoading /> },
);
const ExportStep = dynamic(
  () => import("./editor/export-step").then((mod) => mod.ExportStep),
  { loading: () => <InspectorLoading /> },
);

// ── Main component ────────────────────────────────────────────────────────────

export function EditorClient(props: EditorClientProps) {
  const {
    projectId, workspaceId, userId, apiAccessToken, title,
    upload, uploadAssetUrls, transcript, captionTracks,
    brollSlots, musicSelection, effectsConfig, renderJobs, exports,
    maxExportResolution, visualStatus,
  } = props;
  const router = useRouter();
  const [localTitle, setLocalTitle] = useState(title);
  const [saveStatus, setSaveStatus] = useState<"saving" | "saved" | "error">("saved");
  const lastSavedTitleRef = useRef(title);

  // Local mutable copies of server state so the UI updates immediately
  // when AI generates a plan or user switches tabs (no round-trip wait).
  const [localEditPlans, setLocalEditPlans] = useState<EditPlanRecordSummary[]>(
    props.editPlans,
  );
  const [localActivePlanId, setLocalActivePlanId] = useState<string | null>(
    props.activePlanId,
  );
  const [localCaptionTracks, setLocalCaptionTracks] = useState<CaptionTrackSummary[]>(
    captionTracks,
  );
  const [localTranscript, setLocalTranscript] = useState<TranscriptSummary | null>(transcript);
  const [localBrollSlots, setLocalBrollSlots] = useState<BrollSlotSummary[]>(brollSlots);
  const [localMusicSelection, setLocalMusicSelection] =
    useState<MusicSelectionSummary | null>(musicSelection);
  const [localEffectsConfig, setLocalEffectsConfig] =
    useState<EffectsConfigSummary | null>(effectsConfig);
  const [draftCaptionTrack, setDraftCaptionTrack] = useState<CaptionTrackSummary | null>(null);
  const [previewAudio, setPreviewAudio] = useState<PreviewAudioState | null>(null);
  const [previewBroll, setPreviewBroll] = useState<PreviewBrollState | null>(null);
  const [previewLook, setPreviewLook] = useState<PreviewLookState | null>(null);
  const [previewExport, setPreviewExport] = useState<PreviewExportState | null>(null);

  const activePlan = useMemo(
    () => localEditPlans.find((p) => p.id === localActivePlanId) ?? null,
    [localEditPlans, localActivePlanId],
  );

  // Timeline + playback state
  const [playheadMs, setPlayheadMs] = useState(0);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [inspectorTab, setInspectorTab]     = useState<InspectorTab>(() =>
    localActivePlanId ? "captions" : "ai",
  );

  // Timeline data derived from all current project state
  const timelineState = useMemo(
    () =>
      buildTimelineState({
        upload: upload ? { duration_ms: upload.durationMs } : null,
        activePlan,
        captionTrack: localCaptionTracks[0] ?? null,
        brollSlots: localBrollSlots,
        musicSelection: localMusicSelection,
      }),
    [upload, activePlan, localCaptionTracks, localBrollSlots, localMusicSelection],
  );
  const selectedTimelineClip = useMemo(
    () =>
      timelineState.tracks
        .flatMap((track) => track.clips)
        .find((clip) => clip.id === selectedClipId) ?? null,
    [selectedClipId, timelineState],
  );
  const activePreviewLook = useMemo(() => {
    const base = previewLook ?? previewLookFromEffectsConfig(localEffectsConfig);
    if (!base) return null;
    if (!selectedTimelineClip || selectedTimelineClip.sourceRef.kind !== "cut") return base;
    if (base.selectedSourceClipId) return base;
    return {
      ...base,
      selectedSourceClipId: selectedTimelineClip.id,
      selectedSourceClipStartMs: selectedTimelineClip.startMs,
      selectedSourceClipEndMs: selectedTimelineClip.endMs,
    };
  }, [localEffectsConfig, previewLook, selectedTimelineClip]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- server refresh can replace transcript props while local polling owns interim state
    setLocalTranscript(transcript);
  }, [transcript]);

  useEffect(() => {
    if (!upload || upload.status !== "ready") return;
    if (
      localTranscript?.status === "ready" ||
      localTranscript?.status === "captions_unavailable"
    ) {
      return;
    }

    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/transcript`, {
          headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken }),
          cache: "no-store",
        });
        if (cancelled) return;
        if (response.status === 404) {
          setLocalTranscript(null);
          return;
        }
        if (!response.ok) return;
        const nextTranscript = (await response.json()) as TranscriptSummary;
        setLocalTranscript(nextTranscript);
        if (
          nextTranscript.status === "ready" ||
          nextTranscript.status === "failed" ||
          nextTranscript.status === "captions_unavailable"
        ) {
          router.refresh();
        }
      } catch (error) {
        console.error(error);
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    apiAccessToken,
    localTranscript?.status,
    projectId,
    router,
    upload,
    userId,
    workspaceId,
  ]);

  // When AI generates a new plan, update locally and advance inspector
  const handlePlanGenerated = useCallback((plan: EditPlanRecordSummary) => {
    setLocalEditPlans((prev) => {
      const without = prev.filter((p) => p.id !== plan.id);
      return [...without, plan];
    });
    setLocalActivePlanId(plan.id);
    setInspectorTab("captions");
  }, []);

  const handlePlanUpdated = useCallback((plan: EditPlanRecordSummary) => {
    setLocalEditPlans((prev) => {
      const without = prev.filter((p) => p.id !== plan.id);
      return [plan, ...without];
    });
    setLocalActivePlanId(plan.id);
  }, []);

  const synchronizeCanonicalTimeline = useCallback(async () => {
    if (!localActivePlanId) return;
    try {
      const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/${localActivePlanId}/compile-timeline`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "The timeline could not be synchronized.",
          body: {},
        },
      );
      handlePlanUpdated(payload.plan);
    } catch (error) {
      console.error(error);
    }
  }, [
    apiAccessToken,
    handlePlanUpdated,
    localActivePlanId,
    projectId,
    userId,
    workspaceId,
  ]);

  const handleCaptionTrackUpdated = useCallback((track: CaptionTrackSummary) => {
    setDraftCaptionTrack(track);
    setLocalCaptionTracks((prev) => [
      track,
      ...prev.filter((item) => item.id !== track.id),
    ]);
    void synchronizeCanonicalTimeline();
  }, [synchronizeCanonicalTimeline]);

  const handleBrollSlotUpdated = useCallback((slot: BrollSlotSummary) => {
    setLocalBrollSlots((prev) =>
      prev
        .map((item) => (item.id === slot.id ? slot : item))
        .sort((a, b) => a.start_ms - b.start_ms),
    );
    void synchronizeCanonicalTimeline();
  }, [synchronizeCanonicalTimeline]);

  const handleBrollSlotsUpdated = useCallback((slots: BrollSlotSummary[]) => {
    setLocalBrollSlots([...slots].sort((a, b) => a.start_ms - b.start_ms));
    void synchronizeCanonicalTimeline();
  }, [synchronizeCanonicalTimeline]);

  const handleMusicSelectionUpdated = useCallback((selection: MusicSelectionSummary) => {
    setLocalMusicSelection(selection);
    void synchronizeCanonicalTimeline();
  }, [synchronizeCanonicalTimeline]);

  const handleEffectsConfigUpdated = useCallback((config: EffectsConfigSummary) => {
    setLocalEffectsConfig(config);
    void synchronizeCanonicalTimeline();
  }, [synchronizeCanonicalTimeline]);

  const saveProjectTitle = useCallback(
    async (nextTitle: string) => {
      const trimmed = nextTitle.trim() || "Untitled reel";
      setLocalTitle(trimmed);
      if (trimmed === lastSavedTitleRef.current) return;

      setSaveStatus("saving");
      try {
        const payload = await editorApiRequest<{ title?: string }>(
          `${env.apiBaseUrl}/projects/${projectId}`,
          {
          method: "PATCH",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
            friendlyError: "Project title could not be saved.",
            body: { title: trimmed },
          },
        );
        const savedTitle = payload.title ?? trimmed;
        lastSavedTitleRef.current = savedTitle;
        setLocalTitle(savedTitle);
        setSaveStatus("saved");
        router.refresh();
      } catch (error) {
        console.error(error);
        setSaveStatus("error");
      }
    },
    [apiAccessToken, projectId, router, userId, workspaceId],
  );

  // ── Phase 1: no upload yet ───────────────────────────────────────────────
  if (!upload) {
    return (
      <UploadScreen
        title={title}
        projectId={projectId}
        workspaceId={workspaceId}
        userId={userId}
        apiAccessToken={apiAccessToken}
      />
    );
  }

  // ── Phase 2: upload exists but still processing ──────────────────────────
  if (upload.status !== "ready") {
    return <ProcessingScreen title={title} status={upload.status} />;
  }

  // ── Phase 3: full 5-panel editor ─────────────────────────────────────────
  return (
    <EditorHistoryProvider>
      <EditorClientEditor
        {...props}
        localEditPlans={localEditPlans}
        localActivePlanId={localActivePlanId}
        localCaptionTracks={localCaptionTracks}
        localTranscript={localTranscript}
        localBrollSlots={localBrollSlots}
        localMusicSelection={localMusicSelection}
        localEffectsConfig={localEffectsConfig}
        draftCaptionTrack={draftCaptionTrack}
        previewAudio={previewAudio}
        previewBroll={previewBroll}
        previewLook={previewLook}
        previewExport={previewExport}
        activePlan={activePlan}
        timelineState={timelineState}
        selectedTimelineClip={selectedTimelineClip}
        activePreviewLook={activePreviewLook}
        localTitle={localTitle}
        saveStatus={saveStatus}
        playheadMs={playheadMs}
        selectedClipId={selectedClipId}
        inspectorTab={inspectorTab}
        setLocalEditPlans={setLocalEditPlans}
        setLocalActivePlanId={setLocalActivePlanId}
        setLocalCaptionTracks={setLocalCaptionTracks}
        setLocalTranscript={setLocalTranscript}
        setLocalBrollSlots={setLocalBrollSlots}
        setLocalMusicSelection={setLocalMusicSelection}
        setLocalEffectsConfig={setLocalEffectsConfig}
        setDraftCaptionTrack={setDraftCaptionTrack}
        setPreviewAudio={setPreviewAudio}
        setPreviewBroll={setPreviewBroll}
        setPreviewLook={setPreviewLook}
        setPreviewExport={setPreviewExport}
        setPlayheadMs={setPlayheadMs}
        setSelectedClipId={setSelectedClipId}
        setInspectorTab={setInspectorTab}
        setLocalTitle={setLocalTitle}
        handlePlanGenerated={handlePlanGenerated}
        handlePlanUpdated={handlePlanUpdated}
        handleCaptionTrackUpdated={handleCaptionTrackUpdated}
        handleBrollSlotUpdated={handleBrollSlotUpdated}
        handleBrollSlotsUpdated={handleBrollSlotsUpdated}
        handleMusicSelectionUpdated={handleMusicSelectionUpdated}
        handleEffectsConfigUpdated={handleEffectsConfigUpdated}
        saveProjectTitle={saveProjectTitle}
      />
    </EditorHistoryProvider>
  );
}

interface EditorClientEditorProps extends EditorClientProps {
  localTitle: string;
  saveStatus: "saving" | "saved" | "error";
  playheadMs: number;
  selectedClipId: string | null;
  inspectorTab: InspectorTab;
  localEditPlans: EditPlanRecordSummary[];
  localActivePlanId: string | null;
  localCaptionTracks: CaptionTrackSummary[];
  localTranscript: TranscriptSummary | null;
  localBrollSlots: BrollSlotSummary[];
  localMusicSelection: MusicSelectionSummary | null;
  localEffectsConfig: EffectsConfigSummary | null;
  draftCaptionTrack: CaptionTrackSummary | null;
  previewAudio: PreviewAudioState | null;
  previewBroll: PreviewBrollState | null;
  previewLook: PreviewLookState | null;
  previewExport: PreviewExportState | null;
  activePlan: EditPlanRecordSummary | null;
  timelineState: TimelineState;
  selectedTimelineClip: TimelineClip | null;
  activePreviewLook: PreviewLookState | null;
  setLocalEditPlans: React.Dispatch<React.SetStateAction<EditPlanRecordSummary[]>>;
  setLocalActivePlanId: React.Dispatch<React.SetStateAction<string | null>>;
  setLocalCaptionTracks: React.Dispatch<React.SetStateAction<CaptionTrackSummary[]>>;
  setLocalTranscript: React.Dispatch<React.SetStateAction<TranscriptSummary | null>>;
  setLocalBrollSlots: React.Dispatch<React.SetStateAction<BrollSlotSummary[]>>;
  setLocalMusicSelection: React.Dispatch<React.SetStateAction<MusicSelectionSummary | null>>;
  setLocalEffectsConfig: React.Dispatch<React.SetStateAction<EffectsConfigSummary | null>>;
  setDraftCaptionTrack: React.Dispatch<React.SetStateAction<CaptionTrackSummary | null>>;
  setPreviewAudio: React.Dispatch<React.SetStateAction<PreviewAudioState | null>>;
  setPreviewBroll: React.Dispatch<React.SetStateAction<PreviewBrollState | null>>;
  setPreviewLook: React.Dispatch<React.SetStateAction<PreviewLookState | null>>;
  setPreviewExport: React.Dispatch<React.SetStateAction<PreviewExportState | null>>;
  setPlayheadMs: React.Dispatch<React.SetStateAction<number>>;
  setSelectedClipId: React.Dispatch<React.SetStateAction<string | null>>;
  setInspectorTab: React.Dispatch<React.SetStateAction<InspectorTab>>;
  setLocalTitle: React.Dispatch<React.SetStateAction<string>>;
  handlePlanGenerated: (plan: EditPlanRecordSummary) => void;
  handlePlanUpdated: (plan: EditPlanRecordSummary) => void;
  handleCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  handleBrollSlotUpdated: (slot: BrollSlotSummary) => void;
  handleBrollSlotsUpdated: (slots: BrollSlotSummary[]) => void;
  handleMusicSelectionUpdated: (selection: MusicSelectionSummary) => void;
  handleEffectsConfigUpdated: (config: EffectsConfigSummary) => void;
  saveProjectTitle: (nextTitle: string) => Promise<void>;
}

function EditorClientEditor(props: EditorClientEditorProps) {
  const {
    projectId, workspaceId, userId, apiAccessToken, title,
    upload, uploadAssetUrls, transcript, captionTracks,
    brollSlots, musicSelection, effectsConfig, renderJobs, exports,
    maxExportResolution, visualStatus,
    localEditPlans, localActivePlanId, localCaptionTracks, localTranscript,
    localBrollSlots, localMusicSelection, localEffectsConfig, draftCaptionTrack,
    previewAudio, previewBroll, previewLook, previewExport,
    activePlan, timelineState, selectedTimelineClip, activePreviewLook,
    localTitle, saveStatus, playheadMs, selectedClipId, inspectorTab,
    setLocalEditPlans, setLocalActivePlanId, setLocalCaptionTracks, setLocalTranscript,
    setLocalBrollSlots, setLocalMusicSelection, setLocalEffectsConfig,
    setDraftCaptionTrack, setPreviewAudio, setPreviewBroll, setPreviewLook, setPreviewExport,
    setPlayheadMs, setSelectedClipId, setInspectorTab, setLocalTitle,
    handlePlanGenerated, handlePlanUpdated, handleCaptionTrackUpdated,
    handleBrollSlotUpdated, handleBrollSlotsUpdated, handleMusicSelectionUpdated,
    handleEffectsConfigUpdated, saveProjectTitle,
  } = props;
  const router = useRouter();
  const { undo, redo, canUndo, canRedo, pushHistory } = useEditorHistory();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [selectedElementId, setSelectedElementId] = useState<string | null>(null);
  const [activePaletteElement, setActivePaletteElement] = useState<PaletteElement | null>(null);
  const [elementSaving, setElementSaving] = useState(false);
  const [dropMessage, setDropMessage] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor),
  );
  const manualElements = useMemo(
    () =>
      activePlan?.plan.timeline
        ? [
            ...activePlan.plan.timeline.manual_layers,
            ...activePlan.plan.timeline.transitions,
          ]
        : activePlan?.plan.manual_elements ?? [],
    [activePlan],
  );
  const selectedManualElement = manualElements.find((element) => element.id === selectedElementId) ?? null;

  const setPlayheadMsWrapped = useCallback((ms: number | ((prev: number) => number)) => {
    setPlayheadMs(ms);
  }, [setPlayheadMs]);

  const setSelectedClipIdWrapped = useCallback((id: string | null) => {
    setSelectedClipId(id);
  }, [setSelectedClipId]);

  const setInspectorTabWrapped = useCallback((tab: InspectorTab) => {
    setInspectorTab(tab);
  }, [setInspectorTab]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const isTyping = ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "");
      if (isTyping) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) {
          if (canRedo) void redo();
        } else {
          if (canUndo) void undo();
        }
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        if (canRedo) void redo();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canRedo, canUndo, redo, undo]);

  const persistManualElements = useCallback(async (elements: ManualElement[]) => {
    if (!activePlan) throw new Error("Generate an AI edit before placing elements.");
    const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
      `${env.apiBaseUrl}/projects/${projectId}/plans/${activePlan.id}/manual-elements`,
      { method: "PATCH", workspaceId, userId, accessToken: apiAccessToken, friendlyError: "Element changes could not be saved.", body: { manual_elements: elements } },
    );
    handlePlanUpdated(payload.plan);
    return payload.plan;
  }, [activePlan, apiAccessToken, handlePlanUpdated, projectId, userId, workspaceId]);

  const commitManualElements = useCallback(async (next: ManualElement[], label: string, before = manualElements) => {
    setElementSaving(true);
    setDropMessage(null);
    try {
      await persistManualElements(next);
      pushHistory({
        id: `element-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label,
        undo: async () => { await persistManualElements(before); return `${label} undone.`; },
        redo: async () => { await persistManualElements(next); return `${label} redone.`; },
      });
      setDropMessage(`${label} saved.`);
    } catch (error) {
      setDropMessage((error as Error).message);
    } finally {
      setElementSaving(false);
    }
  }, [manualElements, persistManualElements, pushHistory]);

  const runAIElement = useCallback(async (entry: PaletteElement, clip: TimelineClip | null) => {
    if (!entry.aiAction) return;
    setElementSaving(true);
    setDropMessage(`Running ${entry.name}…`);
    try {
      if (entry.aiAction === "auto_caption") {
        const payload = await editorApiRequest<{ track: CaptionTrackSummary }>(`${env.apiBaseUrl}/projects/${projectId}/captions/generate`, { method: "POST", workspaceId, userId, accessToken: apiAccessToken, friendlyError: "Auto-captioning failed.", body: { preset: "bold_influencer", hide_filler_words: false } });
        handleCaptionTrackUpdated(payload.track);
      } else if (entry.aiAction === "smart_broll") {
        const payload = await editorApiRequest<{ slots: BrollSlotSummary[] }>(`${env.apiBaseUrl}/projects/${projectId}/broll/generate`, { method: "POST", workspaceId, userId, accessToken: apiAccessToken, friendlyError: "Smart B-roll fill failed.", body: { density: "from_plan", force: true } });
        handleBrollSlotsUpdated(payload.slots);
      } else if (entry.aiAction === "music_match") {
        const payload = await editorApiRequest<{ selection: MusicSelectionSummary }>(`${env.apiBaseUrl}/projects/${projectId}/music/select`, { method: "POST", workspaceId, userId, accessToken: apiAccessToken, friendlyError: "AI music matching failed.", body: { mood: activePlan?.plan.music_mood, include_music: true, music_volume: .25, sfx_intensity: .25, rights_confirmed: true, force: true } });
        handleMusicSelectionUpdated(payload.selection);
      } else {
        const prompt = entry.aiAction === "highlight_reel"
          ? `Create the strongest 30-60 second highlight reel${clip ? ` emphasizing ${clip.startMs}-${clip.endMs}ms` : ""}.`
          : "Re-analyze the audio and visual style, then update captions, grade, motion, and pacing.";
        const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, { method: "POST", workspaceId, userId, accessToken: apiAccessToken, friendlyError: `${entry.name} failed.`, body: { prompt, target_duration_s: entry.aiAction === "highlight_reel" ? 45 : activePlan?.target_duration_s ?? 35, broll_density: activePlan?.broll_density ?? "medium", force: true } });
        handlePlanGenerated(payload.plan);
      }
      setDropMessage(`${entry.name} complete.`);
    } catch (error) {
      setDropMessage((error as Error).message);
    } finally {
      setElementSaving(false);
    }
  }, [activePlan, apiAccessToken, handleBrollSlotsUpdated, handleCaptionTrackUpdated, handleMusicSelectionUpdated, handlePlanGenerated, projectId, userId, workspaceId]);

  const applyClipEffect = useCallback(async (entry: PaletteElement, clip: TimelineClip, applyAll: boolean) => {
    const current = localEffectsConfig;
    const motionEffect = entry.motionEffect;
    const clipEffects = { ...(current?.motion.clip_effects ?? {}) };
    if (motionEffect) {
      if (applyAll) timelineState.tracks.find((track) => track.kind === "video")?.clips.forEach((item) => { clipEffects[item.id] = unique([...(clipEffects[item.id] ?? []), motionEffect]); });
      else clipEffects[clip.id] = unique([...(clipEffects[clip.id] ?? []), motionEffect]);
    }
    const body = effectsRequestBody(current, entry.grade, motionEffect, clipEffects);
    setElementSaving(true);
    try {
      const payload = await editorApiRequest<{ config: EffectsConfigSummary }>(`${env.apiBaseUrl}/projects/${projectId}/effects`, { method: "PATCH", workspaceId, userId, accessToken: apiAccessToken, friendlyError: "Clip effect could not be applied.", body });
      handleEffectsConfigUpdated(payload.config);
      setSelectedClipIdWrapped(clip.id);
      setInspectorTabWrapped("look");
      setDropMessage(`${entry.name} applied ${applyAll ? "to all clips" : `to ${clip.label}`}.`);
    } catch (error) { setDropMessage((error as Error).message); }
    finally { setElementSaving(false); }
  }, [apiAccessToken, handleEffectsConfigUpdated, localEffectsConfig, projectId, setInspectorTabWrapped, setSelectedClipIdWrapped, timelineState.tracks, userId, workspaceId]);

  const handleDragStart = useCallback((event: DragStartEvent) => {
    const data = event.active.data.current;
    setActivePaletteElement(data?.kind === "palette" ? data.element as PaletteElement : null);
  }, []);

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    setActivePaletteElement(null);
    const over = event.over;
    if (!over) return;
    const activeData = event.active.data.current;
    const overData = over.data.current;
    const activator = event.activatorEvent as MouseEvent | KeyboardEvent;
    const fineSnap = Boolean(activator?.shiftKey);
    const duplicate = Boolean(activator?.ctrlKey || activator?.metaKey);
    const applyAll = Boolean(activator?.altKey);
    if (activeData?.kind === "placed") {
      const placed = activeData.element as ManualElement;
      if (String(over.id) !== "canvas") return;
      const center = event.active.rect.current.translated;
      if (!center) return;
      const step = fineSnap ? 1 : 5;
      const nextElement = { ...placed, id: duplicate ? `${placed.id}-copy-${Date.now()}` : placed.id, x_pct: snap(((center.left + center.width / 2 - over.rect.left) / over.rect.width) * 100, step, 0, 100), y_pct: snap(((center.top + center.height / 2 - over.rect.top) / over.rect.height) * 100, step, 0, 100) };
      const next = duplicate ? [...manualElements, nextElement] : manualElements.map((item) => item.id === placed.id ? nextElement : item);
      setSelectedElementId(nextElement.id);
      void commitManualElements(next, duplicate ? `Duplicate ${placed.name}` : `Move ${placed.name}`);
      return;
    }
    if (activeData?.kind !== "palette") return;
    const entry = activeData.element as PaletteElement;
    const zone = String(overData?.zone ?? (String(over.id) === "canvas" ? "canvas" : ""));
    const acceptedZone = zone === "transition" ? "transition" : zone;
    if (!entry.accepts.includes(acceptedZone as never)) { setDropMessage(`${entry.name} cannot be dropped on ${zone || "this target"}.`); return; }
    const clip = (overData?.clip as TimelineClip | undefined) ?? null;
    if (entry.elementType === "ai") { void runAIElement(entry, clip); return; }
    if ((entry.grade || entry.motionEffect) && clip) { void applyClipEffect(entry, clip, applyAll); return; }
    if (!activePlan) { setDropMessage("Generate an AI edit before placing elements."); return; }
    const translated = event.active.rect.current.translated;
    let startMs = clip?.startMs ?? playheadMs;
    let endMs = clip?.endMs ?? Math.min(timelineState.totalDurationMs, startMs + (entry.durationMs ?? 3000));
    let xPct = 50, yPct = 50;
    if (zone === "canvas" && translated) {
      const step = fineSnap ? 1 : 5;
      xPct = snap(((translated.left + translated.width / 2 - over.rect.left) / over.rect.width) * 100, step, 0, 100);
      yPct = snap(((translated.top + translated.height / 2 - over.rect.top) / over.rect.height) * 100, step, 0, 100);
    } else if (zone === "timeline" && translated) {
      const stepMs = fineSnap ? 100 : 500;
      const rawStartMs = ((translated.left + translated.width / 2 - over.rect.left) / over.rect.width) * timelineState.totalDurationMs;
      const boundaries = timelineState.tracks.flatMap((track) => track.clips.flatMap((item) => [item.startMs, item.endMs]));
      startMs = snapTimelineMs(rawStartMs, stepMs, fineSnap ? 60 : 260, boundaries, timelineState.totalDurationMs);
      endMs = Math.min(timelineState.totalDurationMs, startMs + (entry.durationMs ?? 3000));
    } else if (zone === "transition") {
      const afterIndex = Number(overData?.afterClipIndex ?? 0);
      const boundary = timelineState.tracks.find((track) => track.kind === "video")?.clips[afterIndex]?.endMs ?? playheadMs;
      const duration = Math.max(1, entry.durationMs ?? 350);
      startMs = Math.max(0, boundary - Math.floor(duration / 2)); endMs = Math.min(timelineState.totalDurationMs, startMs + duration);
    }
    if (endMs <= startMs) endMs = startMs + 1;
    const element: ManualElement = { id: `${entry.id}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, type: entry.elementType as ManualElement["type"], name: entry.name, zone: zone === "transition" ? "timeline" : zone as ManualElement["zone"], start_ms: Math.round(startMs), end_ms: Math.round(endMs), x_pct: xPct, y_pct: yPct, width_pct: entry.elementType === "shape" ? 24 : 48, height_pct: entry.elementType === "shape" ? 18 : 14, rotation_deg: 0, opacity: 1, clip_id: clip?.id ?? null, properties: { ...(entry.properties ?? {}), durationMs: entry.durationMs }, ai_reason: null, confidence: null };
    setSelectedElementId(element.id);
    void commitManualElements([...manualElements, element], `Add ${entry.name}`);
  }, [activePlan, applyClipEffect, commitManualElements, manualElements, playheadMs, runAIElement, timelineState]);

  if (!upload || upload.status !== "ready") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <DndContext sensors={sensors} collisionDetection={editorCollisionDetection} onDragStart={handleDragStart} onDragCancel={() => setActivePaletteElement(null)} onDragEnd={handleDragEnd}>
    <EditorLayout
      title={localTitle}
      onTitleChange={setLocalTitle}
      onTitleCommit={saveProjectTitle}
      saveStatus={elementSaving ? "saving" : saveStatus}
      onExport={() => setInspectorTabWrapped("export")}
      onAiEdit={() => setInspectorTabWrapped("ai")}
    >
      {{
        leftPanel: (
          <ElementPalette projectId={projectId} workspaceId={workspaceId} userId={userId} apiAccessToken={apiAccessToken} />
        ),

        centerPanel: (
          <VideoPreview
            videoRef={videoRef}
            sourceUrl={uploadAssetUrls.source_video_url}
            thumbnailUrl={uploadAssetUrls.thumbnail_url}
            sourceWidth={upload.width}
            sourceHeight={upload.height}
            playheadMs={playheadMs}
            captionSegments={
              activePlan?.plan.timeline?.captions?.length
                ? activePlan.plan.timeline.captions
                : (draftCaptionTrack ?? localCaptionTracks[0])?.segments ?? []
            }
            captionStyle={(draftCaptionTrack ?? localCaptionTracks[0])?.style ?? null}
            totalDurationMs={timelineState.totalDurationMs}
            previewAudio={previewAudio}
            previewBroll={previewBroll}
            previewLook={activePreviewLook}
            previewExport={previewExport}
            manualElements={manualElements}
            selectedElementId={selectedElementId}
            onElementSelect={setSelectedElementId}
            sourceClips={activePlan?.plan.timeline?.clips ?? []}
          />
        ),

        rightPanel: (
          selectedManualElement ? <PropertiesInspector
            key={`${selectedManualElement.id}:${activePlan?.version ?? 0}`}
            element={selectedManualElement}
            saving={elementSaving}
            onClose={() => setSelectedElementId(null)}
            onChange={async (element) => { await commitManualElements(manualElements.map((item) => item.id === element.id ? element : item), `Update ${element.name}`); }}
            onDelete={async (id) => { setSelectedElementId(null); await commitManualElements(manualElements.filter((item) => item.id !== id), `Delete ${selectedManualElement.name}`); }}
          /> : <InspectorPanel
            tab={inspectorTab}
            onTabChange={setInspectorTabWrapped}
            hasActivePlan={Boolean(localActivePlanId)}
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            upload={upload}
            transcript={localTranscript}
            captionTracks={localCaptionTracks}
            localEditPlans={localEditPlans}
            localActivePlanId={localActivePlanId}
            activePlan={activePlan}
            brollSlots={localBrollSlots}
            musicSelection={localMusicSelection}
            effectsConfig={localEffectsConfig}
            selectedTimelineClip={selectedTimelineClip}
            renderJobs={renderJobs}
            exports={exports}
            maxExportResolution={maxExportResolution}
            visualStatus={visualStatus}
            onTranscriptUpdated={setLocalTranscript}
            onPlanGenerated={handlePlanGenerated}
            onPlanUpdated={handlePlanUpdated}
            onCaptionTrackUpdated={handleCaptionTrackUpdated}
            onPreviewCaptionTrackUpdated={setDraftCaptionTrack}
            onBrollSlotUpdated={handleBrollSlotUpdated}
            onBrollSlotsUpdated={handleBrollSlotsUpdated}
            onPreviewBrollUpdated={setPreviewBroll}
            onMusicSelectionUpdated={handleMusicSelectionUpdated}
            onPreviewAudioUpdated={setPreviewAudio}
            onEffectsConfigUpdated={handleEffectsConfigUpdated}
            onPreviewLookUpdated={setPreviewLook}
            onPreviewExportUpdated={setPreviewExport}
          />
        ),

        bottomPanel: (
          <TimelinePanel
            state={timelineState}
            videoRef={videoRef}
            playheadMs={playheadMs}
            setPlayheadMs={setPlayheadMsWrapped}
            selectedClipId={selectedClipId}
            onClipSelect={(id) => { setSelectedClipIdWrapped(id); if (id?.startsWith("element-")) setSelectedElementId(id.slice("element-".length)); }}
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            activePlan={activePlan}
            captionTrack={localCaptionTracks[0] ?? null}
            musicSelection={localMusicSelection}
            onPlanUpdated={handlePlanUpdated}
            onCaptionTrackUpdated={handleCaptionTrackUpdated}
            onBrollSlotUpdated={handleBrollSlotUpdated}
            onMusicSelectionUpdated={handleMusicSelectionUpdated}
          />
        ),
      }}
    </EditorLayout>
    <DragOverlay dropAnimation={{ duration: 160, easing: "ease-out" }}>
      {activePaletteElement ? <div className="w-32 border-2 border-primary bg-background/95 p-2 text-xs font-black shadow-xl">{activePaletteElement.preview || activePaletteElement.name}<span className="mt-1 block truncate text-[10px]">{activePaletteElement.name}</span></div> : null}
    </DragOverlay>
    {dropMessage ? <div className="pointer-events-none fixed bottom-4 left-1/2 z-[100] -translate-x-1/2 border border-border bg-background px-3 py-2 text-xs font-bold shadow-xl" aria-live="polite">{dropMessage}</div> : null}
    </DndContext>
  );
}

// ── Inspector (right panel) ───────────────────────────────────────────────────

interface InspectorPanelProps {
  tab:              InspectorTab;
  onTabChange:      (tab: InspectorTab) => void;
  hasActivePlan:    boolean;
  projectId:        string;
  workspaceId:      string;
  userId:           string;
  apiAccessToken:   string;
  upload:           UploadedVideoSummary;
  transcript:       TranscriptSummary | null;
  captionTracks:    CaptionTrackSummary[];
  localEditPlans:   EditPlanRecordSummary[];
  localActivePlanId:string | null;
  activePlan:       EditPlanRecordSummary | null;
  brollSlots:       BrollSlotSummary[];
  musicSelection:   MusicSelectionSummary | null;
  effectsConfig:    EffectsConfigSummary | null;
  selectedTimelineClip: TimelineClip | null;
  renderJobs:       RenderJobSummary[];
  exports:          ExportSummary[];
  maxExportResolution: ExportResolution;
  visualStatus: VisualAnalysisStatusSummary;
  onTranscriptUpdated: (transcript: TranscriptSummary | null) => void;
  onPlanGenerated:  (plan: EditPlanRecordSummary) => void;
  onPlanUpdated: (plan: EditPlanRecordSummary) => void;
  onCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  onPreviewCaptionTrackUpdated: (track: CaptionTrackSummary | null) => void;
  onBrollSlotUpdated: (slot: BrollSlotSummary) => void;
  onBrollSlotsUpdated: (slots: BrollSlotSummary[]) => void;
  onPreviewBrollUpdated: (state: PreviewBrollState | null) => void;
  onMusicSelectionUpdated: (selection: MusicSelectionSummary) => void;
  onPreviewAudioUpdated: (state: PreviewAudioState | null) => void;
  onEffectsConfigUpdated: (config: EffectsConfigSummary) => void;
  onPreviewLookUpdated: (state: PreviewLookState | null) => void;
  onPreviewExportUpdated: (state: PreviewExportState | null) => void;
}

const INSPECTOR_TABS: Array<{
  id: InspectorTab;
  label: string;
  icon: React.ElementType;
  requiresPlan: boolean;
}> = [
  { id: "ai",      label: "AI",      icon: Sparkles,  requiresPlan: false },
  { id: "captions",label: "Text",    icon: Type,       requiresPlan: false },
  { id: "broll",   label: "B-roll",  icon: Layers,     requiresPlan: false },
  { id: "audio",   label: "Audio",   icon: Music2,     requiresPlan: false },
  { id: "look",    label: "Look",    icon: Palette,    requiresPlan: false },
  { id: "history", label: "History", icon: History, requiresPlan: true },
  { id: "scale",   label: "Scale",   icon: Network, requiresPlan: false },
  { id: "export",  label: "Export",  icon: Download,   requiresPlan: false },
];

function InspectorPanel({
  tab, onTabChange, hasActivePlan,
  projectId, workspaceId, userId, apiAccessToken,
  upload, transcript, captionTracks, localEditPlans, localActivePlanId,
  activePlan, brollSlots, musicSelection, effectsConfig,
  selectedTimelineClip, renderJobs, exports, maxExportResolution, visualStatus, onTranscriptUpdated, onPlanGenerated, onPlanUpdated, onCaptionTrackUpdated, onPreviewCaptionTrackUpdated, onBrollSlotUpdated, onBrollSlotsUpdated, onPreviewBrollUpdated,
  onMusicSelectionUpdated, onPreviewAudioUpdated, onEffectsConfigUpdated, onPreviewLookUpdated, onPreviewExportUpdated,
}: InspectorPanelProps) {
  const brollDensity = activePlan?.broll_density ?? null;
  const musicMood    = activePlan?.summary?.music_mood ?? null;
  const suggestedPreset = (activePlan?.summary?.visual_preset ?? null) as VisualPresetKey | null;
  const activeTabMeta = INSPECTOR_TABS.find((item) => item.id === tab) ?? INSPECTOR_TABS[0];

  function onTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) {
    if (event.key === "Escape") {
      event.currentTarget.blur();
      return;
    }
    const direction =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (!direction && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const enabledTabs = INSPECTOR_TABS.filter((item) => !(item.requiresPlan && !hasActivePlan));
    const current = enabledTabs.findIndex((item) => item.id === INSPECTOR_TABS[currentIndex].id);
    const next =
      event.key === "Home"
        ? enabledTabs[0]
        : event.key === "End"
          ? enabledTabs[enabledTabs.length - 1]
          : enabledTabs[(current + direction + enabledTabs.length) % enabledTabs.length];
    onTabChange(next.id);
    window.requestAnimationFrame(() => {
      document.getElementById(`editor-tab-${next.id}`)?.focus();
    });
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[hsl(var(--surface))]">
      {/* Tab strip */}
      <div
        className="pixel-grid-fine flex shrink-0 snap-x gap-1.5 overflow-x-auto overscroll-x-contain border-b border-border bg-muted px-1.5 py-2 sm:gap-2 sm:px-2"
        role="tablist"
        aria-label="Editor tools"
      >
        {INSPECTOR_TABS.map(({ id, label, icon: Icon, requiresPlan }, index) => {
          const disabled = requiresPlan && !hasActivePlan;
          const selected = tab === id;
          return (
            <button
              id={`editor-tab-${id}`}
              key={id}
              role="tab"
              type="button"
              aria-selected={selected}
              aria-controls={`editor-panel-${id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => !disabled && onTabChange(id)}
              onKeyDown={(event) => onTabKeyDown(event, index)}
              disabled={disabled}
              title={disabled ? "Generate an AI edit first" : label}
              className={cn(
                "pixel-button flex min-h-11 min-w-[4.6rem] shrink-0 snap-start items-center justify-center gap-1.5 whitespace-nowrap px-2 py-2 text-xs font-bold transition sm:min-w-20 sm:px-3 sm:text-sm",
                selected
                  ? "bg-primary text-primary-foreground shadow-[3px_3px_0_hsl(var(--shadow-color)/0.18)]"
                  : disabled
                    ? "cursor-not-allowed bg-background text-muted-foreground opacity-45"
                    : "bg-surface text-foreground hover:bg-primary/10",
              )}
            >
              <Icon className="size-4 shrink-0" />
              <span className="sr-only">{selected ? "Selected: " : ""}</span>
              {label}
            </button>
          );
        })}
      </div>

      {/* Tab content — scrollable */}
      <div
        id={`editor-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`editor-tab-${tab}`}
        className="min-h-0 max-w-full flex-1 overflow-x-hidden overflow-y-auto p-2 sm:p-4"
      >
        <h2 className="sr-only">{activeTabMeta.label}</h2>
        <SelectedElementInsight
          clip={selectedTimelineClip}
          activePlan={activePlan}
          captionTracks={captionTracks}
          brollSlots={brollSlots}
          musicSelection={musicSelection}
        />
        {tab === "ai" && (
          <AIStep
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialPlans={localEditPlans}
            activePlanId={localActivePlanId}
            transcript={transcript}
            initialVisualStatus={visualStatus}
            onTranscriptUpdated={onTranscriptUpdated}
            onPlanGenerated={onPlanGenerated}
            onCaptionTrackUpdated={onCaptionTrackUpdated}
            onBrollSlotsUpdated={onBrollSlotsUpdated}
            onMusicSelectionUpdated={onMusicSelectionUpdated}
          />
        )}
        {tab === "captions" && (
          <CaptionsTab
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialTracks={captionTracks}
            activePlan={activePlan}
            onTrackUpdated={onCaptionTrackUpdated}
            onPreviewTrackUpdated={onPreviewCaptionTrackUpdated}
            onPlanUpdated={onPlanUpdated}
          />
        )}
        {tab === "broll" && (
          <BrollTab
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialSlots={brollSlots}
            activePlanId={localActivePlanId}
            activePlanBrollDensity={brollDensity}
            onSlotUpdated={onBrollSlotUpdated}
            onSlotsUpdated={onBrollSlotsUpdated}
            onPreviewBrollUpdated={onPreviewBrollUpdated}
          />
        )}
        {tab === "audio" && (
          <AudioTab
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialSelection={musicSelection}
            activePlanMusicMood={musicMood}
            onSelectionUpdated={onMusicSelectionUpdated}
            onPreviewAudioUpdated={onPreviewAudioUpdated}
          />
        )}
        {tab === "look" && (
          <LookTab
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialConfig={effectsConfig}
            suggestedPreset={suggestedPreset}
            selectedTimelineClip={selectedTimelineClip}
            onConfigUpdated={onEffectsConfigUpdated}
            onPreviewLookUpdated={onPreviewLookUpdated}
          />
        )}
        {tab === "export" && (
          <ExportStep
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialJobs={renderJobs}
            initialExports={exports}
            captionTrackId={captionTracks[0]?.id ?? null}
            activePlanId={localActivePlanId}
            sourceWidth={upload.width}
            sourceHeight={upload.height}
            maxExportResolution={maxExportResolution}
            onPreviewExportUpdated={onPreviewExportUpdated}
          />
        )}
        {tab === "history" && (
          <VersionHistoryPanel projectId={projectId} workspaceId={workspaceId} userId={userId} apiAccessToken={apiAccessToken} activePlan={activePlan} onPlanRestored={onPlanUpdated} />
        )}
        {tab === "scale" && (
          <ScalePanel projectId={projectId} workspaceId={workspaceId} userId={userId} apiAccessToken={apiAccessToken} activePlan={activePlan} onPlanUpdated={onPlanUpdated} />
        )}
      </div>
    </div>
  );
}

function previewLookFromEffectsConfig(
  config: EffectsConfigSummary | null,
): PreviewLookState | null {
  if (!config) return null;
  return {
    preset: config.preset,
    intensity: Math.round(config.lut_intensity * 100),
    grain: config.texture.grain,
    vignette: config.texture.vignette,
    lightLeak: config.texture.light_leak,
    gradientRamp: {
      enabled: config.gradient_ramp.enabled,
      startColor: config.gradient_ramp.start_color,
      endColor: config.gradient_ramp.end_color,
      opacity: config.gradient_ramp.opacity,
      angleDeg: config.gradient_ramp.angle_deg,
    },
    motionEffects: config.motion_effects,
    motion: config.motion,
    selectedSourceClipId: null,
    reducedMotion: false,
  };
}

function SelectedElementInsight({
  clip,
  activePlan,
  captionTracks,
  brollSlots,
  musicSelection,
}: {
  clip: TimelineClip | null;
  activePlan: EditPlanRecordSummary | null;
  captionTracks: CaptionTrackSummary[];
  brollSlots: BrollSlotSummary[];
  musicSelection: MusicSelectionSummary | null;
}) {
  if (!clip) return null;
  const insight = selectedElementInsight({
    clip,
    activePlan,
    captionTracks,
    brollSlots,
    musicSelection,
  });
  return (
    <div className="mb-3 border border-primary/20 bg-primary/5 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-black uppercase tracking-wide text-primary">
          Selected {clip.trackKind}
        </p>
        {insight.confidence !== null ? (
          <span className="border border-border bg-surface px-2 py-1 text-[10px] font-black text-foreground">
            {Math.round(insight.confidence * 100)}% confidence
          </span>
        ) : null}
      </div>
      <p className="mt-2 text-xs font-bold text-foreground">
        AI reason: {insight.reason}
      </p>
      {insight.detail ? (
        <p className="mt-1 text-[11px] font-semibold text-muted-foreground">
          {insight.detail}
        </p>
      ) : null}
    </div>
  );
}

function selectedElementInsight({
  clip,
  activePlan,
  captionTracks,
  brollSlots,
  musicSelection,
}: {
  clip: TimelineClip;
  activePlan: EditPlanRecordSummary | null;
  captionTracks: CaptionTrackSummary[];
  brollSlots: BrollSlotSummary[];
  musicSelection: MusicSelectionSummary | null;
}) {
  if (clip.sourceRef.kind === "cut") {
    const cut = activePlan?.plan.cuts[clip.sourceRef.index ?? -1];
    return {
      reason: cut?.reason || clip.label,
      detail: activePlan?.summary.style_classification
        ? `Style: ${activePlan.summary.style_classification.effective_style}`
        : activePlan?.summary.source_topic,
      confidence: activePlan?.summary.style_classification?.confidence ?? null,
    };
  }
  if (clip.sourceRef.kind === "caption_segment") {
    const segment = captionTracks[0]?.segments.find((item) =>
      clip.sourceRef.id ? item.id === clip.sourceRef.id : item.index === clip.sourceRef.index,
    );
    const confidenceValues = segment?.words
      .map((word) => word.confidence)
      .filter((value): value is number => typeof value === "number");
    const confidence = confidenceValues?.length
      ? confidenceValues.reduce((sum, value) => sum + value, 0) / confidenceValues.length
      : null;
    return {
      reason: segment?.highlight_words?.length
        ? `Highlighted words: ${segment.highlight_words.join(", ")}`
        : "Caption segment generated from word-level transcript timing.",
      detail: segment?.text,
      confidence,
    };
  }
  if (clip.sourceRef.kind === "broll_slot") {
    const slot = brollSlots.find((item) => item.id === clip.sourceRef.id);
    return {
      reason: slot?.intent || slot?.keywords?.join(", ") || "AI-selected supporting B-roll.",
      detail: slot?.selected_asset?.title ?? slot?.keywords?.join(", "),
      confidence: typeof slot?.score === "number" ? slot.score : null,
    };
  }
  if (clip.sourceRef.kind === "music") {
    return {
      reason: activePlan?.summary.music_mood || "Music selected to match the timeline mood.",
      detail: musicSelection?.music_track?.title ?? "Background music",
      confidence: musicSelection?.music_track?.energy ?? null,
    };
  }
  return {
    reason: clip.label,
    detail: null,
    confidence: null,
  };
}

function InspectorLoading() {
  return (
    <div className="flex min-h-40 items-center justify-center">
      <Loader2 className="size-5 animate-spin text-primary" />
    </div>
  );
}

function unique<T>(items: T[]): T[] {
  return [...new Set(items)];
}

function snap(value: number, step: number, min: number, max: number) {
  return Math.max(min, Math.min(max, Math.round(value / step) * step));
}

function snapTimelineMs(value: number, gridMs: number, boundaryToleranceMs: number, boundaries: number[], totalMs: number) {
  const nearest = boundaries.reduce<number | null>((best, boundary) =>
    best == null || Math.abs(boundary - value) < Math.abs(best - value) ? boundary : best,
  null);
  if (nearest != null && Math.abs(nearest - value) <= boundaryToleranceMs) {
    return Math.max(0, Math.min(totalMs - 1, nearest));
  }
  return snap(value, gridMs, 0, totalMs - 1);
}

const editorCollisionDetection: CollisionDetection = (args) => {
  const activeData = args.active.data.current;
  const allowed = activeData?.kind === "placed"
    ? ["canvas"]
    : (activeData?.element as PaletteElement | undefined)?.accepts ?? [];
  const containers = args.droppableContainers.filter((container) => {
    const zone = String(container.data.current?.zone ?? (String(container.id) === "canvas" ? "canvas" : ""));
    return allowed.includes(zone as never);
  });
  const scoped = { ...args, droppableContainers: containers };
  return pointerWithin(scoped).length ? pointerWithin(scoped) : closestCenter(scoped);
};

function effectsRequestBody(
  config: EffectsConfigSummary | null,
  grade: VisualPresetKey | undefined,
  motionEffect: MotionEffectKey | undefined,
  clipEffects: Record<string, MotionEffectKey[]>,
) {
  const motionEffects = unique([...(config?.motion_effects ?? []), ...(motionEffect ? [motionEffect] : [])]);
  return {
    preset: grade ?? config?.preset ?? "clean_founder",
    lut_intensity: config?.lut_intensity ?? .75,
    texture: config?.texture ?? { grain: .08, vignette: .08, light_leak: 0, anamorphic_flare: false },
    motion_effects: motionEffects,
    motion: {
      keyframes: config?.motion?.keyframes ?? [],
      ken_burns: config?.motion?.ken_burns ?? { enabled: motionEffect === "ken_burns", intensity: .08, direction: "push_in", easing: "ease_in_out" },
      speed_ramp: config?.motion?.speed_ramp ?? { enabled: motionEffect === "speed_ramp", ramp_in_ms: 280, ramp_out_ms: 320, peak_speed: 1.35, easing: "ease_in_out" },
      beat_impact: config?.motion?.beat_impact ?? { enabled: motionEffect === "beat_impact", intensity: .22, shake_px: 10, zoom: 1.035, max_impacts: 12 },
      auto_reframe: config?.motion?.auto_reframe ?? { enabled: false, target_aspect: "9:16", subject_x_pct: 50, subject_y_pct: 50, smoothing: .65 },
      clip_effects: clipEffects,
    },
    background_removal: config?.background_removal ?? { enabled: false, apply_to_broll: true, feather_px: 18, spill_suppression: .45, replacement: "gradient_ramp" },
    gradient_ramp: config?.gradient_ramp ?? { enabled: false, start_color: "#0f172a", end_color: "#f59e0b", angle_deg: 135, opacity: .28, blend_mode: "soft-light" },
    after_effects_presets: config?.after_effects_presets ?? [],
    force: true,
  };
}

// ── Upload screen (phase 1 — no video yet) ────────────────────────────────────

function UploadScreen({
  title, projectId, workspaceId, userId, apiAccessToken,
}: {
  title: string; projectId: string; workspaceId: string;
  userId: string; apiAccessToken: string;
}) {
  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden bg-background">
      <header className="pixel-grid-fine flex min-h-12 flex-wrap items-center gap-3 border-b border-border bg-surface/95 px-4 py-2">
        <div className="flex items-center gap-3">
          <Link
            href="/dashboard"
            className="pixel-button flex items-center gap-1 bg-surface px-2 py-1 text-sm text-muted-foreground transition hover:border-primary/30 hover:bg-muted hover:text-foreground"
          >
            <ChevronLeft className="size-4" />
            Dashboard
          </Link>
          <span className="text-sm font-semibold text-foreground">{title}</span>
        </div>
        <div className="ml-auto">
          <ThemeMenu items={[{ href: "/dashboard", label: "Dashboard" }]} />
        </div>
      </header>
      <div className="flex flex-1 items-start justify-center px-4 py-10 sm:px-6 lg:py-16">
        <div className="w-full max-w-5xl">
          <div className="mb-8 text-center">
            <div className="pixel-button mx-auto mb-4 flex size-14 items-center justify-center bg-primary/10">
              <Film className="size-7 text-primary" />
            </div>
            <h1 className="text-2xl font-bold">Upload your footage</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Drop in your raw talking-head video. We&apos;ll transcribe it and build the first AI edit directly on the timeline.
            </p>
          </div>
          <UploadPanel
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
          />
        </div>
      </div>
    </div>
  );
}

// ── Processing screen (phase 2 — upload processing) ───────────────────────────

function ProcessingScreen({ title, status }: { title: string; status: string }) {
  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden bg-background">
      <header className="pixel-grid-fine flex min-h-12 flex-wrap items-center gap-3 border-b border-border bg-surface/95 px-4 py-2">
        <div className="flex items-center gap-3">
          <Link
            href="/dashboard"
            className="pixel-button flex items-center gap-1 bg-surface px-2 py-1 text-sm text-muted-foreground transition hover:border-primary/30 hover:bg-muted hover:text-foreground"
          >
            <ChevronLeft className="size-4" />
            Dashboard
          </Link>
          <span className="text-sm font-semibold text-foreground">{title}</span>
        </div>
        <div className="ml-auto">
          <ThemeMenu items={[{ href: "/dashboard", label: "Dashboard" }]} />
        </div>
      </header>
      <div className="flex flex-1 flex-col items-center justify-center gap-5 px-4 text-center">
        <div className="pixel-panel flex size-16 items-center justify-center">
          <Loader2 className="size-8 animate-spin text-primary" />
        </div>
        <div>
          <p className="text-lg font-bold capitalize">{status.replace(/_/g, " ")}…</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Transcribing your footage and preparing the editor. This takes about a minute.
          </p>
        </div>
        <div className="pixel-progress h-2 w-full max-w-xs">
          <div className="pixel-progress-fill h-full w-2/3 animate-pulse" />
        </div>
        <p className="text-xs text-muted-foreground/50">This page will refresh automatically.</p>
      </div>
    </div>
  );
}
