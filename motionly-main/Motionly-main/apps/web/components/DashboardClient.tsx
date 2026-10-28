"use client";

import React, { useState, useMemo, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Film,
  Clock,
  AlertTriangle,
  PlusCircle,
  Trash2,
  ShieldAlert,
  Loader2,
  Search,
  Play,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { messageFor } from "@/lib/error-messages";
import { useToast } from "./toast";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { Button } from "./button";
import { LoadErrorAlert } from "./load-error-alert";
import { deleteProjectResult } from "../app/(app)/projects/actions";

interface UploadedVideo {
  status: string;
  original_filename: string;
  duration_ms: number | null;
  metadata: { warnings?: string[] } | null;
}

interface Project {
  id: string;
  title: string;
  updated_at: string;
  uploaded_videos: UploadedVideo[];
}

interface DashboardClientProps {
  initialProjects: Project[];
  /** The projects request failed: the list is unknown, not empty. */
  loadError?: boolean;
  workspaceName: string;
  storage?: {
    metered: boolean;
    label: string;
    percent: number | null;
    percentLabel: string | null;
    ariaLabel: string;
  };
}

function formatDuration(durationMs: number | null) {
  if (!durationMs) return "0:00";
  const seconds = Math.round(durationMs / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function getStatusBadgeClass(status: string | undefined) {
  switch (status) {
    case "ready":
      return "text-success";
    case "processing":
      return "text-warning";
    case "error":
      return "text-danger";
    default:
      return "text-muted-foreground";
  }
}

export function DashboardClient({
  initialProjects,
  loadError = false,
  workspaceName,
  storage,
}: DashboardClientProps) {
  const router = useRouter();
  const { toast } = useToast();
  const [localProjects, setLocalProjects] =
    useState<Project[]>(initialProjects);
  // A refresh (Retry, or after a delete) brings new server data: adopt it.
  const [serverProjects, setServerProjects] = useState(initialProjects);
  if (serverProjects !== initialProjects) {
    setServerProjects(initialProjects);
    setLocalProjects(initialProjects);
  }
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<"updated" | "title">("updated");

  // Delete project modal state
  const [projectToDelete, setProjectToDelete] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const isDeletingRef = useRef(false);

  // Focus trap for delete modal
  const focusTrapRef = useFocusTrap(Boolean(projectToDelete), () =>
    setProjectToDelete(null),
  );

  const sortedProjects = useMemo(() => {
    return [...localProjects].sort((a, b) => {
      if (sortBy === "updated") {
        return (
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        );
      } else {
        return a.title.localeCompare(b.title);
      }
    });
  }, [localProjects, sortBy]);

  const filteredProjects = useMemo(() => {
    if (!searchQuery.trim()) return sortedProjects;
    const query = searchQuery.toLowerCase();
    return sortedProjects.filter((p) => p.title.toLowerCase().includes(query));
  }, [sortedProjects, searchQuery]);

  const handleCancelDelete = () => {
    setProjectToDelete(null);
  };

  const confirmDelete = async () => {
    if (!projectToDelete || isDeletingRef.current || deleteLoading) return;
    isDeletingRef.current = true;
    setDeleteLoading(true);

    try {
      const result = await deleteProjectResult(projectToDelete.id);

      if (!result.ok) {
        toast({
          title: "Delete Failed",
          description: `Could not delete "${projectToDelete.title}". ${messageFor(
            "projects",
            result.error,
          )} The project was kept.`,
          variant: "error",
        });
        return;
      }

      toast({
        title: "Project Deleted",
        description: `"${projectToDelete.title}" has been deleted.`,
        variant: "success",
      });

      setLocalProjects((current) =>
        current.filter((p) => p.id !== projectToDelete.id),
      );
      setProjectToDelete(null);
      router.refresh();
    } catch (error) {
      console.error(error);
      toast({
        title: "Delete Failed",
        description:
          "Failed to delete project. Please check connection and try again.",
        variant: "error",
      });
    } finally {
      isDeletingRef.current = false;
      setDeleteLoading(false);
    }
  };

  // The projects could not be loaded: say so and offer Retry. Showing the
  // first-run onboarding here would tell an existing user their work is gone.
  if (loadError) {
    return (
      <div className="flex flex-col gap-8">
        <div className="min-w-0">
          <span
            className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-xs font-semibold text-foreground shadow-neu-raised-sm"
            data-testid="workspace-name"
          >
            {workspaceName}
          </span>
          <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            Your Projects
          </h1>
        </div>
        <LoadErrorAlert
          title="Couldn't load your projects"
          body="Motionly could not reach its servers. Your projects are safe; try again in a moment."
        />
      </div>
    );
  }

  // Onboarding card deck when empty
  if (localProjects.length === 0) {
    return (
      <div className="flex flex-col gap-8">
        {/* Header */}
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <span
              className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-xs font-semibold text-foreground shadow-neu-raised-sm"
              data-testid="workspace-name"
            >
              {workspaceName}
            </span>
            <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
              Your Projects
            </h1>
            <p className="mt-1 text-sm font-medium text-muted-foreground">
              AI-powered video editing pipeline
            </p>
          </div>
          <Link
            href="/projects/new"
            className="inline-flex min-h-12 shrink-0 items-center justify-center gap-2 rounded-neu bg-primary px-6 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
          >
            <PlusCircle
              className="size-5"
              strokeWidth={1.75}
              aria-hidden="true"
            />
            Create New Video
          </Link>
        </div>

        <div className="space-y-8">
          {/* Empty state hero */}
          <section className="rounded-neu-2xl bg-surface p-8 text-center shadow-neu-raised-lg sm:p-10">
            <div className="mx-auto mb-6 flex size-16 items-center justify-center rounded-neu-md bg-primary text-primary-foreground shadow-neu-glow">
              <Film
                className="size-7 text-primary-foreground"
                aria-hidden="true"
                strokeWidth={1.75}
              />
            </div>
            <h2 className="text-2xl font-bold tracking-tight text-foreground">
              No projects yet
            </h2>
            <p className="mx-auto mt-2 max-w-sm text-sm font-medium leading-relaxed text-muted-foreground">
              Follow the quick onboarding steps below to start transforming your
              footage.
            </p>
            <Link
              href="/projects/new"
              className="mt-6 inline-flex min-h-11 items-center justify-center rounded-neu bg-primary px-6 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
            >
              Create my first reel
            </Link>
          </section>

          {/* Onboarding step cards */}
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              {
                step: 1,
                title: "Upload Video",
                desc: "Choose an MP4, MOV, or WebM video file. The system will preflight validate your audio track.",
              },
              {
                step: 2,
                title: "AI Drafting",
                desc: "Pick a storytelling style. AI generates captions, indexes B-roll, and maps beats.",
              },
              {
                step: 3,
                title: "Fine-Tune",
                desc: "Edit captions manually, drag-and-drop overlays, and layer background music.",
              },
              {
                step: 4,
                title: "Export & Download",
                desc: "Render your edit at up to 1080p and download the finished video.",
              },
            ].map(({ step, title, desc }) => (
              <div
                key={step}
                className="flex flex-col gap-3 rounded-neu-lg bg-surface p-5 shadow-neu-raised"
              >
                <div className="flex size-10 select-none items-center justify-center rounded-full bg-surface text-sm font-bold tabular-nums text-primary shadow-neu-inset-sm">
                  {step}
                </div>
                <h3 className="text-sm font-semibold text-foreground">
                  {title}
                </h3>
                <p className="text-xs font-medium leading-relaxed text-muted-foreground">
                  {desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // Bento layout splits
  const featuredProject = searchQuery.trim() ? null : sortedProjects[0];
  const bentoOtherProjects = searchQuery.trim()
    ? filteredProjects
    : sortedProjects.slice(1);

  return (
    <div className="flex flex-col gap-8">
      {/* Header */}
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <span
            className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-xs font-semibold text-foreground shadow-neu-raised-sm"
            data-testid="workspace-name"
          >
            {workspaceName}
          </span>
          <h1 className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            Your Projects
          </h1>
          <p className="mt-1 text-sm font-medium text-muted-foreground">
            AI-powered video editing pipeline
          </p>
        </div>
        <Link
          href="/projects/new"
          className="inline-flex min-h-12 shrink-0 items-center justify-center gap-2 rounded-neu bg-primary px-6 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
        >
          <PlusCircle
            className="size-5"
            strokeWidth={1.75}
            aria-hidden="true"
          />
          Create New Video
        </Link>
      </div>

      {/* Bento Grid */}
      {!searchQuery.trim() && featuredProject && (
        <div className="grid gap-6 lg:grid-cols-3">
          {/* Featured Recent Project */}
          <div className="group overflow-hidden rounded-neu-lg bg-surface shadow-neu-raised transition-shadow duration-200 hover:shadow-neu-raised-lg lg:col-span-2">
            {/* Thumbnail */}
            <div className="relative flex h-40 items-center justify-center bg-ink">
              <span className="absolute right-3 top-3">
                <span
                  className={cn(
                    getStatusBadgeClass(
                      featuredProject.uploaded_videos?.[0]?.status,
                    ),
                    "inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold capitalize shadow-neu-inset-sm",
                  )}
                >
                  {featuredProject.uploaded_videos?.[0]?.status ?? "draft"}
                </span>
              </span>
              <div className="flex size-14 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow transition-transform group-hover:scale-105">
                <Play
                  className="size-6 fill-current text-primary-foreground"
                  strokeWidth={1.75}
                />
              </div>
            </div>

            {/* Card body */}
            <div className="flex flex-1 flex-col justify-between p-5">
              <div className="space-y-1">
                <h3 className="truncate text-xl font-bold tracking-tight text-foreground transition-colors group-hover:text-primary">
                  <Link
                    href={`/projects/${featuredProject.id}`}
                    className="focus:underline focus:outline-none"
                  >
                    {featuredProject.title}
                  </Link>
                </h3>
                <p className="truncate text-xs font-medium text-muted-foreground">
                  {featuredProject.uploaded_videos?.[0]?.original_filename ??
                    "Waiting for source video upload..."}
                </p>
              </div>

              <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-t border-hairline pt-4">
                {/* Metadata chips */}
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-xs font-semibold tabular-nums text-foreground shadow-neu-raised-sm">
                    <Play
                      className="size-3"
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                    {formatDuration(
                      featuredProject.uploaded_videos?.[0]?.duration_ms ?? null,
                    )}
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-surface px-3 py-1 text-xs font-semibold tabular-nums text-foreground shadow-neu-raised-sm">
                    <Clock
                      className="size-3"
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                    {new Date(featuredProject.updated_at).toLocaleDateString()}
                  </span>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2">
                  <Link
                    href={`/projects/${featuredProject.id}`}
                    className="inline-flex items-center justify-center rounded-neu bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
                  >
                    Open Reel
                  </Link>
                  <button
                    onClick={() =>
                      setProjectToDelete({
                        id: featuredProject.id,
                        title: featuredProject.title,
                      })
                    }
                    className="inline-flex items-center justify-center rounded-neu bg-surface px-3 py-2 text-xs font-semibold text-danger shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed"
                    aria-label={`Delete ${featuredProject.title}`}
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Workspace Stats Card */}
          <div className="flex flex-col justify-between rounded-neu-lg bg-surface p-5 shadow-neu-raised transition-shadow duration-200 hover:shadow-neu-raised-lg">
            <div>
              <h3 className="text-xs font-semibold text-primary">
                Workspace Stats
              </h3>
              <p className="mt-0.5 text-xs font-medium text-muted-foreground">
                Status overview of reels.
              </p>

              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between rounded-neu-md bg-surface p-3 shadow-neu-inset-sm">
                  <span className="text-xs font-semibold text-muted-foreground">
                    Total Projects
                  </span>
                  <span className="text-sm font-bold tabular-nums text-foreground">
                    {localProjects.length}
                  </span>
                </div>
                <div className="flex items-center justify-between rounded-neu-md bg-surface p-3 shadow-neu-inset-sm">
                  <span className="text-xs font-semibold text-muted-foreground">
                    Ready to Render
                  </span>
                  <span className="text-sm font-bold tabular-nums text-foreground">
                    {
                      localProjects.filter(
                        (p) => p.uploaded_videos?.[0]?.status === "ready",
                      ).length
                    }
                  </span>
                </div>
                <div className="flex items-center justify-between rounded-neu-md bg-surface p-3 shadow-neu-inset-sm">
                  <span className="text-xs font-semibold text-muted-foreground">
                    Draft Phase
                  </span>
                  <span className="text-sm font-bold tabular-nums text-foreground">
                    {
                      localProjects.filter(
                        (p) =>
                          !p.uploaded_videos?.[0] ||
                          p.uploaded_videos?.[0]?.status !== "ready",
                      ).length
                    }
                  </span>
                </div>
              </div>

              {/* Usage progress — real storage quota from the API (B23). */}
              <div className="mt-4">
                <div className="mb-1.5 flex justify-between text-xs font-medium text-muted-foreground">
                  <span>Storage used</span>
                  <span className="tabular-nums">
                    {storage?.percentLabel ?? "Not metered"}
                  </span>
                </div>
                {storage?.metered && storage.percent !== null ? (
                  <div
                    className="mt-1 h-4 overflow-hidden rounded-full bg-surface shadow-neu-inset-sm"
                    role="progressbar"
                    aria-label={storage.ariaLabel}
                    aria-valuenow={storage.percent}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <div
                      className="h-full rounded-full bg-primary transition-[width] duration-[600ms]"
                      style={{ width: `${storage.percent}%` }}
                    />
                  </div>
                ) : (
                  <p className="mt-1 text-xs font-medium text-muted-foreground">
                    {storage?.label ?? "Storage usage unavailable"}
                  </p>
                )}
                {storage?.metered ? (
                  <p className="mt-1 text-xs font-medium text-muted-foreground">
                    {storage.label}
                  </p>
                ) : null}
              </div>
            </div>

            <Link
              href="/projects/new"
              className="mt-6 block w-full rounded-neu bg-primary py-3 text-center text-xs font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
            >
              + Create New Reel
            </Link>
          </div>
        </div>
      )}

      {/* Grid of Other Projects */}
      <div className="space-y-4 border-t border-hairline pt-6">
        {/* Search + Sort bar */}
        <div className="flex flex-col gap-4 rounded-neu-xl bg-surface px-3 py-2 shadow-neu-raised sm:flex-row sm:items-center sm:justify-between">
          <div className="relative max-w-md flex-1">
            <label htmlFor="project-search" className="sr-only">
              Search projects
            </label>
            <Search
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              strokeWidth={1.75}
            />
            <input
              id="project-search"
              type="text"
              placeholder="Search projects by title..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-11 w-full rounded-neu border border-control bg-surface pl-9 pr-4 text-sm font-medium text-foreground shadow-neu-inset-sm transition focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
            />
          </div>

          <div className="inline-flex gap-1 rounded-full bg-surface p-1 shadow-neu-inset-sm">
            <button
              type="button"
              aria-pressed={sortBy === "updated"}
              onClick={() => setSortBy("updated")}
              className={cn(
                "min-h-10 rounded-full px-4 text-sm font-semibold transition-shadow duration-200",
                sortBy === "updated"
                  ? "bg-surface text-foreground shadow-neu-raised-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              Recent
            </button>
            <button
              type="button"
              aria-pressed={sortBy === "title"}
              onClick={() => setSortBy("title")}
              className={cn(
                "min-h-10 rounded-full px-4 text-sm font-semibold transition-shadow duration-200",
                sortBy === "title"
                  ? "bg-surface text-foreground shadow-neu-raised-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              A–Z
            </button>
          </div>
        </div>

        {bentoOtherProjects.length > 0 ? (
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {bentoOtherProjects.map((project, index) => {
              const upload = project.uploaded_videos?.[0];
              const warnings = upload?.metadata?.warnings ?? [];
              return (
                <div
                  key={project.id}
                  className="group overflow-hidden rounded-neu-lg bg-surface shadow-neu-raised transition-shadow duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
                >
                  {/* Thumbnail */}
                  <div className="relative flex h-36 items-center justify-center bg-ink">
                    {/* Status badge overlay */}
                    <span className="absolute right-2 top-2">
                      <span
                        className={cn(
                          getStatusBadgeClass(upload?.status),
                          "inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold capitalize shadow-neu-inset-sm",
                        )}
                      >
                        {upload?.status ?? "draft"}
                      </span>
                    </span>
                    <div className="flex size-10 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow transition-transform group-hover:scale-105">
                      <Play
                        className="size-4 fill-current text-primary-foreground"
                        strokeWidth={1.75}
                      />
                    </div>
                  </div>

                  {/* Content */}
                  <div className="p-4">
                    <h3 className="truncate text-sm font-semibold text-foreground transition-colors hover:text-primary">
                      <Link
                        href={`/projects/${project.id}`}
                        className="focus:underline focus:outline-none"
                      >
                        {project.title}
                      </Link>
                    </h3>
                    <p className="mt-0.5 truncate text-xs font-medium text-muted-foreground">
                      {upload?.original_filename ?? "Waiting for source..."}
                    </p>

                    {warnings.length > 0 && (
                      <div className="mt-2 flex items-center gap-1 text-xs font-semibold text-warning">
                        <AlertTriangle
                          className="size-3"
                          strokeWidth={1.75}
                          aria-hidden="true"
                        />
                        {warnings.length} issues found
                      </div>
                    )}

                    {/* Footer */}
                    <div className="mt-3 flex items-center justify-between border-t border-hairline pt-3">
                      {/* Metadata chips */}
                      <div className="flex items-center gap-1.5">
                        <span className="inline-flex items-center gap-1 rounded-full bg-surface px-2 py-0.5 text-[10px] font-semibold tabular-nums text-foreground shadow-neu-raised-sm">
                          <Play
                            className="size-2.5"
                            strokeWidth={1.75}
                            aria-hidden="true"
                          />
                          {formatDuration(upload?.duration_ms ?? null)}
                        </span>
                        <span className="inline-flex items-center gap-1 rounded-full bg-surface px-2 py-0.5 text-[10px] font-semibold tabular-nums text-foreground shadow-neu-raised-sm">
                          <Clock
                            className="size-2.5"
                            strokeWidth={1.75}
                            aria-hidden="true"
                          />
                          {new Date(project.updated_at).toLocaleDateString()}
                        </span>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-1.5">
                        <Link
                          href={`/projects/${project.id}`}
                          className="inline-flex items-center justify-center rounded-neu bg-primary px-2.5 py-1 text-[10px] font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
                        >
                          Open
                        </Link>
                        <button
                          onClick={() =>
                            setProjectToDelete({
                              id: project.id,
                              title: project.title,
                            })
                          }
                          className="inline-flex items-center justify-center rounded-neu bg-surface px-2 py-1 text-[10px] font-semibold text-danger shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed"
                          aria-label={`Delete ${project.title}`}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="rounded-neu-lg bg-surface p-8 text-center text-sm font-medium text-muted-foreground shadow-neu-raised">
            No projects found matching your search.
          </p>
        )}
      </div>

      {/* Delete confirmation modal */}
      {projectToDelete && (
        <>
          <button
            type="button"
            aria-label="Close delete confirmation overlay"
            className="fixed inset-0 z-45 h-full w-full cursor-default bg-background/60 text-left backdrop-blur-sm"
            onClick={handleCancelDelete}
          />
          <div
            ref={focusTrapRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-dialog-title"
            className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg animate-scale-in"
          >
            {/* Warning icon */}
            <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-neu text-warning shadow-neu-inset-sm">
              <ShieldAlert
                className="size-6"
                strokeWidth={1.75}
                aria-hidden="true"
              />
            </div>

            <h3
              id="delete-dialog-title"
              className="mt-2 text-center text-xl font-bold tracking-tight text-foreground"
            >
              Confirm Delete
            </h3>
            <p className="mt-2 text-center text-sm font-medium leading-relaxed text-muted-foreground">
              Are you sure you want to permanently delete project{" "}
              <strong className="text-foreground">
                &quot;{projectToDelete.title}&quot;
              </strong>
              ? This will remove all captions, visual analyses, and cached
              exports. This action is irreversible.
            </p>

            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                disabled={deleteLoading}
                onClick={handleCancelDelete}
                className="inline-flex items-center justify-center rounded-neu bg-surface px-4 py-2 text-xs font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised active:shadow-neu-pressed disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={deleteLoading}
                onClick={confirmDelete}
                aria-busy={deleteLoading}
                className="inline-flex items-center justify-center rounded-neu bg-danger px-4 py-2 text-xs font-semibold text-danger-foreground shadow-neu-raised transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
              >
                {deleteLoading ? (
                  <>
                    <Loader2
                      className="mr-1.5 size-3.5 animate-spin"
                      strokeWidth={1.75}
                    />
                    Deleting...
                  </>
                ) : (
                  "Delete Project"
                )}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
