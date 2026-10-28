"use client";

import { useState, useMemo } from "react";
import { Search, Filter, RotateCcw, Square } from "lucide-react";
import { ConfirmFormButton } from "@/components/confirm-form-button";
import { SubmitButton } from "@/components/submit-button";
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
  totalJobs?: number;
  limit?: number;
  onRetry: (formData: FormData) => Promise<void>;
  onCancel: (formData: FormData) => Promise<void>;
};

export function AdminJobsList({
  jobs,
  totalJobs = jobs.length,
  limit = jobs.length,
  onRetry,
  onCancel,
}: AdminJobsListProps) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      const matchesSearch =
        job.id.toLowerCase().includes(search.toLowerCase()) ||
        job.queue_name.toLowerCase().includes(search.toLowerCase()) ||
        job.kind.toLowerCase().includes(search.toLowerCase());

      const matchesStatus =
        statusFilter === "all" ||
        job.status.toLowerCase() === statusFilter.toLowerCase();

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
            className="min-h-11 w-full rounded-neu border border-control bg-surface px-4 py-2 pl-9 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
          />
        </div>
        <div className="relative">
          <span className="absolute inset-y-0 left-3 flex items-center text-muted-foreground pointer-events-none">
            <Filter className="size-4" />
          </span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="min-h-11 appearance-none rounded-neu border border-control bg-surface px-8 py-2 pr-10 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2 focus:ring-offset-background"
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
        <p className="text-xs font-semibold text-muted-foreground">
          Showing {Math.min(jobs.length, limit)} of {totalJobs} latest jobs
        </p>
      </div>

      <div className="grid gap-3">
        {filteredJobs.map((job) => (
          <div
            key={job.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-neu-md bg-surface p-4 shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">
                {job.id}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                <span className="font-semibold text-foreground">
                  {job.queue_name}
                </span>{" "}
                | {job.kind} |{" "}
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-2xs font-semibold shadow-neu-inset-sm",
                    ["failed", "dead_letter"].includes(job.status) &&
                      "bg-danger text-danger-foreground",
                    ["queued"].includes(job.status) && "bg-info text-ink",
                    ["running"].includes(job.status) && "bg-warning text-ink",
                    ["succeeded"].includes(job.status) && "bg-success text-ink",
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
                  <SubmitButton
                    variant="secondary"
                    className="min-h-10 px-4 text-xs"
                    pendingLabel="Retrying…"
                  >
                    <RotateCcw className="size-3.5" aria-hidden="true" />
                    Retry
                  </SubmitButton>
                </form>
              ) : null}
              {["queued", "running"].includes(job.status) ? (
                <ConfirmFormButton
                  action={onCancel}
                  triggerClassName="min-h-10 px-4 text-xs text-danger"
                  triggerLabel={
                    <>
                      <Square className="size-3.5" aria-hidden="true" />
                      Cancel
                    </>
                  }
                  title="Cancel this job?"
                  body={`Job ${job.id} in ${job.queue_name} stops and will not run again unless it is retried.`}
                  confirmLabel="Cancel job"
                  pendingLabel="Cancelling…"
                >
                  <input type="hidden" name="jobId" value={job.id} />
                </ConfirmFormButton>
              ) : null}
            </div>
          </div>
        ))}
        {filteredJobs.length === 0 ? (
          <div className="rounded-neu-lg bg-surface p-8 text-center text-sm font-medium text-muted-foreground shadow-neu-inset">
            No queue jobs match your filters.
          </div>
        ) : null}
      </div>
    </div>
  );
}
