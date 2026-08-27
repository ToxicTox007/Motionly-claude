import {
  CheckCircle2,
  Circle,
  Clock3,
  Download,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { CreatorFlow, CreatorTask, CreatorTaskStatus } from "./types";

export function CreatorProgressRail({
  flow,
  onExport,
  onAiEdit,
}: {
  flow: CreatorFlow;
  onExport: () => void;
  onAiEdit: () => void;
}) {
  const activeTask =
    flow.tasks.find((task) => task.key === flow.activeTaskKey) ?? flow.tasks[0];

  return (
    <nav
      className="border-b border-foreground/40 bg-muted px-3 py-2"
      aria-label="Creator workflow"
    >
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
        <div className="min-w-0 lg:w-64">
          <div className="flex items-center justify-between gap-3 text-sm font-semibold">
            <span>Project progress</span>
            <span className="text-muted-foreground">{flow.progressPct}%</span>
          </div>
          {flow.workflowStatusLabel ? (
            <p
              className="mt-1 truncate text-xs font-semibold text-muted-foreground"
              role="status"
              aria-live="polite"
              data-testid="workflow-status"
              title={flow.workflowStatusDetail ?? flow.workflowStatusLabel}
            >
              {flow.workflowStatusLabel}
            </p>
          ) : null}
          <div
            className="mt-1.5 h-2 overflow-hidden rounded-full border border-foreground bg-surface"
            role="progressbar"
            aria-label="Project progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={flow.progressPct}
          >
            <div
              className="h-full bg-primary"
              style={{ width: `${flow.progressPct}%` }}
            />
          </div>
        </div>
        <div className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto py-0.5">
          {flow.tasks.map((task) => (
            <button
              key={task.key}
              type="button"
              aria-current={task.key === activeTask?.key ? "step" : undefined}
              aria-label={`${task.label}: ${task.detail ?? task.status}`}
              onClick={() => flow.onTaskSelect?.(task)}
              className={cn(
                "flex min-h-10 min-w-[7rem] shrink-0 items-center gap-2 rounded border border-foreground px-2 text-left text-sm font-semibold transition-colors",
                task.key === activeTask?.key
                  ? "bg-primary text-primary-foreground shadow-brutal-sm"
                  : "bg-surface text-foreground hover:bg-surface-elevated",
              )}
            >
              <TaskStatusIcon status={task.status} />
              <span className="min-w-0 truncate">{task.shortLabel}</span>
            </button>
          ))}
        </div>
        <div className="flex gap-2 lg:w-52 lg:justify-end">
          <button
            type="button"
            onClick={onAiEdit}
            className="inline-flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded border-2 border-foreground bg-accent px-3 text-sm font-semibold text-accent-foreground lg:flex-none"
          >
            <Sparkles className="size-4" strokeWidth={2} />
            Draft
          </button>
          <button
            type="button"
            onClick={onExport}
            className="inline-flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded border-2 border-foreground bg-primary px-3 text-sm font-semibold text-primary-foreground lg:flex-none"
          >
            <Download className="size-4" strokeWidth={2} />
            Export
          </button>
        </div>
      </div>
    </nav>
  );
}

export function CurrentTaskCard({
  task,
  onSelect,
}: {
  task?: CreatorTask;
  onSelect?: (task: CreatorTask) => void;
}) {
  if (!task) return null;
  const progress =
    typeof task.progress === "number"
      ? Math.max(0, Math.min(100, task.progress))
      : null;

  return (
    <button
      type="button"
      onClick={() => onSelect?.(task)}
      aria-label={`Current workflow step: ${task.label}. ${task.detail ?? task.status}`}
      className="block w-full p-3 text-left"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-muted-foreground">Current task</p>
          <h2 className="mt-1 text-base font-bold">{task.label}</h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            {task.description}
          </p>
        </div>
        <TaskStatusIcon status={task.status} />
      </div>
      {progress !== null ? (
        <div
          className="mt-3 h-2 overflow-hidden rounded-full border border-foreground bg-muted"
          role="progressbar"
          aria-label={`${task.label} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
        >
          <div className="h-full bg-primary" style={{ width: `${progress}%` }} />
        </div>
      ) : null}
      <div className="mt-3 flex items-center justify-between gap-3 text-sm font-semibold">
        <span className="truncate text-muted-foreground">
          {task.detail ?? task.status}
        </span>
        {task.actionLabel ? (
          <span className="shrink-0 rounded border border-foreground bg-primary px-2 py-1 text-primary-foreground">
            {task.actionLabel}
          </span>
        ) : null}
      </div>
    </button>
  );
}

export function TaskStatusIcon({ status }: { status: CreatorTaskStatus }) {
  if (status === "done") {
    return <CheckCircle2 className="size-4 shrink-0 text-success" strokeWidth={2} />;
  }
  if (status === "active") {
    return <Clock3 className="size-4 shrink-0 text-primary" strokeWidth={2} />;
  }
  if (status === "blocked") {
    return <RotateCcw className="size-4 shrink-0 text-danger" strokeWidth={2} />;
  }
  return <Circle className="size-4 shrink-0 text-muted-foreground" strokeWidth={2} />;
}
