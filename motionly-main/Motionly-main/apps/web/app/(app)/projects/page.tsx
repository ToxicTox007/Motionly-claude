import { Film, Pencil, PlusCircle, Search } from "lucide-react";
import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { ButtonLink } from "@/components/button";
import { Alert } from "@/components/brutal-ui";
import { EmptyState } from "@/components/empty-state";
import { LoadErrorAlert } from "@/components/load-error-alert";
import { Pagination } from "@/components/pagination";
import { SubmitButton } from "@/components/submit-button";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { messageFor, searchParam } from "@/lib/error-messages";
import {
  PROJECT_SEARCH_MAX_LENGTH,
  PROJECT_SORTS,
  PROJECT_SORT_LABELS,
  pageCountFor,
  parseProjectListQuery,
  projectListApiPath,
  projectListHref,
} from "@/lib/project-list-query";
import { serverApiFetch } from "@/lib/server-api";
import { cn } from "@/lib/utils";
import { updateProject } from "./actions";
import { DeleteProjectButton } from "./delete-project-button";

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

  // Search, sort and page come from the URL and are applied by the API, so
  // they cover every project and survive reloads and back/forward (FE-27).
  const query = parseProjectListQuery(params);
  const result = await serverApiFetch<{
    projects?: ProjectRow[];
    total?: number;
  }>(projectListApiPath(query), { headers: context.headers });
  const loadError = !result.ok || !Array.isArray(result.data?.projects);
  const projects = result.ok ? (result.data?.projects ?? []) : [];
  const total =
    result.ok && typeof result.data?.total === "number"
      ? result.data.total
      : projects.length;
  const pageCount = pageCountFor(total);
  const pastTheEnd = !loadError && projects.length === 0 && total > 0;
  const summary = query.q
    ? `${total} ${total === 1 ? "project matches" : "projects match"} “${query.q}”`
    : `${total} ${total === 1 ? "project" : "projects"}`;

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

        <form
          role="search"
          action="/projects"
          method="get"
          className="flex flex-col gap-3 rounded-neu-xl bg-surface p-3 shadow-neu-raised sm:flex-row sm:items-center"
        >
          <div className="relative min-w-0 flex-1">
            <label htmlFor="projects-search" className="sr-only">
              Search projects
            </label>
            <Search
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            <input
              id="projects-search"
              type="search"
              name="q"
              defaultValue={query.q}
              maxLength={PROJECT_SEARCH_MAX_LENGTH}
              placeholder="Search projects by title"
              className="h-11 w-full rounded-neu border border-control bg-surface pl-9 pr-4 text-sm font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
            />
          </div>
          <label htmlFor="projects-sort" className="sr-only">
            Sort
          </label>
          <select
            id="projects-sort"
            name="sort"
            defaultValue={query.sort}
            className="h-11 rounded-neu border border-control bg-surface px-4 text-sm font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          >
            {PROJECT_SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {PROJECT_SORT_LABELS[sort]}
              </option>
            ))}
          </select>
          <SubmitButton variant="secondary" pendingLabel="Searching…">
            Search
          </SubmitButton>
        </form>

        {loadError ? null : (
          <p
            className="text-sm font-medium text-muted-foreground"
            aria-live="polite"
          >
            {summary}
          </p>
        )}

        {loadError ? (
          <LoadErrorAlert title="Couldn't load your projects" />
        ) : pastTheEnd ? (
          <div className="rounded-neu-lg bg-surface p-8 text-center shadow-neu-raised">
            <p className="text-sm font-medium text-muted-foreground">
              This page is past the end of the list.
            </p>
            <Link
              href={projectListHref({ ...query, page: pageCount })}
              className="mt-3 inline-flex min-h-11 items-center text-sm font-semibold text-primary underline-offset-4 hover:underline"
            >
              Go to the last page
            </Link>
          </div>
        ) : projects.length === 0 && query.q ? (
          <div className="rounded-neu-lg bg-surface p-8 text-center shadow-neu-raised">
            <p className="text-sm font-semibold text-foreground">
              No projects match “{query.q}”
            </p>
            <Link
              href="/projects"
              className="mt-3 inline-flex min-h-11 items-center text-sm font-semibold text-primary underline-offset-4 hover:underline"
            >
              Clear search
            </Link>
          </div>
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
                    <SubmitButton
                      variant="secondary"
                      className="gap-2"
                      pendingLabel="Saving…"
                    >
                      <Pencil className="size-4" aria-hidden="true" />
                      Save
                    </SubmitButton>
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
                    <DeleteProjectButton
                      projectId={project.id}
                      title={project.title}
                    />
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

        {loadError ? null : (
          <Pagination
            page={query.page}
            pageCount={pageCount}
            hrefFor={(page) => projectListHref({ ...query, page })}
          />
        )}
      </div>
    </AppShell>
  );
}
