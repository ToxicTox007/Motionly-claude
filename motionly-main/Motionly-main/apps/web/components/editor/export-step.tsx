"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Select } from "./ui";
import { Button } from "../button";
import { Video, Loader2, Download, CheckCircle2 } from "lucide-react";
import type { PlatformPreset, ExportResolution, ExportSummary, RenderJobSummary } from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";

interface ExportStepProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialJobs: RenderJobSummary[];
  initialExports: ExportSummary[];
  captionTrackId?: string | null;
  activePlanId?: string | null;
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

export function ExportStep({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialJobs,
  initialExports,
  captionTrackId,
  activePlanId,
}: ExportStepProps) {
  const router = useRouter();
  const [platform, setPlatform] = useState<PlatformPreset>("instagram");
  const [resolution, setResolution] = useState<ExportResolution>("1080p");
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState(initialJobs);
  const [exports, setExports] = useState(initialExports);
  const [message, setMessage] = useState<string | null>(null);

  const latestJob = jobs[0];
  const latestExport = exports[0];
  const isRendering = latestJob && ["queued", "preparing", "rendering", "uploading"].includes(latestJob.status);

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
          duration_preset: "medium",
          include_captions: true,
          include_broll: true,
          export_options: {
            platform,
            resolution,
            format: "mp4",
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
      await pollRender(payload.job.id);
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function pollRender(renderJobId: string) {
    for (let attempt = 0; attempt < 36; attempt += 1) {
      await sleep(2500);
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/renders/${renderJobId}`, {
        headers: await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken, json: false }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Render status could not be refreshed.");
      setJobs((current) => [
        payload,
        ...current.filter((job) => job.id !== payload.id),
      ]);
      setMessage(`Rendering: ${payload.status} (${payload.progress_pct ?? 0}%)`);
      if (payload.status === "complete") {
        await refreshExports();
        setMessage("Export is ready to download.");
        return;
      }
      if (payload.status === "failed" || payload.status === "canceled") {
        throw new Error(payload.error_message ?? `Render ${payload.status}.`);
      }
    }
    setMessage("Render is still running. Refresh this project in a moment.");
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
          Ready to post? We&apos;ll render your high-quality vertical MP4.
        </p>
      </div>

      <div className="pixel-panel pixel-corners space-y-6 p-4">
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
