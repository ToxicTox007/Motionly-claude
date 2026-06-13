"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Sparkles, Type, Layers, Music2, Palette, Download,
  Film, Loader2, ChevronLeft,
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
import { LeftMediaPanel } from "./editor/left-panel/LeftMediaPanel";
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
  MusicSelectionSummary,
  RenderJobSummary,
  TranscriptSummary,
  UploadedVideoSummary,
  VisualPresetKey,
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
}

type InspectorTab = "ai" | "captions" | "broll" | "audio" | "look" | "export";

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
  const videoRef       = useRef<HTMLVideoElement>(null);
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

  useEffect(() => {
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

  const handleCaptionTrackUpdated = useCallback((track: CaptionTrackSummary) => {
    setDraftCaptionTrack(track);
    setLocalCaptionTracks((prev) => [
      track,
      ...prev.filter((item) => item.id !== track.id),
    ]);
  }, []);

  const handleBrollSlotUpdated = useCallback((slot: BrollSlotSummary) => {
    setLocalBrollSlots((prev) =>
      prev
        .map((item) => (item.id === slot.id ? slot : item))
        .sort((a, b) => a.start_ms - b.start_ms),
    );
  }, []);

  const handleBrollSlotsUpdated = useCallback((slots: BrollSlotSummary[]) => {
    setLocalBrollSlots([...slots].sort((a, b) => a.start_ms - b.start_ms));
  }, []);

  const handleMusicSelectionUpdated = useCallback((selection: MusicSelectionSummary) => {
    setLocalMusicSelection(selection);
  }, []);

  const handleEffectsConfigUpdated = useCallback((config: EffectsConfigSummary) => {
    setLocalEffectsConfig(config);
  }, []);

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
    <EditorLayout
      title={localTitle}
      onTitleChange={setLocalTitle}
      onTitleCommit={saveProjectTitle}
      saveStatus={saveStatus}
      onExport={() => setInspectorTab("export")}
      onAiEdit={() => setInspectorTab("ai")}
    >
      {{
        leftPanel: (
          <LeftMediaPanel
            upload={upload}
            thumbnailUrl={uploadAssetUrls.thumbnail_url}
            sourceVideoUrl={uploadAssetUrls.source_video_url}
            activePlan={activePlan}
            brollSlots={localBrollSlots}
            musicSelection={localMusicSelection}
            captionTracks={localCaptionTracks}
          />
        ),

        centerPanel: (
          <VideoPreview
            videoRef={videoRef}
            sourceUrl={uploadAssetUrls.source_video_url}
            thumbnailUrl={uploadAssetUrls.thumbnail_url}
            sourceWidth={upload.width}
            sourceHeight={upload.height}
            playheadMs={playheadMs}
            captionSegments={(draftCaptionTrack ?? localCaptionTracks[0])?.segments ?? []}
            captionStyle={(draftCaptionTrack ?? localCaptionTracks[0])?.style ?? null}
            totalDurationMs={timelineState.totalDurationMs}
            previewAudio={previewAudio}
            previewBroll={previewBroll}
            previewLook={previewLook}
            previewExport={previewExport}
          />
        ),

        rightPanel: (
          <InspectorPanel
            tab={inspectorTab}
            onTabChange={setInspectorTab}
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
            renderJobs={renderJobs}
            exports={exports}
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
            setPlayheadMs={setPlayheadMs}
            selectedClipId={selectedClipId}
            onClipSelect={setSelectedClipId}
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            activePlan={activePlan}
            captionTrack={localCaptionTracks[0] ?? null}
            onPlanUpdated={handlePlanUpdated}
            onCaptionTrackUpdated={handleCaptionTrackUpdated}
            onBrollSlotUpdated={handleBrollSlotUpdated}
          />
        ),
      }}
    </EditorLayout>
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
  renderJobs:       RenderJobSummary[];
  exports:          ExportSummary[];
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
  { id: "export",  label: "Export",  icon: Download,   requiresPlan: false },
];

function InspectorPanel({
  tab, onTabChange, hasActivePlan,
  projectId, workspaceId, userId, apiAccessToken,
  upload, transcript, captionTracks, localEditPlans, localActivePlanId,
  activePlan, brollSlots, musicSelection, effectsConfig,
  renderJobs, exports, onTranscriptUpdated, onPlanGenerated, onPlanUpdated, onCaptionTrackUpdated, onPreviewCaptionTrackUpdated, onBrollSlotUpdated, onBrollSlotsUpdated, onPreviewBrollUpdated,
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
        {tab === "ai" && (
          <AIStep
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialPlans={localEditPlans}
            activePlanId={localActivePlanId}
            transcript={transcript}
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
            onPreviewExportUpdated={onPreviewExportUpdated}
          />
        )}
      </div>
    </div>
  );
}

function InspectorLoading() {
  return (
    <div className="flex min-h-40 items-center justify-center">
      <Loader2 className="size-5 animate-spin text-primary" />
    </div>
  );
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
              Drop in your raw talking-head video. We&apos;ll transcribe it and build an AI edit plan automatically.
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
