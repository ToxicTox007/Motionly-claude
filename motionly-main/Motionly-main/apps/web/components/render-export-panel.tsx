"use client";

import type {
  DurationPreset,
  ExportResolution,
  ExportSummary,
  PlatformPreset,
  RegenerationAction,
  RenderJobSummary,
} from "@motionly/shared";
import { Download, Loader2, RefreshCw, RotateCcw, Video } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/button";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";

type RenderExportPanelProps = {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  latestCaptionTrackId?: string | null;
  activePlanId?: string | null;
  initialJobs: RenderJobSummary[];
  initialExports: ExportSummary[];
  hasReadySource: boolean;
};

type ExportControlState = {
  platform: PlatformPreset;
  resolution: ExportResolution;
  durationPreset: DurationPreset;
  includeCaptions: boolean;
  includeBroll: boolean;
};

type RenderAdjustmentAction = Extract<
  RegenerationAction,
  "make_punchier" | "more_cinematic"
>;

const actions: Array<{ key: RenderAdjustmentAction; label: string }> = [
  { key: "make_punchier", label: "Make punchier" },
  { key: "more_cinematic", label: "More cinematic" },
];

const platforms: Array<{ key: PlatformPreset; label: string }> = [
  { key: "instagram", label: "Instagram" },
  { key: "tiktok", label: "TikTok" },
  { key: "shorts", label: "Shorts" },
];

const resolutions: Array<{ key: ExportResolution; label: string }> = [
  { key: "720p", label: "720p" },
  { key: "1080p", label: "1080p" },
  { key: "4k", label: "4K" },
];

const durationPresets: Array<{ key: DurationPreset; label: string }> = [
  { key: "short", label: "Short" },
  { key: "medium", label: "Medium" },
  { key: "long", label: "Long" },
];

