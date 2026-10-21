"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
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
import { DEFAULT_RENDER_FPS } from "@motionly/shared";
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
import {
  apiFetch,
  editorApiRequest,
  waitForInteractivePollingTurn,
} from "@/lib/client-api";
import { env } from "@/lib/env";
import { shouldToast } from "@/lib/notification-taxonomy";
import {
  queuedWaitBandLabel,
  renderStatusLabel,
} from "@/lib/product-flow-status";
import type { PreviewExportState } from "./preview/VideoPreview";
import { useToast } from "../toast";
import { StaleIndicator } from "./StaleIndicator";
import type { BrandKitResource } from "./BrandKitPanel";
import {
  createRepurposeTarget,
  RepurposePanel,
  type RepurposeTarget,
} from "./repurpose-panel";
import {
  captionSelectionForBulkExport,
  captionSelectionForExport,
} from "./export-caption-selection.mjs";

interface ExportStepProps {
  projectId: string;
  workspaceId: string;
  initialJobs: RenderJobSummary[];
  initialExports: ExportSummary[];
  captionTrackId?: string | null;
  activePlanId?: string | null;
  sourceWidth?: number | null;
  sourceHeight?: number | null;
  maxExportResolution: ExportResolution;
  onPreviewExportUpdated?: (state: PreviewExportState | null) => void;
  /** Current plan revision for stale detection; null if unknown */
  planRevision?: number | null;
  /** Callback to trigger a re-render when the render is stale */
  onRerender?: () => void;
}

