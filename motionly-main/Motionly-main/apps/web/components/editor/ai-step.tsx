"use client";

import React, { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Input, Label, Select, TextArea, Slider } from "./ui";
import { Button } from "../button";
import { AlertCircle, CheckCircle2, Eye, History, Loader2, RefreshCw, Save, Sparkles } from "lucide-react";
import { env } from "@/lib/env";
import {
  editorApiRequest,
  pollPlanJob,
  resolveQueuedLayer,
  startAdaptivePolling,
} from "@/lib/client-api";
import type {
  BrollSlotSummary,
  BrollDensity,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  EditPlanStyleKey,
  MusicSelectionSummary,
  PlanSnapshotSummary,
  TranscriptSummary,
  VisualAnalysisStatusSummary,
} from "@motionly/shared";

interface AIStepProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialPlans: EditPlanRecordSummary[];
  activePlanId: string | null;
  transcript: TranscriptSummary | null;
  initialVisualStatus: VisualAnalysisStatusSummary;
  onTranscriptUpdated: (transcript: TranscriptSummary | null) => void;
  onPlanGenerated: (plan: EditPlanRecordSummary) => void;
  onCaptionTrackUpdated: (track: CaptionTrackSummary) => void;
  onBrollSlotsUpdated: (slots: BrollSlotSummary[]) => void;
  onMusicSelectionUpdated: (selection: MusicSelectionSummary) => void;
}

type StylePickerValue = EditPlanStyleKey | "auto";

const styles: { value: StylePickerValue; label: string }[] = [
  { value: "auto", label: "Auto (Audio + Visual)" },
  { value: "motivational", label: "Motivational" },
  { value: "emotional", label: "Emotional" },
  { value: "cinematic", label: "Cinematic" },
  { value: "educational", label: "Educational" },
  { value: "luxury", label: "Luxury (Warm & Premium)" },
  { value: "storytelling", label: "Storytelling" },
  { value: "dramatic", label: "Dramatic" },
  { value: "fast_paced", label: "Fast-Paced" },
  { value: "clean_minimal", label: "Clean Minimal" },
  { value: "creator_influencer", label: "Creator / Influencer" },
];

const captionLanguages = [
  { value: "original", label: "Original language" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "es", label: "Spanish" },
  { value: "pt", label: "Portuguese" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "ar", label: "Arabic" },
];

const sourceLanguages = [
  { value: "auto", label: "Auto detect" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "es", label: "Spanish" },
  { value: "pt", label: "Portuguese" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "ar", label: "Arabic" },
];

