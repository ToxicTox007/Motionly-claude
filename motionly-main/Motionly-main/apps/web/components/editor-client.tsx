"use client";

import React, {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
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
  Sparkles,
  Type,
  Layers,
  Music2,
  Palette,
  Download,
  Film,
  Loader2,
  ChevronLeft,
  History,
  Network,
  MessageSquare,
  Languages,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "./toast";
import { LoadingPixelSkeleton } from "./editor/ui";
import {
  apiFetch,
  editorApiRequest,
  pollPlanJob,
  resolveQueuedLayer,
  startAdaptivePolling,
  RevisionConflictError,
} from "@/lib/client-api";
import { env } from "@/lib/env";
import { buildTimelineState } from "@/lib/timeline";
import { useRevisionTracking } from "@/hooks/useRevisionTracking";
import { useWorkflowStatus } from "@/hooks/useWorkflowStatus";
import type { ConflictState } from "@/hooks/useRevisionTracking";
import { ConflictDialog } from "./editor/ConflictDialog";
import {
  PRODUCT_FLOW_STAGES,
  productFlowStageIndex,
  uploadStatusMessage,
} from "@/lib/product-flow-status";
import { EditorLayout } from "./editor-layout";
import {
  buildCreatorTasks,
  inspectorTabForCreatorTask,
} from "./editor/creator-flow";
import { ThemeMenu } from "./theme-menu";
import { UploadPanel } from "./upload-panel";
import { type PaletteElement } from "./editor/dnd/element-catalog";
import { PropertiesInspector } from "./editor/PropertiesInspector";
import {
  EditorHistoryProvider,
  useEditorHistory,
  type DurableHistoryAction,
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
  WorkflowDetail,
} from "@motionly/shared";

// ── Props (unchanged from old editor — project page needs no changes) ─────────

interface EditorClientProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  title: string;
  upload: UploadedVideoSummary;
  uploadAssetUrls: {
    source_video_url: string | null;
    thumbnail_url: string | null;
    source_reference_id?: string | null;
    thumbnail_reference_id?: string | null;
    expires_at?: string | null;
    expires_in_seconds?: number;
  };
  transcript: TranscriptSummary | null;
  captionTracks: CaptionTrackSummary[];
  editPlans: EditPlanRecordSummary[];
  activePlanId: string | null;
  brollSlots: BrollSlotSummary[];
  musicSelection: MusicSelectionSummary | null;
  effectsConfig: EffectsConfigSummary | null;
  renderJobs: RenderJobSummary[];
  exports: ExportSummary[];
  maxExportResolution: ExportResolution;
  visualStatus: VisualAnalysisStatusSummary;
  initialWorkflow: WorkflowDetail | null;
}

type InspectorTab =
  | "ai"
  | "captions"
  | "broll"
  | "audio"
  | "look"
  | "history"
  | "scale"
  | "review"
  | "localize"
  | "export";

type PendingReviewProposal = {
  proposalId: string;
  proposalRevision: number;
  statusUrl: string | null;
};

type EditorUiState = {
  title: string;
  saveStatus: "saving" | "saved" | "error";
  playheadMs: number;
  selectedClipId: string | null;
  inspectorTab: InspectorTab;
};

type EditorUiAction = {
  key: keyof EditorUiState;
  value: unknown;
};

function editorUiReducer(
  state: EditorUiState,
  action: EditorUiAction,
): EditorUiState {
  const current = state[action.key];
  const value =
    typeof action.value === "function"
      ? (action.value as (previous: typeof current) => typeof current)(current)
      : action.value;
  return { ...state, [action.key]: value } as EditorUiState;
}

