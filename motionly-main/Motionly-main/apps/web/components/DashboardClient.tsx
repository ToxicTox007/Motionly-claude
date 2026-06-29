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
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "./toast";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { Button } from "./button";

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
  workspaceName: string;
}

function formatDuration(durationMs: number | null) {
  if (!durationMs) return "0:00";
  const seconds = Math.round(durationMs / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function DashboardClient({
  initialProjects,
  workspaceName,
}: DashboardClientProps) {
  const router = useRouter();
  const { toast } = useToast();
  const [localProjects, setLocalProjects] =
    useState<Project[]>(initialProjects);
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
      const formData = new FormData();
      formData.set("projectId", projectToDelete.id);

      const { deleteProject } = await import("../app/(app)/projects/actions");
      await deleteProject(formData);

      toast({
        title: "Project Deleted",
        description: `"${projectToDelete.title}" has been deleted.`,
        variant: "success",
      });

      setLocalProjects((current) =>
        current.filter((p) => p.id !== projectToDelete.id),
      );
      setProjectToDelete(null);
    } catch (error) {
      const errMsg = (error as Error).message;
      if (errMsg === "NEXT_REDIRECT") {
        setLocalProjects((current) =>
          current.filter((p) => p.id !== projectToDelete.id),
        );
        setProjectToDelete(null);
        router.refresh();
        return;
      }
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

  // Onboarding card deck when empty
  if (localProjects.length === 0) {
    return (
      <div className="flex flex-col gap-8">
        <div className="pixel-panel pixel-corners pixel-grid-fine flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-black uppercase tracking-widest text-primary">
              {workspaceName}
            </p>
            <h2 className="text-3xl font-black tracking-tight">
              Your Projects
            </h2>
            <p className="text-muted-foreground mt-1">
              Manage and edit your AI-powered reels.
            </p>
          </div>
          <Link
            href="/projects/new"
            className="pixel-button inline-flex h-12 shrink-0 items-center justify-center bg-primary px-6 text-sm font-bold text-primary-foreground"
          >
            <PlusCircle className="mr-2 size-5" />
            Create New Video
          </Link>
        </div>

        <div className="space-y-8">
          <section className="pixel-panel pixel-corners pixel-grid-fine glass-surface overflow-hidden px-6 py-14 text-center">
            <div className="relative mx-auto flex size-14 items-center justify-center border border-border bg-surface text-foreground shadow-soft">
              <span className="absolute -right-2 -top-2 size-2 bg-primary shadow-[8px_8px_0_hsl(var(--primary)/0.28)]" />
              <Film className="size-6" aria-hidden="true" />
            </div>
            <h2 className="mt-5 text-xl font-black">No projects yet</h2>
            <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
              Follow the quick onboarding steps below to start transforming your
              footage.
            </p>
            <Link
              href="/projects/new"
              className="pixel-button inline-flex h-11 items-center justify-center bg-primary px-6 text-xs font-bold text-primary-foreground mt-6"
            >
              Create my first reel
            </Link>
          </section>

          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <div className="pixel-panel pixel-corners p-5 bg-surface/30 flex flex-col gap-3">
              <div className="flex size-9 items-center justify-center bg-primary/10 text-primary border border-primary/20 text-xs font-black select-none">
                1
              </div>
              <h3 className="text-sm font-black uppercase tracking-wider">
                Upload Video
              </h3>
              <p className="text-[11px] text-muted-foreground leading-relaxed font-medium">
                Choose an MP4, MOV, or WebM video file. The system will
                preflight validate your audio track.
              </p>
            </div>

            <div className="pixel-panel pixel-corners p-5 bg-surface/30 flex flex-col gap-3">
              <div className="flex size-9 items-center justify-center bg-primary/10 text-primary border border-primary/20 text-xs font-black select-none">
                2
              </div>
              <h3 className="text-sm font-black uppercase tracking-wider">
                AI Drafting
              </h3>
              <p className="text-[11px] text-muted-foreground leading-relaxed font-medium">
                Pick a storytelling style. AI generates captions, indexes
                B-roll, and maps beats.
              </p>
            </div>

            <div className="pixel-panel pixel-corners p-5 bg-surface/30 flex flex-col gap-3">
              <div className="flex size-9 items-center justify-center bg-primary/10 text-primary border border-primary/20 text-xs font-black select-none">
                3
              </div>
              <h3 className="text-sm font-black uppercase tracking-wider">
                Fine-Tune
              </h3>
              <p className="text-[11px] text-muted-foreground leading-relaxed font-medium">
                Edit captions manually, drag-and-drop overlays, and layer
                background music.
              </p>
            </div>

            <div className="pixel-panel pixel-corners p-5 bg-surface/30 flex flex-col gap-3">
              <div className="flex size-9 items-center justify-center bg-primary/10 text-primary border border-primary/20 text-xs font-black select-none">
                4
              </div>
              <h3 className="text-sm font-black uppercase tracking-wider">
                Export & Post
              </h3>
              <p className="text-[11px] text-muted-foreground leading-relaxed font-medium">
                Compile video drafts up to 1080p and publish directly using
                OAuth creator accounts.
              </p>
            </div>
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
      <div className="pixel-panel pixel-corners pixel-grid-fine flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-black uppercase tracking-widest text-primary">
            {workspaceName}
          </p>
          <h2 className="text-3xl font-black tracking-tight">Your Projects</h2>
          <p className="text-muted-foreground mt-1">
            Manage and edit your AI-powered reels.
          </p>
        </div>
        <Link
          href="/projects/new"
          className="pixel-button inline-flex h-12 shrink-0 items-center justify-center bg-primary px-6 text-sm font-bold text-primary-foreground"
        >
          <PlusCircle className="mr-2 size-5" />
          Create New Video
        </Link>
      </div>

      {/* Bento Grid */}
      {!searchQuery.trim() && featuredProject && (
        <div className="grid gap-6 lg:grid-cols-3">
          {/* Featured Recent Project */}
          <div className="lg:col-span-2 pixel-panel pixel-corners pixel-grid-fine p-6 flex flex-col justify-between min-h-[320px] bg-surface hover:border-primary/30 transition-colors duration-150 relative group">
            <span className="absolute top-3 right-3 border px-3 py-1 text-[9px] font-black uppercase tracking-widest bg-primary/10 text-primary border-primary/30 select-none">
              Featured Recent
            </span>
            <div className="space-y-4">
              <div className="flex size-14 items-center justify-center border border-primary/20 bg-primary/5 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                <Film className="size-8" />
              </div>
              <div className="max-w-md">
                <h3 className="text-2xl font-black truncate group-hover:text-primary transition-colors">
                  <Link
                    href={`/projects/${featuredProject.id}`}
                    className="focus:outline-none focus:underline"
                  >
                    {featuredProject.title}
                  </Link>
                </h3>
                <p className="text-xs text-muted-foreground mt-1 truncate">
                  {featuredProject.uploaded_videos?.[0]?.original_filename ??
                    "Waiting for source video upload..."}
                </p>
              </div>
            </div>

            <div className="mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-border pt-4 text-xs font-bold text-muted-foreground/70">
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
                <span>
                  Length:{" "}
                  {formatDuration(
                    featuredProject.uploaded_videos?.[0]?.duration_ms ?? null,
                  )}
                </span>
                <span className="flex items-center gap-1">
                  <Clock className="size-3.5" />
                  {new Date(featuredProject.updated_at).toLocaleDateString()}
                </span>
                <span className="capitalize">
                  Status:{" "}
                  {featuredProject.uploaded_videos?.[0]?.status ?? "draft"}
                </span>
              </div>

              <div className="flex items-center gap-3">
                <Link
                  href={`/projects/${featuredProject.id}`}
                  className="pixel-button bg-primary text-primary-foreground px-4 py-2 text-xs font-bold"
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
                  className="pixel-button bg-surface text-danger border-danger-strong hover:bg-danger/5 px-3 py-2 text-xs font-bold"
                  aria-label={`Delete ${featuredProject.title}`}
                >
                  Delete
                </button>
              </div>
            </div>
          </div>

          {/* Quick-start & Stats Card */}
          <div className="pixel-panel pixel-corners p-6 flex flex-col justify-between bg-surface/50 border-dashed">
            <div>
              <h3 className="text-xs font-black uppercase tracking-widest text-primary">
                Workspace Stats
              </h3>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Status overview of reels.
              </p>

              <div className="mt-6 space-y-3 font-bold text-xs">
                <div className="flex justify-between border-b border-border/50 pb-1.5">
                  <span className="text-muted-foreground">Total Projects</span>
                  <span>{localProjects.length}</span>
                </div>
                <div className="flex justify-between border-b border-border/50 pb-1.5">
                  <span className="text-muted-foreground">Ready to Render</span>
                  <span>
                    {
                      localProjects.filter(
                        (p) => p.uploaded_videos?.[0]?.status === "ready",
                      ).length
                    }
                  </span>
                </div>
                <div className="flex justify-between border-b border-border/50 pb-1.5">
                  <span className="text-muted-foreground">Draft Phase</span>
                  <span>
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
            </div>

            <Link
              href="/projects/new"
              className="pixel-button bg-primary text-primary-foreground text-center py-3 text-xs mt-6 font-bold"
            >
              + Create New Reel
            </Link>
          </div>
        </div>
      )}

      {/* Grid of Other Projects */}
      <div className="space-y-4 border-t border-border pt-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative flex-1 max-w-md">
            <label htmlFor="project-search" className="sr-only">
              Search projects
            </label>
            <input
              id="project-search"
              type="text"
              placeholder="Search projects by title..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pixel-input w-full h-11 px-4 text-xs focus:outline-none"
            />
          </div>

          <div className="flex items-center gap-3">
            <label
              htmlFor="project-sort"
              className="text-2xs font-bold text-muted-foreground uppercase tracking-wider"
            >
              Sort
            </label>
            <select
              id="project-sort"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as "updated" | "title")}
              className="pixel-input h-11 px-3 text-xs font-bold bg-background focus:outline-none cursor-pointer"
            >
              <option value="updated">Recent Activity</option>
              <option value="title">Alphabetical</option>
            </select>
          </div>
        </div>

        {bentoOtherProjects.length > 0 ? (
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {bentoOtherProjects.map((project) => {
              const upload = project.uploaded_videos?.[0];
              const warnings = upload?.metadata?.warnings ?? [];
              return (
                <div
                  key={project.id}
                  className="pixel-panel pixel-corners group relative flex min-h-[190px] flex-col justify-between p-5 hover:-translate-y-0.5 hover:border-primary/45 transition-all duration-150 bg-surface shadow-soft"
                >
                  <div>
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="truncate font-bold text-sm group-hover:text-primary transition-colors max-w-[200px]">
                        <Link
                          href={`/projects/${project.id}`}
                          className="focus:outline-none focus:underline"
                        >
                          {project.title}
                        </Link>
                      </h3>
                      <span
                        className={cn(
                          "border px-2 py-0.5 text-[9px] font-extrabold uppercase tracking-widest",
                          upload?.status === "ready"
                            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                            : "border-border bg-muted text-muted-foreground",
                        )}
                      >
                        {upload?.status ?? "draft"}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-1 truncate">
                      {upload?.original_filename ?? "Waiting for source..."}
                    </p>
                  </div>

                  {warnings.length ? (
                    <div className="flex gap-1 items-center text-[10px] text-amber-600 font-bold uppercase tracking-tight">
                      <AlertTriangle className="size-3" />
                      {warnings.length} issues found
                    </div>
                  ) : null}

                  <div className="mt-4 flex items-center justify-between border-t border-border/80 pt-3 text-2xs font-bold text-muted-foreground/70">
                    <div className="flex items-center gap-3">
                      <span>{formatDuration(upload?.duration_ms ?? null)}</span>
                      <span className="flex items-center gap-0.5">
                        <Clock className="size-3" />
                        {new Date(project.updated_at).toLocaleDateString()}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <Link
                        href={`/projects/${project.id}`}
                        className="pixel-button bg-primary text-primary-foreground px-2 py-1 text-[10px] font-bold"
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
                        className="pixel-button bg-surface text-danger border-danger-strong hover:bg-danger/5 px-2 py-1 text-[10px] font-bold"
                        aria-label={`Delete ${project.title}`}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="border border-border bg-muted/30 p-5 text-center text-xs font-bold text-muted-foreground">
            No projects found matching your search.
          </p>
        )}
      </div>

      {/* Motionly-style delete confirmation modal overlay */}
      {projectToDelete && (
        <>
          <button
            type="button"
            aria-label="Close delete confirmation overlay"
            className="fixed inset-0 z-45 cursor-default bg-foreground/30 backdrop-blur-[2px] w-full h-full text-left"
            onClick={handleCancelDelete}
          />
          <div
            ref={focusTrapRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-dialog-title"
            className="pixel-panel-strong fixed left-1/2 top-1/2 z-50 w-[min(24rem,90vw)] -translate-x-1/2 -translate-y-1/2 p-5 bg-surface text-foreground shadow-[-10px_10px_0_hsl(var(--shadow-color)/0.12)] border-danger-strong"
          >
            <div className="flex items-center gap-2 text-danger border-b border-border pb-3 mb-3">
              <ShieldAlert className="size-5" />
              <h3
                id="delete-dialog-title"
                className="text-sm font-black uppercase tracking-wider"
              >
                Confirm Delete
              </h3>
            </div>
            <p className="text-xs font-bold leading-relaxed text-muted-foreground">
              Are you sure you want to permanently delete project{" "}
              <strong className="text-foreground">
                &quot;{projectToDelete.title}&quot;
              </strong>
              ? This will remove all captions, visual analyses, and cached
              exports. This action is irreversible.
            </p>
            <div className="mt-5 flex justify-end gap-3 font-bold text-xs">
              <Button
                type="button"
                variant="secondary"
                disabled={deleteLoading}
                onClick={handleCancelDelete}
                className="px-4 py-2 min-h-10 text-xs"
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="primary"
                disabled={deleteLoading}
                onClick={confirmDelete}
                aria-busy={deleteLoading}
                className="px-4 py-2 min-h-10 text-xs bg-danger text-white border-danger-strong hover:bg-danger/90 hover:brightness-100"
              >
                {deleteLoading ? (
                  <>
                    <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                    Deleting...
                  </>
                ) : (
                  "Delete Project"
                )}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
