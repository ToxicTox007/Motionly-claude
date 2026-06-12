"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Select, TextArea, Slider } from "./ui";
import { Button } from "../button";
import { AlertCircle, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { env } from "@/lib/env";
import { buildApiHeaders } from "@/lib/client-api";
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

const styles: { value: EditPlanStyleKey; label: string }[] = [
  { value: "luxury", label: "Luxury (Warm & Premium)" },
  { value: "founder", label: "Founder (Clean & Pro)" },
  { value: "travel", label: "Travel (Vibrant & Immersive)" },
  { value: "fashion", label: "Fashion (Editorial & Dramatic)" },
  { value: "podcast", label: "Podcast (Speaker-focused)" },
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
  
  const [style, setStyle] = useState<EditPlanStyleKey>(activePlan?.style ?? "luxury");
  const [density, setDensity] = useState<BrollDensity>(activePlan?.broll_density ?? "medium");
  const [prompt, setPrompt] = useState(activePlan?.prompt ?? "");
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
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/plans`, {
        method: "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          prompt,
          style,
          broll_density: density,
          target_duration_s: 35, // Default for now
          force: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Failed to generate plan");
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
    const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/captions/generate`, {
      method: "POST",
      headers: {
        ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        transcript_id: transcript?.id ?? null,
        preset: defaultCaptionPreset(style),
        hide_filler_words: false,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message ?? "Caption generation failed");
    }
    onCaptionTrackUpdated(payload.track);
  }

  async function ensureBroll() {
    const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/broll/generate`, {
      method: "POST",
      headers: {
        ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        density,
        force: true,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message ?? "B-roll generation failed");
    }
    onBrollSlotsUpdated(payload.slots ?? []);
  }

  async function ensureMusic() {
    const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/music/select`, {
      method: "POST",
      headers: {
        ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        include_music: true,
        music_volume: 0.25,
        sfx_intensity: 0.25,
        rights_confirmed: true,
        force: false,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message ?? "Music selection failed");
    }
    onMusicSelectionUpdated(payload.selection);
  }

  async function requestTranscript(force = false) {
    setTranscriptLoading(true);
    setMessage(null);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/transcriptions`, {
        method: "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ force }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error?.message ?? "Failed to start transcription");
      }
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
    <div className="space-y-6">
      <div className="space-y-2">
        <h3 className="text-xl font-black tracking-tight">AI Creative Direction</h3>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Our AI will analyze your video and plan the perfect edit based on your chosen style.
        </p>
      </div>

      <div className="pixel-panel pixel-corners space-y-6 p-4">
        <div className="space-y-2">
          <Label>Edit Style</Label>
          <Select
            value={style}
            onChange={(val) => setStyle(val as EditPlanStyleKey)}
            options={styles}
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
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. Focus on the product demo, keep the tone humorous..."
            className="min-h-36 font-mono text-[13px]"
          />
        </div>
      </div>

      {!hasTranscript ? (
        <div className="space-y-3 border border-border bg-muted/40 p-4">
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
          <div className="h-2 overflow-hidden bg-background">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${Math.max(5, Math.min(transcriptProgress, 100))}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-[11px] font-medium text-muted-foreground">
            <span>{Math.max(5, Math.min(transcriptProgress, 100))}% complete</span>
            <div className="flex items-center gap-2">
              {!transcript ? (
                <Button onClick={() => requestTranscript(false)} disabled={transcriptLoading} className="h-8 px-3">
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
        className="h-14 w-full bg-primary text-primary-foreground group"
      >
        {loading ? (
          <Loader2 className="mr-2 size-5 animate-spin" />
        ) : (
          <Sparkles className="mr-2 size-5 group-hover:rotate-12 transition-transform" />
        )}
        <span className="text-base font-bold">Generate AI Edit</span>
      </Button>
      
      {message ? (
        <p className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function defaultCaptionPreset(style: EditPlanStyleKey) {
  switch (style) {
    case "fashion":
      return "vogue_white";
    case "founder":
      return "founder_clean";
    case "travel":
      return "bold_influencer";
    case "podcast":
      return "founder_clean";
    case "luxury":
    default:
      return "luxury_minimal";
  }
}
