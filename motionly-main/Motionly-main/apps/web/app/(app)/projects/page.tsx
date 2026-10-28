import { Film, Pencil, PlusCircle, Trash2 } from "lucide-react";
import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { Button, ButtonLink } from "@/components/button";
import { Alert } from "@/components/brutal-ui";
import { EmptyState } from "@/components/empty-state";
import { LoadErrorAlert } from "@/components/load-error-alert";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { messageFor, searchParam } from "@/lib/error-messages";
import { serverApiFetch } from "@/lib/server-api";
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
  const notice =
    typeof params.updated === "string"
      ? "Project updated."
      : typeof params.deleted === "string"
        ? "Project deleted."
        : null;
  const errorCode = searchParam(params, "error");
  // Mapped copy only: a crafted ?error= never puts its text on the page.
  const error = errorCode ? messageFor("projects", errorCode) : null;
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

  const result = await serverApiFetch<{ projects?: ProjectRow[] }>(
    "/projects",
    { headers: context.headers },
  );
  const loadError = !result.ok || !Array.isArray(result.data?.projects);
  const projects = result.ok ? (result.data?.projects ?? []) : [];

  return (
    <AppShell title="Projects">
      <div className="space-y-6">
        <section className="flex flex-col gap-4 rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg sm:flex-row sm:items-center sm:justify-between sm:p-8">
          <div>
            <p className="text-xs font-semibold text-primary">
              Project library
            </p>
            <h2 className="mt-2 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              Manage reels
            </h2>
            <p className="mt-1 text-sm font-medium text-muted-foreground">
              Rename, review, open, and remove projects before they reach
              export.
            </p>
          </div>
          <ButtonLink href="/projects/new" className="shrink-0 gap-2">
            <PlusCircle className="size-5" aria-hidden="true" />
            New project
          </ButtonLink>
        </section>

        {notice ? <Alert tone="success">{notice}</Alert> : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}

        {loadError ? (
          <LoadErrorAlert title="Couldn't load your projects" />
        ) : projects.length ? (
          <div className="grid gap-4">
            {projects.map((project) => (
              <article
                key={project.id}
                className="rounded-neu-lg bg-surface p-5 shadow-neu-raised"
              >
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px_110px] lg:items-center">
                  <form
                    action={updateProject}
                    className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_150px_auto] sm:items-end"
                  >
                    <input type="hidden" name="projectId" value={project.id} />
                    <label className="min-w-0">
                      <span className="mb-2 block text-xs font-semibold text-muted-foreground">
                        Title
                      </span>
                      <input
                        name="title"
                        defaultValue={project.title}
                        maxLength={120}
                        className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 text-sm font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
                      />
                    </label>
                    <label>
                      <span className="mb-2 block text-xs font-semibold text-muted-foreground">
                        Status
                      </span>
                      <select
                        name="status"
                        defaultValue={project.status}
                        className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 text-sm font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
                      >
                        <option value="draft">Draft</option>
                        <option value="ready">Ready</option>
                        <option value="failed">Failed</option>
                      </select>
                    </label>
                    <Button type="submit" variant="secondary" className="gap-2">
                      <Pencil className="size-4" aria-hidden="true" />
                      Save
                    </Button>
                  </form>

                  <div className="text-sm font-medium text-muted-foreground lg:text-right">
                    <p>
                      Updated{" "}
                      <span className="font-semibold tabular-nums text-foreground">
                        {new Date(project.updated_at).toLocaleDateString()}
                      </span>
                    </p>
                    <span
                      className={cn(
                        "mt-2 inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold capitalize shadow-neu-inset-sm",
                        project.status === "ready"
                          ? "text-success"
                          : project.status === "failed"
                            ? "text-danger"
                            : "text-muted-foreground",
                      )}
                    >
                      {project.status}
                    </span>
                  </div>

                  <div className="flex gap-2 lg:justify-end">
                    <Link
                      href={`/projects/${project.id}`}
                      className="inline-flex min-h-10 flex-1 items-center justify-center rounded-neu bg-primary px-3 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed lg:flex-none"
                    >
                      Open
                    </Link>
                    <form action={deleteProject}>
                      <input
                        type="hidden"
                        name="projectId"
                        value={project.id}
                      />
                      <Button
                        type="submit"
                        variant="secondary"
                        className="text-danger"
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
