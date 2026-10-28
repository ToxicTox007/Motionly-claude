import type {
  ExportResolution,
  UploadedVideoSummary,
  WorkflowDetail,
} from "@motionly/shared";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { EditorClient } from "@/components/editor-client";
import {
  ProcessingProjectScreen,
  UploadProjectScreen,
} from "@/components/project-early-phase";
import { loadEditorPanels } from "@/lib/editor-panel-loader";
import { panelErrorKey } from "@/lib/editor-panels";
import { env } from "@/lib/env";
import { createClient } from "@/lib/supabase/server";
import {
  assertCanonicalWorkflowIdentity,
  selectLatestFirstDraftWorkflow,
} from "@/lib/workflow-ui.mjs";
import { PageTitle } from "@/components/page-title";

type ApiContext = {
  headers: Record<string, string>;
  workspaceId: string;
  userId: string;
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
  media_revision?: number | null;
  source_object_reference_id?: string | null;
  verified_sha256?: string | null;
  updated_at: string;
};

export default async function ProjectDetailsPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const contexts = await getApiContexts(projectId);
  const projectMatches = await Promise.all(
    contexts.map(async (context) => ({
      context,
      project: await apiGet<ProjectResponse>(
        `/projects/${projectId}`,
        context.headers,
      ),
    })),
  );
  const projectMatch = projectMatches.find(
    (candidate) => candidate.project !== null,
  );

  if (!projectMatch?.project) {
    return (
      <AppShell title="Project not found">
        <p className="rounded-neu-lg bg-surface p-6 text-sm font-medium text-muted-foreground shadow-neu-raised">
          This project does not exist or you do not have access to it.
        </p>
      </AppShell>
    );
  }

  const { context, project } = projectMatch;

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
  };

  if (!upload) {
    return (
      <>
        <PageTitle title={project.title} />
        <UploadProjectScreen {...phaseAuth} />
      </>
    );
  }

  if (upload.status !== "ready") {
    return (
      <>
        <PageTitle title={project.title} />
        <ProcessingProjectScreen
          {...phaseAuth}
          uploadId={upload.id}
          initialStatus={upload.status}
        />
      </>
    );
  }

  // Each panel loads on its own (B27). A failed panel arrives as an entry in
  // `panelErrors`, never as empty data, so the editor can say so and retry.
  const { data: panels, errors: panelErrors } = await loadEditorPanels(
    projectId,
    headers,
    upload,
  );
  const {
    assetUrls: uploadAssetUrls,
    transcript,
    captions: captionsPayload,
    plans: plansPayload,
    broll: brollPayload,
    music: musicSelection,
    effects: effectsConfig,
    renders: rendersPayload,
    exports: exportsPayload,
    billing: billingStatus,
    visual: visualStatus,
    workflows: workflowsPayload,
  } = panels;

  // The API is canonical latest-first. Select in response order, then hydrate
  // exact detail so reloads and other devices recover steps/artifacts safely.
  // If the detail cannot be read, the editor's workflow poller fetches it.
  const workflowSummary = selectLatestFirstDraftWorkflow(
    workflowsPayload?.workflows ?? [],
    projectId,
  );
  let workflowDetail: WorkflowDetail | null = null;
  if (workflowSummary) {
    try {
      workflowDetail = await apiGet<WorkflowDetail>(
        `/projects/${projectId}/workflows/${workflowSummary.id}`,
        headers,
      );
    } catch {
      panelErrors.workflows = { requestId: null };
    }
  }
  const initialWorkflow = workflowDetail
    ? assertCanonicalWorkflowIdentity(workflowDetail, {
        projectId,
        expectedWorkflowId: workflowSummary?.id,
      })
    : null;

  const editPlans = plansPayload?.plans ?? [];
  const activePlanId =
    plansPayload?.active_plan_id ??
    editPlans.find((plan) => plan.is_active)?.id ??
    null;

  return (
    <>
      <PageTitle title={project.title} />
      <EditorClient
        // A Retry that loads a failed panel changes this key, so the editor
        // remounts with the fresh data instead of keeping its empty copy.
        key={`panels:${panelErrorKey(panelErrors)}`}
        panelErrors={panelErrors}
        projectId={projectId}
        workspaceId={workspaceId}
        userId={context.userId}
        title={project.title}
        upload={upload}
        uploadAssetUrls={{
          source_video_url: uploadAssetUrls?.source_video_url ?? null,
          thumbnail_url: uploadAssetUrls?.thumbnail_url ?? null,
          source_reference_id: uploadAssetUrls?.source_reference_id ?? null,
          thumbnail_reference_id:
            uploadAssetUrls?.thumbnail_reference_id ?? null,
          expires_at: uploadAssetUrls?.expires_at ?? null,
          expires_in_seconds: uploadAssetUrls?.expires_in_seconds,
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
        visualStatus={
          visualStatus ?? {
            status: "unavailable",
            message: panelErrors.visual
              ? "Visual analysis status couldn't be loaded; editing will continue using transcript and audio."
              : "Visual context is unavailable; editing will continue using transcript and audio.",
          }
        }
        initialWorkflow={initialWorkflow}
      />
    </>
  );
}

function toExportResolution(value: string | undefined): ExportResolution {
  return value === "4k" || value === "1080p" ? value : "720p";
}

async function getApiContexts(projectId: string): Promise<ApiContext[]> {
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
  const { data: memberships } = await supabase
    .from("workspace_members")
    .select("workspace_id")
    .eq("user_id", user.id)
    .eq("status", "active");
  if (!memberships?.length) {
    redirect("/dashboard?error=workspace_missing");
  }

  return memberships.map((membership) => {
    const workspaceId = membership.workspace_id as string;
    return {
      userId: user.id,
      workspaceId,
      headers: {
        authorization: `Bearer ${session.access_token}`,
        "x-motionly-workspace-id": workspaceId,
      },
    };
  });
}

async function apiGet<T>(
  path: string,
  headers: Record<string, string>,
): Promise<T | null> {
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

function toUploadSummary(
  upload: UploadResponse | null,
): UploadedVideoSummary | null {
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
    mediaRevision: upload.media_revision ?? 1,
    sourceObjectReferenceId: upload.source_object_reference_id ?? null,
    verifiedSha256: upload.verified_sha256 ?? null,
    thumbnailPath: upload.thumbnail_path ?? null,
    warnings: upload.metadata?.warnings ?? [],
    updatedAt: upload.updated_at,
  };
}
