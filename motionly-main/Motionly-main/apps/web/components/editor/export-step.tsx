"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Select, Slider } from "./ui";
import { Button } from "../button";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Loader2,
  RotateCcw,
  Square,
  Video,
} from "lucide-react";
import type {
  ExportFitMode,
  ExportFraming,
  ExportResolution,
  ExportSummary,
  OutputAspect,
  PlatformPreset,
  RenderJobSummary,
  RenderTemplateId,
} from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { createClient } from "@/lib/supabase/browser";

interface ExportStepProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialJobs: RenderJobSummary[];
  initialExports: ExportSummary[];
  captionTrackId?: string | null;
  activePlanId?: string | null;
  sourceWidth?: number | null;
  sourceHeight?: number | null;
}

const platforms: { value: PlatformPreset; label: string }[] = [
  { value: "instagram", label: "Instagram Reel" },
  { value: "tiktok", label: "TikTok" },
  { value: "shorts", label: "YouTube Shorts" },
];

const resolutions: { value: ExportResolution; label: string }[] = [
  { value: "1080p", label: "1080p (Standard)" },
  { value: "4k", label: "4K (Premium)" },
  { value: "720p", label: "720p (Fast)" },
];

const outputAspects: { value: OutputAspect; label: string }[] = [
  { value: "9:16", label: "Vertical 9:16" },
  { value: "original", label: "Original" },
  { value: "1:1", label: "Square 1:1" },
  { value: "4:5", label: "Portrait 4:5" },
  { value: "16:9", label: "Landscape 16:9" },
];

const fitModes: { value: ExportFitMode; label: string }[] = [
  { value: "fill", label: "Fill / Crop" },
  { value: "fit", label: "Fit with Padding" },
  { value: "custom", label: "Custom Crop" },
];

const templates: { value: RenderTemplateId; label: string }[] = [
  { value: "luxury_reel_v1", label: "Cinematic luxury reel" },
  { value: "podcast_reel_v1", label: "Podcast clip" },
  { value: "talking_head_reel_v1", label: "Talking head" },
  { value: "product_promo_reel_v1", label: "Product promo" },
  { value: "travel_reel_v1", label: "Travel vlog" },
  { value: "founder_reel_v1", label: "Founder reel" },
  { value: "fashion_reel_v1", label: "Fashion reel" },
];

