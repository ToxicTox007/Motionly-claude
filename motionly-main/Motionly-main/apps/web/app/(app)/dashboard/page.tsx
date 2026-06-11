import { AlertTriangle, ChevronRight, Clock, Film, PlusCircle } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { ButtonLink } from "@/components/button";
import { EmptyState } from "@/components/empty-state";
import { createClient } from "@/lib/supabase/server";
import { cn } from "@/lib/utils";
import Link from "next/link";

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

      const { data } = await supabase
        .from("projects")
        .select(
          "id,title,updated_at,uploaded_videos!uploaded_videos_project_id_fkey(status,original_filename,duration_ms,metadata)",
        )
        .order("updated_at", { ascending: false })
        .limit(12);

      projects = (data ?? []) as typeof projects;
    }
  }

  return (
    <AppShell title="Dashboard">
      <div className="flex flex-col gap-8">
        <div className="pixel-panel pixel-corners pixel-grid-fine flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-black uppercase tracking-widest text-primary">{workspaceName}</p>
            <h2 className="text-3xl font-black tracking-tight">Your Projects</h2>
            <p className="text-muted-foreground mt-1">Manage and edit your AI-powered reels.</p>
          </div>
          <ButtonLink href="/projects/new" className="h-12 shrink-0 px-6">
             <PlusCircle className="mr-2 size-5" />
             Create New Video
          </ButtonLink>
        </div>

        {projects.length ? (
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {projects.map((project) => {
              const upload = project.uploaded_videos?.[0];
              const warnings = upload?.metadata?.warnings ?? [];
              return (
                <Link
                  key={project.id}
                  href={`/projects/${project.id}`}
                  className="pixel-panel pixel-corners group relative flex min-h-56 flex-col overflow-hidden p-5 transition-all duration-200 hover:-translate-y-1 hover:border-primary/50 hover:shadow-[6px_6px_0_hsl(var(--primary)/0.12)]"
                >
                  <div className="flex items-start justify-between mb-4">
                    <div className="flex size-12 items-center justify-center border border-primary/20 bg-primary/5 text-primary transition-colors duration-300 group-hover:bg-primary group-hover:text-primary-foreground">
                      <Film className="size-6" />
                    </div>
                    <span className={cn(
                      "border px-3 py-1 text-[10px] font-bold uppercase tracking-widest",
                      upload?.status === "ready" ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-border bg-muted text-muted-foreground"
                    )}>
                      {upload?.status ?? "draft"}
                    </span>
                  </div>

                  <div className="flex-1 min-w-0">
                    <h3 className="truncate text-lg font-bold group-hover:text-primary transition-colors">{project.title}</h3>
                    <p className="mt-1 truncate text-sm text-muted-foreground">
                      {upload?.original_filename ?? "Waiting for source..."}
                    </p>
                  </div>

                  <div className="mt-6 flex items-center justify-between border-t border-border pt-4 text-xs font-medium text-muted-foreground/60">
                    <div className="flex items-center gap-4">
                      <span>{formatDuration(upload?.duration_ms ?? null)}</span>
                      <div className="flex items-center gap-1.5">
                        <Clock className="size-3.5" />
                        {new Date(project.updated_at).toLocaleDateString()}
                      </div>
                    </div>
                    <ChevronRight className="size-4 opacity-0 group-hover:opacity-100 group-hover:translate-x-1 transition-all" />
                  </div>

                  {warnings.length ? (
                    <div className="mt-4 flex gap-1 items-center text-[10px] text-amber-600 font-bold uppercase tracking-tight">
                      <AlertTriangle className="size-3" />
                      {warnings.length} issues found
                    </div>
                  ) : null}
                </Link>
              );
            })}
          </div>
        ) : (
          <EmptyState
            icon={Film}
            title="No reels yet"
            body="Upload your first video to start creating amazing AI-edited reels in seconds."
            actionHref="/projects/new"
            actionLabel="Create my first reel"
          />
        )}
      </div>
    </AppShell>
  );
}

function formatDuration(durationMs: number | null) {
  if (!durationMs) return "0:00";
  const seconds = Math.round(durationMs / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