const BrollTab = dynamic(
  () => import("./editor/tabs/broll-tab").then((mod) => mod.BrollTab),
  { loading: () => <InspectorLoading /> },
);
const CaptionsTab = dynamic(
  () => import("./editor/tabs/captions-tab").then((mod) => mod.CaptionsTab),
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
const AIStep = dynamic(
  () => import("./editor/ai-step").then((mod) => mod.AIStep),
  { loading: () => <InspectorLoading /> },
);
const ExportStep = dynamic(
  () => import("./editor/export-step").then((mod) => mod.ExportStep),
  { loading: () => <InspectorLoading /> },
);
const PublishingLibraryPanel = dynamic(
  () =>
    import("./editor/PublishingLibraryPanel").then(
      (mod) => mod.PublishingLibraryPanel,
    ),
  { loading: () => <InspectorLoading /> },
);
const VersionHistoryPanel = dynamic(
  () =>
    import("./editor/VersionHistoryPanel").then(
      (mod) => mod.VersionHistoryPanel,
    ),
  { loading: () => <InspectorLoading /> },
);
const ScalePanel = dynamic(
  () => import("./editor/ScalePanel").then((mod) => mod.ScalePanel),
  { loading: () => <InspectorLoading /> },
);
const ReviewPanel = dynamic(
  () => import("./editor/ReviewPanel").then((mod) => mod.ReviewPanel),
  { loading: () => <InspectorLoading /> },
);
const ElementPalette = dynamic(
  () => import("./editor/dnd/ElementPalette").then((mod) => mod.ElementPalette),
  { loading: () => <InspectorLoading /> },
);

// ── Main component ────────────────────────────────────────────────────────────

export function EditorClient(props: EditorClientProps) {
  const {
    projectId,
    workspaceId,
    userId,
    title,
    upload,
    uploadAssetUrls,
    transcript,
    captionTracks,
    brollSlots,
    musicSelection,
    effectsConfig,
    renderJobs,
    exports,
    maxExportResolution,
    visualStatus,
  } = props;
  const router = useRouter();
  const { toast } = useToast();
  const [editorUi, dispatchEditorUi] = useReducer(editorUiReducer, {
    title,
    saveStatus: "saved",
    playheadMs: 0,
    selectedClipId: null,
    inspectorTab: props.activePlanId ? "captions" : "ai",
  });
  const [pendingReviewProposal, setPendingReviewProposal] =
    useState<PendingReviewProposal | null>(null);
  const {
    title: localTitle,
    saveStatus,
    playheadMs,
    selectedClipId,
    inspectorTab,
  } = editorUi;
  const setLocalTitle = useCallback<
    React.Dispatch<React.SetStateAction<string>>
  >((value) => dispatchEditorUi({ key: "title", value }), []);
  const setSaveStatus = useCallback<
    React.Dispatch<React.SetStateAction<"saving" | "saved" | "error">>
  >((value) => dispatchEditorUi({ key: "saveStatus", value }), []);
  const setPlayheadMs = useCallback<
    React.Dispatch<React.SetStateAction<number>>
  >((value) => dispatchEditorUi({ key: "playheadMs", value }), []);
  const setSelectedClipId = useCallback<
    React.Dispatch<React.SetStateAction<string | null>>
  >((value) => dispatchEditorUi({ key: "selectedClipId", value }), []);
  const setInspectorTab = useCallback<
    React.Dispatch<React.SetStateAction<InspectorTab>>
  >((value) => dispatchEditorUi({ key: "inspectorTab", value }), []);
  const lastSavedTitleRef = useRef(title);

  // Local mutable copies of server state so the UI updates immediately
  // when AI generates a plan or user switches tabs (no round-trip wait).
  const [localEditPlans, setLocalEditPlans] = useState<EditPlanRecordSummary[]>(
    props.editPlans,
  );
  const [localActivePlanId, setLocalActivePlanId] = useState<string | null>(
    props.activePlanId,
  );
  const [localCaptionTracks, setLocalCaptionTracks] =
    useState<CaptionTrackSummary[]>(captionTracks);
  const [localTranscript, setLocalTranscript] =
    useState<TranscriptSummary | null>(transcript);
  const [localBrollSlots, setLocalBrollSlots] =
    useState<BrollSlotSummary[]>(brollSlots);
  const [localMusicSelection, setLocalMusicSelection] =
    useState<MusicSelectionSummary | null>(musicSelection);
  const [localEffectsConfig, setLocalEffectsConfig] =
    useState<EffectsConfigSummary | null>(effectsConfig);
  const [draftCaptionTrack, setDraftCaptionTrack] =
    useState<CaptionTrackSummary | null>(null);
  const [previewAudio, setPreviewAudio] = useState<PreviewAudioState | null>(
    null,
  );
  const [previewBroll, setPreviewBroll] = useState<PreviewBrollState | null>(
    null,
  );
  const [previewLook, setPreviewLook] = useState<PreviewLookState | null>(null);
  const [previewExport, setPreviewExport] = useState<PreviewExportState | null>(
    null,
  );

  const activePlan = useMemo(
    () => localEditPlans.find((p) => p.id === localActivePlanId) ?? null,
    [localEditPlans, localActivePlanId],
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
    [
      upload,
      activePlan,
      localCaptionTracks,
      localBrollSlots,
      localMusicSelection,
    ],
  );
  const selectedTimelineClip = useMemo(
    () =>
      timelineState.tracks
        .flatMap((track) => track.clips)
        .find((clip) => clip.id === selectedClipId) ?? null,
    [selectedClipId, timelineState],
  );
  const activePreviewLook = useMemo(() => {
    const base =
      previewLook ?? previewLookFromEffectsConfig(localEffectsConfig);
    if (!base) return null;
    const mattePreview = visualStatus.matte_preview;
    const withMatte = {
      ...base,
      backgroundRemoval: {
        enabled: Boolean(localEffectsConfig?.background_removal.enabled),
        featherPx: localEffectsConfig?.background_removal.feather_px ?? 18,
      },
      mattePreview: localEffectsConfig?.background_removal.enabled
        ? {
            status: mattePreview?.status ?? "approximate",
            reason: mattePreview?.reason ?? "matte_pending",
            previewUrl: mattePreview?.preview_url ?? null,
            bucket: mattePreview?.bucket ?? null,
            path: mattePreview?.path ?? null,
            cacheKey: mattePreview?.cache_key ?? null,
            source: mattePreview?.source ?? "approximate_preview",
          }
        : {
            status: "skipped" as const,
            reason: "matte_not_requested",
            previewUrl: null,
            source: "none" as const,
          },
    };
    if (!selectedTimelineClip || selectedTimelineClip.sourceRef.kind !== "cut")
      return withMatte;
    if (withMatte.selectedSourceClipId) return withMatte;
    return {
      ...withMatte,
      selectedSourceClipId: selectedTimelineClip.id,
      selectedSourceClipStartMs: selectedTimelineClip.startMs,
      selectedSourceClipEndMs: selectedTimelineClip.endMs,
    };
  }, [
    localEffectsConfig,
    previewLook,
    selectedTimelineClip,
    visualStatus.matte_preview,
  ]);

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
    let stopPolling: (() => void) | null = null;
    const poll = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const response = await apiFetch(
          `${env.apiBaseUrl}/projects/${projectId}/transcript`,
          { cache: "no-store" },
          { workspaceId },
        );
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
          stopPolling?.();
        }
      } catch (error) {
        console.error(error);
      }
    };

    stopPolling = startAdaptivePolling(() => poll(), {
      intervalMs: 5000,
      maxIntervalMs: 15000,
    });
    return () => {
      cancelled = true;
      stopPolling?.();
    };
  }, [localTranscript?.status, projectId, router, upload, workspaceId]);

  // When AI generates a new plan, update locally and advance inspector
  const handlePlanGenerated = useCallback(
    (plan: EditPlanRecordSummary) => {
      setLocalEditPlans((prev) => {
        const without = prev.filter((p) => p.id !== plan.id);
        return [...without, plan];
      });
      setLocalActivePlanId(plan.id);
      setInspectorTab("captions");
    },
    [setInspectorTab],
  );

  const revisionTracking = useRevisionTracking(projectId, localActivePlanId);
  const {
    updateRevision,
    handleConflict,
    clearConflict,
    conflictState,
    getExpectedRevision,
  } = revisionTracking;

  const handlePlanUpdated = useCallback(
    (plan: EditPlanRecordSummary) => {
      setLocalEditPlans((prev) => {
        const without = prev.filter((p) => p.id !== plan.id);
        return [plan, ...without];
      });
      setLocalActivePlanId(plan.id);
      // Track revision from API response (backward compatible — ignores null)
      if ((plan as any).revision != null) {
        updateRevision((plan as any).revision, "plan");
      }
    },
    [updateRevision],
  );

  const synchronizeCanonicalTimeline = useCallback(async () => {
    if (!localActivePlanId) return;
    try {
      const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/${localActivePlanId}/compile-timeline`,
        {
          method: "POST",
          workspaceId,
          friendlyError: "The timeline could not be synchronized.",
          body: {},
        },
      );
      handlePlanUpdated(payload.plan);
    } catch (error) {
      console.error(error);
    }
  }, [handlePlanUpdated, localActivePlanId, projectId, workspaceId]);

  const handleCaptionTrackUpdated = useCallback(
    (track: CaptionTrackSummary) => {
      setDraftCaptionTrack(track);
      setLocalCaptionTracks((prev) => [
        track,
        ...prev.filter((item) => item.id !== track.id),
      ]);
      void synchronizeCanonicalTimeline();
    },
    [synchronizeCanonicalTimeline],
  );

  const handleBrollSlotUpdated = useCallback(
    (slot: BrollSlotSummary) => {
      setLocalBrollSlots((prev) =>
        prev
          .map((item) => (item.id === slot.id ? slot : item))
          .sort((a, b) => a.start_ms - b.start_ms),
      );
      void synchronizeCanonicalTimeline();
    },
    [synchronizeCanonicalTimeline],
  );

  const handleBrollSlotsUpdated = useCallback(
    (slots: BrollSlotSummary[]) => {
      setLocalBrollSlots([...slots].sort((a, b) => a.start_ms - b.start_ms));
      void synchronizeCanonicalTimeline();
    },
    [synchronizeCanonicalTimeline],
  );

  const handleMusicSelectionUpdated = useCallback(
    (selection: MusicSelectionSummary) => {
      setLocalMusicSelection(selection);
      void synchronizeCanonicalTimeline();
    },
    [synchronizeCanonicalTimeline],
  );

  const handleEffectsConfigUpdated = useCallback(
    (config: EffectsConfigSummary) => {
      setLocalEffectsConfig(config);
      void synchronizeCanonicalTimeline();
    },
    [synchronizeCanonicalTimeline],
  );

  const executeHistoryAction = useCallback(
    async (action: DurableHistoryAction) => {
      const expectedRev =
        action.resultKind === "plan" ? getExpectedRevision() : undefined;
      const payload = await editorApiRequest<Record<string, any>>(
        `${env.apiBaseUrl}${action.path}`,
        {
          method: "PATCH",
          workspaceId,
          friendlyError:
            "The saved editor history action could not be applied.",
          body: {
            ...action.body,
            ...(expectedRev != null ? { expected_revision: expectedRev } : {}),
          },
        },
      );
      if (action.resultKind === "plan" && payload.plan)
        handlePlanUpdated(payload.plan);
      if (action.resultKind === "caption" && payload.track)
        handleCaptionTrackUpdated(payload.track);
      if (action.resultKind === "broll" && payload.slot)
        handleBrollSlotUpdated(payload.slot);
      if (action.resultKind === "music")
        handleMusicSelectionUpdated(
          payload as unknown as MusicSelectionSummary,
        );
      router.refresh();
      return action.successLabel;
    },
    [
      getExpectedRevision,
      handleBrollSlotUpdated,
      handleCaptionTrackUpdated,
      handleMusicSelectionUpdated,
      handlePlanUpdated,
      router,
      workspaceId,
    ],
  );

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
        toast({
          title: "Save Failed",
          description:
            "Project title could not be saved. Check your connection.",
          variant: "error",
        });
      }
    },
    [projectId, router, setLocalTitle, setSaveStatus, workspaceId, toast],
  );

  // ── Conflict resolution handlers ──────────────────────────────────────────
  const refetchActivePlan = useCallback(async () => {
    if (!localActivePlanId) return;
    try {
      const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/${localActivePlanId}`,
        {
          workspaceId,
          friendlyError: "Could not reload the plan.",
        },
      );
      handlePlanUpdated(payload.plan);
      clearConflict();
      router.refresh();
    } catch (error) {
      console.error("Failed to refetch plan after conflict:", error);
    }
  }, [
    clearConflict,
    handlePlanUpdated,
    localActivePlanId,
    projectId,
    router,
    workspaceId,
  ]);

  // The server route renders lightweight upload/processing clients before this
  // ready-only editor entry is referenced.
  return (
    <EditorHistoryProvider
      storageKey={`motionly:editor-history:${workspaceId}:${projectId}`}
      executeAction={executeHistoryAction}
    >
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
        conflictState={conflictState}
        onConflictReload={refetchActivePlan}
        onConflictForceSave={clearConflict}
        onConflictDismiss={clearConflict}
        getExpectedRevision={getExpectedRevision}
        onConflictDetected={handleConflict}
        planRevision={revisionTracking.planRevision}
        pendingReviewProposal={pendingReviewProposal}
        setPendingReviewProposal={setPendingReviewProposal}
        onReviewProposalCreated={(proposal) => {
          setPendingReviewProposal(proposal);
          setInspectorTab("ai");
        }}
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
  setLocalEditPlans: React.Dispatch<
    React.SetStateAction<EditPlanRecordSummary[]>
  >;
  setLocalActivePlanId: React.Dispatch<React.SetStateAction<string | null>>;
  setLocalCaptionTracks: React.Dispatch<
    React.SetStateAction<CaptionTrackSummary[]>
  >;
  setLocalTranscript: React.Dispatch<
    React.SetStateAction<TranscriptSummary | null>
  >;
  setLocalBrollSlots: React.Dispatch<React.SetStateAction<BrollSlotSummary[]>>;
  setLocalMusicSelection: React.Dispatch<
    React.SetStateAction<MusicSelectionSummary | null>
  >;
  setLocalEffectsConfig: React.Dispatch<
    React.SetStateAction<EffectsConfigSummary | null>
  >;
  setDraftCaptionTrack: React.Dispatch<
    React.SetStateAction<CaptionTrackSummary | null>
  >;
  setPreviewAudio: React.Dispatch<
    React.SetStateAction<PreviewAudioState | null>
  >;
  setPreviewBroll: React.Dispatch<
    React.SetStateAction<PreviewBrollState | null>
  >;
  setPreviewLook: React.Dispatch<React.SetStateAction<PreviewLookState | null>>;
  setPreviewExport: React.Dispatch<
    React.SetStateAction<PreviewExportState | null>
  >;
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
  conflictState: ConflictState | null;
  onConflictReload: () => Promise<void>;
  onConflictForceSave: () => void;
  onConflictDismiss: () => void;
  getExpectedRevision: () => number | undefined;
  onConflictDetected: (error: unknown, source: string) => boolean;
  planRevision: number | null;
  pendingReviewProposal: PendingReviewProposal | null;
  setPendingReviewProposal: React.Dispatch<
    React.SetStateAction<PendingReviewProposal | null>
  >;
  onReviewProposalCreated: (proposal: PendingReviewProposal) => void;
}

