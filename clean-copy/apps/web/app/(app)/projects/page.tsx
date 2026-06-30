import { AlertTriangle, Film, Pencil, PlusCircle, Trash2 } from "lucide-react";
import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { Button, ButtonLink } from "@/components/button";
import { EmptyState } from "@/components/empty-state";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { cn } from "@/lib/utils";
import { deleteProject, updateProject } from "./actions";

type ProjectRow = {
  id: string;
  title: string;
  status: "draft" | "ready" | "failed";
  updated_at: string;
  created_at: string;
};

export default async function ProjectsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await searchParams) ?? {};
  const notice = typeof params.updated === "string"
    ? "Project updated."
    : typeof params.deleted === "string"
      ? "Project deleted."
      : null;
  const error = typeof params.error === "string" ? params.error.replaceAll("_", " ") : null;
  const context = await getWorkspaceApiContext();
  if (!context) {
    return (
      <AppShell title="Projects">
        <EmptyState
          icon={Film}
          title="Sign in required"
          body="Sign in to manage your Motionly projects."
          actionHref="/login?next=/projects"
          actionLabel="Log in"
        />
      </AppShell>
    );
  }

  const response = await fetch(`${env.apiBaseUrl}/projects`, {
    headers: context.headers,
    cache: "no-store",
  });
  const payload = response.ok ? await response.json() : { projects: [] };
  const projects = (payload.projects ?? []) as ProjectRow[];

  return (
    <AppShell title="Projects">
      <div className="space-y-6">
        <section className="pixel-panel pixel-corners flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-black uppercase tracking-widest text-primary">
              Project library
            </p>
            <h2 className="mt-2 text-3xl font-black tracking-tight">Manage reels</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Rename, review, open, and remove projects before they reach export.
            </p>
          </div>
          <ButtonLink href="/projects/new" className="h-12 shrink-0 gap-2 px-6">
            <PlusCircle className="size-5" aria-hidden="true" />
            New project
          </ButtonLink>
        </section>

        {notice ? (
          <div className="border border-emerald-200 bg-emerald-50 p-3 text-sm font-bold text-emerald-700">
            {notice}
          </div>
        ) : null}
        {error ? (
          <div className="flex gap-2 border border-red-200 bg-red-50 p-3 text-sm font-bold text-red-700">
            <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
            {error}
          </div>
        ) : null}

        {projects.length ? (
          <div className="grid gap-4">
            {projects.map((project) => (
              <article key={project.id} className="pixel-panel p-4">
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px_110px] lg:items-center">
                  <form action={updateProject} className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_150px_auto] sm:items-end">
                    <input type="hidden" name="projectId" value={project.id} />
                    <label className="min-w-0">
                      <span className="mb-2 block text-xs font-black uppercase tracking-widest text-muted-foreground">
                        Title
                      </span>
                      <input
                        name="title"
                        defaultValue={project.title}
                        maxLength={120}
                        className="pixel-input h-11 w-full px-3 text-sm font-bold"
                      />
                    </label>
                    <label>
                      <span className="mb-2 block text-xs font-black uppercase tracking-widest text-muted-foreground">
                        Status
                      </span>
                      <select
                        name="status"
                        defaultValue={project.status}
                        className="pixel-input h-11 w-full bg-background px-3 text-sm font-bold"
                      >
                        <option value="draft">Draft</option>
                        <option value="ready">Ready</option>
                        <option value="failed">Failed</option>
                      </select>
                    </label>
                    <Button type="submit" variant="secondary" className="h-11 gap-2">
                      <Pencil className="size-4" aria-hidden="true" />
                      Save
                    </Button>
                  </form>

                  <div className="text-sm text-muted-foreground lg:text-right">
                    <p>
                      Updated{" "}
                      <span className="font-bold text-foreground">
                        {new Date(project.updated_at).toLocaleDateString()}
                      </span>
                    </p>
                    <span
                      className={cn(
                        "mt-2 inline-flex border px-2 py-1 text-[10px] font-black uppercase tracking-widest",
                        project.status === "ready"
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                          : project.status === "failed"
                            ? "border-red-200 bg-red-50 text-red-700"
                            : "border-border bg-muted text-muted-foreground",
                      )}
                    >
                      {project.status}
                    </span>
                  </div>

                  <div className="flex gap-2 lg:justify-end">
                    <Link
                      href={`/projects/${project.id}`}
                      className="pixel-button inline-flex h-10 flex-1 items-center justify-center bg-primary px-3 text-sm font-bold text-primary-foreground hover:brightness-105 lg:flex-none"
                    >
                      Open
                    </Link>
                    <form action={deleteProject}>
                      <input type="hidden" name="projectId" value={project.id} />
                      <Button
                        type="submit"
                        variant="secondary"
                        className="h-10 border-red-200 text-red-700 hover:bg-red-50"
                        title="Delete project"
                      >
                        <Trash2 className="size-4" aria-hidden="true" />
                      </Button>
                    </form>
                  </div>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={Film}
            title="No projects yet"
            body="Create your first reel project, upload footage, and start editing."
            actionHref="/projects/new"
            actionLabel="Create project"
          />
        )}
      </div>
    </AppShell>
  );
}