export function RenderExportPanel({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  latestCaptionTrackId,
  activePlanId,
  initialJobs,
  initialExports,
  hasReadySource,
}: RenderExportPanelProps) {
  const initialControls = exportControlStateFrom(
    initialJobs[0] ?? null,
    initialExports[0] ?? null,
  );
  const [jobs, setJobs] = useState(initialJobs);
  const [exports, setExports] = useState(initialExports);
  const [phase, setPhase] = useState<"idle" | "loading" | "polling" | "ready" | "error">(
    "idle",
  );
  const [message, setMessage] = useState<string | null>(null);
  const [platform, setPlatform] = useState<PlatformPreset>(initialControls.platform);
  const [resolution, setResolution] = useState<ExportResolution>(initialControls.resolution);
  const [durationPreset, setDurationPreset] =
    useState<DurationPreset>(initialControls.durationPreset);
  const [captionTrackId, setCaptionTrackId] = useState(
    latestCaptionTrackId ?? null,
  );
  const [activeEditPlanId, setActiveEditPlanId] = useState(activePlanId ?? null);
  const [includeCaptions, setIncludeCaptions] = useState(initialControls.includeCaptions);
  const [includeBroll, setIncludeBroll] = useState(initialControls.includeBroll);
  const latestJob = jobs[0] ?? null;
  const latestExport = exports[0] ?? null;
  const hasRunningJob = latestJob
    ? ["queued", "preparing", "rendering", "uploading"].includes(latestJob.status)
    : false;
  const canRetryLatestJob = latestJob
    ? latestJob.status === "failed" || latestJob.status === "canceled"
    : false;
  const progress = latestJob?.progress_pct ?? 0;
  const specs = latestJob?.specs ?? latestExport;
  const durationMs = latestExport?.duration_ms ?? sumKeepSegments(latestJob);
  const renderBlockedMessage = !hasReadySource
    ? "Upload preprocessing must finish before export."
    : !captionTrackId
      ? "Generate captions before export."
      : null;
  const renderButtonTitle = renderBlockedMessage ?? undefined;

  useEffect(() => {
    setCaptionTrackId(latestCaptionTrackId ?? null);
  }, [latestCaptionTrackId]);

  useEffect(() => {
    setActiveEditPlanId(activePlanId ?? null);
  }, [activePlanId]);

  useEffect(() => {
    function handlePlanReady(event: Event) {
      const detail = (event as CustomEvent<{ planId?: string; version?: number }>).detail ?? {};
      if (!detail.planId) return;
      setActiveEditPlanId(detail.planId);
      setMessage(`Plan v${detail.version ?? "current"} active for export.`);
      setPhase("idle");
    }

    window.addEventListener("motionly:edit-plan-ready", handlePlanReady);
    return () => window.removeEventListener("motionly:edit-plan-ready", handlePlanReady);
  }, []);

  useEffect(() => {
    function handleCaptionTrackReady(event: Event) {
      const detail = (
        event as CustomEvent<{
          trackId?: string | null;
          includeCaptions?: boolean;
        }>
      ).detail ?? {};
      if ("trackId" in detail) setCaptionTrackId(detail.trackId ?? null);
      if (detail.trackId && typeof detail.includeCaptions === "boolean") {
        setIncludeCaptions(detail.includeCaptions);
      }
    }

    window.addEventListener(
      "motionly:caption-track-ready",
      handleCaptionTrackReady,
    );
    return () => {
      window.removeEventListener(
        "motionly:caption-track-ready",
        handleCaptionTrackReady,
      );
    };
  }, []);

  useEffect(() => {
    if (!latestJob || !hasRunningJob || phase === "loading") return;
    let canceled = false;
    async function pollExistingRender() {
      setPhase("polling");
      setMessage("Refreshing render progress...");
      try {
        const completedJob = await waitForRenderReady(latestJob.id);
        if (canceled) return;
        if (completedJob) {
          await refreshExports();
          setPhase("ready");
          setMessage("Export is ready to download.");
        }
      } catch (error) {
        if (canceled) return;
        setPhase("error");
        setMessage((error as Error).message);
      }
    }
    void pollExistingRender();
    return () => {
      canceled = true;
    };
  }, [latestJob?.id]);

  async function createRender(
    force = false,
    adjustmentAction?: RenderAdjustmentAction,
  ) {
    setPhase("loading");
    setMessage(
      adjustmentAction
        ? "Rendering a new adjusted export version..."
        : "Generating captioned MP4 export...",
    );
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/renders`,
        {
          method: "POST",
          headers: await requestHeaders(workspaceId, userId, apiAccessToken),
          body: JSON.stringify({
            caption_track_id: captionTrackId,
            include_captions: includeCaptions,
            include_broll: includeBroll,
            edit_plan_id: activeEditPlanId,
            adjustment_action: adjustmentAction,
            duration_preset: durationPreset,
            export_options: {
              platform,
              resolution,
              format: "mp4",
            },
            force,
          }),
        },
      );
      const payload = await readJson(response);
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Render could not be started.",
        );
      setJobs((current) => [
        payload.job,
        ...current.filter((job) => job.id !== payload.job.id),
      ]);
      if (payload.job.status === "complete") {
        setJobs((current) => [
          payload.job,
          ...current.filter((job) => job.id !== payload.job.id),
        ]);
        applyExportControlState(exportControlStateFrom(payload.job, null), {
          setPlatform,
          setResolution,
          setDurationPreset,
          setIncludeCaptions,
          setIncludeBroll,
        });
        await refreshExports();
        setPhase("ready");
        setMessage(`Export is ready: ${payload.queue_job_id}`);
        return;
      }
      setPhase("polling");
      setMessage(`Render queued: ${payload.queue_job_id}. Progress will update here.`);
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  async function retryRender(renderJobId: string) {
    setPhase("loading");
    setMessage("Retrying failed render...");
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}/retry`,
        {
          method: "POST",
          headers: await requestHeaders(workspaceId, userId, apiAccessToken),
        },
      );
      const payload = await readJson(response);
      if (!response.ok)
        throw new Error(payload.error?.message ?? "Render could not be retried.");
      setJobs((current) => [
        payload.job,
        ...current.filter((job) => job.id !== payload.job.id),
      ]);
      if (payload.job.status === "complete") {
        setJobs((current) => [
          payload.job,
          ...current.filter((job) => job.id !== payload.job.id),
        ]);
        await refreshExports();
        setPhase("ready");
        setMessage(`Retry completed: ${payload.queue_job_id}`);
        return;
      }
      setPhase("polling");
      setMessage(`Retry queued: ${payload.queue_job_id}. Progress will update here.`);
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  async function refreshExports() {
    const response = await fetch(
      `${env.apiBaseUrl}/projects/${projectId}/exports`,
      {
        headers: await requestHeaders(
          workspaceId,
          userId,
          apiAccessToken,
          false,
        ),
      },
    );
    const payload = await readJson(response);
    if (response.ok) setExports(payload.exports ?? []);
  }

  async function waitForRenderReady(renderJobId: string) {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await sleep(2500);
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}`,
        {
          headers: await requestHeaders(
            workspaceId,
            userId,
            apiAccessToken,
            false,
          ),
        },
      );
      const payload = await readJson(response);
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Render status could not be refreshed.",
        );
      setJobs((current) => [
        payload,
        ...current.filter((job) => job.id !== payload.id),
      ]);
      setMessage(
        `Rendering: ${payload.status} (${payload.progress_pct ?? 0}%)`,
      );
      if (payload.status === "complete") return payload;
      if (payload.status === "failed" || payload.status === "canceled") {
        throw new Error(payload.error_message ?? `Render ${payload.status}.`);
      }
    }
    throw new Error("Render is still running. Refresh the page in a moment.");
  }

  async function downloadExport(exportId: string) {
    setPhase("loading");
    setMessage("Creating secure download link...");
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/exports/${exportId}/download-url`,
        {
          headers: await requestHeaders(
            workspaceId,
            userId,
            apiAccessToken,
            false,
          ),
        },
      );
      const payload = await readJson(response);
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Download link could not be created.",
        );
      window.location.href = payload.signed_download_url;
      setPhase("ready");
      setMessage(
        `Download link expires in ${payload.expires_in_seconds} seconds.`,
      );
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  async function restoreExport(exportId: string) {
    setPhase("loading");
    setMessage("Restoring export as a new version...");
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/projects/${projectId}/exports/${exportId}/restore`,
        {
          method: "POST",
          headers: await requestHeaders(workspaceId, userId, apiAccessToken),
        },
      );
      const payload = await readJson(response);
      if (!response.ok)
        throw new Error(
          payload.error?.message ?? "Version could not be restored.",
        );
      setExports((current) => [payload.export, ...current]);
      applyExportControlState(exportControlStateFrom(null, payload.export), {
        setPlatform,
        setResolution,
        setDurationPreset,
        setIncludeCaptions,
        setIncludeBroll,
      });
      setPhase("ready");
      setMessage(
        `Restored v${payload.export.version}. Older exports were kept.`,
      );
    } catch (error) {
      setPhase("error");
      setMessage((error as Error).message);
    }
  }

  const events = useMemo(
    () => latestJob?.events.slice().reverse() ?? [],
    [latestJob],
  );

  return (
    <section className="rounded-lg border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Video className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-2xl font-bold">Export</h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {latestExport
              ? `Version ${latestExport.version} is ready`
              : "Create a captioned 9:16 MP4"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canRetryLatestJob ? (
            <Button
              type="button"
              variant="secondary"
              onClick={() => retryRender(latestJob.id)}
              disabled={phase === "loading" || phase === "polling"}
            >
              <RotateCcw className="mr-2 size-4" aria-hidden="true" />
              Retry
            </Button>
          ) : null}
          <Button
            type="button"
            onClick={() => createRender(Boolean(latestJob || latestExport))}
            disabled={
              !hasReadySource ||
                !captionTrackId ||
                phase === "loading" ||
                phase === "polling" ||
                hasRunningJob
            }
            title={renderButtonTitle}
          >
            {phase === "loading" || phase === "polling" ? (
              <Loader2
                className="mr-2 size-4 animate-spin"
                aria-hidden="true"
              />
            ) : (
              <RefreshCw className="mr-2 size-4" aria-hidden="true" />
            )}
            {latestExport ? "Generate new export version" : "Generate export"}
          </Button>
        </div>
      </div>
      {renderBlockedMessage ? (
        <p className="mt-4 rounded-lg border border-border bg-muted p-3 text-sm text-muted-foreground">
          {renderBlockedMessage}
        </p>
      ) : null}

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div>
          <div className="mb-4 grid gap-3 md:grid-cols-3">
            <SegmentedRow
              label="Platform"
              value={platform}
              options={platforms}
              onChange={setPlatform}
            />
            <SegmentedRow
              label="Quality"
              value={resolution}
              options={resolutions}
              onChange={setResolution}
            />
            <SegmentedRow
              label="Length"
              value={durationPreset}
              options={durationPresets}
              onChange={setDurationPreset}
            />
          </div>
          <div className="mb-4 flex flex-wrap gap-2">
            <label className="inline-flex h-10 items-center gap-2 rounded-lg border border-border bg-white px-3 text-sm font-semibold">
              <input
                type="checkbox"
                checked={includeCaptions}
                onChange={(event) => setIncludeCaptions(event.target.checked)}
                disabled={!captionTrackId || phase === "loading"}
                className="size-4 accent-teal-600"
              />
              Include captions
            </label>
            <label className="inline-flex h-10 items-center gap-2 rounded-lg border border-border bg-white px-3 text-sm font-semibold">
              <input
                type="checkbox"
                checked={includeBroll}
                onChange={(event) => setIncludeBroll(event.target.checked)}
                disabled={phase === "loading"}
                className="size-4 accent-teal-600"
              />
              Include B-roll
            </label>
            {actions.map((action) => (
              <Button
                key={action.key}
                type="button"
                variant="secondary"
                onClick={() => createRender(true, action.key)}
                disabled={
                  !hasReadySource ||
                  !captionTrackId ||
                  phase === "loading" ||
                  phase === "polling" ||
                  hasRunningJob
                }
                title={renderButtonTitle}
              >
                {action.label}
              </Button>
            ))}
            <Button
              type="button"
              variant="secondary"
              onClick={() => createRender(true)}
              disabled={
                !hasReadySource ||
                !captionTrackId ||
                phase === "loading" ||
                phase === "polling" ||
                hasRunningJob
              }
              title={renderButtonTitle}
            >
              Regenerate export
            </Button>
          </div>
          <div
            className="h-2 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-label="Render progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-3 text-sm text-muted-foreground">
            <span>Render status: {latestJob?.status ?? "idle"}</span>
            <span>{latestJob?.template_id ?? "template follows plan"}</span>
            <span>{activeEditPlanId ? "AI plan connected" : "auto plan fallback"}</span>
            <span>{latestJob?.render_engine ?? "ffmpeg_basic"}</span>
            <span>{includeCaptions ? "captions on" : "captions hidden"}</span>
            <span>{includeBroll ? "B-roll on" : "B-roll off"}</span>
            <span>
              {specs ? `${specs.width} x ${specs.height}` : "1080 x 1920"}
            </span>
            <span>{specs ? `${specs.fps}fps` : "30fps"}</span>
            <span>{formatDuration(durationMs)}</span>
          </div>

          {latestJob?.manifest?.template?.downgraded_effects?.length ? (
            <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              Unsupported effects were downgraded:{" "}
              {latestJob.manifest.template.downgraded_effects.join(", ")}
            </p>
          ) : null}

          {message ? (
            <p
              role={phase === "error" ? "alert" : "status"}
              aria-live="polite"
              className={cn(
                "mt-4 rounded-lg border p-3 text-sm",
                phase === "error"
                  ? "border-red-200 bg-red-50 text-red-800"
                  : "border-border bg-muted text-muted-foreground",
              )}
            >
              {message}
            </p>
          ) : null}

          <div className="mt-4 space-y-2">
            {events.length ? (
              events.map((event) => (
                <div
                  key={event.id}
                  className="flex items-center justify-between gap-3 rounded-lg bg-muted px-3 py-2 text-sm"
                >
                  <span>{event.message}</span>
                  <span className="shrink-0 text-muted-foreground">
                    {event.progress_pct}%
                  </span>
                </div>
              ))
            ) : (
              <p className="rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
                Generate after captions are ready to create the first export.
              </p>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-white p-4">
            <p className="text-sm font-bold uppercase text-muted-foreground">
              Latest MP4
            </p>
            {latestExport ? (
              <>
                <p className="mt-3 text-2xl font-bold">
                  v{latestExport.version}
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {latestExport.format.toUpperCase()} / H.264/AAC
                </p>
                <Button
                  type="button"
                  className="mt-4 w-full"
                  onClick={() => downloadExport(latestExport.id)}
                  disabled={phase === "loading"}
                >
                  <Download className="mr-2 size-4" aria-hidden="true" />
                  Download MP4
                </Button>
              </>
            ) : (
              <p className="mt-3 text-sm text-muted-foreground">
                No export yet.
              </p>
            )}
          </div>
          <div className="rounded-lg border border-border bg-white p-4">
            <p className="text-sm font-bold uppercase text-muted-foreground">
              Versions
            </p>
            <div className="mt-3 space-y-2">
              {exports.length ? (
                exports.map((exportItem) => (
                  <div
                    key={exportItem.id}
                    className="rounded-lg border border-border p-3"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-bold">
                          v{exportItem.version}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {exportItem.width} x {exportItem.height} /{" "}
                          {exportItem.format.toUpperCase()}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => restoreExport(exportItem.id)}
                        disabled={phase === "loading"}
                      >
                        <RotateCcw className="mr-2 size-4" aria-hidden="true" />
                        Restore version
                      </Button>
                    </div>
                  </div>
                ))
              ) : (
                <p className="rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
                  Export versions appear here after rendering.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function SegmentedRow<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: Array<{ key: T; label: string }>;
  onChange: (value: T) => void;
}) {
  return (
    <div className="rounded-lg border border-border bg-white p-3">
      <p className="text-sm font-semibold">{label}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            key={option.key}
            type="button"
            onClick={() => onChange(option.key)}
            className={cn(
              "rounded-lg border px-3 py-2 text-sm font-semibold transition",
              value === option.key
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-white hover:bg-muted",
            )}
            aria-pressed={value === option.key}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function requestHeaders(
  workspaceId: string,
  userId: string,
  apiAccessToken: string,
  json = true,
) {
  return buildApiHeaders({
    workspaceId,
    userId,
    accessToken: apiAccessToken,
    json,
  });
}

async function readJson(response: Response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function sumKeepSegments(job: RenderJobSummary | null) {
  if (!job) return null;
  return job.keep_segments.reduce(
    (total, segment) => total + segment.end_ms - segment.start_ms,
    0,
  );
}

function formatDuration(durationMs: number | null | undefined) {
  if (!durationMs) return "Duration pending";
  const seconds = Math.round(durationMs / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function exportControlStateFrom(
  job: RenderJobSummary | null,
  exportItem: ExportSummary | null,
): ExportControlState {
  const settings = exportSettingsFromJob(job) ?? exportSettingsFromExport(exportItem);
  return {
    platform: parsePlatform(settings?.platform),
    resolution: parseResolution(settings?.requested_resolution ?? settings?.resolution),
    durationPreset: parseDurationPreset(settings?.duration_preset),
    includeCaptions: parseIncludeCaptions(job, exportItem),
    includeBroll: parseIncludeBroll(job, exportItem),
  };
}

function applyExportControlState(
  state: ExportControlState,
  setters: {
    setPlatform: (value: PlatformPreset) => void;
    setResolution: (value: ExportResolution) => void;
    setDurationPreset: (value: DurationPreset) => void;
    setIncludeCaptions: (value: boolean) => void;
    setIncludeBroll: (value: boolean) => void;
  },
) {
  setters.setPlatform(state.platform);
  setters.setResolution(state.resolution);
  setters.setDurationPreset(state.durationPreset);
  setters.setIncludeCaptions(state.includeCaptions);
  setters.setIncludeBroll(state.includeBroll);
}

function exportSettingsFromJob(job: RenderJobSummary | null): Record<string, unknown> | null {
  const settings = job?.manifest?.export_settings;
  return isRecord(settings) ? settings : null;
}

function exportSettingsFromExport(exportItem: ExportSummary | null): Record<string, unknown> | null {
  const summary = exportItem?.settings_summary;
  if (!isRecord(summary)) return null;
  const nested = summary.export_settings;
  return isRecord(nested) ? nested : summary;
}

function parseIncludeCaptions(job: RenderJobSummary | null, exportItem: ExportSummary | null) {
  const manifestValue = job?.manifest?.include_captions;
  if (typeof manifestValue === "boolean") return manifestValue;
  const summary = exportItem?.settings_summary;
  if (isRecord(summary) && typeof summary.include_captions === "boolean") {
    return summary.include_captions;
  }
  return true;
}

function parseIncludeBroll(job: RenderJobSummary | null, exportItem: ExportSummary | null) {
  const manifestValue = job?.manifest?.include_broll;
  if (typeof manifestValue === "boolean") return manifestValue;
  const summary = exportItem?.settings_summary;
  if (isRecord(summary) && typeof summary.include_broll === "boolean") {
    return summary.include_broll;
  }
  return true;
}

function parsePlatform(value: unknown): PlatformPreset {
  return value === "tiktok" || value === "shorts" ? value : "instagram";
}

function parseResolution(value: unknown): ExportResolution {
  return value === "720p" || value === "4k" ? value : "1080p";
}

function parseDurationPreset(value: unknown): DurationPreset {
  return value === "short" || value === "long" ? value : "medium";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
