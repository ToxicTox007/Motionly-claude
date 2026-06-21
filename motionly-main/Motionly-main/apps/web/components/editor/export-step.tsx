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
import { buildApiHeaders, editorApiRequest } from "@/lib/client-api";
import { env } from "@/lib/env";
import { createClient } from "@/lib/supabase/browser";
import type { PreviewExportState } from "./preview/VideoPreview";

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
  onPreviewExportUpdated?: (state: PreviewExportState | null) => void;
}

const platforms: { value: PlatformPreset; label: string }[] = [
  { value: "instagram", label: "Instagram Reel" },
  { value: "tiktok", label: "TikTok" },
  { value: "shorts", label: "YouTube Shorts" },
  { value: "youtube_landscape", label: "YouTube Landscape" },
  { value: "instagram_post", label: "Instagram Post" },
  { value: "x_landscape", label: "X Landscape" },
  { value: "custom", label: "Custom Download" },
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

type RenderUiState = "idle" | "queued" | "rendering" | "completed" | "failed" | "canceled";

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
  onPreviewExportUpdated,
}: ExportStepProps) {
  const router = useRouter();
  const [templateId, setTemplateId] = useState<RenderTemplateId>("luxury_reel_v1");
  const [platform, setPlatform] = useState<PlatformPreset>("instagram");
  const [resolution, setResolution] = useState<ExportResolution>("1080p");
  const [outputAspect, setOutputAspect] = useState<OutputAspect>("9:16");
  const [fitMode, setFitMode] = useState<ExportFitMode>("fill");
  const [bulkPlatforms, setBulkPlatforms] = useState<PlatformPreset[]>([
    "tiktok",
    "shorts",
    "youtube_landscape",
  ]);
  const [cropX, setCropX] = useState(50);
  const [cropY, setCropY] = useState(50);
  const [cropZoom, setCropZoom] = useState(100);
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState(initialJobs);
  const [exports, setExports] = useState(initialExports);
  const [message, setMessage] = useState<string | null>(null);
  const [streamingJobId, setStreamingJobId] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const streamAbortRef = useRef<AbortController | null>(null);

  const latestJob = jobs[0];
  const latestExport = exports[0];
  const activeStatuses = useMemo(() => ["queued", "processing", "preparing", "rendering", "uploading"], []);
  const isRendering = latestJob && activeStatuses.includes(latestJob.status);
  const renderUiState = getRenderUiState(latestJob);
  const progress = getDisplayProgress(latestJob);
  const etaLabel = estimateRenderEta(latestJob, nowMs);
  const retryableJob = latestJob?.status === "failed" ? latestJob : null;
  const canceledJob = latestJob?.status === "canceled" ? latestJob : null;
  const primaryRenderAction = getPrimaryRenderAction({
    renderUiState,
    loading,
    isRendering: Boolean(isRendering),
    retryableJobId: retryableJob?.id ?? null,
    captionTrackId: captionTrackId ?? null,
    startRender,
    retryRender,
  });
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

  useEffect(() => {
    onPreviewExportUpdated?.({
      outputAspect,
      fitMode,
      cropX,
      cropY,
      cropZoom,
    });
  }, [cropX, cropY, cropZoom, fitMode, onPreviewExportUpdated, outputAspect]);

  useEffect(() => {
    if (!isRendering) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [isRendering]);

  async function startRender() {
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ job: RenderJobSummary; queue_job_id: string }>(
        `${env.apiBaseUrl}/projects/${projectId}/renders`,
        {
        method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Render could not be started. Please try again.",
          body: {
            caption_track_id: captionTrackId ?? null,
            edit_plan_id: activePlanId ?? null,
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
          },
        },
      );
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

  async function startBulkRender() {
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{
        jobs: RenderJobSummary[];
        queue_job_ids: string[];
      }>(
        `${env.apiBaseUrl}/projects/${projectId}/renders/bulk`,
        {
        method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Bulk export could not be started. Please try again.",
          body: {
            caption_track_id: captionTrackId ?? null,
            edit_plan_id: activePlanId ?? null,
            template_id: templateId,
            duration_preset: "medium",
            include_captions: true,
            include_broll: true,
            export_targets: bulkPlatforms.map((targetPlatform) => ({
              platform: targetPlatform,
              resolution,
              format: "mp4",
              framing: {
                ...framing,
                output_aspect: defaultAspectForPlatform(targetPlatform),
              },
            })),
          },
        },
      );
      setJobs((current) => [
        ...payload.jobs,
        ...current.filter((job) => !payload.jobs.some((next) => next.id === job.id)),
      ]);
      setMessage(`Queued ${payload.jobs.length} export targets.`);
      setStreamingJobId(payload.jobs[0]?.id ?? null);
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
            setMessage(status === "failed" ? row.error_message ?? "Render failed." : "Render canceled.");
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
        console.warn("Render event stream unavailable; falling back to polling.", error);
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
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (attempt > 0) await sleep(2500);
      let response: Response;
      try {
        response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}`, {
          cache: "no-store",
          headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken, json: false }),
        });
      } catch (error) {
        console.warn("Render status poll failed; retrying.", error);
        continue;
      }
      const payload = await response.json();
      if (!response.ok) {
        console.warn("Render status poll returned an error; retrying.", payload);
        continue;
      }
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
        setMessage(payload.status === "failed" ? payload.error_message ?? "Render failed." : "Render canceled.");
        return;
      }
    }
    setMessage("Render is still running. Status updates are delayed.");
  }

  async function retryRender(renderJobId: string) {
    setLoading(true);
    setMessage("Requeueing render...");
    try {
      const payload = await editorApiRequest<{ job: RenderJobSummary; queue_job_id: string }>(
        `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}/retry`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Render could not be retried.",
          body: {},
        },
      );
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
      const payload = await editorApiRequest<RenderJobSummary>(
        `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}/cancel`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Render could not be canceled.",
          body: {},
        },
      );
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
    <div id="editor-export" className="space-y-4" aria-labelledby="export-heading">
      <div className="space-y-2">
        <h2 id="export-heading" className="text-xl font-black tracking-tight">Export</h2>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Ready to post? Choose the export frame, then render your MP4.
        </p>
      </div>

      <div className="pixel-panel space-y-3 p-3 sm:pixel-corners sm:p-4">
        <ExportStepSection step="1" title="Format" status="completed">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="mb-0">Creator Template</Label>
              <Select
                id="creator-template"
                label="Creator Template"
                value={templateId}
                onChange={(val) => setTemplateId(val as RenderTemplateId)}
                options={templates}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="mb-0">Target Platform</Label>
              <Select
                id="target-platform"
                label="Target Platform"
                value={platform}
                onChange={(val) => {
                  const nextPlatform = val as PlatformPreset;
                  setPlatform(nextPlatform);
                  setOutputAspect(defaultAspectForPlatform(nextPlatform));
                }}
                options={platforms}
              />
            </div>
          </div>
          <div className="mt-3 border-t border-border pt-3">
            <Label className="mb-2">Bulk Targets</Label>
            <div className="grid gap-2 sm:grid-cols-2">
              {platforms
                .filter((item) => item.value !== "custom")
                .map((item) => {
                  const checked = bulkPlatforms.includes(item.value);
                  return (
                    <label
                      key={item.value}
                      className="flex min-h-11 cursor-pointer items-center justify-between gap-2 border border-border bg-muted/25 px-3 text-xs font-bold text-muted-foreground"
                    >
                      {item.label}
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => {
                          setBulkPlatforms((current) =>
                            event.target.checked
                              ? current.includes(item.value) ? current : [...current, item.value]
                              : current.filter((target) => target !== item.value),
                          );
                        }}
                        className="size-5 accent-primary"
                      />
                    </label>
                  );
                })}
            </div>
          </div>
        </ExportStepSection>

        <ExportStepSection step="2" title="Frame" status={fitMode === "custom" ? "active" : "completed"}>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="mb-0">Output Frame</Label>
              <Select
                id="output-frame"
                label="Output Frame"
                value={outputAspect}
                onChange={(val) => setOutputAspect(val as OutputAspect)}
                options={outputAspects}
              />
              <p className="text-[10px] font-medium text-muted-foreground">
                Source: {sourceAspectLabel}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label className="mb-0">Crop Mode</Label>
              <Select
                id="crop-mode"
                label="Crop Mode"
                value={fitMode}
                onChange={(val) => setFitMode(val as ExportFitMode)}
                options={fitModes}
              />
            </div>
          </div>

          {fitMode === "custom" ? (
            <div className="mt-3 space-y-3 border-t border-border pt-3">
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
                aria-label="Reset crop to centered original size"
                className="min-h-11 w-full gap-2 text-xs"
                onClick={() => {
                  setCropX(50);
                  setCropY(50);
                  setCropZoom(100);
                }}
              >
                <RotateCcw className="size-3.5" />
                Reset Crop
              </Button>
            </div>
          ) : null}
        </ExportStepSection>

        <ExportStepSection step="3" title="Quality" status="completed">
          <div className="space-y-1.5">
            <Label className="mb-0">Video Quality</Label>
            <Select
              id="video-quality"
              label="Video Quality"
              value={resolution}
              onChange={(val) => setResolution(val as ExportResolution)}
              options={resolutions}
            />
          </div>
        </ExportStepSection>
      </div>

      <Button 
        onClick={primaryRenderAction.onClick} 
        disabled={primaryRenderAction.disabled}
        aria-busy={loading || Boolean(isRendering)}
        aria-describedby={message ? "export-message" : !captionTrackId ? "export-missing-captions" : undefined}
        className="h-14 w-full bg-primary text-primary-foreground"
      >
        {loading || isRendering ? (
          <Loader2 className="mr-2 size-5 animate-spin" />
        ) : primaryRenderAction.icon === "retry" ? (
          <RotateCcw className="mr-2 size-5" />
        ) : (
          <Video className="mr-2 size-5" />
        )}
        <span className="text-base font-bold">
          {primaryRenderAction.label}
        </span>
      </Button>
      <Button
        type="button"
        variant="secondary"
        onClick={startBulkRender}
        disabled={loading || Boolean(isRendering) || !captionTrackId || bulkPlatforms.length === 0}
        aria-busy={loading || Boolean(isRendering)}
        className="h-12 w-full"
      >
        {loading || isRendering ? (
          <Loader2 className="mr-2 size-4 animate-spin" />
        ) : (
          <Download className="mr-2 size-4" />
        )}
        Queue Selected Formats
      </Button>

      {latestJob ? (
        <div className="space-y-3 border border-border bg-muted/40 p-3" aria-live="polite">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <RenderStatusIcon state={renderUiState} loading={loading || Boolean(isRendering)} />
              <div className="min-w-0">
              <p className="text-xs font-black uppercase tracking-widest text-muted-foreground">
                Render job
              </p>
              <p className="truncate text-sm font-bold">{uiStateLabel(renderUiState)}</p>
              </div>
            </div>
            {renderUiState === "queued" || renderUiState === "rendering" || renderUiState === "completed" ? (
              <span className="text-right text-sm font-black">
                {progress}%
                {etaLabel ? (
                  <span className="block text-[10px] font-bold text-muted-foreground">
                    ETA {etaLabel}
                  </span>
                ) : null}
              </span>
            ) : null}
          </div>
          {renderUiState === "queued" || renderUiState === "rendering" || renderUiState === "completed" ? (
            <div
              className="h-3 overflow-hidden border border-border bg-background"
              role="progressbar"
              aria-label={`Render progress: ${uiStateLabel(renderUiState)}`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress}
              aria-valuetext={`${progress}% ${uiStateLabel(renderUiState)}`}
            >
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          ) : null}
          {renderUiState === "failed" && latestJob.error_message ? (
            <div role="alert" className="flex gap-2 border border-red-200 bg-red-50 p-3 text-xs font-bold text-red-700">
              <AlertTriangle className="size-4 shrink-0" />
              <span>{latestJob.error_message} Try again, or reduce caption/B-roll complexity before retrying.</span>
            </div>
          ) : null}
          <div className="flex gap-2">
            {isRendering ? (
              <Button
                type="button"
                variant="secondary"
                aria-label="Cancel render"
                className="min-h-11 gap-2 text-xs"
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
                aria-label="Retry failed render"
                className="min-h-11 gap-2 text-xs"
                onClick={() => retryRender(retryableJob.id)}
                disabled={loading}
              >
                <RotateCcw className="size-3.5" />
                Retry
              </Button>
            ) : null}
            {canceledJob ? (
              <Button
                type="button"
                variant="secondary"
                aria-label="Start render again"
                className="min-h-11 gap-2 text-xs"
                onClick={startRender}
                disabled={loading || !captionTrackId}
              >
                <Video className="size-3.5" />
                Start again
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
                aria-label="Download latest export"
                className="min-h-11 gap-2 px-4 text-xs font-bold"
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
        <p id="export-missing-captions" role="alert" className="border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-700">
          Generate captions before export. Open the Text tab and generate or save captions, then start render again.
        </p>
      ) : null}
      {message ? (
        <p id="export-message" role={message.toLowerCase().includes("failed") || message.toLowerCase().includes("could not") ? "alert" : "status"} aria-live="polite" className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function ExportStepSection({
  step,
  title,
  status,
  children,
}: {
  step: string;
  title: string;
  status: "active" | "completed" | "idle";
  children: React.ReactNode;
}) {
  return (
    <section className="border border-border bg-surface/55">
      <div className="flex min-h-11 items-center gap-3 border-b border-border px-3">
        <span className="flex size-6 shrink-0 items-center justify-center border border-border bg-background text-xs font-black">
          {step}
        </span>
        <h3 className="flex-1 text-sm font-black">{title}</h3>
        <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
          {status === "completed" ? <CheckCircle2 className="size-3.5 text-primary" /> : null}
          {status === "active" ? <Video className="size-3.5 text-primary" /> : null}
          {status}
        </span>
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}

function RenderStatusIcon({
  state,
  loading,
}: {
  state: RenderUiState;
  loading: boolean;
}) {
  if (loading || state === "queued" || state === "rendering") {
    return <Loader2 className="size-5 shrink-0 animate-spin text-primary" aria-hidden="true" />;
  }
  if (state === "completed") {
    return <CheckCircle2 className="size-5 shrink-0 text-primary" aria-hidden="true" />;
  }
  if (state === "failed") {
    return <AlertTriangle className="size-5 shrink-0 text-red-600" aria-hidden="true" />;
  }
  if (state === "canceled") {
    return <Square className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />;
  }
  return <Video className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />;
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
    complete: "Completed",
    failed: "Failed",
    canceled: "Canceled",
  };
  return labels[status] ?? status;
}

function getRenderUiState(job?: RenderJobSummary): RenderUiState {
  if (!job) return "idle";
  if (job.status === "queued") return "queued";
  if (job.status === "complete") return "completed";
  if (job.status === "failed") return "failed";
  if (job.status === "canceled") return "canceled";
  return "rendering";
}

function defaultAspectForPlatform(platform: PlatformPreset): OutputAspect {
  if (platform === "youtube_landscape" || platform === "x_landscape") return "16:9";
  if (platform === "instagram_post") return "4:5";
  if (platform === "custom") return "original";
  return "9:16";
}

function uiStateLabel(state: RenderUiState) {
  const labels: Record<RenderUiState, string> = {
    idle: "Idle",
    queued: "Queued",
    rendering: "Rendering",
    completed: "Completed",
    failed: "Failed",
    canceled: "Canceled",
  };
  return labels[state];
}

function getDisplayProgress(job?: RenderJobSummary) {
  if (!job) return 0;
  if (job.status === "complete") return 100;
  if (job.status === "failed" || job.status === "canceled") return 0;
  return Math.max(0, Math.min(100, job.progress_pct ?? 0));
}

function estimateRenderEta(job: RenderJobSummary | undefined, nowMs: number) {
  if (!job || job.status === "complete" || job.status === "failed" || job.status === "canceled") {
    return null;
  }
  const progress = getDisplayProgress(job);
  if (progress < 5) return "calculating";
  const startedAt = Date.parse(job.created_at);
  if (!Number.isFinite(startedAt)) return "calculating";
  const elapsedMs = Math.max(1, nowMs - startedAt);
  const totalMs = elapsedMs / (progress / 100);
  const remainingMs = Math.max(0, totalMs - elapsedMs);
  return formatDuration(remainingMs);
}

function formatDuration(ms: number) {
  const totalSeconds = Math.max(1, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes <= 0) return `${seconds}s`;
  return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

function getPrimaryRenderAction({
  renderUiState,
  loading,
  isRendering,
  retryableJobId,
  captionTrackId,
  startRender,
  retryRender,
}: {
  renderUiState: RenderUiState;
  loading: boolean;
  isRendering: boolean;
  retryableJobId: string | null;
  captionTrackId: string | null;
  startRender: () => void;
  retryRender: (renderJobId: string) => void;
}) {
  if (loading) {
    return {
      label: "Starting render, please wait",
      disabled: true,
      icon: "video" as const,
      onClick: startRender,
    };
  }
  if (isRendering) {
    return {
      label: renderUiState === "queued" ? "Queued" : "Rendering",
      disabled: true,
      icon: "video" as const,
      onClick: startRender,
    };
  }
  if (renderUiState === "failed" && retryableJobId) {
    return {
      label: "Retry Render",
      disabled: false,
      icon: "retry" as const,
      onClick: () => retryRender(retryableJobId),
    };
  }
  if (renderUiState === "canceled") {
    return {
      label: "Start Again",
      disabled: !captionTrackId,
      icon: "video" as const,
      onClick: startRender,
    };
  }
  return {
    label: "Start Render",
    disabled: !captionTrackId,
    icon: "video" as const,
    onClick: startRender,
  };
}

function renderMessage(job: RenderJobSummary) {
  if (job.status === "failed") {
    return job.error_message ?? "Render failed.";
  }
  if (job.status === "canceled") return "Render canceled.";
  if (job.status === "complete") return "Export is ready to download.";
  return `${statusLabel(job.status)} (${job.progress_pct ?? 0}%)`;
}