const platforms: { value: PlatformPreset; label: string }[] = [
  { value: "instagram", label: "Instagram Reel" },
  { value: "tiktok", label: "TikTok" },
  { value: "shorts", label: "YouTube Shorts" },
  { value: "youtube_landscape", label: "YouTube Landscape" },
  { value: "instagram_post", label: "Instagram Post" },
  { value: "linkedin", label: "LinkedIn" },
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

function isExportResolution(
  value: string | undefined,
): value is ExportResolution {
  return value === "720p" || value === "1080p" || value === "4k";
}

function isPlatformPreset(value: string | undefined): value is PlatformPreset {
  return platforms.some((option) => option.value === value);
}

function isOutputAspect(value: string | undefined): value is OutputAspect {
  return outputAspects.some((option) => option.value === value);
}

function isExportFitMode(value: string | undefined): value is ExportFitMode {
  return fitModes.some((option) => option.value === value);
}

function isRenderTemplateId(
  value: string | null | undefined,
): value is RenderTemplateId {
  return templates.some((option) => option.value === value);
}

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
type LocalizedPublishPackage = {
  id: string;
  caption_track_id: string;
  language: string;
  review_status: "needs_review" | "approved";
  selected_title?: string | null;
};

export function ExportStep({
  projectId,
  workspaceId,
  initialJobs,
  initialExports,
  captionTrackId,
  activePlanId,
  sourceWidth,
  sourceHeight,
  maxExportResolution,
  onPreviewExportUpdated,
  planRevision,
  onRerender,
}: ExportStepProps) {
  const { toast } = useToast();
  const [templateId, setTemplateId] =
    useState<RenderTemplateId>("luxury_reel_v1");
  const [platform, setPlatform] = useState<PlatformPreset>("instagram");
  const [resolution, setResolution] = useState<ExportResolution>(
    maxExportResolution === "720p" ? "720p" : "1080p",
  );
  const [outputAspect, setOutputAspect] = useState<OutputAspect>("9:16");
  const [fitMode, setFitMode] = useState<ExportFitMode>("fill");
  const [repurposeTargets, setRepurposeTargets] = useState<RepurposeTarget[]>(
    () =>
      ["instagram", "tiktok", "shorts", "linkedin"].map((targetPlatform) =>
        createRepurposeTarget(
          targetPlatform as Exclude<PlatformPreset, "custom">,
          Boolean(captionTrackId),
        ),
      ),
  );
  const [cropX, setCropX] = useState(50);
  const [cropY, setCropY] = useState(50);
  const [cropZoom, setCropZoom] = useState(100);
  const [includeCaptions, setIncludeCaptions] = useState(
    Boolean(captionTrackId),
  );
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
  const [brandKits, setBrandKits] = useState<BrandKitResource[]>([]);
  const [selectedBrandKitId, setSelectedBrandKitId] = useState("");
  const [localizedPackages, setLocalizedPackages] = useState<
    LocalizedPublishPackage[]
  >([]);
  const [selectedLocalizedPackageId, setSelectedLocalizedPackageId] =
    useState("");
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
  const waitBandLabel =
    latestJob?.status === "queued"
      ? queuedWaitBandLabel(latestJob.created_at, nowMs)
      : null;
  const retryableJob = latestJob?.status === "failed" ? latestJob : null;
  const canceledJob = latestJob?.status === "canceled" ? latestJob : null;
  const selectedLocalizedPackage = localizedPackages.find(
    (item) => item.id === selectedLocalizedPackageId,
  );
  const captionSourceAvailable = Boolean(
    captionTrackId || selectedLocalizedPackage,
  );
  const primaryRenderAction = getPrimaryRenderAction({
    renderUiState,
    loading,
    isRendering: Boolean(isRendering),
    retryableJobId: retryableJob?.id ?? null,
    captionSourceAvailable,
    includeCaptions,
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

  function applyBrandKitExportDefaults(kit: BrandKitResource) {
    const settings = kit.payload.settings ?? {};
    const preset = settings.default_export ?? {};
    if (isRenderTemplateId(settings.creator_template))
      setTemplateId(settings.creator_template);
    if (isPlatformPreset(preset.platform)) setPlatform(preset.platform);
    if (isOutputAspect(preset.output_aspect))
      setOutputAspect(preset.output_aspect);
    if (isExportFitMode(preset.fit_mode)) setFitMode(preset.fit_mode);
    if (isExportResolution(preset.resolution)) {
      const allowedResolution =
        resolutionRank[preset.resolution] > resolutionRank[maxExportResolution]
          ? maxExportResolution
          : preset.resolution;
      setResolution(allowedResolution);
    }
    if (typeof preset.include_captions === "boolean") {
      setIncludeCaptions(preset.include_captions);
    }
    setSelectedBrandKitId(kit.id);
    window.localStorage.setItem(`motionly:brand-kit:${projectId}`, kit.id);
  }

  useEffect(() => {
    let cancelled = false;
    void editorApiRequest<{ resources: BrandKitResource[] }>(
      `${env.apiBaseUrl}/phase5/brand-kits`,
      {
        workspaceId,
        friendlyError: "Brand Kit defaults could not be loaded.",
      },
    )
      .then((response) => {
        if (cancelled) return;
        const kits = response.resources ?? [];
        setBrandKits(kits);
        const rememberedId = window.localStorage.getItem(
          `motionly:brand-kit:${projectId}`,
        );
        const remembered = kits.find((kit) => kit.id === rememberedId);
        if (remembered) applyBrandKitExportDefaults(remembered);
      })
      .catch((error) => {
        if (!cancelled)
          console.warn("Brand Kit defaults were not loaded.", error);
      });
    return () => {
      cancelled = true;
    };
    // Export defaults are intentionally restored once for this project/session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, workspaceId]);

  useEffect(() => {
    let cancelled = false;
    void editorApiRequest<{ packages: LocalizedPublishPackage[] }>(
      `${env.apiBaseUrl}/projects/${projectId}/localized-publish-packages`,
      {
        workspaceId,
        friendlyError: "Localized publishing packages could not be loaded.",
      },
    )
      .then((response) => {
        if (cancelled) return;
        const approved = (response.packages ?? []).filter(
          (item) => item.review_status === "approved",
        );
        setLocalizedPackages(approved);
        setSelectedLocalizedPackageId((current) =>
          approved.some((item) => item.id === current) ? current : "",
        );
      })
      .catch((error) => {
        if (!cancelled)
          console.warn("Localized publishing packages were not loaded.", error);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, workspaceId]);

  useEffect(() => {
    onPreviewExportUpdated?.({
      fps: DEFAULT_RENDER_FPS,
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
      const payload = await editorApiRequest<{
        job: RenderJobSummary;
        queue_job_id: string;
      }>(`${env.apiBaseUrl}/projects/${projectId}/renders`, {
        method: "POST",
        workspaceId,
        friendlyError: "Render could not be started. Please try again.",
        body: {
          ...captionSelectionForExport({
            includeCaptions,
            captionTrackId,
            localizedPublishPackageId: selectedLocalizedPackage?.id ?? null,
          }),
          edit_plan_id: activePlanId ?? null,
          template_id: templateId,
          duration_preset: "medium",
          include_captions: includeCaptions,
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
      if (shouldToast("short_action_feedback")) {
        toast({
          title: "Render Queued",
          description: "Your video has been added to the render queue.",
          variant: "success",
        });
      }
    } catch (error) {
      console.error(error);
      const errMsg = (error as Error).message;
      setMessage(errMsg);
      if (shouldToast("short_action_feedback")) {
        toast({
          title: "Export Failed to Start",
          description:
            "Check for missing timeline clips, verify your network connection, or try again.",
          variant: "error",
        });
      }
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
        friendlyError: "Bulk export could not be started. Please try again.",
        body: {
          ...captionSelectionForBulkExport({
            includeCaptions,
            captionTrackId,
            localizedPublishPackageId: selectedLocalizedPackage?.id ?? null,
            targetIncludesCaptions: repurposeTargets.map(
              (target) => target.includeCaptions,
            ),
          }),
          edit_plan_id: activePlanId ?? null,
          template_id: templateId,
          duration_preset: "medium",
          include_captions: includeCaptions,
          include_broll: true,
          export_targets: repurposeTargets.map((target) => ({
            platform: target.platform,
            resolution,
            format: "mp4",
            duration_preset: target.durationPreset,
            include_captions: target.includeCaptions,
            hook_text: target.hookText.trim() || null,
            framing: target.framing,
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
      if (shouldToast("short_action_feedback")) {
        toast({
          title: "Bulk Render Queued",
          description: `Successfully enqueued ${payload.jobs.length} export targets.`,
          variant: "success",
        });
      }
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
      if (shouldToast("short_action_feedback")) {
        toast({
          title: "Bulk Render Failed",
          description:
            "Failed to queue multiple exports. Verify that captions are active and try again.",
          variant: "error",
        });
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!activeStreamJobId) return;
    const renderJobId = activeStreamJobId;
    let canceled = false;
    streamAbortRef.current?.abort();
    const abortController = new AbortController();
    streamAbortRef.current = abortController;

    async function subscribe() {
      try {
        const response = await apiFetch(
          `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}/events`,
          { signal: abortController.signal },
          { workspaceId },
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
            if (abortController.signal.aborted || canceled) return;
            updateJob(job);
            setMessage(renderMessage(job));
            if (job.status === "complete") {
              await refreshExports(abortController.signal);
              if (abortController.signal.aborted || canceled) return;
              setStreamingJobId(null);
              if (shouldToast("short_action_feedback")) {
                toast({
                  title: "Render Complete",
                  description: "Your video export is ready.",
                  variant: "success",
                });
              }
              return;
            }
            if (job.status === "failed" || job.status === "canceled") {
              setStreamingJobId(null);
              setLoading(false);
              if (shouldToast("short_action_feedback")) {
                toast({
                  title:
                    job.status === "failed"
                      ? "Render Failed"
                      : "Render Canceled",
                  description:
                    job.status === "failed"
                      ? "Server-side compilation failed. Click Retry or reduce complexity."
                      : "Render was canceled.",
                  variant: job.status === "failed" ? "error" : "info",
                });
              }
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
        try {
          await pollRender(renderJobId, abortController.signal);
        } catch (pollError) {
          if (abortController.signal.aborted || canceled) return;
          console.warn(
            "Render status polling stopped unexpectedly.",
            pollError,
          );
          setMessage("Render status updates are temporarily unavailable.");
        }
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
  }, [activeStreamJobId, projectId, workspaceId]);

  function updateJob(job: RenderJobSummary) {
    setJobs((current) => [
      job,
      ...current.filter((item) => item.id !== job.id),
    ]);
  }

  async function pollRender(renderJobId: string, signal: AbortSignal) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      try {
        if (attempt > 0) {
          await sleep(Math.min(8000, 2500 + attempt * 250), signal);
        }
        await waitForInteractivePollingTurn(signal);
        const response = await apiFetch(
          `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}`,
          { cache: "no-store", signal },
          { workspaceId },
        );
        const payload = (await response.json()) as RenderJobSummary & {
          error?: { message?: string };
        };
        if (!response.ok) {
          console.warn(
            "Render status poll returned an error; retrying.",
            payload,
          );
          continue;
        }
        signal.throwIfAborted();
        updateJob(payload);
        setMessage(renderMessage(payload));
        if (payload.status === "complete") {
          await refreshExports(signal);
          signal.throwIfAborted();
          setMessage("Export is ready to download.");
          setStreamingJobId((current) =>
            current === renderJobId ? null : current,
          );
          return;
        }
        if (payload.status === "failed" || payload.status === "canceled") {
          setStreamingJobId((current) =>
            current === renderJobId ? null : current,
          );
          setMessage(
            payload.status === "failed"
              ? (payload.error_message ?? "Render failed.")
              : "Render canceled.",
          );
          return;
        }
      } catch (error) {
        if (signal.aborted || (error as Error).name === "AbortError")
          throw error;
        console.warn("Render status poll failed; retrying.", error);
      }
    }
    signal.throwIfAborted();
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

  async function refreshExports(signal?: AbortSignal) {
    const response = await apiFetch(
      `${env.apiBaseUrl}/projects/${projectId}/exports`,
      { signal },
      { workspaceId },
    );
    const payload = await response.json();
    signal?.throwIfAborted();
    if (response.ok) setExports(payload.exports ?? []);
  }

  async function downloadExport(exportId: string) {
    setLoading(true);
    setMessage("Creating secure download link...");
    try {
      const response = await apiFetch(
        `${env.apiBaseUrl}/exports/${exportId}/download-url`,
        {},
        { workspaceId },
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

  return (
    <div
      id="editor-export"
      tabIndex={-1}
      className="space-y-4 rounded-neu-lg focus-visible:outline-none"
      aria-labelledby="export-heading"
    >
      <div className="space-y-2 border-b border-hairline pb-4">
        <h2
          id="export-heading"
          className="text-2xl font-bold tracking-tight text-foreground"
        >
          Repurpose
        </h2>
        <p className="text-xs font-medium text-muted-foreground leading-relaxed">
          Turn one source into tailored, platform-ready outputs without
          rebuilding your edit.
        </p>
      </div>

      {brandKits.length ? (
        <div className="rounded-neu-lg bg-surface p-3 shadow-neu-raised">
          <Label className="mb-1 block">Brand Kit export defaults</Label>
          <Select
            id="brand-kit-export"
            label="Brand Kit export defaults"
            value={selectedBrandKitId}
            onChange={(value) => {
              const kit = brandKits.find((item) => item.id === value);
              if (kit) applyBrandKitExportDefaults(kit);
              else {
                setSelectedBrandKitId("");
                window.localStorage.removeItem(
                  `motionly:brand-kit:${projectId}`,
                );
              }
            }}
            options={[
              { value: "", label: "No Brand Kit defaults" },
              ...brandKits.map((kit) => ({ value: kit.id, label: kit.name })),
            ]}
          />
          <p className="mt-1 text-[10px] font-medium text-muted-foreground">
            Applies the selected kit’s creator template, platform, frame,
            captions, and permitted quality.
          </p>
        </div>
      ) : null}

      <div className="space-y-4 rounded-neu-lg bg-surface p-4 shadow-neu-raised sm:p-5">
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
          <RepurposePanel
            targets={repurposeTargets}
            onTargetsChange={setRepurposeTargets}
            captionTrackId={captionTrackId}
            onPreview={(target) => {
              setPlatform(target.platform);
              setOutputAspect(target.framing.output_aspect);
              setFitMode(target.framing.fit_mode);
              setCropX(target.framing.crop.x * 100);
              setCropY(target.framing.crop.y * 100);
              setCropZoom(target.framing.crop.zoom * 100);
              setIncludeCaptions(target.includeCaptions);
            }}
          />
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
                      className={`flex items-center justify-center min-h-[40px] px-3 py-1.5 rounded-neu text-xs font-semibold transition-shadow duration-200 ${
                        active
                          ? "bg-primary text-primary-foreground shadow-neu-glow"
                          : "bg-surface text-foreground shadow-neu-raised-sm hover:shadow-neu-raised"
                      }`}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
              <p className="text-[10px] font-medium text-muted-foreground mt-1">
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
                      className={`flex items-center justify-center min-h-[40px] px-3 py-1.5 rounded-neu text-xs font-semibold transition-shadow duration-200 ${
                        active
                          ? "bg-primary text-primary-foreground shadow-neu-glow"
                          : "bg-surface text-foreground shadow-neu-raised-sm hover:shadow-neu-raised"
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
            <div className="mt-3 space-y-3 border-t border-hairline pt-3">
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
                className="w-full inline-flex items-center justify-center gap-2 h-11 rounded-neu bg-surface text-foreground font-semibold text-xs shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed"
                onClick={() => {
                  setCropX(50);
                  setCropY(50);
                  setCropZoom(100);
                }}
              >
                <RotateCcw className="size-3.5" aria-hidden="true" />
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
                    className={`flex items-center justify-center px-4 py-1.5 rounded-full text-xs font-semibold tabular-nums transition-shadow duration-200 ${
                      active
                        ? "bg-primary text-primary-foreground shadow-neu-glow"
                        : "bg-surface text-foreground shadow-neu-raised-sm hover:shadow-neu-raised"
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

      <div className="rounded-neu-lg bg-surface px-4 py-3 shadow-neu-inset">
        <Label className="mb-1">Caption language</Label>
        <Select
          id="localized-publish-package"
          label="Caption language"
          value={selectedLocalizedPackageId}
          onChange={(value) => {
            setSelectedLocalizedPackageId(value);
            if (value) setIncludeCaptions(true);
          }}
          options={[
            { value: "", label: "Original caption track" },
            ...localizedPackages.map((item) => ({
              value: item.id,
              label: `${item.language.toUpperCase()} — ${
                item.selected_title || "Approved localized package"
              }`,
            })),
          ]}
        />
        <p className="mt-1 text-[10px] font-medium text-muted-foreground">
          Approved packages use their reviewed captions and localized text
          overlays.
        </p>
      </div>

      <label className="flex min-h-12 items-center justify-between gap-4 rounded-neu-lg bg-surface px-4 py-3 text-sm font-semibold text-foreground shadow-neu-raised-sm">
        <span>
          <span className="block">Burn captions into export</span>
          <span className="block text-xs font-medium text-muted-foreground">
            Turn this off to export without a caption track.
          </span>
        </span>
        <input
          type="checkbox"
          aria-label="Burn captions into export"
          checked={includeCaptions}
          onChange={(event) => setIncludeCaptions(event.target.checked)}
          className="size-5 accent-primary"
        />
      </label>

      <button
        onClick={primaryRenderAction.onClick}
        disabled={primaryRenderAction.disabled}
        aria-busy={loading || Boolean(isRendering)}
        aria-describedby={
          message
            ? "export-message"
            : includeCaptions && !captionSourceAvailable
              ? "export-missing-captions"
              : undefined
        }
        className="w-full inline-flex items-center justify-center gap-2 h-14 rounded-neu bg-primary text-primary-foreground shadow-neu-glow transition-shadow duration-200 active:shadow-neu-pressed disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-neu-raised-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background font-semibold text-sm"
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
          repurposeTargets.length === 0 ||
          (repurposeTargets.some((target) => target.includeCaptions) &&
            !captionSourceAvailable)
        }
        aria-busy={loading || Boolean(isRendering)}
        className="w-full inline-flex items-center justify-center gap-2 h-12 mt-2 rounded-neu bg-surface text-foreground shadow-neu-raised transition-shadow duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-neu-raised-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background font-semibold text-xs"
      >
        {loading || isRendering ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Download className="size-4" />
        )}
        {repurposeTargets.length
          ? `Create ${repurposeTargets.length} Tailored Output${repurposeTargets.length === 1 ? "" : "s"}`
          : "Select Platforms to Repurpose"}
      </button>

      {latestJob &&
        latestJob.status === "complete" &&
        ((latestJob as any).stale === true ||
          (planRevision != null &&
            (latestJob as any).source_plan_revision != null &&
            (latestJob as any).source_plan_revision < planRevision)) && (
          <StaleIndicator
            message="Render is outdated — timeline has been modified since last render"
            actionLabel="Re-render"
            onAction={onRerender ?? primaryRenderAction.onClick}
          />
        )}

      {latestJob ? (
        <div
          className="space-y-3 rounded-neu-lg bg-surface p-4 shadow-neu-raised"
          aria-live="polite"
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <RenderStatusIcon
                state={renderUiState}
                loading={loading || Boolean(isRendering)}
              />
              <div className="min-w-0">
                <p className="text-xs font-semibold tracking-wide text-muted-foreground">
                  Render job
                </p>
                <p className="truncate text-sm font-bold text-foreground">
                  {uiStateLabel(renderUiState)}
                </p>
                {latestJob.specs ? (
                  <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-0.5 text-xs font-medium text-muted-foreground tabular-nums">
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
              <span className="text-right text-sm font-bold text-primary tabular-nums">
                {progress}%
                {etaLabel ? (
                  <span className="block text-[10px] font-semibold text-muted-foreground tabular-nums">
                    ETA {etaLabel}
                  </span>
                ) : null}
                {waitBandLabel ? (
                  <span className="block text-[10px] font-semibold text-muted-foreground">
                    {waitBandLabel}
                  </span>
                ) : null}
              </span>
            ) : null}
          </div>
          {renderUiState === "queued" ||
          renderUiState === "rendering" ||
          renderUiState === "completed" ? (
            <div
              className="mt-2 h-3 bg-muted rounded-full overflow-hidden shadow-neu-inset-sm"
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
              className="flex flex-col gap-2 rounded-neu-lg bg-surface p-4 text-xs font-semibold text-danger shadow-neu-raised ring-1 ring-danger/30"
            >
              <div className="flex gap-2 items-center">
                <AlertTriangle className="size-4 shrink-0" strokeWidth={2} />
                <span className="font-bold">
                  Render Failed: {latestJob.error_message}
                </span>
              </div>
              <div className="pl-6 font-medium text-muted-foreground leading-relaxed">
                What you can do next:
                <ul className="list-disc pl-4 mt-1 space-y-0.5">
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
                className="min-h-10 flex-1 inline-flex items-center justify-center gap-2 text-xs font-semibold rounded-neu bg-surface text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed disabled:opacity-50"
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
                className="min-h-10 flex-1 inline-flex items-center justify-center gap-2 text-xs font-semibold rounded-neu bg-surface text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed disabled:opacity-50"
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
                className="min-h-10 flex-1 inline-flex items-center justify-center gap-2 text-xs font-semibold rounded-neu bg-surface text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed disabled:opacity-50"
                onClick={startRender}
                disabled={loading || (includeCaptions && !captionTrackId)}
              >
                <Video className="size-3.5" strokeWidth={2} />
                Start again
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {latestExport && (
        <div className="space-y-4 border-t border-hairline pt-6">
          <Label>Latest Export</Label>
          <div className="flex items-center justify-between rounded-neu-lg bg-surface p-4 shadow-neu-raised">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow">
                <CheckCircle2 className="size-5" strokeWidth={2} />
              </div>
              <div>
                <p className="text-xs font-semibold tracking-wide text-muted-foreground tabular-nums">
                  v{latestExport.version}
                </p>
                <p className="text-sm font-bold text-foreground">
                  Ready to download
                </p>
                <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-1 text-xs font-medium text-muted-foreground tabular-nums">
                  <span>
                    {latestExport.width}x{latestExport.height}
                  </span>
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
                      <span>
                        {(latestExport.file_size_bytes / (1024 * 1024)).toFixed(
                          1,
                        )}
                        MB
                      </span>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
            <button
              type="button"
              aria-label="Download latest export"
              className="inline-flex min-h-11 items-center justify-center gap-2 px-4 text-xs font-semibold bg-surface text-foreground rounded-neu shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed disabled:opacity-60 disabled:cursor-not-allowed"
              onClick={() => downloadExport(latestExport.id)}
              disabled={loading}
            >
              <Download className="size-3.5" strokeWidth={2} />
              Download
            </button>
          </div>
        </div>
      )}
      {includeCaptions && !captionSourceAvailable ? (
        <p
          id="export-missing-captions"
          role="alert"
          className="rounded-neu-lg bg-surface p-3.5 text-xs font-semibold text-warning flex items-start gap-2.5 mt-4 shadow-neu-raised ring-1 ring-warning/30"
        >
          <AlertTriangle
            className="size-4 shrink-0 mt-0.5 text-warning"
            strokeWidth={2}
          />
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
          className="rounded-neu bg-surface p-3.5 text-xs font-semibold text-muted-foreground mt-4 shadow-neu-inset-sm"
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
    <section className="rounded-neu-lg bg-surface overflow-hidden shadow-neu-raised-sm">
      <div className="flex min-h-[44px] items-center gap-3 border-b border-hairline px-3.5 bg-surface-elevated">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-semibold select-none tabular-nums shadow-neu-glow">
          {step}
        </span>
        <h3 className="flex-1 text-sm font-bold tracking-tight text-foreground">
          {title}
        </h3>
        {status === "completed" && (
          <span className="rounded-full px-2 py-0.5 font-semibold text-[10px] bg-success text-ink shadow-neu-raised-sm">
            Completed
          </span>
        )}
        {status === "active" && (
          <span className="rounded-full px-2 py-0.5 font-semibold text-[10px] bg-primary text-primary-foreground shadow-neu-glow">
            Active
          </span>
        )}
        {status === "idle" && (
          <span className="rounded-full px-2 py-0.5 font-semibold text-[10px] bg-muted text-muted-foreground shadow-neu-inset-sm">
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
        className="size-5 shrink-0 text-danger"
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

function sleep(ms: number, signal?: AbortSignal) {
  signal?.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function statusLabel(status: RenderJobSummary["status"]) {
  return renderStatusLabel(status);
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
  if (platform === "instagram_post" || platform === "linkedin") return "4:5";
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
  captionSourceAvailable,
  includeCaptions,
  startRender,
  retryRender,
}: {
  renderUiState: RenderUiState;
  loading: boolean;
  isRendering: boolean;
  retryableJobId: string | null;
  captionSourceAvailable: boolean;
  includeCaptions: boolean;
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
      disabled: includeCaptions && !captionSourceAvailable,
      icon: "video" as const,
      onClick: startRender,
    };
  }
  return {
    label: "Start Render",
    disabled: includeCaptions && !captionSourceAvailable,
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
