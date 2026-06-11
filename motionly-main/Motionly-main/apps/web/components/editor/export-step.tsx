"use client";

import React, { useState } from "react";
import { Label, Select } from "./ui";
import { Button } from "../button";
import { Video, Loader2, Download, CheckCircle2 } from "lucide-react";
import type { PlatformPreset, ExportResolution, DurationPreset, ExportSummary, RenderJobSummary } from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";

interface ExportStepProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialJobs: RenderJobSummary[];
  initialExports: ExportSummary[];
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
}: ExportStepProps) {
  const [platform, setPlatform] = useState<PlatformPreset>("instagram");
  const [resolution, setResolution] = useState<ExportResolution>("1080p");
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState(initialJobs);
  const [exports, setExports] = useState(initialExports);

  const latestJob = jobs[0];
  const latestExport = exports[0];
  const isRendering = latestJob && ["queued", "preparing", "rendering", "uploading"].includes(latestJob.status);

  async function startRender() {
    setLoading(true);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/renders`, {
        method: "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          platform,
          resolution,
          duration_preset: "medium",
        }),
      });
      if (!response.ok) throw new Error("Failed to start render");
      alert("Render started! Check status in a moment.");
      // In a real app, we'd start polling here
    } catch (error) {
      console.error(error);
      alert("Failed to start render.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8 animate-in fade-in slide-in-from-right-4 duration-500">
      <div className="space-y-2">
        <h3 className="text-xl font-bold tracking-tight">Generate Final Video</h3>
        <p className="text-sm text-muted-foreground leading-relaxed">
          Ready to post? We'll render your high-quality vertical MP4.
        </p>
      </div>

      <div className="space-y-6">
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
        disabled={loading || isRendering}
        className="w-full h-14 rounded-2xl bg-primary text-primary-foreground shadow-xl hover:scale-[1.02] active:scale-[0.98] transition-all"
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
        <div className="pt-8 border-t border-border space-y-4">
           <Label>Latest Export</Label>
           <div className="p-4 rounded-2xl bg-muted/50 border border-border flex items-center justify-between">
              <div className="flex items-center gap-3">
                 <div className="size-10 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center border border-emerald-100">
                    <CheckCircle2 className="size-5" />
                 </div>
                 <div>
                    <p className="text-xs font-bold uppercase tracking-widest text-muted-foreground">v{latestExport.version}</p>
                    <p className="text-sm font-bold truncate max-w-[120px]">Ready to post</p>
                 </div>
              </div>
              <Button variant="secondary" className="h-10 px-4 rounded-xl text-xs font-bold gap-2">
                 <Download className="size-3.5" />
                 Download
              </Button>
           </div>
        </div>
      )}
    </div>
  );
}
