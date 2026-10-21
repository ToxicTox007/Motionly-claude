import { cookies } from "next/headers";
import { AppShell } from "@/components/app-shell";
import { createClient } from "@/lib/supabase/server";
import { getWorkspaceApiContext, WORKSPACE_COOKIE_NAME } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { DashboardClient } from "@/components/DashboardClient";
import { WorkspacePicker } from "@/components/workspace-picker";
import { storageGauge } from "@/lib/storage-gauge.mjs";

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
  let storage: ReturnType<typeof storageGauge> = storageGauge(null);

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
                latest_upload?: {
                  id: string;
                  status: string;
                  original_filename: string;
                  duration_ms?: number | null;
                  warnings?: string[];
                } | null;
              }>;
            })
          : {};
        // B29: GET /projects embeds each project's latest upload, so the
        // dashboard renders upload state in ONE API call instead of one extra
        // /uploads/{id} request per project.
        projects = (payload.projects ?? []).slice(0, 12).map((project) => {
          const upload = project.latest_upload ?? null;
          return {
            id: project.id,
            title: project.title,
            updated_at: project.updated_at,
            uploaded_videos: upload
              ? [
                  {
                    status: upload.status,
                    original_filename: upload.original_filename,
                    duration_ms: upload.duration_ms ?? null,
                    metadata: { warnings: upload.warnings ?? [] },
                  },
                ]
              : [],
          };
        }) as typeof projects;

        // B23: the storage gauge shows the real workspace quota from the API.
        // A failed/absent quota degrades to "not metered" instead of a fake bar.
        try {
          const quotaResponse = await fetch(`${env.apiBaseUrl}/storage/quota`, {
            headers: context.headers,
            cache: "no-store",
          });
          storage = storageGauge(
            quotaResponse.ok
              ? ((await quotaResponse.json()) as {
                  used_bytes?: number;
                  max_bytes?: number;
                  percentage?: number;
                })
              : null,
          );
        } catch {
          storage = storageGauge(null);
        }
      }
    }
  }

  return (
    <AppShell title="Dashboard">
      <DashboardClient
        initialProjects={projects}
        workspaceName={workspaceName}
        storage={storage}
      />
    </AppShell>
  );
}
