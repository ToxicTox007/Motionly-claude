import { cookies } from "next/headers";
import { AppShell } from "@/components/app-shell";
import { createClient } from "@/lib/supabase/server";
import { getWorkspaceApiContext, WORKSPACE_COOKIE_NAME } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { DashboardClient } from "@/components/DashboardClient";
import { WorkspacePicker } from "@/components/workspace-picker";

export default async function DashboardPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const resolvedSearchParams = (await searchParams) ?? {};
  const rawWorkspaceError = resolvedSearchParams.error;
  const workspaceError =
    typeof rawWorkspaceError === "string" &&
    [
      "workspace_missing",
      "workspace_invalid",
      "workspace_selection_required",
      "workspace_unavailable",
    ].includes(rawWorkspaceError)
      ? rawWorkspaceError
      : null;
  let workspaceName = "Personal workspace";
  let projects:
    | Array<{
        id: string;
        title: string;
        updated_at: string;
        uploaded_videos: Array<{
          status: string;
          original_filename: string;
          duration_ms: number | null;
          metadata: { warnings?: string[] } | null;
        }>;
      }>
    | [] = [];

  if (env.skipAuthMiddleware) {
    // Local bypass: use defaults and avoid Supabase calls
    workspaceName = "Personal workspace";
    projects = [];
  } else {
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    const cookieStore = await cookies();
    const selectedWorkspaceId =
      cookieStore.get(WORKSPACE_COOKIE_NAME)?.value?.trim() || undefined;
    const { data: memberships } = user
      ? await supabase
          .from("workspace_members")
          .select("workspace_id, workspaces(name)")
          .eq("user_id", user.id)
          .eq("status", "active")
      : { data: [] };
    const effectiveWorkspaceId =
      selectedWorkspaceId ??
      (memberships?.length === 1
        ? String(memberships[0].workspace_id)
        : undefined);
    const hasSelectedWorkspace = Boolean(
      effectiveWorkspaceId &&
      memberships?.some(
        (membership) =>
          String(membership.workspace_id) === effectiveWorkspaceId,
      ),
    );

    if (memberships && memberships.length > 0 && !hasSelectedWorkspace) {
      return (
        <AppShell title="Choose workspace">
          <WorkspacePicker
            workspaces={memberships.map((membership) => {
              const workspace = Array.isArray(membership.workspaces)
                ? membership.workspaces[0]
                : membership.workspaces;
              return {
                id: String(membership.workspace_id),
                name: workspace?.name ?? "Workspace",
              };
            })}
            error={workspaceError}
          />
        </AppShell>
      );
    }

    const context = await getWorkspaceApiContext();

    if (user) {
      const membership = memberships?.find(
        (item) => String(item.workspace_id) === effectiveWorkspaceId,
      );
      const workspace = Array.isArray(membership?.workspaces)
        ? membership.workspaces[0]
        : membership?.workspaces;
      workspaceName = workspace?.name ?? workspaceName;

      if (context) {
        const response = await fetch(`${env.apiBaseUrl}/projects`, {
          headers: context.headers,
          cache: "no-store",
        });
        const payload = response.ok
          ? ((await response.json()) as {
              projects?: Array<{
                id: string;
                title: string;
                updated_at: string;
                active_video_id?: string | null;
              }>;
            })
          : {};
        const projectRows = (payload.projects ?? []).slice(0, 12);
        projects = (await Promise.all(
          projectRows.map(async (project) => {
            const uploadResponse = project.active_video_id
              ? await fetch(
                  `${env.apiBaseUrl}/projects/${project.id}/uploads/${project.active_video_id}`,
                  { headers: context.headers, cache: "no-store" },
                )
              : null;
            const upload = uploadResponse?.ok
              ? ((await uploadResponse.json()) as {
                  status: string;
                  original_filename: string;
                  metadata?: {
                    duration_ms?: number | null;
                    warnings?: string[];
                  } | null;
                })
              : null;
            return {
              ...project,
              uploaded_videos: upload
                ? [
                    {
                      ...upload,
                      duration_ms: upload.metadata?.duration_ms ?? null,
                      metadata: upload.metadata ?? null,
                    },
                  ]
                : [],
            };
          }),
        )) as typeof projects;
      }
    }
  }

  return (
    <AppShell title="Dashboard">
      <DashboardClient
        initialProjects={projects}
        workspaceName={workspaceName}
      />
    </AppShell>
  );
}
