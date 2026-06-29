import { AppShell } from "@/components/app-shell";
import { createClient } from "@/lib/supabase/server";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { DashboardClient } from "@/components/DashboardClient";

export default async function DashboardPage() {
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

  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    // Local bypass: use defaults and avoid Supabase calls
    workspaceName = "Personal workspace";
    projects = [];
  } else {
    const context = await getWorkspaceApiContext();
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();

    if (user) {
      const { data: membership } = await supabase
        .from("workspace_members")
        .select("workspaces(name)")
        .eq("user_id", user.id)
        .eq("status", "active")
        .limit(1)
        .maybeSingle();

      const workspace = Array.isArray(membership?.workspaces)
        ? membership?.workspaces[0]
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
