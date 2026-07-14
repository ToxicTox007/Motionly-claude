import type {
  BrollSlotSummary,
  BillingStatusSummary,
  CaptionTrackSummary,
  EditPlanRecordSummary,
  EffectsConfigSummary,
  ExportSummary,
  ExportResolution,
  MusicSelectionSummary,
  RenderJobSummary,
  TranscriptSummary,
  UploadedVideoSummary,
  VisualAnalysisStatusSummary,
} from "@motionly/shared";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { EditorClient } from "@/components/editor-client";
import {
  ProcessingProjectScreen,
  UploadProjectScreen,
} from "@/components/project-early-phase";
import { env, resolveProxiedUrl } from "@/lib/env";
import { createClient } from "@/lib/supabase/server";

type ApiContext = {
  headers: Record<string, string>;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
};

type ProjectResponse = {
  id: string;
  workspace_id: string;
  title: string;
  active_video_id?: string | null;
};

type UploadResponse = {
  id: string;
  project_id: string;
  workspace_id: string;
  original_filename: string;
  status: UploadedVideoSummary["status"];
  metadata?: {
    duration_ms?: number | null;
    width?: number | null;
    height?: number | null;
    fps?: number | null;
    warnings?: string[];
  };
  thumbnail_path?: string | null;
  updated_at: string;
};

export default async function ProjectDetailsPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const context = await getApiContext(projectId);
  const project = await apiGet<ProjectResponse>(`/projects/${projectId}`, context.headers);

  if (!project) {
    return (
      <AppShell title="Project not found">
        <p className="text-sm text-muted-foreground">
          This project does not exist or you do not have access to it.
        </p>
      </AppShell>
    );
  }

  const workspaceId = project.workspace_id || context.workspaceId;
  const headers = {
    ...context.headers,
    "x-motionly-workspace-id": workspaceId,
  };

  const upload = project.active_video_id
    ? toUploadSummary(
        await apiGet<UploadResponse>(
          `/projects/${projectId}/uploads/${project.active_video_id}`,
          headers,
        ),
      )
    : null;

  const phaseAuth = {
    title: project.title,
    projectId,
    workspaceId,
    userId: context.userId,
    apiAccessToken: context.apiAccessToken,
  };

  if (!upload) {
    return <UploadProjectScreen {...phaseAuth} />;
  }

  if (upload.status !== "ready") {
    return (
      <ProcessingProjectScreen
        {...phaseAuth}
        uploadId={upload.id}
        initialStatus={upload.status}
      />
    );
  }

  const [
    uploadAssetUrls,
    transcript,
    captionsPayload,
    plansPayload,
    brollPayload,
    musicSelection,
    effectsConfig,
    rendersPayload,
    exportsPayload,
    billingStatus,
    visualStatus,
  ] = await Promise.all([
    upload
      ? apiGet<{ source_video_url?: string; thumbnail_url?: string | null }>(
          `/projects/${projectId}/uploads/${upload.id}/asset-urls`,
          headers,
        )
      : Promise.resolve(null),
    apiGet<TranscriptSummary>(`/projects/${projectId}/transcript`, headers),
    apiGet<{ tracks?: CaptionTrackSummary[] }>(`/projects/${projectId}/captions`, headers),
    apiGet<{ plans?: EditPlanRecordSummary[]; active_plan_id?: string | null }>(
      `/projects/${projectId}/plans`,
      headers,
    ),
    apiGet<{ slots?: BrollSlotSummary[] }>(`/projects/${projectId}/broll`, headers),
    apiGet<MusicSelectionSummary>(`/projects/${projectId}/music/selection`, headers),
    apiGet<EffectsConfigSummary>(`/projects/${projectId}/effects`, headers),
    apiGet<{ jobs?: RenderJobSummary[] }>(`/projects/${projectId}/renders`, headers),
    apiGet<{ exports?: ExportSummary[] }>(`/projects/${projectId}/exports`, headers),
    apiGet<BillingStatusSummary>(`/billing/status`, headers),
    apiGet<VisualAnalysisStatusSummary>(`/projects/${projectId}/visual/status`, headers),
  ]);

  const editPlans = plansPayload?.plans ?? [];
  const activePlanId =
    plansPayload?.active_plan_id ?? editPlans.find((plan) => plan.is_active)?.id ?? null;

  return (
    <EditorClient
      projectId={projectId}
      workspaceId={workspaceId}
      userId={context.userId}
      apiAccessToken={context.apiAccessToken}
      title={project.title}
      upload={upload}
      uploadAssetUrls={{
        source_video_url: resolveProxiedUrl(uploadAssetUrls?.source_video_url) ?? null,
        thumbnail_url: resolveProxiedUrl(uploadAssetUrls?.thumbnail_url) ?? null,
      }}
      transcript={transcript}
      captionTracks={captionsPayload?.tracks ?? []}
      editPlans={editPlans}
      activePlanId={activePlanId}
      brollSlots={brollPayload?.slots ?? []}
      musicSelection={musicSelection}
      effectsConfig={effectsConfig}
      renderJobs={rendersPayload?.jobs ?? []}
      exports={exportsPayload?.exports ?? []}
      maxExportResolution={toExportResolution(
        billingStatus?.entitlements.max_resolution,
      )}
      visualStatus={visualStatus ?? { status: "unavailable", message: "Visual context is unavailable; editing will continue using transcript and audio." }}
    />
  );
}

function toExportResolution(value: string | undefined): ExportResolution {
  return value === "4k" || value === "1080p" ? value : "720p";
}

async function getApiContext(projectId: string): Promise<ApiContext> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!user || !session?.access_token) {
    redirect(`/login?next=${encodeURIComponent(`/projects/${projectId}`)}`);
  }
  const { data: membership } = await supabase
    .from("workspace_members")
    .select("workspace_id")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();
  if (!membership?.workspace_id) {
    redirect("/dashboard?error=workspace_missing");
  }
  const workspaceId = membership.workspace_id as string;

  return {
    userId: user.id,
    workspaceId,
    apiAccessToken: session.access_token,
    headers: {
      authorization: `Bearer ${session.access_token}`,
      "x-motionly-user-id": user.id,
      "x-motionly-workspace-id": workspaceId,
    },
  };
}

async function apiGet<T>(path: string, headers: Record<string, string>): Promise<T | null> {
  const response = await fetch(`${env.apiBaseUrl}${path}`, {
    headers,
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    const requestId = response.headers.get("x-request-id");
    throw new Error(
      `Motionly API request failed (${response.status})${
        requestId ? ` [request ${requestId}]` : ""
      }.`,
    );
  }
  return (await response.json()) as T;
}

function toUploadSummary(upload: UploadResponse | null): UploadedVideoSummary | null {
  if (!upload) return null;
  return {
    id: upload.id,
    projectId: upload.project_id,
    workspaceId: upload.workspace_id,
    originalFilename: upload.original_filename,
    status: upload.status,
    durationMs: upload.metadata?.duration_ms ?? null,
    width: upload.metadata?.width ?? null,
    height: upload.metadata?.height ?? null,
    fps: upload.metadata?.fps ?? null,
    thumbnailPath: upload.thumbnail_path ?? null,
    warnings: upload.metadata?.warnings ?? [],
    updatedAt: upload.updated_at,
  };
}
