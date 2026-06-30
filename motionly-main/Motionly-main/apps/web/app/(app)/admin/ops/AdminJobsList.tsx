"use client";

import { useState, useMemo } from "react";
import { Search, Filter, RotateCcw, Square } from "lucide-react";
import { cn } from "@/lib/utils";

type QueueJob = {
  id: string;
  kind: string;
  queue_name: string;
  status: string;
  attempts: number;
  max_attempts: number;
  project_id?: string | null;
  resource_id?: string | null;
};

type AdminJobsListProps = {
  jobs: QueueJob[];
  onRetry: (formData: FormData) => Promise<void>;
  onCancel: (formData: FormData) => Promise<void>;
};

export function AdminJobsList({ jobs, onRetry, onCancel }: AdminJobsListProps) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      const matchesSearch =
        job.id.toLowerCase().includes(search.toLowerCase()) ||
        job.queue_name.toLowerCase().includes(search.toLowerCase()) ||
        job.kind.toLowerCase().includes(search.toLowerCase());

      const matchesStatus =
        statusFilter === "all" || job.status.toLowerCase() === statusFilter.toLowerCase();

      return matchesSearch && matchesStatus;
    });
  }, [jobs, search, statusFilter]);

  const statuses = useMemo(() => {
    const set = new Set<string>();
    jobs.forEach((job) => {
      if (job.status) set.add(job.status);
    });
    return Array.from(set);
  }, [jobs]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[240px]">
          <span className="absolute inset-y-0 left-3 flex items-center text-muted-foreground pointer-events-none">
            <Search className="size-4" />
          </span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by job ID, queue, or kind..."
            className="min-h-11 w-full rounded border-2 border-foreground bg-background px-3 py-2 pl-9 text-sm font-bold focus:border-primary focus:outline-none focus:shadow-brutal-sm"
          />
        </div>
        <div className="relative">
          <span className="absolute inset-y-0 left-3 flex items-center text-muted-foreground pointer-events-none">
            <Filter className="size-4" />
          </span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="min-h-11 appearance-none rounded border-2 border-foreground bg-background px-8 py-2 pr-10 text-sm font-bold focus:border-primary focus:outline-none focus:shadow-brutal-sm"
          >
            <option value="all">All statuses</option>
            {statuses.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
          <span className="absolute inset-y-0 right-3 flex items-center pointer-events-none text-muted-foreground">
            ▼
          </span>
        </div>
      </div>

      <div className="grid gap-3">
        {filteredJobs.slice(0, 50).map((job) => (
          <div
            key={job.id}
            className="pixel-panel flex flex-wrap items-center justify-between gap-3 bg-surface p-4 transition-all hover:-translate-x-0.5 hover:-translate-y-0.5"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{job.id}</p>
              <p className="text-xs text-muted-foreground mt-1">
                <span className="font-bold text-foreground">{job.queue_name}</span>{" "}
                | {job.kind} |{" "}
                <span
                  className={cn(
                    "rounded border-2 border-foreground px-1.5 py-0.5 text-[10px] font-black uppercase",
                    ["failed", "dead_letter"].includes(job.status) && "bg-danger text-white",
                    ["queued"].includes(job.status) && "bg-info text-ink",
                    ["running"].includes(job.status) && "bg-warning text-ink",
                    ["succeeded"].includes(job.status) && "bg-success text-white",
                  )}
                >
                  {job.status}
                </span>{" "}
                | attempts {job.attempts}/{job.max_attempts}
              </p>
            </div>
            <div className="flex gap-2">
              {["failed", "dead_letter", "canceled"].includes(job.status) ? (
                <form action={onRetry}>
                  <input type="hidden" name="jobId" value={job.id} />
                  <button
                    type="submit"
                    className="pixel-button inline-flex h-9 items-center gap-2 bg-background px-3 text-xs font-bold hover:bg-muted"
                  >
                    <RotateCcw className="size-3.5" aria-hidden="true" />
                    Retry
                  </button>
                </form>
              ) : null}
              {["queued", "running"].includes(job.status) ? (
                <form action={onCancel}>
                  <input type="hidden" name="jobId" value={job.id} />
                  <button
                    type="submit"
                    className="pixel-button inline-flex h-9 items-center gap-2 bg-background px-3 text-xs font-bold text-red-600 hover:bg-red-50"
                  >
                    <Square className="size-3.5" aria-hidden="true" />
                    Cancel
                  </button>
                </form>
              ) : null}
            </div>
          </div>
        ))}
        {filteredJobs.length === 0 ? (
          <div className="pixel-panel p-8 text-center text-sm text-muted-foreground bg-muted/20">
            No queue jobs match your filters.
          </div>
        ) : null}
      </div>
    </div>
  );
}
