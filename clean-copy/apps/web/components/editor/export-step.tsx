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
  Send,
  Square,
  Trash2,
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
import { useToast } from "../toast";

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
  maxExportResolution: ExportResolution;
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
  { value: "4k", label: "4K (Agency)" },
  { value: "720p", label: "720p (Fast)" },
];
const resolutionRank: Record<ExportResolution, number> = {
  "720p": 0,
  "1080p": 1,
  "4k": 2,
};

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

type RenderUiState =
  | "idle"
  | "queued"
  | "rendering"
  | "completed"
  | "failed"
  | "canceled";
type PublishPlatform = "tiktok" | "instagram" | "youtube";
type PublishConnection = {
  id: string;
  platform: PublishPlatform;
  external_account_id: string;
  account_name: string;
};
type PublishJob = {
  id: string;
  platform: PublishPlatform;
  status: "queued" | "publishing" | "published" | "failed";
  external_url?: string | null;
  error_message?: string | null;
};

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
  maxExportResolution,
  onPreviewExportUpdated,
}: ExportStepProps) {
  const router = useRouter();
  const { toast } = useToast();
  const [templateId, setTemplateId] =
    useState<RenderTemplateId>("luxury_reel_v1");
  const [platform, setPlatform] = useState<PlatformPreset>("instagram");
  const [resolution, setResolution] = useState<ExportResolution>(
    maxExportResolution === "720p" ? "720p" : "1080p",
  );
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
  const availableResolutions = useMemo(
    () =>
      resolutions.filter(
        (option) =>
          resolutionRank[option.value] <= resolutionRank[maxExportResolution],
      ),
    [maxExportResolution],
  );
  const [streamingJobId, setStreamingJobId] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [connections, setConnections] = useState<PublishConnection[]>([]);
  const [publishJobs, setPublishJobs] = useState<PublishJob[]>([]);
  const [connectPlatform, setConnectPlatform] =
    useState<PublishPlatform>("tiktok");
  const [connectAccountId, setConnectAccountId] = useState("");
  const [connectAccountName, setConnectAccountName] = useState("");
  const [connectToken, setConnectToken] = useState("");
  const [publishCaption, setPublishCaption] = useState("");
  const streamAbortRef = useRef<AbortController | null>(null);

  const latestJob = jobs[0];
  const latestExport = exports[0];
  const activeStatuses = useMemo(
    () => ["queued", "processing", "preparing", "rendering", "uploading"],
    [],
  );
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
    streamingJobId ??
    jobs.find((job) => activeStatuses.includes(job.status))?.id ??
    null;
  const sourceAspectLabel =
    sourceWidth && sourceHeight
      ? `${sourceWidth}x${sourceHeight}`
      : "source size";
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

  useEffect(() => {
    void refreshPublishing();
    // refreshPublishing reads the latest auth/project props.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiAccessToken, projectId, userId, workspaceId]);

  useEffect(() => {
    if (
      !publishJobs.some(
        (job) => job.status === "queued" || job.status === "publishing",
      )
    )
      return;
    const timer = window.setInterval(() => void refreshPublishing(), 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publishJobs]);

  async function startRender() {
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{
        job: RenderJobSummary;
        queue_job_id: string;
      }>(`${env.apiBaseUrl}/projects/${projectId}/renders`, {
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
      });
      setJobs((current) => [
        payload.job,
        ...current.filter((job) => job.id !== payload.job.id),
      ]);
      setMessage(`Render queued: ${payload.queue_job_id}`);
      setStreamingJobId(payload.job.id);
      toast({
        title: "Render Queued",
        description: "Your video has been added to the render queue.",
        variant: "success",
      });
    } catch (error) {
      console.error(error);
      const errMsg = (error as Error).message;
      setMessage(errMsg);
      toast({
        title: "Export Failed to Start",
        description:
          "Check for missing timeline clips, verify your network connection, or try again.",
        variant: "error",
      });
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
      }>(`${env.apiBaseUrl}/projects/${projectId}/renders/bulk`, {
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
      });
      setJobs((current) => [
        ...payload.jobs,
        ...current.filter(
          (job) => !payload.jobs.some((next) => next.id === job.id),
        ),
      ]);
      setMessage(`Queued ${payload.jobs.length} export targets.`);
      setStreamingJobId(payload.jobs[0]?.id ?? null);
      toast({
        title: "Bulk Render Queued",
        description: `Successfully enqueued ${payload.jobs.length} export targets.`,
        variant: "success",
      });
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
      toast({
        title: "Bulk Render Failed",
        description:
          "Failed to queue multiple exports. Verify that captions are active and try again.",
        variant: "error",
      });
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
            toast({
              title: "Export Ready",
              description: "Your video has compiled successfully.",
              variant: "success",
            });
          } else if (status === "failed" || status === "canceled") {
            setStreamingJobId(null);
            setMessage(
              status === "failed"
                ? (row.error_message ?? "Render failed.")
                : "Render canceled.",
            );
            toast({
              title: status === "failed" ? "Render Failed" : "Render Canceled",
              description:
                status === "failed"
                  ? "Server-side compilation failed. Reducing effects or retrying may help."
                  : "The render was canceled.",
              variant: status === "failed" ? "error" : "info",
            });
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
              toast({
                title: "Render Complete",
                description: "Your video export is ready.",
                variant: "success",
              });
              return;
            }
            if (job.status === "failed" || job.status === "canceled") {
              setStreamingJobId(null);
              setLoading(false);
              toast({
                title:
                  job.status === "failed" ? "Render Failed" : "Render Canceled",
                description:
                  job.status === "failed"
                    ? "Server-side compilation failed. Click Retry or reduce complexity."
                    : "Render was canceled.",
                variant: job.status === "failed" ? "error" : "info",
              });
              return;
            }
          }
        }
      } catch (error) {
        if (abortController.signal.aborted || canceled) return;
        console.warn(
          "Render event stream unavailable; falling back to polling.",
          error,
        );
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
  }, [
    activeStreamJobId,
    apiAccessToken,
    projectId,
    router,
    userId,
    workspaceId,
  ]);

  function updateJob(job: RenderJobSummary) {
    setJobs((current) => [
      job,
      ...current.filter((item) => item.id !== job.id),
    ]);
  }

  async function pollRender(renderJobId: string) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (attempt > 0) await sleep(2500);
      let response: Response;
      try {
        response = await fetch(
          `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}`,
          {
            cache: "no-store",
            headers: await buildApiHeaders({
              workspaceId,
              userId,
              accessToken: apiAccessToken,
              json: false,
            }),
          },
        );
      } catch (error) {
        console.warn("Render status poll failed; retrying.", error);
        continue;
      }
      const payload = await response.json();
      if (!response.ok) {
        console.warn(
          "Render status poll returned an error; retrying.",
          payload,
        );
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
        setMessage(
          payload.status === "failed"
            ? (payload.error_message ?? "Render failed.")
            : "Render canceled.",
        );
        return;
      }
    }
    setMessage("Render is still running. Status updates are delayed.");
  }

  async function retryRender(renderJobId: string) {
    setLoading(true);
    setMessage("Requeueing render...");
    try {
      const payload = await editorApiRequest<{
        job: RenderJobSummary;
        queue_job_id: string;
      }>(
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
    const response = await fetch(
      `${env.apiBaseUrl}/projects/${projectId}/exports`,
      {
        headers: await buildApiHeaders({
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          json: false,
        }),
      },
    );
    const payload = await response.json();
    if (response.ok) setExports(payload.exports ?? []);
  }

  async function downloadExport(exportId: string) {
    setLoading(true);
    setMessage("Creating secure download link...");
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/exports/${exportId}/download-url`,
        {
          headers: await buildApiHeaders({
            workspaceId,
            userId,
            accessToken: apiAccessToken,
            json: false,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Download link could not be created.",
        );
      window.location.href = payload.signed_download_url;
      setMessage(
        `Download link expires in ${payload.expires_in_seconds} seconds.`,
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function refreshPublishing() {
    const headers = await buildApiHeaders({
      workspaceId,
      userId,
      accessToken: apiAccessToken,
      json: false,
    });
    const [connectionsResponse, jobsResponse] = await Promise.all([
      fetch(`${env.apiBaseUrl}/publish/connections`, {
        cache: "no-store",
        headers,
      }),
      fetch(`${env.apiBaseUrl}/projects/${projectId}/publish`, {
        cache: "no-store",
        headers,
      }),
    ]);
    if (connectionsResponse.ok) {
      const payload = await connectionsResponse.json();
      setConnections(payload.connections ?? []);
    }
    if (jobsResponse.ok) {
      const payload = await jobsResponse.json();
      setPublishJobs(payload.jobs ?? []);
    }
  }

  async function connectPublishingAccount() {
    setLoading(true);
    setMessage(null);
    try {
      await editorApiRequest(`${env.apiBaseUrl}/publish/connections`, {
        method: "POST",
        workspaceId,
        userId,
        accessToken: apiAccessToken,
        friendlyError: "Social account could not be connected.",
        body: {
          platform: connectPlatform,
          access_token: connectToken,
          external_account_id: connectAccountId,
          account_name: connectAccountName,
        },
      });
      setConnectToken("");
      setConnectAccountId("");
      setConnectAccountName("");
      await refreshPublishing();
      setMessage(`${socialLabel(connectPlatform)} account connected.`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function connectPublishingOAuth() {
    setLoading(true);
    setMessage(null);
    try {
      const payload = await editorApiRequest<{ authorization_url: string }>(
        `${env.apiBaseUrl}/publish/oauth/${connectPlatform}/start`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: `${socialLabel(connectPlatform)} connection could not be started.`,
          body: { return_url: window.location.href },
        },
      );
      window.location.assign(payload.authorization_url);
    } catch (error) {
      setMessage((error as Error).message);
      setLoading(false);
    }
  }

  async function disconnectPublishingAccount(connectionId: string) {
    setLoading(true);
    try {
      await editorApiRequest(
        `${env.apiBaseUrl}/publish/connections/${connectionId}`,
        {
          method: "DELETE",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: "Social account could not be disconnected.",
        },
      );
      await refreshPublishing();
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function publishExport(connection: PublishConnection) {
    if (!latestExport) return;
    setLoading(true);
    setMessage(`Queueing ${socialLabel(connection.platform)} publish...`);
    try {
      await editorApiRequest(
        `${env.apiBaseUrl}/projects/${projectId}/publish`,
        {
          method: "POST",
          workspaceId,
          userId,
          accessToken: apiAccessToken,
          friendlyError: `Could not publish to ${socialLabel(connection.platform)}.`,
          body: {
            export_id: latestExport.id,
            connection_id: connection.id,
            title: `Motionly export v${latestExport.version}`,
            caption: publishCaption,
            privacy: "private",
          },
        },
      );
      await refreshPublishing();
      setMessage(`${socialLabel(connection.platform)} publish queued.`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      id="editor-export"
      className="space-y-4"
      aria-labelledby="export-heading"
    >
      <div className="space-y-2 border-b-2 border-foreground pb-4">
        <h2 id="export-heading" className="text-2xl font-black uppercase tracking-tight">
          Export
        </h2>
        <p className="text-xs text-muted-foreground leading-relaxed font-mono">
          Ready to post? Choose the export frame, then render your MP4.
        </p>
      </div>

      <div className="card rounded-md border-2 border-foreground p-4 sm:p-5 space-y-4 shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] bg-surface">
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
          <div className="mt-4 border-t-2 border-foreground/20 pt-4">
            <Label className="mb-2.5">Bulk Targets</Label>
            <div className="grid gap-2 sm:grid-cols-2">
              {platforms
                .filter((item) => item.value !== "custom")
                .map((item) => {
                  const checked = bulkPlatforms.includes(item.value);
                  return (
                    <label
                      key={item.value}
                      className={`flex min-h-[44px] cursor-pointer items-center justify-between gap-2 rounded-md border-2 p-3 text-xs font-bold transition-all shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none ${
                        checked
                          ? "bg-primary border-foreground text-primary-foreground"
                          : "bg-surface border-foreground/30 hover:border-foreground text-foreground"
                      }`}
                    >
                      {item.label}
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => {
                          setBulkPlatforms((current) =>
                            event.target.checked
                              ? current.includes(item.value)
                                ? current
                                : [...current, item.value]
                              : current.filter(
                                  (target) => target !== item.value,
                                ),
                          );
                        }}
                        className="size-4.5 rounded border-2 border-foreground text-primary focus:ring-primary/30 accent-primary"
                      />
                    </label>
                  );
                })}
            </div>
          </div>
        </ExportStepSection>

        <ExportStepSection
          step="2"
          title="Frame"
          status={fitMode === "custom" ? "active" : "completed"}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5 sm:col-span-2">
              <Label className="mb-1">Output Frame</Label>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {outputAspects.map((opt) => {
                  const active = outputAspect === opt.value;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setOutputAspect(opt.value)}
                      className={`flex items-center justify-center min-h-[40px] px-3 py-1.5 rounded-md border-2 text-xs font-bold transition-all shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none ${
                        active
                          ? "bg-primary border-foreground text-primary-foreground"
                          : "bg-surface border-foreground/30 hover:border-foreground text-foreground"
                      }`}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
              <p className="text-[10px] font-mono text-muted-foreground mt-1">
                Source: {sourceAspectLabel}
              </p>
            </div>

            <div className="space-y-1.5 sm:col-span-2 mt-2">
              <Label className="mb-1">Crop Mode</Label>
              <div className="grid grid-cols-3 gap-2">
                {fitModes.map((opt) => {
                  const active = fitMode === opt.value;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setFitMode(opt.value)}
                      className={`flex items-center justify-center min-h-[40px] px-3 py-1.5 rounded-md border-2 text-xs font-bold transition-all shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none ${
                        active
                          ? "bg-primary border-foreground text-primary-foreground"
                          : "bg-surface border-foreground/30 hover:border-foreground text-foreground"
                      }`}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {fitMode === "custom" ? (
            <div className="mt-3 space-y-3 border-t-2 border-foreground/20 pt-3">
              <Slider label="Crop X" value={cropX} onChange={setCropX} />
              <Slider label="Crop Y" value={cropY} onChange={setCropY} />
              <Slider
                label="Zoom"
                value={cropZoom}
                min={100}
                max={300}
                onChange={setCropZoom}
              />
              <button
                type="button"
                aria-label="Reset crop to centered original size"
                className="w-full inline-flex items-center justify-center gap-2 h-11 rounded-md border-2 border-foreground bg-surface text-foreground font-bold hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] text-xs uppercase"
                onClick={() => {
                  setCropX(50);
                  setCropY(50);
                  setCropZoom(100);
                }}
              >
                <RotateCcw className="size-3.5" />
                Reset Crop
              </button>
            </div>
          ) : null}
        </ExportStepSection>

        <ExportStepSection step="3" title="Quality" status="completed">
          <div className="space-y-1.5">
            <Label className="mb-1">Video Quality</Label>
            <div className="flex flex-wrap gap-2">
              {availableResolutions.map((opt) => {
                const active = resolution === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setResolution(opt.value)}
                    className={`flex items-center justify-center px-4 py-1.5 rounded-full border-2 text-xs font-black font-mono transition-all shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none ${
                      active
                        ? "bg-primary border-foreground text-primary-foreground"
                        : "bg-surface border-foreground/30 hover:border-foreground text-foreground"
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>
        </ExportStepSection>
      </div>

      <button
        onClick={primaryRenderAction.onClick}
        disabled={primaryRenderAction.disabled}
        aria-busy={loading || Boolean(isRendering)}
        aria-describedby={
          message
            ? "export-message"
            : !captionTrackId
              ? "export-missing-captions"
              : undefined
        }
        className="w-full inline-flex items-center justify-center gap-2 h-14 rounded-md bg-primary text-primary-foreground border-2 border-foreground hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none focus:outline-none font-black uppercase text-sm"
      >
        {loading || isRendering ? (
          <Loader2 className="size-5 animate-spin" />
        ) : primaryRenderAction.icon === "retry" ? (
          <RotateCcw className="size-5" />
        ) : (
          <Video className="size-5" />
        )}
        <span>{primaryRenderAction.label}</span>
      </button>
      <button
        type="button"
        onClick={startBulkRender}
        disabled={
          loading ||
          Boolean(isRendering) ||
          !captionTrackId ||
          bulkPlatforms.length === 0
        }
        aria-busy={loading || Boolean(isRendering)}
        className="w-full inline-flex items-center justify-center gap-2 h-12 mt-2 rounded-md bg-surface text-foreground border-2 border-foreground hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none focus:outline-none font-black uppercase text-xs"
      >
        {loading || isRendering ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Download className="size-4" />
        )}
        Queue Selected Formats
      </button>

      {latestJob ? (
        <div
          className="space-y-3 border-2 border-foreground bg-surface rounded-md p-4 shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff]"
          aria-live="polite"
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <RenderStatusIcon
                state={renderUiState}
                loading={loading || Boolean(isRendering)}
              />
              <div className="min-w-0">
                <p className="text-xs font-black uppercase tracking-widest text-muted-foreground font-mono">
                  Render job
                </p>
                <p className="truncate text-sm font-black">
                  {uiStateLabel(renderUiState)}
                </p>
                {latestJob.specs ? (
                  <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-0.5 font-mono text-xs text-muted-foreground">
                    <span>
                      {latestJob.specs.width}x{latestJob.specs.height}
                    </span>
                    <span>•</span>
                    <span className="uppercase">{latestJob.specs.format}</span>
                    <span>•</span>
                    <span>{latestJob.specs.fps}fps</span>
                  </div>
                ) : null}
              </div>
            </div>
            {renderUiState === "queued" ||
            renderUiState === "rendering" ||
            renderUiState === "completed" ? (
              <span className="text-right text-sm font-black text-primary font-mono">
                {progress}%
                {etaLabel ? (
                  <span className="block text-[10px] font-bold text-muted-foreground font-mono">
                    ETA {etaLabel}
                  </span>
                ) : null}
              </span>
            ) : null}
          </div>
          {renderUiState === "queued" ||
          renderUiState === "rendering" ||
          renderUiState === "completed" ? (
            <div
              className="progress-bar mt-2 h-3 border-2 border-foreground bg-muted rounded-full overflow-hidden"
              role="progressbar"
              aria-label={`Render progress: ${uiStateLabel(renderUiState)}`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress}
              aria-valuetext={`${progress}% ${uiStateLabel(renderUiState)}`}
            >
              <div
                className="bg-primary h-full transition-all duration-[150ms]"
                style={{ width: `${progress}%` }}
              />
            </div>
          ) : null}
          {renderUiState === "failed" && latestJob.error_message ? (
            <div
              role="alert"
              className="flex flex-col gap-2 rounded-md border-2 border-foreground bg-danger/10 p-4 text-xs font-bold text-danger"
            >
              <div className="flex gap-2 items-center">
                <AlertTriangle className="size-4 shrink-0" strokeWidth={2} />
                <span className="font-black">Render Failed: {latestJob.error_message}</span>
              </div>
              <div className="pl-6 font-semibold text-muted-foreground leading-relaxed">
                What you can do next:
                <ul className="list-disc pl-4 mt-1 space-y-0.5 font-mono">
                  <li>Verify that no timeline clips are missing or empty.</li>
                  <li>Reduce caption formatting or B-roll complexity.</li>
                  <li>
                    Check your network connection and click &quot;Retry&quot;
                    below to compile again.
                  </li>
                </ul>
              </div>
            </div>
          ) : null}
          <div className="flex gap-2">
            {isRendering ? (
              <button
                type="button"
                aria-label="Cancel render"
                className="min-h-10 flex-1 inline-flex items-center justify-center gap-2 text-xs font-black uppercase rounded-md border-2 border-foreground bg-surface text-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-50"
                onClick={() => cancelRender(latestJob.id)}
                disabled={loading}
              >
                <Square className="size-3.5" strokeWidth={2} />
                Cancel
              </button>
            ) : null}
            {retryableJob ? (
              <button
                type="button"
                aria-label="Retry failed render"
                className="min-h-10 flex-1 inline-flex items-center justify-center gap-2 text-xs font-black uppercase rounded-md border-2 border-foreground bg-surface text-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-50"
                onClick={() => retryRender(retryableJob.id)}
                disabled={loading}
              >
                <RotateCcw className="size-3.5" strokeWidth={2} />
                Retry
              </button>
            ) : null}
            {canceledJob ? (
              <button
                type="button"
                aria-label="Start render again"
                className="min-h-10 flex-1 inline-flex items-center justify-center gap-2 text-xs font-black uppercase rounded-md border-2 border-foreground bg-surface text-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-50"
                onClick={startRender}
                disabled={loading || !captionTrackId}
              >
                <Video className="size-3.5" strokeWidth={2} />
                Start again
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {latestExport && (
        <div className="space-y-4 border-t-2 border-foreground pt-6">
          <Label>Latest Export</Label>
          <div className="flex items-center justify-between border-2 border-foreground bg-surface p-4 rounded-md shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff]">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-md border-2 border-foreground bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="size-5" strokeWidth={2} />
              </div>
              <div>
                <p className="text-xs font-black uppercase tracking-widest text-muted-foreground font-mono">
                  v{latestExport.version}
                </p>
                <p className="text-sm font-black text-foreground">
                  Ready to post
                </p>
                <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-1 font-mono text-xs text-muted-foreground">
                  <span>{latestExport.width}x{latestExport.height}</span>
                  <span>•</span>
                  <span>{latestExport.format.toUpperCase()}</span>
                  {latestExport.duration_ms ? (
                    <>
                      <span>•</span>
                      <span>{formatDuration(latestExport.duration_ms)}</span>
                    </>
                  ) : null}
                  {latestExport.file_size_bytes ? (
                    <>
                      <span>•</span>
                      <span>{(latestExport.file_size_bytes / (1024 * 1024)).toFixed(1)}MB</span>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
            <button
              type="button"
              aria-label="Download latest export"
              className="inline-flex min-h-11 items-center justify-center gap-2 px-4 text-xs font-black uppercase border-2 border-foreground bg-surface text-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-60 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none"
              onClick={() => downloadExport(latestExport.id)}
              disabled={loading}
            >
              <Download className="size-3.5" strokeWidth={2} />
              Download
            </button>
          </div>
        </div>
      )}
      <section className="space-y-3 border-t-2 border-foreground pt-5">
        <div>
          <Label>Direct publish</Label>
          <p className="mt-1 text-xs text-muted-foreground">
            Connect an API-enabled creator account, then send the latest export
            without downloading it.
          </p>
        </div>
        <div>
          <select
            aria-label="Social platform"
            value={connectPlatform}
            onChange={(event) =>
              setConnectPlatform(event.target.value as PublishPlatform)
            }
            className="flex min-h-11 w-full appearance-none rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 pr-10 text-xs font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[3px_3px_0px_#000] dark:focus:shadow-[3px_3px_0px_#fff] focus:outline-none text-foreground"
          >
            <option value="tiktok" className="bg-surface text-foreground">TikTok</option>
            <option value="instagram" className="bg-surface text-foreground">Instagram</option>
            <option value="youtube" className="bg-surface text-foreground">YouTube</option>
          </select>
        </div>
        <button
          type="button"
          disabled={loading}
          onClick={connectPublishingOAuth}
          className="min-h-11 w-full inline-flex items-center justify-center gap-2 rounded-md bg-primary text-primary-foreground border-2 border-foreground hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] focus:outline-none text-xs font-black uppercase"
        >
          Connect {socialLabel(connectPlatform)} with OAuth
        </button>
        <p className="text-[10px] font-black uppercase tracking-wider text-muted-foreground font-mono">
          Developer fallback
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          <input
            aria-label="Account name"
            placeholder="Account name"
            value={connectAccountName}
            onChange={(event) => setConnectAccountName(event.target.value)}
            className="flex min-h-11 w-full rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 text-xs font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[2px_2px_0px_#000] dark:focus:shadow-[2px_2px_0px_#fff] focus:outline-none text-foreground"
          />
          <input
            aria-label="Platform account ID"
            placeholder="Platform account ID"
            value={connectAccountId}
            onChange={(event) => setConnectAccountId(event.target.value)}
            className="flex min-h-11 w-full rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 text-xs font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[2px_2px_0px_#000] dark:focus:shadow-[2px_2px_0px_#fff] focus:outline-none text-foreground"
          />
        </div>
        <input
          type="password"
          autoComplete="off"
          aria-label="Platform access token"
          placeholder="Platform access token (stored server-side)"
          value={connectToken}
          onChange={(event) => setConnectToken(event.target.value)}
          className="flex min-h-11 w-full rounded-md border-2 border-foreground bg-surface-elevated px-4 py-2 text-xs font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[2px_2px_0px_#000] dark:focus:shadow-[2px_2px_0px_#fff] focus:outline-none text-foreground"
        />
        <button
          type="button"
          className="min-h-11 w-full inline-flex items-center justify-center gap-2 rounded-md bg-surface text-foreground border-2 border-foreground hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none focus:outline-none font-black uppercase text-xs"
          disabled={
            loading ||
            connectToken.length < 8 ||
            !connectAccountId.trim() ||
            !connectAccountName.trim()
          }
          onClick={connectPublishingAccount}
        >
          Connect account
        </button>
        {connections.length > 0 ? (
          <div className="space-y-2 border-t-2 border-foreground/20 pt-3">
            <textarea
              aria-label="Post caption"
              placeholder="Post caption"
              value={publishCaption}
              onChange={(event) => setPublishCaption(event.target.value)}
              maxLength={2200}
              className="flex min-h-20 w-full resize-y rounded-md border-2 border-foreground bg-surface-elevated p-3 text-xs font-bold transition-all hover:border-primary focus:bg-surface focus:shadow-[3px_3px_0px_#000] dark:focus:shadow-[3px_3px_0px_#fff] focus:outline-none text-foreground"
            />
            {connections.map((connection) => {
              const job = publishJobs.find(
                (item) => item.platform === connection.platform,
              );
              return (
                <div
                  key={connection.id}
                  className="flex items-center justify-between gap-2 border-2 border-foreground bg-surface p-3 rounded-md shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff]"
                >
                  <div className="min-w-0">
                    <p className="truncate text-xs font-black text-foreground">
                      {socialLabel(connection.platform)} ·{" "}
                      {connection.account_name}
                    </p>
                    <div className="flex items-center gap-2 mt-1">
                      <span className="rounded px-2 py-0.5 font-bold font-mono text-[9px] uppercase border-2 border-foreground bg-primary text-primary-foreground shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
                        {job ? publishStatusLabel(job.status) : "Connected"}
                      </span>
                      {job?.error_message ? (
                        <span className="text-[10px] font-mono text-danger truncate max-w-[150px]">
                          {job.error_message}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    {job?.external_url ? (
                      <a
                        href={job.external_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex min-h-10 items-center justify-center border-2 border-foreground px-3 text-xs font-bold rounded-md bg-surface text-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms]"
                      >
                        View
                      </a>
                    ) : null}
                    <button
                      type="button"
                      className="inline-flex min-h-10 items-center justify-center gap-1 px-3 text-xs font-bold border-2 border-foreground rounded-md bg-surface text-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-60 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none focus:outline-none"
                      disabled={
                        loading ||
                        !latestExport ||
                        job?.status === "queued" ||
                        job?.status === "publishing"
                      }
                      onClick={() => publishExport(connection)}
                    >
                      <Send className="size-3.5" strokeWidth={2} />
                      Publish
                    </button>
                    <button
                      type="button"
                      aria-label={`Disconnect ${connection.account_name}`}
                      className="inline-flex min-h-10 items-center justify-center px-3 border-2 border-foreground rounded-md bg-danger text-danger-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] disabled:opacity-60 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none focus:outline-none"
                      disabled={loading}
                      onClick={() => disconnectPublishingAccount(connection.id)}
                    >
                      <Trash2 className="size-3.5" strokeWidth={2} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}
      </section>
      {!captionTrackId ? (
        <p
          id="export-missing-captions"
          role="alert"
          className="rounded-md border-2 border-foreground bg-amber-500/10 p-3.5 text-xs font-bold text-amber-700 dark:text-amber-300 flex items-start gap-2.5 mt-4 shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff]"
        >
          <AlertTriangle className="size-4 shrink-0 mt-0.5 text-amber-700 dark:text-amber-300" strokeWidth={2} />
          <span>
            Generate captions before export. Open the Text tab and generate or
            save captions, then start render again.
          </span>
        </p>
      ) : null}
      {message ? (
        <p
          id="export-message"
          role={
            message.toLowerCase().includes("failed") ||
            message.toLowerCase().includes("could not")
              ? "alert"
              : "status"
          }
          aria-live="polite"
          className="rounded-md border-2 border-foreground bg-muted p-3.5 text-xs font-bold text-muted-foreground mt-4 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] font-mono"
        >
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
    <section className="border-2 border-foreground bg-surface rounded-md overflow-hidden shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
      <div className="flex min-h-[44px] items-center gap-3 border-b-2 border-foreground px-3.5 bg-secondary/10">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold border-2 border-foreground select-none font-mono">
          {step}
        </span>
        <h3 className="flex-1 text-sm font-black uppercase tracking-tight text-foreground">{title}</h3>
        {status === "completed" && (
          <span className="rounded px-2 py-0.5 font-bold font-mono text-[10px] uppercase border-2 border-foreground bg-green-500 text-white shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
            Completed
          </span>
        )}
        {status === "active" && (
          <span className="rounded px-2 py-0.5 font-bold font-mono text-[10px] uppercase border-2 border-foreground bg-primary text-primary-foreground shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
            Active
          </span>
        )}
        {status === "idle" && (
          <span className="rounded px-2 py-0.5 font-bold font-mono text-[10px] uppercase border-2 border-foreground bg-muted text-muted-foreground shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]">
            Idle
          </span>
        )}
      </div>
      <div className="p-3 bg-surface">{children}</div>
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
    return (
      <Loader2
        className="size-5 shrink-0 animate-spin text-primary"
        aria-hidden="true"
      />
    );
  }
  if (state === "completed") {
    return (
      <CheckCircle2
        className="size-5 shrink-0 text-primary"
        aria-hidden="true"
      />
    );
  }
  if (state === "failed") {
    return (
      <AlertTriangle
        className="size-5 shrink-0 text-red-600"
        aria-hidden="true"
      />
    );
  }
  if (state === "canceled") {
    return (
      <Square
        className="size-5 shrink-0 text-muted-foreground"
        aria-hidden="true"
      />
    );
  }
  return (
    <Video
      className="size-5 shrink-0 text-muted-foreground"
      aria-hidden="true"
    />
  );
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function socialLabel(platform: PublishPlatform) {
  return { tiktok: "TikTok", instagram: "Instagram", youtube: "YouTube" }[
    platform
  ];
}

function publishStatusLabel(status: PublishJob["status"]) {
  return {
    queued: "Queued",
    publishing: "Publishing",
    published: "Published",
    failed: "Failed",
  }[status];
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
  if (platform === "youtube_landscape" || platform === "x_landscape")
    return "16:9";
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
  if (
    !job ||
    job.status === "complete" ||
    job.status === "failed" ||
    job.status === "canceled"
  ) {
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