function EditorClientEditor(props: EditorClientEditorProps) {
  const {
    projectId,
    workspaceId,
    userId,
    title,
    upload,
    uploadAssetUrls,
    transcript,
    captionTracks,
    brollSlots,
    musicSelection,
    effectsConfig,
    renderJobs,
    exports,
    maxExportResolution,
    visualStatus,
    localEditPlans,
    localActivePlanId,
    localCaptionTracks,
    localTranscript,
    localBrollSlots,
    localMusicSelection,
    localEffectsConfig,
    draftCaptionTrack,
    previewAudio,
    previewBroll,
    previewLook,
    previewExport,
    activePlan,
    timelineState,
    selectedTimelineClip,
    activePreviewLook,
    localTitle,
    saveStatus,
    playheadMs,
    selectedClipId,
    inspectorTab,
    setLocalEditPlans,
    setLocalActivePlanId,
    setLocalCaptionTracks,
    setLocalTranscript,
    setLocalBrollSlots,
    setLocalMusicSelection,
    setLocalEffectsConfig,
    setDraftCaptionTrack,
    setPreviewAudio,
    setPreviewBroll,
    setPreviewLook,
    setPreviewExport,
    setPlayheadMs,
    setSelectedClipId,
    setInspectorTab,
    setLocalTitle,
    handlePlanGenerated,
    handlePlanUpdated,
    handleCaptionTrackUpdated,
    handleBrollSlotUpdated,
    handleBrollSlotsUpdated,
    handleMusicSelectionUpdated,
    handleEffectsConfigUpdated,
    saveProjectTitle,
    conflictState,
    onConflictReload,
    onConflictForceSave,
    onConflictDismiss,
    getExpectedRevision,
    onConflictDetected,
    planRevision,
    pendingReviewProposal,
    setPendingReviewProposal,
    onReviewProposalCreated,
  } = props;
  const router = useRouter();
  const { toast } = useToast();
  const { undo, redo, canUndo, canRedo, pushHistory } = useEditorHistory();
  const workflowStatus = useWorkflowStatus({
    projectId,
    workspaceId,
    initialWorkflow: props.initialWorkflow,
  });
  const videoRef = useRef<HTMLVideoElement>(null);
  const [mediaAssetUrls, setMediaAssetUrls] = useState(uploadAssetUrls);
  const mediaRefreshPromiseRef = useRef<Promise<boolean> | null>(null);
  const sourceErrorRefreshUsedRef = useRef(false);
  const posterErrorRefreshUsedRef = useRef(false);
  const aiElementAbortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => aiElementAbortRef.current?.abort();
  }, []);

  const refreshMediaUrls = useCallback((): Promise<boolean> => {
    if (mediaRefreshPromiseRef.current) return mediaRefreshPromiseRef.current;
    const referenceIds = [
      uploadAssetUrls.source_reference_id,
      uploadAssetUrls.thumbnail_reference_id,
    ].filter((value): value is string => Boolean(value));
    if (!referenceIds.length) return Promise.resolve(false);

    const request = (async () => {
      try {
        const response = await apiFetch(
          `${env.apiBaseUrl}/projects/${projectId}/media/refresh-urls`,
          {
            method: "POST",
            body: JSON.stringify({ reference_ids: referenceIds }),
            cache: "no-store",
          },
          { workspaceId, json: true },
        );
        const payload = (await response.json().catch(() => null)) as {
          refreshed?: Array<{
            reference_id: string;
            url: string;
            expires_at: string;
            object_type: string;
          }>;
          error?: { message?: string };
        } | null;
        if (!response.ok) {
          throw new Error(
            payload?.error?.message ??
              `Media URLs could not be refreshed (${response.status}).`,
          );
        }
        const refreshed = payload?.refreshed ?? [];
        if (!refreshed.length) return false;
        setMediaAssetUrls((current) => {
          const source = refreshed.find(
            (item) =>
              item.reference_id === uploadAssetUrls.source_reference_id ||
              (!uploadAssetUrls.source_reference_id &&
                item.object_type === "source_video"),
          );
          const thumbnail = refreshed.find(
            (item) =>
              item.reference_id === uploadAssetUrls.thumbnail_reference_id ||
              (!uploadAssetUrls.thumbnail_reference_id &&
                item.object_type === "thumbnail"),
          );
          const expiries = refreshed
            .map((item) => item.expires_at)
            .filter((value) => Number.isFinite(Date.parse(value)))
            .sort((left, right) => Date.parse(left) - Date.parse(right));
          return {
            ...current,
            source_video_url: source?.url ?? current.source_video_url,
            thumbnail_url: thumbnail?.url ?? current.thumbnail_url,
            expires_at: expiries[0] ?? current.expires_at,
          };
        });
        return true;
      } catch (error) {
        console.error("Failed to refresh browser media URLs:", error);
        return false;
      }
    })();
    mediaRefreshPromiseRef.current = request;
    void request.finally(() => {
      if (mediaRefreshPromiseRef.current === request) {
        mediaRefreshPromiseRef.current = null;
      }
    });
    return request;
  }, [
    projectId,
    uploadAssetUrls.source_reference_id,
    uploadAssetUrls.thumbnail_reference_id,
    workspaceId,
  ]);

  useEffect(() => {
    const hasReferences = Boolean(
      uploadAssetUrls.source_reference_id ||
      uploadAssetUrls.thumbnail_reference_id,
    );
    if (!hasReferences || !mediaAssetUrls.expires_at) return;
    const expiresAt = Date.parse(mediaAssetUrls.expires_at);
    if (!Number.isFinite(expiresAt)) return;
    const refreshInMs = Math.max(0, expiresAt - Date.now() - 60_000);
    const timeout = window.setTimeout(() => {
      void refreshMediaUrls();
    }, refreshInMs);
    return () => window.clearTimeout(timeout);
  }, [
    mediaAssetUrls.expires_at,
    refreshMediaUrls,
    uploadAssetUrls.source_reference_id,
    uploadAssetUrls.thumbnail_reference_id,
  ]);

  useEffect(() => {
    const hasReferences = Boolean(
      uploadAssetUrls.source_reference_id ||
      uploadAssetUrls.thumbnail_reference_id,
    );
    if (!hasReferences) return;
    const refreshAfterSleep = () => {
      const expiry = mediaAssetUrls.expires_at
        ? Date.parse(mediaAssetUrls.expires_at)
        : Number.NaN;
      if (!Number.isFinite(expiry) || expiry <= Date.now() + 60_000) {
        void refreshMediaUrls();
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refreshAfterSleep();
    };
    window.addEventListener("focus", refreshAfterSleep);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("focus", refreshAfterSleep);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [
    mediaAssetUrls.expires_at,
    refreshMediaUrls,
    uploadAssetUrls.source_reference_id,
    uploadAssetUrls.thumbnail_reference_id,
  ]);

  const handlePreviewSourceError = useCallback(() => {
    if (sourceErrorRefreshUsedRef.current) return;
    sourceErrorRefreshUsedRef.current = true;
    void refreshMediaUrls();
  }, [refreshMediaUrls]);

  const handlePreviewPosterError = useCallback(() => {
    if (posterErrorRefreshUsedRef.current) return;
    posterErrorRefreshUsedRef.current = true;
    void refreshMediaUrls();
  }, [refreshMediaUrls]);

  const [selectedElementId, setSelectedElementId] = useState<string | null>(
    null,
  );
  const [activePaletteElement, setActivePaletteElement] =
    useState<PaletteElement | null>(null);
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
        : (activePlan?.plan.manual_elements ?? []),
    [activePlan],
  );
  const selectedManualElement =
    manualElements.find((element) => element.id === selectedElementId) ?? null;
  const creatorTasks = useMemo(
    () =>
      buildCreatorTasks({
        upload: upload!,
        transcript: localTranscript,
        visualStatus,
        activePlan,
        captionTracks: localCaptionTracks,
        brollSlots: localBrollSlots,
        musicSelection: localMusicSelection,
        renderJobs,
        exports,
        workflow: workflowStatus.workflow,
      }),
    [
      activePlan,
      exports,
      localBrollSlots,
      localCaptionTracks,
      localMusicSelection,
      localTranscript,
      renderJobs,
      upload,
      visualStatus,
      workflowStatus.workflow,
    ],
  );
  const activeCreatorTask =
    creatorTasks.find((task) => task.status === "blocked") ??
    creatorTasks.find((task) => task.status === "active") ??
    creatorTasks.find((task) => task.status === "waiting") ??
    creatorTasks[creatorTasks.length - 1];
  const creatorProgressPct = Math.round(
    (creatorTasks.filter((task) => task.status === "done").length /
      creatorTasks.length) *
      100,
  );
  const canonicalWorkflow = workflowStatus.workflow;
  const hasCancelableWorkflow = Boolean(
    canonicalWorkflow?.allowed_actions.includes("cancel"),
  );
  const hasRetryableWorkflow = Boolean(
    canonicalWorkflow?.allowed_actions.includes("retry"),
  );
  const workflowStateLabel = canonicalWorkflow
    ? canonicalWorkflow.state.replaceAll("_", " ")
    : "Discovering workflow";
  const jobLabel = canonicalWorkflow?.draft_render
    ? `${workflowStateLabel} · ${canonicalWorkflow.draft_render.progress_pct}%`
    : workflowStateLabel;

  const setPlayheadMsWrapped = useCallback(
    (ms: number | ((prev: number) => number)) => {
      setPlayheadMs(ms);
    },
    [setPlayheadMs],
  );

  const setSelectedClipIdWrapped = useCallback(
    (id: string | null) => {
      setSelectedClipId(id);
    },
    [setSelectedClipId],
  );

  const setInspectorTabWrapped = useCallback(
    (tab: InspectorTab) => {
      setInspectorTab(tab);
    },
    [setInspectorTab],
  );

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const isTyping = ["INPUT", "TEXTAREA", "SELECT"].includes(
        target?.tagName ?? "",
      );
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

  const persistManualElements = useCallback(
    async (elements: ManualElement[]) => {
      if (!activePlan)
        throw new Error("Generate an AI edit before placing elements.");
      const expectedRev = getExpectedRevision();
      const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/${activePlan.id}/manual-elements`,
        {
          method: "PATCH",
          workspaceId,
          friendlyError: "Element changes could not be saved.",
          body: {
            manual_elements: elements,
            ...(expectedRev != null ? { expected_revision: expectedRev } : {}),
          },
        },
      );
      handlePlanUpdated(payload.plan);
      return payload.plan;
    },
    [
      activePlan,
      getExpectedRevision,
      handlePlanUpdated,
      projectId,
      workspaceId,
    ],
  );

  const commitManualElements = useCallback(
    async (next: ManualElement[], label: string, before = manualElements) => {
      setElementSaving(true);
      setDropMessage(null);
      try {
        await persistManualElements(next);
        pushHistory({
          id: `element-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          label,
          undo: async () => {
            await persistManualElements(before);
            return `${label} undone.`;
          },
          redo: async () => {
            await persistManualElements(next);
            return `${label} redone.`;
          },
          undoAction: {
            path: `/projects/${projectId}/plans/${activePlan!.id}/manual-elements`,
            body: { manual_elements: before },
            resultKind: "plan",
            successLabel: `${label} undone.`,
          },
          redoAction: {
            path: `/projects/${projectId}/plans/${activePlan!.id}/manual-elements`,
            body: { manual_elements: next },
            resultKind: "plan",
            successLabel: `${label} redone.`,
          },
        });
        setDropMessage(`${label} saved.`);
      } catch (error) {
        if (onConflictDetected(error, "manual_elements")) return;
        setDropMessage((error as Error).message);
        toast({
          title: "Action Failed",
          description: (error as Error).message,
          variant: "error",
        });
      } finally {
        setElementSaving(false);
      }
    },
    [
      activePlan,
      manualElements,
      onConflictDetected,
      persistManualElements,
      projectId,
      pushHistory,
      toast,
    ],
  );

  const runAIElement = useCallback(
    async (entry: PaletteElement, clip: TimelineClip | null) => {
      if (!entry.aiAction) return;
      aiElementAbortRef.current?.abort();
      const operationController = new AbortController();
      aiElementAbortRef.current = operationController;
      const { signal } = operationController;
      setElementSaving(true);
      setDropMessage(`Running ${entry.name}…`);
      try {
        if (entry.aiAction === "auto_caption") {
          const payload = await editorApiRequest<{
            track?: CaptionTrackSummary | null;
            status: "ready" | "queued";
            status_url?: string | null;
          }>(`${env.apiBaseUrl}/projects/${projectId}/captions/generate`, {
            method: "POST",
            workspaceId,
            signal,
            friendlyError: "Auto-captioning failed.",
            body: { preset: "bold_influencer", hide_filler_words: false },
          });
          const track = await resolveQueuedLayer({
            submission: payload,
            immediate: payload.track,
            apiBaseUrl: env.apiBaseUrl,
            options: {
              workspaceId,
              signal,
            },
            load: async () => {
              const result = await editorApiRequest<{
                tracks: CaptionTrackSummary[];
              }>(`${env.apiBaseUrl}/projects/${projectId}/captions`, {
                workspaceId,
                signal,
              });
              return result.tracks[0] ?? null;
            },
            resourceLabel: "Captions",
            onProgress: (progress) =>
              setDropMessage(`Running ${entry.name}: ${progress}%`),
          });
          handleCaptionTrackUpdated(track);
        } else if (entry.aiAction === "smart_broll") {
          const payload = await editorApiRequest<{
            slots?: BrollSlotSummary[] | null;
            status: "ready" | "queued";
            status_url?: string | null;
          }>(`${env.apiBaseUrl}/projects/${projectId}/broll/generate`, {
            method: "POST",
            workspaceId,
            signal,
            friendlyError: "Smart B-roll fill failed.",
            body: { density: "from_plan" },
          });
          const slots = await resolveQueuedLayer({
            submission: payload,
            immediate: payload.slots,
            apiBaseUrl: env.apiBaseUrl,
            options: {
              workspaceId,
              signal,
            },
            load: async () => {
              const result = await editorApiRequest<{
                slots: BrollSlotSummary[];
              }>(`${env.apiBaseUrl}/projects/${projectId}/broll`, {
                workspaceId,
                signal,
              });
              return result.slots;
            },
            resourceLabel: "B-roll",
            onProgress: (progress) =>
              setDropMessage(`Running ${entry.name}: ${progress}%`),
          });
          handleBrollSlotsUpdated(slots);
        } else if (entry.aiAction === "music_match") {
          const payload = await editorApiRequest<{
            selection?: MusicSelectionSummary | null;
            status: "ready" | "queued";
            status_url?: string | null;
          }>(`${env.apiBaseUrl}/projects/${projectId}/music/select`, {
            method: "POST",
            workspaceId,
            signal,
            friendlyError: "AI music matching failed.",
            body: {
              mood: activePlan?.plan.music_mood,
              include_music: true,
              music_volume: 0.25,
              sfx_intensity: 0.25,
              rights_decision: false,
            },
          });
          const selection = await resolveQueuedLayer({
            submission: payload,
            immediate: payload.selection,
            apiBaseUrl: env.apiBaseUrl,
            options: {
              workspaceId,
              signal,
            },
            load: () =>
              editorApiRequest<MusicSelectionSummary | null>(
                `${env.apiBaseUrl}/projects/${projectId}/music/selection`,
                { workspaceId, signal },
              ),
            resourceLabel: "Music selection",
            onProgress: (progress) =>
              setDropMessage(`Running ${entry.name}: ${progress}%`),
          });
          handleMusicSelectionUpdated(selection);
        } else {
          const prompt =
            entry.aiAction === "highlight_reel"
              ? `Create the strongest 30-60 second highlight reel${clip ? ` emphasizing ${clip.startMs}-${clip.endMs}ms` : ""}.`
              : "Re-analyze the audio and visual style, then update captions, grade, motion, and pacing.";
          const payload = await editorApiRequest<{
            plan?: EditPlanRecordSummary;
            status: string;
            job_id?: string;
            status_url?: string;
          }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
            method: "POST",
            workspaceId,
            signal,
            friendlyError: `${entry.name} failed.`,
            body: {
              prompt,
              target_duration_s:
                entry.aiAction === "highlight_reel"
                  ? 45
                  : (activePlan?.target_duration_s ?? 35),
              broll_density: activePlan?.broll_density ?? "medium",
            },
          });

          let plan: EditPlanRecordSummary;
          if (payload.status === "queued" && payload.status_url) {
            setDropMessage(`Running ${entry.name}…`);
            const identity = await pollPlanJob(
              `${env.apiBaseUrl}${payload.status_url}`,
              {
                workspaceId,
                signal,
              },
              (progress) => {
                setDropMessage(`Running ${entry.name}: ${progress}%`);
              },
              true,
            );
            if (identity.status === "awaiting_approval") {
              onReviewProposalCreated({
                proposalId: identity.proposalId,
                proposalRevision: identity.proposalRevision,
                statusUrl: payload.status_url,
              });
              setDropMessage(
                `${entry.name} proposal is ready for review in the AI panel.`,
              );
              return;
            }
            const planId = identity.planId;
            const planList = await editorApiRequest<{
              plans: EditPlanRecordSummary[];
            }>(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
              workspaceId,
              signal,
            });
            const foundPlan = planList.plans.find((p) => p.id === planId);
            if (!foundPlan) {
              throw new Error(
                "Plan was generated successfully but could not be loaded.",
              );
            }
            plan = foundPlan;
          } else {
            plan = payload.plan as EditPlanRecordSummary;
          }
          handlePlanGenerated(plan);
        }

        signal.throwIfAborted();
        if (aiElementAbortRef.current !== operationController) return;
        setDropMessage(`${entry.name} complete.`);
      } catch (error) {
        if (
          signal.aborted ||
          aiElementAbortRef.current !== operationController ||
          (error as Error).name === "AbortError"
        ) {
          return;
        }
        setDropMessage((error as Error).message);
        toast({
          title: "AI Action Failed",
          description: (error as Error).message,
          variant: "error",
        });
      } finally {
        if (aiElementAbortRef.current === operationController) {
          aiElementAbortRef.current = null;
          setElementSaving(false);
        }
      }
    },
    [
      activePlan,
      handleBrollSlotsUpdated,
      handleCaptionTrackUpdated,
      handleMusicSelectionUpdated,
      handlePlanGenerated,
      onReviewProposalCreated,
      projectId,
      workspaceId,
      toast,
    ],
  );

  const applyClipEffect = useCallback(
    async (entry: PaletteElement, clip: TimelineClip, applyAll: boolean) => {
      const current = localEffectsConfig;
      const motionEffect = entry.motionEffect;
      const clipEffects = { ...(current?.motion.clip_effects ?? {}) };
      if (motionEffect) {
        if (applyAll)
          timelineState.tracks
            .find((track) => track.kind === "video")
            ?.clips.forEach((item) => {
              clipEffects[item.id] = unique([
                ...(clipEffects[item.id] ?? []),
                motionEffect,
              ]);
            });
        else
          clipEffects[clip.id] = unique([
            ...(clipEffects[clip.id] ?? []),
            motionEffect,
          ]);
      }
      const body = effectsRequestBody(
        current,
        entry.grade,
        motionEffect,
        clipEffects,
      );
      setElementSaving(true);
      try {
        const payload = await editorApiRequest<{
          config?: EffectsConfigSummary | null;
          status: "ready" | "queued";
          status_url?: string | null;
        }>(`${env.apiBaseUrl}/projects/${projectId}/effects`, {
          method: "PATCH",
          workspaceId,
          friendlyError: "Clip effect could not be applied.",
          body,
        });
        const config = await resolveQueuedLayer({
          submission: payload,
          immediate: payload.config,
          apiBaseUrl: env.apiBaseUrl,
          options: { workspaceId },
          load: () =>
            editorApiRequest<EffectsConfigSummary | null>(
              `${env.apiBaseUrl}/projects/${projectId}/effects`,
              { workspaceId },
            ),
          resourceLabel: "Clip effect",
          onProgress: (progress) =>
            setDropMessage(`Applying ${entry.name}: ${progress}%`),
        });
        handleEffectsConfigUpdated(config);
        setSelectedClipIdWrapped(clip.id);
        setInspectorTabWrapped("look");
        setDropMessage(
          `${entry.name} applied ${applyAll ? "to all clips" : `to ${clip.label}`}.`,
        );
      } catch (error) {
        setDropMessage((error as Error).message);
        toast({
          title: "Effect Failed",
          description: (error as Error).message,
          variant: "error",
        });
      } finally {
        setElementSaving(false);
      }
    },
    [
      handleEffectsConfigUpdated,
      localEffectsConfig,
      projectId,
      setInspectorTabWrapped,
      setSelectedClipIdWrapped,
      timelineState.tracks,
      workspaceId,
      toast,
    ],
  );

  const handleDragStart = useCallback((event: DragStartEvent) => {
    const data = event.active.data.current;
    setActivePaletteElement(
      data?.kind === "palette" ? (data.element as PaletteElement) : null,
    );
  }, []);

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
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
        const nextElement = {
          ...placed,
          id: duplicate ? `${placed.id}-copy-${Date.now()}` : placed.id,
          x_pct: snap(
            ((center.left + center.width / 2 - over.rect.left) /
              over.rect.width) *
              100,
            step,
            0,
            100,
          ),
          y_pct: snap(
            ((center.top + center.height / 2 - over.rect.top) /
              over.rect.height) *
              100,
            step,
            0,
            100,
          ),
        };
        const next = duplicate
          ? [...manualElements, nextElement]
          : manualElements.map((item) =>
              item.id === placed.id ? nextElement : item,
            );
        setSelectedElementId(nextElement.id);
        void commitManualElements(
          next,
          duplicate ? `Duplicate ${placed.name}` : `Move ${placed.name}`,
        );
        return;
      }
      if (activeData?.kind !== "palette") return;
      const entry = activeData.element as PaletteElement;
      const zone = String(
        overData?.zone ?? (String(over.id) === "canvas" ? "canvas" : ""),
      );
      const acceptedZone = zone === "transition" ? "transition" : zone;
      if (!entry.accepts.includes(acceptedZone as never)) {
        setDropMessage(
          `${entry.name} cannot be dropped on ${zone || "this target"}.`,
        );
        return;
      }
      const clip = (overData?.clip as TimelineClip | undefined) ?? null;
      if (entry.elementType === "ai") {
        void runAIElement(entry, clip);
        return;
      }
      if ((entry.grade || entry.motionEffect) && clip) {
        void applyClipEffect(entry, clip, applyAll);
        return;
      }
      if (!activePlan) {
        setDropMessage("Generate an AI edit before placing elements.");
        return;
      }
      const translated = event.active.rect.current.translated;
      let startMs = clip?.startMs ?? playheadMs;
      let endMs =
        clip?.endMs ??
        Math.min(
          timelineState.totalDurationMs,
          startMs + (entry.durationMs ?? 3000),
        );
      let xPct = 50,
        yPct = 50;
      if (zone === "canvas" && translated) {
        const step = fineSnap ? 1 : 5;
        xPct = snap(
          ((translated.left + translated.width / 2 - over.rect.left) /
            over.rect.width) *
            100,
          step,
          0,
          100,
        );
        yPct = snap(
          ((translated.top + translated.height / 2 - over.rect.top) /
            over.rect.height) *
            100,
          step,
          0,
          100,
        );
      } else if (zone === "timeline" && translated) {
        const stepMs = fineSnap ? 100 : 500;
        const rawStartMs =
          ((translated.left + translated.width / 2 - over.rect.left) /
            over.rect.width) *
          timelineState.totalDurationMs;
        const boundaries = timelineState.tracks.flatMap((track) =>
          track.clips.flatMap((item) => [item.startMs, item.endMs]),
        );
        startMs = snapTimelineMs(
          rawStartMs,
          stepMs,
          fineSnap ? 60 : 260,
          boundaries,
          timelineState.totalDurationMs,
        );
        endMs = Math.min(
          timelineState.totalDurationMs,
          startMs + (entry.durationMs ?? 3000),
        );
      } else if (zone === "transition") {
        const afterIndex = Number(overData?.afterClipIndex ?? 0);
        const boundary =
          timelineState.tracks.find((track) => track.kind === "video")?.clips[
            afterIndex
          ]?.endMs ?? playheadMs;
        const duration = Math.max(1, entry.durationMs ?? 350);
        startMs = Math.max(0, boundary - Math.floor(duration / 2));
        endMs = Math.min(timelineState.totalDurationMs, startMs + duration);
      }
      if (endMs <= startMs) endMs = startMs + 1;
      const element: ManualElement = {
        id: `${entry.id}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        type: entry.elementType as ManualElement["type"],
        name: entry.name,
        zone:
          zone === "transition" ? "timeline" : (zone as ManualElement["zone"]),
        start_ms: Math.round(startMs),
        end_ms: Math.round(endMs),
        x_pct: xPct,
        y_pct: yPct,
        width_pct: entry.elementType === "shape" ? 24 : 48,
        height_pct: entry.elementType === "shape" ? 18 : 14,
        rotation_deg: 0,
        opacity: 1,
        clip_id: clip?.id ?? null,
        properties: {
          ...(entry.properties ?? {}),
          durationMs: entry.durationMs,
        },
        ai_reason: null,
        confidence: null,
      };
      setSelectedElementId(element.id);
      void commitManualElements(
        [...manualElements, element],
        `Add ${entry.name}`,
      );
    },
    [
      activePlan,
      applyClipEffect,
      commitManualElements,
      manualElements,
      playheadMs,
      runAIElement,
      timelineState,
    ],
  );

  if (!upload || upload.status !== "ready") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={editorCollisionDetection}
      onDragStart={handleDragStart}
      onDragCancel={() => setActivePaletteElement(null)}
      onDragEnd={handleDragEnd}
    >
      <EditorLayout
        title={localTitle}
        onTitleChange={setLocalTitle}
        onTitleCommit={saveProjectTitle}
        saveStatus={elementSaving ? "saving" : saveStatus}
        onExport={() => setInspectorTabWrapped("export")}
        onAiEdit={() => setInspectorTabWrapped("ai")}
        creatorFlow={{
          tasks: creatorTasks,
          activeTaskKey: activeCreatorTask?.key ?? creatorTasks[0].key,
          progressPct: creatorProgressPct,
          workflowStatusLabel: canonicalWorkflow
            ? `Workflow: ${workflowStateLabel}`
            : "Workflow: discovering latest run",
          workflowStatusDetail:
            workflowStatus.error ??
            canonicalWorkflow?.draft_render?.error_message ??
            jobLabel,
          onTaskSelect: (task) =>
            setInspectorTabWrapped(inspectorTabForCreatorTask(task.key)),
        }}
        commandBar={{
          canUndo,
          canRedo,
          onUndo: () => void undo(),
          onRedo: () => void redo(),
          onRetry: () => void workflowStatus.runCommand("retry"),
          onCancel: () => void workflowStatus.runCommand("cancel"),
          hasRetry: hasRetryableWorkflow,
          hasCancel: hasCancelableWorkflow,
          isPending: workflowStatus.pendingCommand !== null,
          error: workflowStatus.error,
          jobLabel,
        }}
      >
        {{
          leftPanel: (
            <ElementPalette projectId={projectId} workspaceId={workspaceId} />
          ),

          centerPanel: (
            <VideoPreview
              videoRef={videoRef}
              sourceUrl={mediaAssetUrls.source_video_url}
              thumbnailUrl={mediaAssetUrls.thumbnail_url}
              onSourceError={handlePreviewSourceError}
              onPosterError={handlePreviewPosterError}
              sourceWidth={upload.width}
              sourceHeight={upload.height}
              playheadMs={playheadMs}
              captionSegments={
                activePlan?.plan.timeline?.captions?.length
                  ? activePlan.plan.timeline.captions
                  : ((draftCaptionTrack ?? localCaptionTracks[0])?.segments ??
                    [])
              }
              captionStyle={
                (draftCaptionTrack ?? localCaptionTracks[0])?.style ?? null
              }
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

          rightPanel: selectedManualElement ? (
            <PropertiesInspector
              key={`${selectedManualElement.id}:${activePlan?.version ?? 0}`}
              element={selectedManualElement}
              saving={elementSaving}
              onClose={() => setSelectedElementId(null)}
              onChange={async (element) => {
                await commitManualElements(
                  manualElements.map((item) =>
                    item.id === element.id ? element : item,
                  ),
                  `Update ${element.name}`,
                );
              }}
              onDelete={async (id) => {
                setSelectedElementId(null);
                await commitManualElements(
                  manualElements.filter((item) => item.id !== id),
                  `Delete ${selectedManualElement.name}`,
                );
              }}
            />
          ) : (
            <InspectorPanel
              tab={inspectorTab}
              onTabChange={setInspectorTabWrapped}
              hasActivePlan={Boolean(localActivePlanId)}
              projectId={projectId}
              workspaceId={workspaceId}
              userId={userId}
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
              workflow={canonicalWorkflow}
              onWorkflowRefresh={workflowStatus.refresh}
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
              onClipSelected={(startMs) => setPlayheadMsWrapped(startMs)}
              planRevision={planRevision}
              mediaRevision={upload.mediaRevision}
              playheadMs={playheadMs}
              pendingReviewProposal={pendingReviewProposal}
              onReviewProposalHandled={() => setPendingReviewProposal(null)}
              onReviewProposalCreated={(proposal) => {
                setPendingReviewProposal(proposal);
                setInspectorTabWrapped("ai");
              }}
            />
          ),

          bottomPanel: (
            <TimelinePanel
              state={timelineState}
              videoRef={videoRef}
              playheadMs={playheadMs}
              setPlayheadMs={setPlayheadMsWrapped}
              selectedClipId={selectedClipId}
              onClipSelect={(id) => {
                setSelectedClipIdWrapped(id);
                if (id?.startsWith("element-"))
                  setSelectedElementId(id.slice("element-".length));
              }}
              projectId={projectId}
              workspaceId={workspaceId}
              activePlan={activePlan}
              captionTrack={localCaptionTracks[0] ?? null}
              musicSelection={localMusicSelection}
              onPlanUpdated={handlePlanUpdated}
              onCaptionTrackUpdated={handleCaptionTrackUpdated}
              brollSlots={localBrollSlots}
              onBrollSlotUpdated={handleBrollSlotUpdated}
              onMusicSelectionUpdated={handleMusicSelectionUpdated}
              getExpectedRevision={getExpectedRevision}
              onConflictDetected={onConflictDetected}
            />
          ),
        }}
      </EditorLayout>
      <DragOverlay dropAnimation={{ duration: 160, easing: "ease-out" }}>
        {activePaletteElement ? (
          <div className="w-32 border-2 border-primary bg-background/95 p-2 text-xs font-black shadow-xl">
            {activePaletteElement.preview || activePaletteElement.name}
            <span className="mt-1 block truncate text-[10px]">
              {activePaletteElement.name}
            </span>
          </div>
        ) : null}
      </DragOverlay>
      {dropMessage ? (
        <div
          className="pointer-events-none fixed bottom-4 left-1/2 z-[100] -translate-x-1/2 border border-border bg-background px-3 py-2 text-xs font-bold shadow-xl"
          aria-live="polite"
        >
          {dropMessage}
        </div>
      ) : null}
      {conflictState && (
        <ConflictDialog
          conflict={conflictState}
          onReload={onConflictReload}
          onForceSave={async () => onConflictForceSave()}
          onDismiss={onConflictDismiss}
        />
      )}
    </DndContext>
  );
}

// ── Inspector (right panel) ───────────────────────────────────────────────────

interface InspectorPanelProps {
  tab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  hasActivePlan: boolean;
  projectId: string;
  workspaceId: string;
  userId: string;
  upload: UploadedVideoSummary;
  transcript: TranscriptSummary | null;
  captionTracks: CaptionTrackSummary[];
  localEditPlans: EditPlanRecordSummary[];
  localActivePlanId: string | null;
  activePlan: EditPlanRecordSummary | null;
  brollSlots: BrollSlotSummary[];
  musicSelection: MusicSelectionSummary | null;
  effectsConfig: EffectsConfigSummary | null;
  selectedTimelineClip: TimelineClip | null;
  renderJobs: RenderJobSummary[];
  exports: ExportSummary[];
  maxExportResolution: ExportResolution;
  visualStatus: VisualAnalysisStatusSummary;
  workflow: WorkflowDetail | null;
  onWorkflowRefresh: () => Promise<WorkflowDetail | null>;
  onTranscriptUpdated: (transcript: TranscriptSummary | null) => void;
  onPlanGenerated: (plan: EditPlanRecordSummary) => void;
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
  onClipSelected: (startMs: number) => void;
  planRevision?: number | null;
  mediaRevision?: number | null;
  playheadMs: number;
  pendingReviewProposal: PendingReviewProposal | null;
  onReviewProposalHandled: () => void;
  onReviewProposalCreated: (proposal: PendingReviewProposal) => void;
}

const INSPECTOR_TABS: Array<{
  id: InspectorTab;
  label: string;
  icon: React.ElementType;
  requiresPlan: boolean;
}> = [
  { id: "ai", label: "AI", icon: Sparkles, requiresPlan: false },
  { id: "captions", label: "Text", icon: Type, requiresPlan: false },
  { id: "broll", label: "B-roll", icon: Layers, requiresPlan: false },
  { id: "audio", label: "Audio", icon: Music2, requiresPlan: false },
  { id: "look", label: "Look", icon: Palette, requiresPlan: false },
  { id: "history", label: "History", icon: History, requiresPlan: true },
  { id: "review", label: "Review", icon: MessageSquare, requiresPlan: true },
  { id: "scale", label: "Scale", icon: Network, requiresPlan: false },
  { id: "localize", label: "Localize", icon: Languages, requiresPlan: false },
  { id: "export", label: "Export", icon: Download, requiresPlan: false },
];

function InspectorPanel({
  tab,
  onTabChange,
  hasActivePlan,
  projectId,
  workspaceId,
  userId,
  upload,
  transcript,
  captionTracks,
  localEditPlans,
  localActivePlanId,
  activePlan,
  brollSlots,
  musicSelection,
  effectsConfig,
  selectedTimelineClip,
  renderJobs,
  exports,
  maxExportResolution,
  visualStatus,
  workflow,
  onWorkflowRefresh,
  onTranscriptUpdated,
  onPlanGenerated,
  onPlanUpdated,
  onCaptionTrackUpdated,
  onPreviewCaptionTrackUpdated,
  onBrollSlotUpdated,
  onBrollSlotsUpdated,
  onPreviewBrollUpdated,
  onMusicSelectionUpdated,
  onPreviewAudioUpdated,
  onEffectsConfigUpdated,
  onPreviewLookUpdated,
  onPreviewExportUpdated,
  onClipSelected,
  planRevision,
  mediaRevision,
  playheadMs,
  pendingReviewProposal,
  onReviewProposalHandled,
  onReviewProposalCreated,
}: InspectorPanelProps) {
  const brollDensity = activePlan?.broll_density ?? null;
  const musicMood = activePlan?.summary?.music_mood ?? null;
  const suggestedPreset = (activePlan?.summary?.visual_preset ??
    null) as VisualPresetKey | null;
  const activeTabMeta =
    INSPECTOR_TABS.find((item) => item.id === tab) ?? INSPECTOR_TABS[0];

  function onTabKeyDown(
    event: React.KeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) {
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
    const enabledTabs = INSPECTOR_TABS.filter(
      (item) => !(item.requiresPlan && !hasActivePlan),
    );
    const current = enabledTabs.findIndex(
      (item) => item.id === INSPECTOR_TABS[currentIndex].id,
    );
    const next =
      event.key === "Home"
        ? enabledTabs[0]
        : event.key === "End"
          ? enabledTabs[enabledTabs.length - 1]
          : enabledTabs[
              (current + direction + enabledTabs.length) % enabledTabs.length
            ];
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
        {INSPECTOR_TABS.map(
          ({ id, label, icon: Icon, requiresPlan }, index) => {
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
          },
        )}
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
            initialPlans={localEditPlans}
            activePlanId={localActivePlanId}
            transcript={transcript}
            initialVisualStatus={visualStatus}
            workflow={workflow}
            onWorkflowRefresh={onWorkflowRefresh}
            onTranscriptUpdated={onTranscriptUpdated}
            onPlanGenerated={onPlanGenerated}
            onCaptionTrackUpdated={onCaptionTrackUpdated}
            onBrollSlotsUpdated={onBrollSlotsUpdated}
            onMusicSelectionUpdated={onMusicSelectionUpdated}
            onClipSelected={onClipSelected}
            externalProposal={pendingReviewProposal}
            onExternalProposalHandled={onReviewProposalHandled}
          />
        )}
        {tab === "captions" && (
          <CaptionsTab
            projectId={projectId}
            workspaceId={workspaceId}
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
            initialConfig={effectsConfig}
            suggestedPreset={suggestedPreset}
            selectedTimelineClip={selectedTimelineClip}
            onConfigUpdated={onEffectsConfigUpdated}
            onPreviewLookUpdated={onPreviewLookUpdated}
          />
        )}
        {tab === "localize" && (
          <PublishingLibraryPanel
            projectId={projectId}
            workspaceId={workspaceId}
            captionTrackId={captionTracks[0]?.id ?? null}
            activePlanId={localActivePlanId}
          />
        )}
        {tab === "export" && (
          <ExportStep
            projectId={projectId}
            workspaceId={workspaceId}
            initialJobs={renderJobs}
            initialExports={exports}
            captionTrackId={captionTracks[0]?.id ?? null}
            activePlanId={localActivePlanId}
            sourceWidth={upload.width}
            sourceHeight={upload.height}
            maxExportResolution={maxExportResolution}
            onPreviewExportUpdated={onPreviewExportUpdated}
            planRevision={planRevision}
          />
        )}
        {tab === "history" && (
          <VersionHistoryPanel
            projectId={projectId}
            workspaceId={workspaceId}
            activePlan={activePlan}
            onPlanRestored={onPlanUpdated}
          />
        )}
        {tab === "review" && (
          <ReviewPanel
            projectId={projectId}
            workspaceId={workspaceId}
            activePlan={activePlan}
            mediaRevision={mediaRevision}
            uploadedVideoId={upload.id}
            sourceObjectReferenceId={upload.sourceObjectReferenceId}
            sourceSha256={upload.verifiedSha256}
            playheadMs={playheadMs}
            onSeek={onClipSelected}
            onProposalCreated={onReviewProposalCreated}
          />
        )}
        {tab === "scale" && (
          <ScalePanel
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            activePlan={activePlan}
            captionTrack={captionTracks[0] ?? null}
            effectsConfig={effectsConfig}
            onPlanUpdated={onPlanUpdated}
            onProposalCreated={onReviewProposalCreated}
            onCaptionTrackUpdated={onCaptionTrackUpdated}
            onEffectsConfigUpdated={onEffectsConfigUpdated}
          />
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
    backgroundRemoval: {
      enabled: config.background_removal.enabled,
      featherPx: config.background_removal.feather_px,
    },
    mattePreview: null,
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
      clip.sourceRef.id
        ? item.id === clip.sourceRef.id
        : item.index === clip.sourceRef.index,
    );
    const confidenceValues = segment?.words
      .map((word) => word.confidence)
      .filter((value): value is number => typeof value === "number");
    const confidence = confidenceValues?.length
      ? confidenceValues.reduce((sum, value) => sum + value, 0) /
        confidenceValues.length
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
      reason:
        slot?.intent ||
        slot?.keywords?.join(", ") ||
        "AI-selected supporting B-roll.",
      detail: slot?.selected_asset?.title ?? slot?.keywords?.join(", "),
      confidence: typeof slot?.score === "number" ? slot.score : null,
    };
  }
  if (clip.sourceRef.kind === "music") {
    return {
      reason:
        activePlan?.summary.music_mood ||
        "Music selected to match the timeline mood.",
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
    <div className="space-y-4 p-4 min-h-[160px] animate-pulse">
      <div className="flex items-center gap-3">
        <LoadingPixelSkeleton className="h-6 w-24 shrink-0" />
        <LoadingPixelSkeleton className="h-4 w-full" />
      </div>
      <LoadingPixelSkeleton className="h-24 w-full" />
      <div className="flex gap-2 justify-end">
        <LoadingPixelSkeleton className="h-10 w-24 shrink-0" />
        <LoadingPixelSkeleton className="h-10 w-20 shrink-0" />
      </div>
    </div>
  );
}

function unique<T>(items: T[]): T[] {
  return [...new Set(items)];
}

function snap(value: number, step: number, min: number, max: number) {
  return Math.max(min, Math.min(max, Math.round(value / step) * step));
}

function snapTimelineMs(
  value: number,
  gridMs: number,
  boundaryToleranceMs: number,
  boundaries: number[],
  totalMs: number,
) {
  const nearest = boundaries.reduce<number | null>(
    (best, boundary) =>
      best == null || Math.abs(boundary - value) < Math.abs(best - value)
        ? boundary
        : best,
    null,
  );
  if (nearest != null && Math.abs(nearest - value) <= boundaryToleranceMs) {
    return Math.max(0, Math.min(totalMs - 1, nearest));
  }
  return snap(value, gridMs, 0, totalMs - 1);
}

const editorCollisionDetection: CollisionDetection = (args) => {
  const activeData = args.active.data.current;
  const allowed =
    activeData?.kind === "placed"
      ? ["canvas"]
      : ((activeData?.element as PaletteElement | undefined)?.accepts ?? []);
  const containers = args.droppableContainers.filter((container) => {
    const zone = String(
      container.data.current?.zone ??
        (String(container.id) === "canvas" ? "canvas" : ""),
    );
    return allowed.includes(zone as never);
  });
  const scoped = { ...args, droppableContainers: containers };
  return pointerWithin(scoped).length
    ? pointerWithin(scoped)
    : closestCenter(scoped);
};

function effectsRequestBody(
  config: EffectsConfigSummary | null,
  grade: VisualPresetKey | undefined,
  motionEffect: MotionEffectKey | undefined,
  clipEffects: Record<string, MotionEffectKey[]>,
) {
  const motionEffects = unique([
    ...(config?.motion_effects ?? []),
    ...(motionEffect ? [motionEffect] : []),
  ]);
  return {
    preset: grade ?? config?.preset ?? "clean_founder",
    lut_intensity: config?.lut_intensity ?? 0.75,
    texture: config?.texture ?? {
      grain: 0.08,
      vignette: 0.08,
      light_leak: 0,
      anamorphic_flare: false,
    },
    motion_effects: motionEffects,
    motion: {
      keyframes: config?.motion?.keyframes ?? [],
      ken_burns: config?.motion?.ken_burns ?? {
        enabled: motionEffect === "ken_burns",
        intensity: 0.08,
        direction: "push_in",
        easing: "ease_in_out",
      },
      speed_ramp: config?.motion?.speed_ramp ?? {
        enabled: motionEffect === "speed_ramp",
        ramp_in_ms: 280,
        ramp_out_ms: 320,
        peak_speed: 1.35,
        easing: "ease_in_out",
      },
      beat_impact: config?.motion?.beat_impact ?? {
        enabled: motionEffect === "beat_impact",
        intensity: 0.22,
        shake_px: 10,
        zoom: 1.035,
        max_impacts: 12,
      },
      auto_reframe: config?.motion?.auto_reframe ?? {
        enabled: false,
        target_aspect: "9:16",
        subject_x_pct: 50,
        subject_y_pct: 50,
        smoothing: 0.65,
      },
      clip_effects: clipEffects,
    },
    background_removal: config?.background_removal ?? {
      enabled: false,
      apply_to_broll: true,
      feather_px: 18,
      spill_suppression: 0.45,
      replacement: "gradient_ramp",
    },
    gradient_ramp: config?.gradient_ramp ?? {
      enabled: false,
      start_color: "#0f172a",
      end_color: "#f59e0b",
      angle_deg: 135,
      opacity: 0.28,
      blend_mode: "soft-light",
    },
    after_effects_presets: config?.after_effects_presets ?? [],
    force: true,
  };
}

// ── Upload screen (phase 1 — no video yet) ────────────────────────────────────

function UploadScreen({
  title,
  projectId,
  workspaceId,
}: {
  title: string;
  projectId: string;
  workspaceId: string;
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
              Drop in your raw talking-head video. We&apos;ll transcribe it and
              prepare an AI edit proposal for you to review before it reaches
              the timeline.
            </p>
          </div>
          <UploadPanel projectId={projectId} workspaceId={workspaceId} />
        </div>
      </div>
    </div>
  );
}

// ── Processing screen (phase 2 — upload processing) ───────────────────────────

function ProcessingScreen({
  title,
  status,
}: {
  title: string;
  status: string;
}) {
  const router = useRouter();

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") router.refresh();
    };
    const timer = window.setInterval(refresh, 4000);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [router]);

  const activeIdx = productFlowStageIndex(status);

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
      <div className="flex flex-1 flex-col items-center justify-center gap-6 px-4 py-8 text-center max-w-lg mx-auto w-full">
        <div className="pixel-panel flex size-16 items-center justify-center shadow-soft">
          <Loader2 className="size-8 animate-spin text-primary" />
        </div>

        <div className="space-y-1">
          <h2 className="text-lg font-black uppercase tracking-wide">
            Processing Video
          </h2>
          <p className="text-xs text-muted-foreground">
            {uploadStatusMessage(status)}
          </p>
        </div>

        <div className="w-full border border-border bg-surface p-5 text-left shadow-soft space-y-4">
          <h3 className="text-xs font-black uppercase tracking-wider text-primary border-b border-border pb-2">
            Processing Stages
          </h3>
          <div className="space-y-2.5 font-bold" aria-live="polite">
            {PRODUCT_FLOW_STAGES.map((stage, idx) => {
              const isCompleted = idx < activeIdx;
              const isActive = idx === activeIdx;
              return (
                <div
                  key={stage.key}
                  className={cn(
                    "flex items-start gap-3 p-2 border transition duration-80ms steps(4, end)",
                    isActive
                      ? "border-primary bg-primary/5"
                      : "border-transparent opacity-60",
                  )}
                >
                  <div className="mt-0.5 flex size-5 shrink-0 items-center justify-center border border-border bg-background text-[10px] font-black">
                    {isCompleted ? "✓" : isActive ? "▶" : idx + 1}
                  </div>
                  <div className="min-w-0">
                    <p
                      className={cn(
                        "text-xs font-black uppercase tracking-wide",
                        isActive ? "text-primary" : "text-foreground",
                      )}
                    >
                      {stage.label}
                    </p>
                    {isActive && (
                      <p className="text-[10px] text-muted-foreground font-medium mt-0.5 leading-normal">
                        {stage.description}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="border-t border-border pt-3 mt-2 text-2xs text-muted-foreground flex flex-col gap-0.5 leading-relaxed font-bold">
            <p className="text-foreground">
              ⏱ Usually takes around 45 seconds.
            </p>
            <p>Large videos or complex templates may take longer.</p>
          </div>
        </div>

        <div className="pixel-progress h-2.5 w-full max-w-xs border border-border bg-background">
          <div
            className="pixel-progress-fill h-full bg-primary transition-all duration-300"
            style={{
              width: `${Math.round(((activeIdx + 0.5) / PRODUCT_FLOW_STAGES.length) * 100)}%`,
            }}
          />
        </div>
        <p className="text-2xs text-muted-foreground font-bold uppercase tracking-wider">
          This page will refresh automatically
        </p>
      </div>
    </div>
  );
}
