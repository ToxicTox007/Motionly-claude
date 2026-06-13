"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Select, TextArea, Slider } from "./ui";
import { Button } from "../button";
import { AlertCircle, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { env } from "@/lib/env";
import { editorApiRequest } from "@/lib/client-api";
import type {
  BrollSlotSummary,
  BrollDensity,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  EditPlanStyleKey,
  MusicSelectionSummary,
  TranscriptSummary,
} from "@motionly/shared";

interface AIStepProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialPlans: EditPlanRecordSummary[];
  activePlanId: string | null;
  transcript: TranscriptSummary | null;
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
  const [message, setMessage] = useState<string | null>(null);
  const hasTranscript = transcript?.status === "ready" && Boolean(transcript.words?.length);

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

  async function generatePlan() {
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ plan: EditPlanRecordSummary }>(
        `${env.apiBaseUrl}/projects/${projectId}/plans`,
        {
        method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "AI edit could not be generated. Please try again.",
          body: {
            prompt,
            ...(style === "auto" ? {} : { style }),
            broll_density: density,
            target_duration_s: 35,
            force: true,
          },
        },
      );
      const plan = payload.plan as EditPlanRecordSummary;
      onPlanGenerated(plan);

      const followUpResults = await Promise.allSettled([
        ensureCaptions(),
        ensureBroll(),
        ensureMusic(),
      ]);
      const failedFollowUps = followUpResults.filter((result) => result.status === "rejected");
      setMessage(
        failedFollowUps.length === 0
          ? `AI edit plan v${plan.version} is ready. Captions, B-roll, and music were prepared.`
          : `AI edit plan v${plan.version} is ready. Some media steps still need attention.`,
      );
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function ensureCaptions() {
    const payload = await editorApiRequest<{ track: CaptionTrackSummary }>(
      `${env.apiBaseUrl}/projects/${projectId}/captions/generate`,
      {
      method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "Captions could not be prepared yet.",
        body: {
          transcript_id: transcript?.id ?? null,
          preset: defaultCaptionPreset(style),
          hide_filler_words: false,
          output_language: outputLanguage,
        },
      },
    );
    onCaptionTrackUpdated(payload.track);
  }

  async function ensureBroll() {
    const payload = await editorApiRequest<{ slots?: BrollSlotSummary[] }>(
      `${env.apiBaseUrl}/projects/${projectId}/broll/generate`,
      {
      method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "B-roll could not be generated for this edit.",
        body: {
          density,
          force: true,
        },
      },
    );
    onBrollSlotsUpdated(payload.slots ?? []);
  }

  async function ensureMusic() {
    const payload = await editorApiRequest<{ selection: MusicSelectionSummary }>(
      `${env.apiBaseUrl}/projects/${projectId}/music/select`,
      {
      method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "No production music track is available for this edit yet.",
        body: {
          include_music: true,
          music_volume: 0.25,
          sfx_intensity: 0.2,
          rights_confirmed: true,
          force: false,
        },
      },
    );
    onMusicSelectionUpdated(payload.selection);
  }

  async function requestTranscript(force = false) {
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
      setTranscriptLoading(false);
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

  return (
    <div className="space-y-6" aria-live="polite">
      <div className="space-y-2">
        <h2 className="text-xl font-black tracking-tight">AI Edit</h2>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Our AI will analyze your video and plan the perfect edit based on your chosen style.
        </p>
      </div>

      <div className="pixel-panel pixel-corners space-y-6 p-4">
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

      {!hasTranscript ? (
        <div className="space-y-3 border border-border bg-muted/40 p-4" role={transcript?.status === "failed" ? "alert" : "status"}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-bold capitalize">
                {transcriptStatus.replace(/_/g, " ")}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{transcriptMessage}</p>
            </div>
            {transcript?.status === "failed" ? (
              <AlertCircle className="mt-0.5 size-4 text-destructive" />
            ) : (
              <Loader2
                className={
                  transcript?.status === "ready" ? "size-4 text-primary" : "size-4 animate-spin text-primary"
                }
              />
            )}
          </div>
          <div className="h-2 overflow-hidden bg-background" role="progressbar" aria-label="Transcription progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.max(5, Math.min(transcriptProgress, 100))}>
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${Math.max(5, Math.min(transcriptProgress, 100))}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-[11px] font-medium text-muted-foreground">
            <span>{Math.max(5, Math.min(transcriptProgress, 100))}% complete</span>
            <div className="flex items-center gap-2">
              {!transcript ? (
                <Button onClick={() => requestTranscript(false)} disabled={transcriptLoading} aria-busy={transcriptLoading} className="min-h-11 px-3">
                  {transcriptLoading ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
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
                    <Loader2 className="mr-2 size-4 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-2 size-4" />
                  )}
                  Retry
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      <Button 
        onClick={generatePlan} 
        disabled={loading || !hasTranscript}
        aria-busy={loading}
        aria-describedby={message ? "ai-edit-message" : undefined}
        className="h-14 w-full bg-primary text-primary-foreground group"
      >
        {loading ? (
          <Loader2 className="mr-2 size-5 animate-spin" />
        ) : (
          <Sparkles className="mr-2 size-5 group-hover:rotate-12 transition-transform" />
        )}
        <span className="text-base font-bold">{loading ? "Generating, please wait" : "Generate AI Edit"}</span>
      </Button>
      
      {message ? (
        <p id="ai-edit-message" role={/failed|could not|try/i.test(message) ? "alert" : "status"} className="border border-border bg-muted p-3 text-sm font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function defaultCaptionPreset(style: StylePickerValue) {
  switch (style) {
    case "auto":
      return "founder_clean";
    case "dramatic":
    case "fashion":
      return "vogue_white";
    case "educational":
    case "clean_minimal":
    case "founder":
      return "founder_clean";
    case "motivational":
    case "fast_paced":
    case "creator_influencer":
    case "travel":
      return "bold_influencer";
    case "storytelling":
    case "podcast":
      return "founder_clean";
    case "emotional":
    case "cinematic":
    case "luxury":
    default:
      return "luxury_minimal";
  }
}