export function AIStep({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialPlans,
  activePlanId,
  transcript,
  initialVisualStatus,
  onTranscriptUpdated,
  onPlanGenerated,
  onCaptionTrackUpdated,
  onBrollSlotsUpdated,
  onMusicSelectionUpdated,
}: AIStepProps) {
  const router = useRouter();
  const activePlan = initialPlans.find(p => p.id === activePlanId) || initialPlans[0];
  
  const [style, setStyle] = useState<StylePickerValue>(activePlan?.style ?? "auto");
  const [density, setDensity] = useState<BrollDensity>(activePlan?.broll_density ?? "medium");
  const [prompt, setPrompt] = useState(activePlan?.prompt ?? "");
  const [sourceLanguage, setSourceLanguage] = useState("auto");
  const [outputLanguage, setOutputLanguage] = useState("original");
  const [loading, setLoading] = useState(false);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [snapshots, setSnapshots] = useState<PlanSnapshotSummary[]>([]);
  const [snapshotName, setSnapshotName] = useState("");
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const isGeneratingRef = useRef(false);
  const isTranscribingRef = useRef(false);
  const isSnapshotSavingRef = useRef(false);
  const isSnapshotRestoringRef = useRef(false);
  const operationAbortRef = useRef<AbortController | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [visualStatus, setVisualStatus] = useState(initialVisualStatus);
  const hasTranscript = transcript?.status === "ready" && Boolean(transcript.words?.length);
  const pendingPlanStorageKey = `motionly:pending-plan:${workspaceId}:${projectId}`;

  useEffect(() => {
    return () => {
      operationAbortRef.current?.abort();
      operationAbortRef.current = null;
    };
  }, []);

  const densityMap: Record<BrollDensity, number> = {
    none: 0,
    low: 33,
    medium: 66,
    high: 100
  };

  const reverseDensityMap = (val: number): BrollDensity => {
    if (val < 25) return "none";
    if (val < 50) return "low";
    if (val < 75) return "medium";
    return "high";
  };

  useEffect(() => {
    if (visualStatus.status !== "processing") return;
    let canceled = false;
    const abortController = new AbortController();
    const poll = async () => {
      try {
        const next = await editorApiRequest<VisualAnalysisStatusSummary>(
          `${env.apiBaseUrl}/projects/${projectId}/visual/status`,
          { method: "GET", workspaceId, userId, accessToken: apiAccessToken, signal: abortController.signal },
        );
        if (!canceled) setVisualStatus(next);
      } catch (error) {
        if (!canceled) console.error(error);
      }
    };
    const stopPolling = startAdaptivePolling(() => poll(), {
      intervalMs: 5000,
      maxIntervalMs: 15000,
    });
    return () => {
      canceled = true;
      abortController.abort();
      stopPolling();
    };
  }, [apiAccessToken, projectId, userId, visualStatus.status, workspaceId]);

  useEffect(() => {
    if (!activePlanId) {
      return;
    }
    let canceled = false;
    const abortController = new AbortController();
    async function loadSnapshots() {
      try {
        const payload = await editorApiRequest<{
          snapshots: PlanSnapshotSummary[];
        }>(`${env.apiBaseUrl}/projects/${projectId}/plans/snapshots`, {
          method: "GET",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Timeline versions could not be loaded.",
          signal: abortController.signal,
        });
        if (!canceled) setSnapshots(payload.snapshots ?? []);
      } catch (error) {
        if (!canceled) console.error(error);
      }
    }
    void loadSnapshots();
    return () => {
      canceled = true;
      abortController.abort();
    };
  }, [activePlanId, apiAccessToken, projectId, userId, workspaceId]);

  async function generatePlan() {
    if (isGeneratingRef.current || loading || !hasTranscript) return;
    const operationController = beginPlanOperation();
    const { signal } = operationController;
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{
        plan?: EditPlanRecordSummary;
        status: string;
        job_id?: string;
        status_url?: string;
      }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "AI edit could not be generated. Please try again.",
          signal,
          body: {
            prompt,
            ...(style === "auto" ? {} : { style }),
            broll_density: density,
            target_duration_s: 35,
          },
        },
      );

      let plan: EditPlanRecordSummary;
      if (payload.status === "queued" && payload.status_url) {
        const statusUrl = new URL(payload.status_url, env.apiBaseUrl).toString();
        window.sessionStorage.setItem(pendingPlanStorageKey, statusUrl);
        setMessage("Edit plan queued... generating timeline.");
        plan = await loadQueuedPlan(statusUrl, signal);
      } else if (payload.plan) {
        plan = payload.plan;
      } else {
        throw new Error("Plan generation returned no plan or queued job.");
      }

      await finalizeGeneratedPlan(plan, operationController);
      window.sessionStorage.removeItem(pendingPlanStorageKey);
    } catch (error) {
      if (operationAbortRef.current !== operationController || signal.aborted) return;
      window.sessionStorage.removeItem(pendingPlanStorageKey);
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      finishPlanOperation(operationController);
    }
  }

  function beginPlanOperation() {
    isGeneratingRef.current = true;
    operationAbortRef.current?.abort();
    const controller = new AbortController();
    operationAbortRef.current = controller;
    return controller;
  }

  function finishPlanOperation(controller: AbortController) {
    if (operationAbortRef.current !== controller) return;
    operationAbortRef.current = null;
    isGeneratingRef.current = false;
    setLoading(false);
  }

  async function loadQueuedPlan(statusUrl: string, signal: AbortSignal) {
    const planId = await pollPlanJob(
      statusUrl,
      {
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        signal,
      },
      (progress) => {
        if (!signal.aborted) setMessage(`Generating plan: ${progress}%`);
      },
    );
    const planList = await editorApiRequest<{ plans: EditPlanRecordSummary[] }>(
      `${env.apiBaseUrl}/projects/${projectId}/plans`,
      { workspaceId, userId, accessToken: apiAccessToken, signal },
    );
    const foundPlan = planList.plans.find((item) => item.id === planId);
    if (!foundPlan) {
      throw new Error("Plan was generated successfully but could not be loaded.");
    }
    return foundPlan;
  }

  async function finalizeGeneratedPlan(
    plan: EditPlanRecordSummary,
    operationController: AbortController,
  ) {
    const { signal } = operationController;
    signal.throwIfAborted();
    if (operationAbortRef.current !== operationController) return;
    onPlanGenerated(plan);

    const followUpResults = await Promise.allSettled([
      ensureCaptions(signal),
      ensureBroll(signal),
      ensureMusic(signal),
    ]);
    signal.throwIfAborted();
    const compiled = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
      `${env.apiBaseUrl}/projects/${projectId}/plans/${plan.id}/compile-timeline`,
      {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError:
          "The AI edit was created, but its media layers could not be synchronized.",
        signal,
        body: {},
      },
    );
    signal.throwIfAborted();
    if (operationAbortRef.current !== operationController) return;
    onPlanGenerated(compiled.plan);
    const failedFollowUps = followUpResults.filter(
      (result) => result.status === "rejected",
    );
    setMessage(
      failedFollowUps.length === 0
        ? `AI edit v${plan.version} is ready on the timeline. Captions, B-roll, and music were prepared.`
        : `AI edit v${plan.version} is ready on the timeline. Some media steps still need attention.`,
    );
    router.refresh();
  }

  async function resumePendingPlan(statusUrl: string) {
    if (isGeneratingRef.current) return;
    const operationController = beginPlanOperation();
    const { signal } = operationController;
    setLoading(true);
    setMessage("Resuming the queued AI edit...");
    try {
      const plan = await loadQueuedPlan(statusUrl, signal);
      await finalizeGeneratedPlan(plan, operationController);
      window.sessionStorage.removeItem(pendingPlanStorageKey);
    } catch (error) {
      if (operationAbortRef.current !== operationController || signal.aborted) return;
      window.sessionStorage.removeItem(pendingPlanStorageKey);
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      finishPlanOperation(operationController);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const statusUrl = window.sessionStorage.getItem(pendingPlanStorageKey);
      if (statusUrl) void resumePendingPlan(statusUrl);
    }, 0);
    return () => window.clearTimeout(timer);
    // Resume only when this project identity changes; the operation owns current props.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingPlanStorageKey]);

  async function ensureCaptions(signal: AbortSignal) {
    const payload = await editorApiRequest<{
      track?: CaptionTrackSummary | null;
      status: "ready" | "queued";
      status_url?: string | null;
    }>(
      `${env.apiBaseUrl}/projects/${projectId}/captions/generate`,
      {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "Captions could not be prepared yet.",
        signal,
        body: {
          transcript_id: transcript?.id ?? null,
          preset: defaultCaptionPreset(style),
          hide_filler_words: false,
          output_language: outputLanguage,
        },
      },
    );
    const track = await resolveQueuedLayer({
      submission: payload,
      immediate: payload.track,
      apiBaseUrl: env.apiBaseUrl,
      options: { workspaceId, userId, accessToken: apiAccessToken, signal },
      load: async () => {
        const result = await editorApiRequest<{ tracks: CaptionTrackSummary[] }>(
          `${env.apiBaseUrl}/projects/${projectId}/captions`,
          { workspaceId, userId, accessToken: apiAccessToken, signal },
        );
        return result.tracks[0] ?? null;
      },
      resourceLabel: "Captions",
      onProgress: (progress) => setMessage(`Preparing captions: ${progress}%`),
    });
    onCaptionTrackUpdated(track);
  }

  async function ensureBroll(signal: AbortSignal) {
    const payload = await editorApiRequest<{
      slots?: BrollSlotSummary[] | null;
      status: "ready" | "queued";
      status_url?: string | null;
    }>(
      `${env.apiBaseUrl}/projects/${projectId}/broll/generate`,
      {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "B-roll could not be generated for this edit.",
        signal,
        body: {
          density,
        },
      },
    );
    const slots = await resolveQueuedLayer({
      submission: payload,
      immediate: payload.slots,
      apiBaseUrl: env.apiBaseUrl,
      options: { workspaceId, userId, accessToken: apiAccessToken, signal },
      load: async () => {
        const result = await editorApiRequest<{ slots: BrollSlotSummary[] }>(
          `${env.apiBaseUrl}/projects/${projectId}/broll`,
          { workspaceId, userId, accessToken: apiAccessToken, signal },
        );
        return result.slots;
      },
      resourceLabel: "B-roll",
      onProgress: (progress) => setMessage(`Preparing B-roll: ${progress}%`),
    });
    onBrollSlotsUpdated(slots);
  }

  async function ensureMusic(signal: AbortSignal) {
    const payload = await editorApiRequest<{
      selection?: MusicSelectionSummary | null;
      status: "ready" | "queued";
      status_url?: string | null;
    }>(
      `${env.apiBaseUrl}/projects/${projectId}/music/select`,
      {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "No production music track is available for this edit yet.",
        signal,
        body: {
          include_music: true,
          music_volume: 0.25,
          sfx_intensity: 0.2,
          rights_confirmed: true,
          force: false,
        },
      },
    );
    const selection = await resolveQueuedLayer({
      submission: payload,
      immediate: payload.selection,
      apiBaseUrl: env.apiBaseUrl,
      options: { workspaceId, userId, accessToken: apiAccessToken, signal },
      load: () =>
        editorApiRequest<MusicSelectionSummary | null>(
          `${env.apiBaseUrl}/projects/${projectId}/music/selection`,
          { workspaceId, userId, accessToken: apiAccessToken, signal },
        ),
      resourceLabel: "Music selection",
      onProgress: (progress) => setMessage(`Preparing music: ${progress}%`),
    });
    onMusicSelectionUpdated(selection);
  }

  async function requestTranscript(force = false) {
    if (isTranscribingRef.current || transcriptLoading) return;
    isTranscribingRef.current = true;
    setTranscriptLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{
        transcript_id: string;
        status: TranscriptSummary["status"];
        message?: string;
      }>(`${env.apiBaseUrl}/projects/${projectId}/transcriptions`, {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "Transcription could not be started. Please try again.",
        body: {
          force,
          language_hint: sourceLanguage,
        },
      });
      onTranscriptUpdated({
        id: String(payload.transcript_id),
        status: payload.status,
        language: "unknown",
        provider: "pending",
        model: "pending",
        progress_pct: payload.status === "queued" ? 15 : 55,
        words: [],
      });
      setMessage(payload.message ?? "Transcription queued.");
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      isTranscribingRef.current = false;
      setTranscriptLoading(false);
    }
  }

  async function saveSnapshot() {
    if (!activePlan) {
      setMessage("Generate an AI edit before saving a timeline version.");
      return;
    }
    if (isSnapshotSavingRef.current || snapshotLoading) return;
    isSnapshotSavingRef.current = true;
    setSnapshotLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ snapshot: PlanSnapshotSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Timeline version could not be saved.",
          body: {
            plan_id: activePlan.id,
            name: snapshotName.trim() || `Timeline v${activePlan.version}`,
            notes: `Saved from editor with ${activePlan.summary.cut_count} cuts.`,
          },
        },
      );
      setSnapshots((current) => [
        payload.snapshot,
        ...current.filter((snapshot) => snapshot.id !== payload.snapshot.id),
      ]);
      setSnapshotName("");
      setMessage(`Snapshot saved: ${payload.snapshot.name}`);
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      isSnapshotSavingRef.current = false;
      setSnapshotLoading(false);
    }
  }

  async function restoreSnapshot(snapshotId: string) {
    if (isSnapshotRestoringRef.current || snapshotLoading) return;
    isSnapshotRestoringRef.current = true;
    setSnapshotLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans/snapshots/${snapshotId}/restore`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Timeline version could not be restored.",
          body: {},
        },
      );
      onPlanGenerated(payload.plan);
      setMessage(`Restored timeline version ${payload.plan.version}.`);
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      isSnapshotRestoringRef.current = false;
      setSnapshotLoading(false);
    }
  }

  const transcriptStatus = transcript?.status ?? "queued";
  const transcriptProgress = transcript?.progress_pct ?? 5;
  const transcriptMessage =
    transcript?.status === "failed"
      ? transcript.error_message || "Transcription failed."
      : transcript?.status === "captions_unavailable"
        ? transcript.error_message || "Captions are unavailable for this upload."
        : transcript?.status === "processing"
          ? "Transcribing audio and preparing timed words."
          : transcript?.status === "queued"
            ? "Queued for background transcription."
            : !transcript
              ? "Starting background transcription."
              : "Transcript ready.";

  const isErrorMessage = message && /failed|could not|try/i.test(message);

  return (
    <div className="space-y-6" aria-live="polite">
      <div className="space-y-2">
        <h2 className="text-xl font-black tracking-tight">AI Edit</h2>
        <p className="text-sm text-muted-foreground leading-relaxed">
          AI analyzes the video and writes the first complete edit directly onto your timeline.
        </p>
      </div>

      {/* Visual analysis status */}
      <div
        className="card rounded-xl flex items-start gap-3 p-4 text-sm"
        data-testid="visual-analysis-status"
      >
        {visualStatus.status === "processing" ? (
          <Loader2 className="mt-0.5 size-4 animate-spin text-primary" strokeWidth={1.5} />
        ) : visualStatus.status === "ready" ? (
          <CheckCircle2 className="mt-0.5 size-4 text-success" strokeWidth={1.5} />
        ) : (
          <AlertCircle className="mt-0.5 size-4 text-warning" strokeWidth={1.5} />
        )}
        <div>
          <div className="flex items-center gap-2 font-bold">
            <Eye className="size-4" strokeWidth={1.5} />
            Visual analysis: {visualStatus.status}
          </div>
          <p className="mt-1 text-muted-foreground">{visualStatus.message}</p>
        </div>
      </div>

      {/* Settings card */}
      <div className="card rounded-xl p-5 space-y-4">
        <div className="space-y-2">
          <Label>Edit Style</Label>
          <Select
            label="Edit Style"
            value={style}
            onChange={(val) => setStyle(val as StylePickerValue)}
            options={styles}
          />
        </div>

        <div className="space-y-2">
          <Label>Source Language</Label>
          <Select
            label="Source Language"
            value={sourceLanguage}
            onChange={setSourceLanguage}
            options={sourceLanguages}
          />
        </div>

        <div className="space-y-2">
          <Label>Caption Output Language</Label>
          <Select
            label="Caption Output Language"
            value={outputLanguage}
            onChange={setOutputLanguage}
            options={captionLanguages}
          />
        </div>

        <div className="space-y-2">
          <Slider
            label="B-Roll Intensity"
            value={densityMap[density]}
            onChange={(val) => setDensity(reverseDensityMap(val))}
          />
          <p className="text-[10px] text-muted-foreground text-right italic font-medium">
            {density === "none" ? "No extra media" : 
             density === "low" ? "Minimal inserts" : 
             density === "medium" ? "Balanced storytelling" : 
             "High-energy visual flow"}
          </p>
        </div>

        <div className="space-y-2">
          <Label>Custom Instructions (Optional)</Label>
          <TextArea
            aria-label="Custom AI edit instructions"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. Focus on the product demo, keep the tone humorous..."
            className="min-h-36 font-mono text-[13px]"
          />
        </div>
      </div>

      {/* Transcript status */}
      {!hasTranscript ? (
        <div
          className="card rounded-lg space-y-3 p-4 border-2 border-foreground bg-surface shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
          role={transcript?.status === "failed" ? "alert" : "status"}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-bold capitalize">
                {transcriptStatus.replace(/_/g, " ")}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{transcriptMessage}</p>
            </div>
            {transcript?.status === "failed" ? (
              <AlertCircle className="mt-0.5 size-4 text-danger" strokeWidth={2} />
            ) : (
              <Loader2
                className={
                  transcript?.status === "ready"
                    ? "size-4 text-primary"
                    : "size-4 animate-spin text-primary"
                }
                strokeWidth={2}
              />
            )}
          </div>

          {/* Progress bar */}
          <div
            className="progress-bar h-3 border-2 border-foreground bg-muted rounded-full overflow-hidden"
            role="progressbar"
            aria-label="Transcription progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.max(5, Math.min(transcriptProgress, 100))}
          >
            <div
              className="progress-fill"
              style={{ width: `${Math.max(5, Math.min(transcriptProgress, 100))}%` }}
            />
          </div>

          <div className="flex items-center justify-between text-[11px] font-bold font-mono text-muted-foreground">
            <span>{Math.max(5, Math.min(transcriptProgress, 100))}% complete</span>
            <div className="flex items-center gap-2">
              {!transcript ? (
                <Button
                  onClick={() => requestTranscript(false)}
                  disabled={transcriptLoading}
                  aria-busy={transcriptLoading}
                  className="min-h-11 px-3"
                >
                  {transcriptLoading ? (
                    <Loader2 className="mr-2 size-4 animate-spin" strokeWidth={2} />
                  ) : null}
                  Start
                </Button>
              ) : null}
              {transcript?.status === "failed" || transcript?.status === "captions_unavailable" ? (
                <Button
                  onClick={() => requestTranscript(true)}
                  disabled={transcriptLoading}
                  className="h-8 px-3"
                >
                  {transcriptLoading ? (
                    <Loader2 className="mr-2 size-4 animate-spin" strokeWidth={2} />
                  ) : (
                    <RefreshCw className="mr-2 size-4" strokeWidth={2} />
                  )}
                  Retry
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {/* Generate button */}
      <button
        onClick={generatePlan}
        disabled={loading || !hasTranscript}
        aria-busy={loading}
        aria-describedby={message ? "ai-edit-message" : undefined}
        className="w-full h-12 rounded-md bg-primary text-primary-foreground font-black text-sm border-2 border-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all flex items-center justify-center gap-2"
      >
        {loading ? (
          <Loader2 className="size-5 animate-spin text-primary-foreground" strokeWidth={2} />
        ) : (
          <Sparkles className="size-5" strokeWidth={2} />
        )}
        <span>
          {loading ? "Generating, please wait…" : "Generate AI Edit"}
        </span>
      </button>

      {/* Version history */}
      <div className="card rounded-lg p-4 space-y-3 border-2 border-foreground bg-surface shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff]">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-black uppercase">Version History</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Save named timeline versions and restore any edit without creating a separate review step.
            </p>
          </div>
          <History className="size-4 text-foreground" strokeWidth={2} />
        </div>

        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="Snapshot name"
            value={snapshotName}
            onChange={(event) => setSnapshotName(event.target.value)}
            placeholder={activePlan ? `Timeline v${activePlan.version}` : "Version name"}
            disabled={!activePlan || snapshotLoading}
          />
          <Button
            type="button"
            variant="secondary"
            onClick={saveSnapshot}
            disabled={!activePlan || snapshotLoading}
            className="min-h-11 gap-2 whitespace-nowrap"
          >
            {snapshotLoading ? (
              <Loader2 className="size-4 animate-spin" strokeWidth={2} />
            ) : (
              <Save className="size-4" strokeWidth={2} />
            )}
            Save
          </Button>
        </div>

        {snapshots.length ? (
          <div className="space-y-2">
            {snapshots.slice(0, 5).map((snapshot) => (
              <div
                key={snapshot.id}
                className="card rounded border-2 border-foreground flex items-center justify-between gap-3 px-3 py-2 bg-surface shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]"
              >
                <div className="min-w-0">
                  <p className="truncate text-xs font-black">{snapshot.name}</p>
                  <p className="text-[10px] font-bold text-muted-foreground font-mono">
                    Timeline v{snapshot.source_plan_version} · {snapshot.summary.cut_count} cuts
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => restoreSnapshot(snapshot.id)}
                  disabled={snapshotLoading}
                  className="pixel-button text-xs h-8 px-3"
                >
                  Restore
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="rounded border-2 border-dashed border-foreground p-3 text-xs font-bold text-muted-foreground text-center font-mono">
            No saved snapshots yet.
          </p>
        )}
      </div>

      {/* Status / error message */}
      {message ? (
        <p
          id="ai-edit-message"
          role={isErrorMessage ? "alert" : "status"}
          className={
            isErrorMessage
              ? "rounded border-2 border-danger bg-danger/10 p-4 text-sm text-danger shadow-[2px_2px_0px_#000]"
              : "rounded border-2 border-foreground bg-muted p-3 text-sm font-bold text-foreground font-mono shadow-[2px_2px_0px_#000]"
          }
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}

function defaultCaptionPreset(style: StylePickerValue) {
  switch (style) {
    case "auto":
      return "creator_ticker";
    case "dramatic":
    case "motivational":
      return "impact_stamp";
    case "fashion":
      return "vogue_white";
    case "educational":
      return "educational_card";
    case "clean_minimal":
    case "founder":
      return "founder_clean";
    case "fast_paced":
    case "creator_influencer":
    case "travel":
      return "creator_ticker";
    case "storytelling":
    case "podcast":
      return "founder_clean";
    case "cinematic":
    case "luxury":
      return "minimal_line";
    case "emotional":
    default:
      return "luxury_minimal";
  }
}