export function ExportStep({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialJobs,
  initialExports,
  captionTrackId,
  activePlanId,
  sourceWidth,
  sourceHeight,
}: ExportStepProps) {
  const router = useRouter();
  const [templateId, setTemplateId] = useState<RenderTemplateId>("luxury_reel_v1");
  const [platform, setPlatform] = useState<PlatformPreset>("instagram");
  const [resolution, setResolution] = useState<ExportResolution>("1080p");
  const [outputAspect, setOutputAspect] = useState<OutputAspect>("9:16");
  const [fitMode, setFitMode] = useState<ExportFitMode>("fill");
  const [cropX, setCropX] = useState(50);
  const [cropY, setCropY] = useState(50);
  const [cropZoom, setCropZoom] = useState(100);
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState(initialJobs);
  const [exports, setExports] = useState(initialExports);
  const [message, setMessage] = useState<string | null>(null);
  const [streamingJobId, setStreamingJobId] = useState<string | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);

  const latestJob = jobs[0];
  const latestExport = exports[0];
  const activeStatuses = useMemo(
    () => ["queued", "processing", "preparing", "rendering", "uploading"],
    [],
  );
  const isRendering = latestJob && activeStatuses.includes(latestJob.status);
  const retryableJob =
    latestJob && ["failed", "canceled"].includes(latestJob.status) ? latestJob : null;
  const activeStreamJobId =
    streamingJobId ?? jobs.find((job) => activeStatuses.includes(job.status))?.id ?? null;
  const sourceAspectLabel =
    sourceWidth && sourceHeight ? `${sourceWidth}x${sourceHeight}` : "source size";
  const framing: ExportFraming = {
    output_aspect: outputAspect,
    fit_mode: fitMode,
    crop: {
      x: cropX / 100,
      y: cropY / 100,
      zoom: cropZoom / 100,
    },
    padding_color: "#000000",
  };

  async function startRender() {
    setLoading(true);
    setMessage(null);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/renders`, {
        method: "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          caption_track_id: captionTrackId,
          edit_plan_id: activePlanId,
          template_id: templateId,
          duration_preset: "medium",
          include_captions: true,
          include_broll: true,
          export_options: {
            platform,
            resolution,
            format: "mp4",
            framing,
          },
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Failed to start render");
      setJobs((current) => [
        payload.job,
        ...current.filter((job) => job.id !== payload.job.id),
      ]);
      setMessage(`Render queued: ${payload.queue_job_id}`);
      setStreamingJobId(payload.job.id);
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!activeStreamJobId) return;
    const supabase = createClient();
    const channel = supabase
      .channel(`render-job-${activeStreamJobId}`)
      .on(
        "postgres_changes",
        {
          event: "UPDATE",
          schema: "public",
          table: "render_jobs",
          filter: `id=eq.${activeStreamJobId}`,
        },
        (payload) => {
          const row = payload.new as {
            id?: string;
            status?: RenderJobSummary["status"] | "processing";
            progress_pct?: number;
            error_code?: string | null;
            error_message?: string | null;
            artifact_path?: string | null;
            updated_at?: string;
          };
          if (!row.id) return;
          const status = row.status === "processing" ? "preparing" : row.status;
          setJobs((current) =>
            current.map((job) =>
              job.id === row.id
                ? {
                    ...job,
                    status: status ?? job.status,
                    progress_pct: row.progress_pct ?? job.progress_pct,
                    error_code: row.error_code ?? job.error_code,
                    error_message: row.error_message ?? job.error_message,
                    artifact_path: row.artifact_path ?? job.artifact_path,
                    updated_at: row.updated_at ?? job.updated_at,
                  }
                : job,
            ),
          );
          if (status === "complete") {
            void refreshExports();
            setStreamingJobId(null);
            setMessage("Export is ready to download.");
          } else if (status === "failed" || status === "canceled") {
            setStreamingJobId(null);
            setMessage(row.error_message ?? `Render ${status}.`);
          } else if (status) {
            setMessage(`${statusLabel(status)} (${row.progress_pct ?? 0}%)`);
          }
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  // refreshExports reads the latest auth/project props and is also used by polling.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStreamJobId]);

  useEffect(() => {
    if (!activeStreamJobId) return;
    const renderJobId = activeStreamJobId;
    let canceled = false;
    streamAbortRef.current?.abort();
    const abortController = new AbortController();
    streamAbortRef.current = abortController;

    async function subscribe() {
      try {
        const response = await fetch(
          `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}/events`,
          {
            headers: await buildApiHeaders({
              workspaceId,
              userId,
              accessToken: apiAccessToken,
              json: false,
            }),
            signal: abortController.signal,
          },
        );
        if (!response.ok || !response.body) {
          throw new Error("Render stream unavailable.");
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!canceled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() ?? "";
          for (const chunk of chunks) {
            const dataLine = chunk
              .split("\n")
              .find((line) => line.startsWith("data: "));
            if (!dataLine) continue;
            const job = JSON.parse(dataLine.slice(6)) as RenderJobSummary;
            updateJob(job);
            setMessage(renderMessage(job));
            if (job.status === "complete") {
              await refreshExports();
              setStreamingJobId(null);
              router.refresh();
              return;
            }
            if (job.status === "failed" || job.status === "canceled") {
              setStreamingJobId(null);
              setLoading(false);
              return;
            }
          }
        }
      } catch (error) {
        if (abortController.signal.aborted || canceled) return;
        await pollRender(renderJobId);
      } finally {
        if (!canceled) setLoading(false);
      }
    }

    void subscribe();
    return () => {
      canceled = true;
      abortController.abort();
    };
    // pollRender and refreshExports intentionally read the latest auth/project props.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStreamJobId, apiAccessToken, projectId, router, userId, workspaceId]);

  function updateJob(job: RenderJobSummary) {
    setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
  }

  async function pollRender(renderJobId: string) {
    for (let attempt = 0; attempt < 36; attempt += 1) {
      await sleep(2500);
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}`, {
        headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken, json: false }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Render status could not be refreshed.");
      updateJob(payload);
      setMessage(renderMessage(payload));
      if (payload.status === "complete") {
        await refreshExports();
        setMessage("Export is ready to download.");
        setStreamingJobId(null);
        return;
      }
      if (payload.status === "failed" || payload.status === "canceled") {
        setStreamingJobId(null);
        setMessage(payload.error_message ?? `Render ${payload.status}.`);
        return;
      }
    }
    setMessage("Render is still running. Refresh this project in a moment.");
  }

  async function retryRender(renderJobId: string) {
    setLoading(true);
    setMessage("Requeueing render...");
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}/retry`,
        {
          method: "POST",
          headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken }),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Render could not be retried.");
      updateJob(payload.job);
      setStreamingJobId(payload.job.id);
      setMessage(`Render requeued: ${payload.queue_job_id}`);
    } catch (error) {
      setMessage((error as Error).message);
      setLoading(false);
    }
  }

  async function cancelRender(renderJobId: string) {
    setLoading(true);
    setMessage("Canceling render...");
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}/cancel`,
        {
          method: "POST",
          headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken }),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Render could not be canceled.");
      updateJob(payload);
      setStreamingJobId(null);
      setMessage("Render canceled.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function refreshExports() {
    const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/exports`, {
      headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken, json: false }),
    });
    const payload = await response.json();
    if (response.ok) setExports(payload.exports ?? []);
  }

  async function downloadExport(exportId: string) {
    setLoading(true);
    setMessage("Creating secure download link...");
    try {
      const response = await fetch(`${env.apiBaseUrl}/exports/${exportId}/download-url`, {
        headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken, json: false }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Download link could not be created.");
      window.location.href = payload.signed_download_url;
      setMessage(`Download link expires in ${payload.expires_in_seconds} seconds.`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h3 className="text-xl font-black tracking-tight">Generate Final Video</h3>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Ready to post? Choose the export frame, then render your MP4.
        </p>
      </div>

      <div className="pixel-panel pixel-corners space-y-6 p-4">
        <div className="space-y-2">
          <Label>Creator Template</Label>
          <Select
            value={templateId}
            onChange={(val) => setTemplateId(val as RenderTemplateId)}
            options={templates}
          />
        </div>

        <div className="space-y-2">
          <Label>Target Platform</Label>
          <Select
            value={platform}
            onChange={(val) => setPlatform(val as PlatformPreset)}
            options={platforms}
          />
        </div>

        <div className="space-y-2">
          <Label>Video Quality</Label>
          <Select
            value={resolution}
            onChange={(val) => setResolution(val as ExportResolution)}
            options={resolutions}
          />
        </div>

        <div className="space-y-2">
          <Label>Output Frame</Label>
          <Select
            value={outputAspect}
            onChange={(val) => setOutputAspect(val as OutputAspect)}
            options={outputAspects}
          />
          <p className="text-[10px] font-medium text-muted-foreground">
            Source: {sourceAspectLabel}
          </p>
        </div>

        <div className="space-y-2">
          <Label>Crop Mode</Label>
          <Select
            value={fitMode}
            onChange={(val) => setFitMode(val as ExportFitMode)}
            options={fitModes}
          />
        </div>

        {fitMode === "custom" ? (
          <div className="space-y-5 border-t border-border pt-4">
            <Slider label="Crop X" value={cropX} onChange={setCropX} />
            <Slider label="Crop Y" value={cropY} onChange={setCropY} />
            <Slider
              label="Zoom"
              value={cropZoom}
              min={100}
              max={300}
              onChange={setCropZoom}
            />
            <Button
              type="button"
              variant="secondary"
              className="h-9 w-full text-xs"
              onClick={() => {
                setCropX(50);
                setCropY(50);
                setCropZoom(100);
              }}
            >
              Reset Crop
            </Button>
          </div>
        ) : null}
      </div>

      <Button 
        onClick={startRender} 
        disabled={loading || isRendering || !captionTrackId}
        className="h-14 w-full bg-primary text-primary-foreground"
      >
        {loading || isRendering ? (
          <Loader2 className="mr-2 size-5 animate-spin" />
        ) : (
          <Video className="mr-2 size-5" />
        )}
        <span className="text-base font-bold">
          {isRendering ? `Rendering (${latestJob.progress_pct}%)` : "Start Render"}
        </span>
      </Button>

      {latestJob ? (
        <div className="space-y-3 border border-border bg-muted/40 p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-black uppercase tracking-widest text-muted-foreground">
                Render job
              </p>
              <p className="truncate text-sm font-bold">{statusLabel(latestJob.status)}</p>
            </div>
            <span className="text-sm font-black">{latestJob.progress_pct ?? 0}%</span>
          </div>
          <div className="h-2 overflow-hidden border border-border bg-background">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${Math.max(0, Math.min(100, latestJob.progress_pct ?? 0))}%` }}
            />
          </div>
          {latestJob.error_message ? (
            <div className="flex gap-2 border border-red-200 bg-red-50 p-3 text-xs font-bold text-red-700">
              <AlertTriangle className="size-4 shrink-0" />
              <span>{latestJob.error_message}</span>
            </div>
          ) : null}
          <div className="flex gap-2">
            {isRendering ? (
              <Button
                type="button"
                variant="secondary"
                className="h-9 gap-2 text-xs"
                onClick={() => cancelRender(latestJob.id)}
                disabled={loading}
              >
                <Square className="size-3.5" />
                Cancel
              </Button>
            ) : null}
            {retryableJob ? (
              <Button
                type="button"
                variant="secondary"
                className="h-9 gap-2 text-xs"
                onClick={() => retryRender(retryableJob.id)}
                disabled={loading}
              >
                <RotateCcw className="size-3.5" />
                Retry
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {latestExport && (
        <div className="space-y-4 border-t border-border pt-6">
           <Label>Latest Export</Label>
           <div className="flex items-center justify-between border border-border bg-muted/50 p-4 shadow-soft">
              <div className="flex items-center gap-3">
                 <div className="flex size-10 items-center justify-center border border-emerald-200 bg-emerald-50 text-emerald-600">
                    <CheckCircle2 className="size-5" />
                 </div>
                 <div>
                    <p className="text-xs font-bold uppercase tracking-widest text-muted-foreground">v{latestExport.version}</p>
                    <p className="text-sm font-bold truncate max-w-[120px]">Ready to post</p>
                 </div>
              </div>
              <Button
                variant="secondary"
                className="h-10 gap-2 px-4 text-xs font-bold"
                onClick={() => downloadExport(latestExport.id)}
                disabled={loading}
              >
                 <Download className="size-3.5" />
                 Download
              </Button>
           </div>
        </div>
      )}
      {!captionTrackId ? (
        <p className="border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-700">
          Generate captions before export.
        </p>
      ) : null}
      {message ? (
        <p className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function statusLabel(status: RenderJobSummary["status"]) {
  const labels: Record<string, string> = {
    queued: "Queued",
    processing: "Processing",
    preparing: "Processing",
    rendering: "Rendering",
    uploading: "Uploading",
    complete: "Complete",
    failed: "Failed",
    canceled: "Canceled",
  };
  return labels[status] ?? status;
}

function renderMessage(job: RenderJobSummary) {
  if (job.status === "failed") {
    return job.error_message ?? "Render failed.";
  }
  if (job.status === "canceled") return "Render canceled.";
  if (job.status === "complete") return "Export is ready to download.";
  return `${statusLabel(job.status)} (${job.progress_pct ?? 0}%)`;
}
