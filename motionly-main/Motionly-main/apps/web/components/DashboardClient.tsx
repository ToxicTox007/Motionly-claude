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
import { useToast } from "./toast";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { Button } from "./button";
import { deleteProject } from "../app/(app)/projects/actions";

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

function getStatusBadgeClass(status: string | undefined) {
  switch (status) {
    case "ready":
      return "bg-emerald-500 text-white";
    case "processing":
      return "bg-amber-500 text-black";
    case "error":
      return "bg-danger text-white";
    default:
      return "bg-muted text-muted-foreground";
  }
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
        {/* Header */}
        <div className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <span className="px-2 py-0.5 font-bold font-mono border-2 border-foreground rounded-md bg-primary text-primary-foreground mb-3 inline-flex">
              Your Workspace
            </span>
            <h1 className="text-4xl font-black uppercase tracking-tight text-foreground">
              Your Projects
            </h1>
            <p className="text-muted-foreground mt-1 text-sm font-mono">
              AI-powered video editing pipeline
            </p>
          </div>
          <Link
            href="/projects/new"
            className="inline-flex h-12 shrink-0 items-center justify-center bg-primary border-2 border-foreground text-primary-foreground rounded-md shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[5px_5px_0px_#000] dark:hover:shadow-[5px_5px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] px-6 text-sm font-bold uppercase tracking-wider"
          >
            <PlusCircle className="mr-2 size-5" strokeWidth={1.5} />
            Create New Video
          </Link>
        </div>

        <div className="space-y-8">
          {/* Empty state hero */}
          <section className="bg-card rounded-md border-4 border-foreground shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] p-10 text-center">
            <div className="size-16 rounded-md border-2 border-foreground bg-primary text-primary-foreground flex items-center justify-center mx-auto mb-6 shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff]">
              <Film
                className="size-7 text-primary-foreground"
                aria-hidden="true"
                strokeWidth={2}
              />
            </div>
            <h2 className="text-2xl font-black uppercase text-foreground">
              No projects yet
            </h2>
            <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
              Follow the quick onboarding steps below to start transforming your
              footage.
            </p>
            <Link
              href="/projects/new"
              className="inline-flex h-11 items-center justify-center bg-primary border-2 border-foreground text-primary-foreground rounded-md hover:shadow-[5px_5px_0px_#000] dark:hover:shadow-[5px_5px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] px-6 mt-6 font-bold uppercase tracking-wider"
            >
              Create my first reel
            </Link>
          </section>

          {/* Onboarding step cards */}
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-8">
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
                className="bg-card rounded-md border-2 border-foreground shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] p-4 flex flex-col gap-3"
              >
                <div className="size-8 rounded-md border-2 border-foreground bg-primary text-primary-foreground flex items-center justify-center text-sm font-bold font-mono mb-3 select-none shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                  {step}
                </div>
                <h3 className="text-sm font-black uppercase text-foreground tracking-wider">
                  {title}
                </h3>
                <p className="text-[11px] text-muted-foreground leading-relaxed font-mono">
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
      <div className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <span className="px-2 py-0.5 font-bold font-mono border-2 border-foreground rounded-md bg-primary text-primary-foreground mb-3 inline-flex">
            Your Workspace
          </span>
          <h1 className="text-4xl font-black uppercase tracking-tight text-foreground">
            Your Projects
          </h1>
          <p className="text-muted-foreground mt-1 text-sm font-mono">
            AI-powered video editing pipeline
          </p>
        </div>
        <Link
          href="/projects/new"
          className="inline-flex h-12 shrink-0 items-center justify-center bg-primary border-2 border-foreground text-primary-foreground rounded-md shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[5px_5px_0px_#000] dark:hover:shadow-[5px_5px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] px-6 text-sm font-bold uppercase tracking-wider"
        >
          <PlusCircle className="mr-2 size-5" strokeWidth={1.5} />
          Create New Video
        </Link>
      </div>

      {/* Bento Grid */}
      {!searchQuery.trim() && featuredProject && (
        <div className="grid gap-6 lg:grid-cols-3">
          {/* Featured Recent Project */}
          <div className="lg:col-span-2 bg-card rounded-md border-2 border-foreground shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[6px_6px_0px_#000] dark:hover:shadow-[6px_6px_0px_#fff] overflow-hidden group">
            {/* Thumbnail */}
            <div className="h-40 bg-muted border-b-2 border-foreground flex items-center justify-center relative">
              <span className="absolute top-3 right-3">
                <span
                  className={cn(
                    getStatusBadgeClass(
                      featuredProject.uploaded_videos?.[0]?.status,
                    ),
                    "rounded-md px-2 py-0.5 font-bold font-mono border-2 border-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]",
                  )}
                >
                  {featuredProject.uploaded_videos?.[0]?.status ?? "draft"}
                </span>
              </span>
              <div className="size-14 rounded-md border-2 border-foreground bg-primary text-primary-foreground flex items-center justify-center shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] transition-transform group-hover:scale-110">
                <Play
                  className="size-6 fill-current text-primary-foreground"
                  strokeWidth={2}
                />
              </div>
            </div>

            {/* Card body */}
            <div className="p-5 flex flex-col justify-between flex-1">
              <div className="space-y-1">
                <h3 className="text-xl font-bold uppercase tracking-tight text-foreground truncate group-hover:text-primary transition-colors">
                  <Link
                    href={`/projects/${featuredProject.id}`}
                    className="focus:outline-none focus:underline"
                  >
                    {featuredProject.title}
                  </Link>
                </h3>
                <p className="text-xs font-mono text-muted-foreground truncate">
                  {featuredProject.uploaded_videos?.[0]?.original_filename ??
                    "Waiting for source video upload..."}
                </p>
              </div>

              <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-t-2 border-foreground pt-4">
                {/* Metadata chips */}
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-md border-2 border-foreground bg-muted px-2.5 py-1 text-xs font-mono font-bold text-foreground inline-flex items-center gap-1 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                    <Play className="size-3" strokeWidth={2} />
                    {formatDuration(
                      featuredProject.uploaded_videos?.[0]?.duration_ms ?? null,
                    )}
                  </span>
                  <span className="rounded-md border-2 border-foreground bg-muted px-2.5 py-1 text-xs font-mono font-bold text-foreground inline-flex items-center gap-1 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                    <Clock className="size-3" strokeWidth={2} />
                    {new Date(featuredProject.updated_at).toLocaleDateString()}
                  </span>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2">
                  <Link
                    href={`/projects/${featuredProject.id}`}
                    className="bg-primary text-primary-foreground border-2 border-foreground rounded-md shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] text-xs px-4 py-2 font-bold uppercase tracking-wider"
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
                    className="bg-danger text-white border-2 border-foreground rounded-md shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] text-xs px-3 py-2 font-bold uppercase tracking-wider"
                    aria-label={`Delete ${featuredProject.title}`}
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Workspace Stats Card */}
          <div className="bg-surface rounded-md border-2 border-foreground shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] p-5 flex flex-col justify-between transition-all duration-[220ms] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[6px_6px_0px_#000] dark:hover:shadow-[6px_6px_0px_#fff]">
            <div>
              <h3 className="text-xs font-mono font-black uppercase tracking-widest text-primary">
                Workspace Stats
              </h3>
              <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                Status overview of reels.
              </p>

              <div className="mt-4 space-y-3">
                <div className="rounded-md border-2 border-foreground bg-muted p-3 flex justify-between items-center shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                  <span className="text-xs font-bold font-mono text-muted-foreground">
                    Total Projects
                  </span>
                  <span className="text-sm font-bold font-mono">
                    {localProjects.length}
                  </span>
                </div>
                <div className="rounded-md border-2 border-foreground bg-muted p-3 flex justify-between items-center shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                  <span className="text-xs font-bold font-mono text-muted-foreground">
                    Ready to Render
                  </span>
                  <span className="text-sm font-bold font-mono">
                    {
                      localProjects.filter(
                        (p) => p.uploaded_videos?.[0]?.status === "ready",
                      ).length
                    }
                  </span>
                </div>
                <div className="rounded-md border-2 border-foreground bg-muted p-3 flex justify-between items-center shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                  <span className="text-xs font-bold font-mono text-muted-foreground">
                    Draft Phase
                  </span>
                  <span className="text-sm font-bold font-mono">
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

              {/* Usage progress */}
              <div className="mt-4">
                <div className="flex justify-between text-xs font-mono text-muted-foreground mb-1.5">
                  <span>Storage used</span>
                  <span>{Math.min(localProjects.length * 10, 100)}%</span>
                </div>
                <div className="h-4 border-2 border-foreground bg-muted rounded-md overflow-hidden shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] mt-1">
                  <div
                    className="bg-primary h-full transition-all"
                    style={{
                      width: `${Math.min(localProjects.length * 10, 100)}%`,
                    }}
                  />
                </div>
              </div>
            </div>

            <Link
              href="/projects/new"
              className="w-full bg-primary text-primary-foreground border-2 border-foreground rounded-md shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] text-center py-3 text-xs mt-6 font-bold uppercase tracking-wider block"
            >
              + Create New Reel
            </Link>
          </div>
        </div>
      )}

      {/* Grid of Other Projects */}
      <div className="space-y-4 border-t-2 border-foreground pt-6">
        {/* Search + Sort bar */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative flex-1 max-w-md">
            <label htmlFor="project-search" className="sr-only">
              Search projects
            </label>
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none"
              strokeWidth={1.5}
            />
            <input
              id="project-search"
              type="text"
              placeholder="Search projects by title..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full h-11 pl-9 pr-4 text-xs font-mono rounded-md border-2 border-foreground bg-background text-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] focus:outline-none focus:shadow-none focus:translate-x-0.5 focus:translate-y-0.5 transition-all"
            />
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setSortBy("updated")}
              className={cn(
                "text-xs px-3 py-2 font-mono font-bold border-2 border-foreground rounded-md transition-all duration-[100ms]",
                sortBy === "updated"
                  ? "bg-primary text-primary-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
                  : "bg-background text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none",
              )}
            >
              Recent
            </button>
            <button
              onClick={() => setSortBy("title")}
              className={cn(
                "text-xs px-3 py-2 font-mono font-bold border-2 border-foreground rounded-md transition-all duration-[100ms]",
                sortBy === "title"
                  ? "bg-primary text-primary-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
                  : "bg-background text-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none",
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
                  className="bg-card rounded-md border-2 border-foreground shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] overflow-hidden group transition-all duration-[220ms] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[6px_6px_0px_#000] dark:hover:shadow-[6px_6px_0px_#fff]"
                >
                  {/* Thumbnail */}
                  <div className="h-36 bg-muted border-b-2 border-foreground flex items-center justify-center relative">
                    {/* Status badge overlay */}
                    <span className="absolute top-2 right-2">
                      <span
                        className={cn(
                          getStatusBadgeClass(upload?.status),
                          "rounded-md px-2 py-0.5 font-bold font-mono border-2 border-foreground shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]",
                        )}
                      >
                        {upload?.status ?? "draft"}
                      </span>
                    </span>
                    <div className="size-10 rounded-md border-2 border-foreground bg-primary text-primary-foreground flex items-center justify-center shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] transition-transform group-hover:scale-110">
                      <Play
                        className="size-4 fill-current text-primary-foreground"
                        strokeWidth={2}
                      />
                    </div>
                  </div>

                  {/* Content */}
                  <div className="p-4">
                    <h3 className="font-bold text-foreground text-sm truncate hover:text-primary transition-colors">
                      <Link
                        href={`/projects/${project.id}`}
                        className="focus:outline-none focus:underline"
                      >
                        {project.title}
                      </Link>
                    </h3>
                    <p className="text-xs font-mono text-muted-foreground truncate mt-0.5">
                      {upload?.original_filename ?? "Waiting for source..."}
                    </p>

                    {warnings.length > 0 && (
                      <div className="flex gap-1 items-center text-[10px] text-amber-500 font-bold font-mono uppercase tracking-tight mt-2">
                        <AlertTriangle className="size-3" strokeWidth={1.5} />
                        {warnings.length} issues found
                      </div>
                    )}

                    {/* Footer */}
                    <div className="mt-3 flex items-center justify-between border-t-2 border-foreground pt-3">
                      {/* Metadata chips */}
                      <div className="flex items-center gap-1.5">
                        <span className="rounded-md border-2 border-foreground bg-muted px-2 py-0.5 text-[10px] font-mono font-bold text-foreground inline-flex items-center gap-1 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                          <Play className="size-2.5" strokeWidth={2} />
                          {formatDuration(upload?.duration_ms ?? null)}
                        </span>
                        <span className="rounded-md border-2 border-foreground bg-muted px-2 py-0.5 text-[10px] font-mono font-bold text-foreground inline-flex items-center gap-1 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
                          <Clock className="size-2.5" strokeWidth={2} />
                          {new Date(project.updated_at).toLocaleDateString()}
                        </span>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-1.5">
                        <Link
                          href={`/projects/${project.id}`}
                          className="bg-primary text-primary-foreground border-2 border-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider"
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
                          className="bg-danger text-white border-2 border-foreground rounded-md shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[3px_3px_0px_#000] dark:hover:shadow-[3px_3px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] px-2 py-1 text-[10px] font-bold uppercase tracking-wider"
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
          <p className="bg-card rounded-md border-2 border-foreground shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] p-8 text-center text-sm font-mono text-muted-foreground">
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
            className="fixed inset-0 z-45 cursor-default bg-background/60 backdrop-blur-sm w-full h-full text-left"
            onClick={handleCancelDelete}
          />
          <div
            ref={focusTrapRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-dialog-title"
            className="bg-card rounded-md border-4 border-foreground shadow-[8px_8px_0px_#000] dark:shadow-[8px_8px_0px_#fff] p-6 w-full max-w-md animate-scale-in fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2"
          >
            {/* Warning icon */}
            <div className="size-12 rounded-md border-2 border-foreground bg-amber-500 text-black flex items-center justify-center mx-auto mb-4 shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]">
              <ShieldAlert className="size-6 text-black" strokeWidth={2} />
            </div>

            <h3
              id="delete-dialog-title"
              className="text-xl font-black uppercase text-center mt-2 text-foreground"
            >
              Confirm Delete
            </h3>
            <p className="text-sm text-muted-foreground text-center mt-2 leading-relaxed">
              Are you sure you want to permanently delete project{" "}
              <strong className="text-foreground">
                &quot;{projectToDelete.title}&quot;
              </strong>
              ? This will remove all captions, visual analyses, and cached
              exports. This action is irreversible.
            </p>

            <div className="flex gap-3 mt-6 justify-end">
              <button
                type="button"
                disabled={deleteLoading}
                onClick={handleCancelDelete}
                className="border-2 border-foreground rounded-md bg-background text-foreground shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] px-4 py-2 text-xs font-bold font-mono uppercase tracking-wider"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={deleteLoading}
                onClick={confirmDelete}
                aria-busy={deleteLoading}
                className="bg-danger text-white border-2 border-foreground rounded-md shadow-[3px_3px_0px_#000] dark:shadow-[3px_3px_0px_#fff] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_#000] dark:hover:shadow-[4px_4px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms] px-4 py-2 text-xs font-bold font-mono uppercase tracking-wider inline-flex items-center"
              >
                {deleteLoading ? (
                  <>
                    <Loader2
                      className="mr-1.5 size-3.5 animate-spin"
                      strokeWidth={2}
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
