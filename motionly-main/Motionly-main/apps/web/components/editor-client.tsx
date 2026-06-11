"use client";

import React, { useCallback, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Sparkles, Type, Layers, Music2, Palette, Download,
  Film, Loader2, ChevronLeft,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { buildTimelineState } from "@/lib/timeline";
import { EditorLayout } from "./editor-layout";
import { UploadPanel } from "./upload-panel";
import { AIStep } from "./editor/ai-step";
import { ExportStep } from "./editor/export-step";
import { CaptionsTab } from "./editor/tabs/captions-tab";
import { BrollTab } from "./editor/tabs/broll-tab";
import { AudioTab } from "./editor/tabs/audio-tab";
import { LookTab } from "./editor/tabs/look-tab";
import { LeftMediaPanel } from "./editor/left-panel/LeftMediaPanel";
import { VideoPreview } from "./editor/preview/VideoPreview";
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

// ── Main component ────────────────────────────────────────────────────────────

export function EditorClient(props: EditorClientProps) {
  const {
    projectId, workspaceId, userId, apiAccessToken, title,
    upload, uploadAssetUrls, transcript, captionTracks,
    brollSlots, musicSelection, effectsConfig, renderJobs, exports,
  } = props;

  // Local mutable copies of server state so the UI updates immediately
  // when AI generates a plan or user switches tabs (no round-trip wait).
  const [localEditPlans, setLocalEditPlans] = useState<EditPlanRecordSummary[]>(
    props.editPlans,
  );
  const [localActivePlanId, setLocalActivePlanId] = useState<string | null>(
    props.activePlanId,
  );

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
        captionTrack: captionTracks[0] ?? null,
        brollSlots,
        musicSelection,
      }),
    [upload, activePlan, captionTracks, brollSlots, musicSelection],
  );

  // When AI generates a new plan, update locally and advance inspector
  const handlePlanGenerated = useCallback((plan: EditPlanRecordSummary) => {
    setLocalEditPlans((prev) => {
      const without = prev.filter((p) => p.id !== plan.id);
      return [...without, plan];
    });
    setLocalActivePlanId(plan.id);
    setInspectorTab("captions");
  }, []);

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
      title={title}
      onTitleChange={() => {}}   // title editing wired via separate API — no-op for now
      onExport={() => setInspectorTab("export")}
      onAiEdit={() => setInspectorTab("ai")}
    >
      {{
        leftPanel: (
          <LeftMediaPanel
            upload={upload}
            thumbnailUrl={uploadAssetUrls.thumbnail_url}
            activePlan={activePlan}
            brollSlots={brollSlots}
            musicSelection={musicSelection}
            captionTracks={captionTracks}
          />
        ),

        centerPanel: (
          <VideoPreview
            videoRef={videoRef}
            sourceUrl={uploadAssetUrls.source_video_url}
            thumbnailUrl={uploadAssetUrls.thumbnail_url}
            playheadMs={playheadMs}
            captionSegments={captionTracks[0]?.segments ?? []}
            captionStyle={captionTracks[0]?.style ?? null}
            totalDurationMs={timelineState.totalDurationMs}
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
            transcript={transcript}
            captionTracks={captionTracks}
            localEditPlans={localEditPlans}
            localActivePlanId={localActivePlanId}
            activePlan={activePlan}
            brollSlots={brollSlots}
            musicSelection={musicSelection}
            effectsConfig={effectsConfig}
            renderJobs={renderJobs}
            exports={exports}
            onPlanGenerated={handlePlanGenerated}
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
  onPlanGenerated:  (plan: EditPlanRecordSummary) => void;
}

const INSPECTOR_TABS: Array<{
  id: InspectorTab;
  label: string;
  icon: React.ElementType;
  requiresPlan: boolean;
}> = [
  { id: "ai",      label: "AI",      icon: Sparkles,  requiresPlan: false },
  { id: "captions",label: "Text",    icon: Type,       requiresPlan: true  },
  { id: "broll",   label: "B-roll",  icon: Layers,     requiresPlan: true  },
  { id: "audio",   label: "Audio",   icon: Music2,     requiresPlan: true  },
  { id: "look",    label: "Look",    icon: Palette,    requiresPlan: true  },
  { id: "export",  label: "Export",  icon: Download,   requiresPlan: false },
];

function InspectorPanel({
  tab, onTabChange, hasActivePlan,
  projectId, workspaceId, userId, apiAccessToken,
  upload, transcript, captionTracks, localEditPlans, localActivePlanId,
  activePlan, brollSlots, musicSelection, effectsConfig,
  renderJobs, exports, onPlanGenerated,
}: InspectorPanelProps) {
  const brollDensity = (activePlan?.summary?.broll_density ?? null) as BrollDensity | null;
  const musicMood    = activePlan?.summary?.music_mood ?? null;
  const suggestedPreset = (activePlan?.summary?.visual_preset ?? null) as VisualPresetKey | null;

  return (
    <div className="flex h-full flex-col bg-[hsl(var(--surface))]">
      {/* Tab strip */}
      <div className="flex shrink-0 items-end gap-0.5 border-b border-border px-2 pt-2">
        {INSPECTOR_TABS.map(({ id, label, icon: Icon, requiresPlan }) => {
          const disabled = requiresPlan && !hasActivePlan;
          return (
            <button
              key={id}
              onClick={() => !disabled && onTabChange(id)}
              disabled={disabled}
              title={disabled ? "Generate an AI edit first" : label}
              className={cn(
                "flex flex-col items-center gap-0.5 px-2 pb-2 pt-1.5 text-[9px] font-bold uppercase tracking-widest transition",
                "border-b-2 -mb-px",
                tab === id
                  ? "border-primary text-primary"
                  : disabled
                    ? "cursor-not-allowed border-transparent text-muted-foreground/30"
                    : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="size-3.5" />
              {label}
            </button>
          );
        })}
      </div>

      {/* Tab content — scrollable */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "ai" && (
          <AIStep
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialPlans={localEditPlans}
            activePlanId={localActivePlanId}
            hasTranscript={Boolean(transcript?.words?.length)}
            onPlanGenerated={onPlanGenerated}
          />
        )}
        {tab === "captions" && (
          <CaptionsTab
            projectId={projectId}
            workspaceId={workspaceId}
            userId={userId}
            apiAccessToken={apiAccessToken}
            initialTracks={captionTracks}
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
          />
        )}
      </div>
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
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex h-11 items-center gap-3 border-b border-border px-4">
        <Link
          href="/dashboard"
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition"
        >
          <ChevronLeft className="size-4" />
          Dashboard
        </Link>
        <span className="text-sm font-semibold text-foreground">{title}</span>
      </header>
      <div className="flex flex-1 items-start justify-center px-6 pt-16">
        <div className="w-full max-w-lg">
          <div className="mb-8 text-center">
            <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-primary/10">
              <Film className="size-7 text-primary" />
            </div>
            <h1 className="text-2xl font-bold">Upload your footage</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Drop in your raw talking-head video. We'll transcribe it and build an AI edit plan automatically.
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
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex h-11 items-center gap-3 border-b border-border px-4">
        <Link
          href="/dashboard"
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition"
        >
          <ChevronLeft className="size-4" />
          Dashboard
        </Link>
        <span className="text-sm font-semibold text-foreground">{title}</span>
      </header>
      <div className="flex flex-1 flex-col items-center justify-center gap-5 text-center">
        <div className="flex size-16 items-center justify-center rounded-2xl bg-primary/10">
          <Loader2 className="size-8 animate-spin text-primary" />
        </div>
        <div>
          <p className="text-lg font-bold capitalize">{status.replace(/_/g, " ")}…</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Transcribing your footage and preparing the editor. This takes about a minute.
          </p>
        </div>
        <p className="text-xs text-muted-foreground/50">This page will refresh automatically.</p>
      </div>
    </div>
  );
}
