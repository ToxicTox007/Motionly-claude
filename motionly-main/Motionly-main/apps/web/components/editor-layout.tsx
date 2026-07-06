"use client";

import React, { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  Sparkles,
  Download,
  ChevronLeft,
  Eye,
  LayoutGrid,
  SlidersHorizontal,
  X,
  CheckCircle2,
  Circle,
  Clock3,
  RotateCcw,
  Square,
  Undo2,
  Redo2,
  Activity,
} from "lucide-react";
import { ThemeMenu } from "@/components/theme-menu";
import { cn } from "@/lib/utils";
import { useFocusTrap } from "@/lib/useFocusTrap";

// ── Public interface ──────────────────────────────────────────────────────────

interface EditorLayoutProps {
  /** Editable project title (passed from EditorClient) */
  title: string;
  /** Callback when user edits the project title */
  onTitleChange: (title: string) => void;
  /** Callback when the title edit should be persisted */
  onTitleCommit: (title: string) => void;
  /** Save indicator — "saving" | "saved" | "error" */
  saveStatus?: "saving" | "saved" | "error";
  /** Called when the Export button is clicked */
  onExport: () => void;
  /** Called when the AI toolbar button is clicked */
  onAiEdit: () => void;
  creatorFlow: {
    tasks: CreatorTask[];
    activeTaskKey: string;
    progressPct: number;
    onTaskSelect?: (task: CreatorTask) => void;
  };
  commandBar: {
    canUndo: boolean;
    canRedo: boolean;
    onUndo: () => void;
    onRedo: () => void;
    onRetry: () => void;
    onCancel: () => void;
    hasRetry: boolean;
    hasCancel: boolean;
    jobLabel: string;
  };
  /** The four main content areas, passed as an object for clarity */
  children: {
    leftPanel: React.ReactNode;
    centerPanel: React.ReactNode;
    rightPanel: React.ReactNode;
    bottomPanel: React.ReactNode;
  };
}

export type CreatorTaskStatus =
  | "done"
  | "active"
  | "waiting"
  | "blocked"
  | "skipped";

export type CreatorTask = {
  key: string;
  label: string;
  shortLabel: string;
  description: string;
  status: CreatorTaskStatus;
  progress?: number | null;
  detail?: string | null;
  actionLabel?: string | null;
};

// ── Layout ────────────────────────────────────────────────────────────────────

export function EditorLayout({
  title,
  onTitleChange,
  onTitleCommit,
  saveStatus = "saved",
  onExport,
  onAiEdit,
  creatorFlow,
  commandBar,
  children: { leftPanel, centerPanel, rightPanel, bottomPanel },
}: EditorLayoutProps) {
  const [activeSheet, setActiveSheet] = useState<"left" | "right" | null>(null);
  const leftTrapRef = useFocusTrap(activeSheet === "left", () =>
    setActiveSheet(null),
  );
  const rightTrapRef = useFocusTrap(activeSheet === "right", () =>
    setActiveSheet(null),
  );

  return (
    <div className="flex min-h-[100dvh] flex-col overflow-x-hidden bg-background lg:h-screen lg:overflow-hidden">
      <a href="#editor-preview" className="skip-link">
        Skip to preview
      </a>
      <a href="#editor-timeline" className="skip-link">
        Skip to timeline
      </a>
      <a href="#editor-export" className="skip-link">
        Skip to export settings
      </a>
      <TopToolbar
        title={title}
        onTitleChange={onTitleChange}
        onTitleCommit={onTitleCommit}
        saveStatus={saveStatus}
        onExport={onExport}
        onAiEdit={onAiEdit}
        commandBar={commandBar}
      />

      <CreatorProgressRail
        flow={creatorFlow}
        onExport={onExport}
        onAiEdit={onAiEdit}
      />

      {/* Mobile/Tablet View (< 1024px) */}
      <div className="lg:hidden flex flex-col flex-1 min-h-0 relative">
        {/* Compact Mobile Toolbar */}
        <div className="fixed inset-x-0 bottom-0 z-40 flex items-center justify-around gap-1 border-t-4 border-foreground bg-surface p-1 font-bold shadow-brutal lg:hidden">
          <button
            type="button"
            className={cn(
              "flex flex-1 flex-col items-center justify-center py-2.5 gap-1 text-[10px] font-black uppercase tracking-widest transition-colors rounded border-2 border-transparent",
              activeSheet === null
                ? "text-primary-foreground bg-primary border-foreground"
                : "text-muted-foreground hover:bg-surface-elevated hover:text-foreground",
            )}
            onClick={() => setActiveSheet(null)}
          >
            <Eye className="size-4" strokeWidth={2} />
            Preview
          </button>
          <button
            type="button"
            className={cn(
              "flex flex-1 flex-col items-center justify-center py-2.5 gap-1 text-[10px] font-black uppercase tracking-widest transition-colors rounded border-2 border-transparent",
              activeSheet === "left"
                ? "text-primary-foreground bg-primary border-foreground"
                : "text-muted-foreground hover:bg-surface-elevated hover:text-foreground",
            )}
            onClick={() => setActiveSheet("left")}
          >
            <LayoutGrid className="size-4" strokeWidth={2} />
            Elements
          </button>
          <button
            type="button"
            className={cn(
              "flex flex-1 flex-col items-center justify-center py-2.5 gap-1 text-[10px] font-black uppercase tracking-widest transition-colors rounded border-2 border-transparent",
              activeSheet === "right"
                ? "text-primary-foreground bg-primary border-foreground"
                : "text-muted-foreground hover:bg-surface-elevated hover:text-foreground",
            )}
            onClick={() => setActiveSheet("right")}
          >
            <SlidersHorizontal className="size-4" strokeWidth={2} />
            Settings
          </button>
        </div>

        {/* Responsive Content Area */}
        <div className="flex-1 flex flex-col min-h-0 overflow-y-auto p-2 pb-20 gap-2">
          <section
            aria-label="Current creator task"
            className="card shrink-0 overflow-hidden border-2 border-foreground bg-surface shadow-[2px_2px_0px_#000]"
          >
            <CurrentTaskCard
              task={
                creatorFlow.tasks.find(
                  (task) => task.key === creatorFlow.activeTaskKey,
                ) ?? creatorFlow.tasks[0]
              }
              onSelect={creatorFlow.onTaskSelect}
            />
          </section>

          <section
            id="editor-preview"
            aria-labelledby="preview-heading"
            className="card overflow-hidden h-[min(42dvh,380px)] min-h-[280px] shrink-0 border-2 border-foreground shadow-[2px_2px_0px_#000]"
          >
            {centerPanel}
          </section>
          <section
            id="editor-timeline"
            aria-labelledby="timeline-heading"
            className="card flex-1 min-h-[220px] overflow-hidden border-2 border-foreground shadow-[2px_2px_0px_#000]"
          >
            {bottomPanel}
          </section>
        </div>

        {/* Slide-out Overlay Sheets */}
        {activeSheet === "left" && (
          <>
            <button
              type="button"
              aria-label="Close Elements Panel"
              className="fixed inset-x-0 bottom-16 top-[4rem] z-40 bg-background/75 w-full text-left"
              onClick={() => setActiveSheet(null)}
            />
            <div
              ref={leftTrapRef}
              role="dialog"
              aria-modal="true"
              aria-label="Element Palette"
              className="fixed bottom-16 left-0 right-0 top-[4.25rem] z-50 flex flex-col overflow-hidden border-x-2 border-t-4 border-foreground bg-surface shadow-brutal-lg"
            >
              <div className="flex items-center justify-between border-b-2 border-foreground p-4 shrink-0 bg-surface-elevated">
                <h2 className="text-sm font-black uppercase tracking-tight">
                  Element Palette
                </h2>
                <button
                  type="button"
                  className="size-8 p-0 flex items-center justify-center rounded border-2 border-foreground bg-surface hover:bg-surface-elevated transition-colors"
                  onClick={() => setActiveSheet(null)}
                  aria-label="Close"
                >
                  <X className="size-4 text-foreground" strokeWidth={2} />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-3">{leftPanel}</div>
            </div>
          </>
        )}

        {activeSheet === "right" && (
          <>
            <button
              type="button"
              aria-label="Close Settings Panel"
              className="fixed inset-x-0 bottom-16 top-[4rem] z-40 bg-background/75 w-full text-left"
              onClick={() => setActiveSheet(null)}
            />
            <div
              ref={rightTrapRef}
              role="dialog"
              aria-modal="true"
              aria-label="Editor Settings"
              className="fixed bottom-16 left-0 right-0 top-[4.25rem] z-50 flex flex-col overflow-hidden border-x-2 border-t-4 border-foreground bg-surface shadow-brutal-lg"
            >
              <div className="flex items-center justify-between border-b-2 border-foreground p-4 shrink-0 bg-surface-elevated">
                <h2 className="text-sm font-black uppercase tracking-tight">
                  Editor Settings
                </h2>
                <button
                  type="button"
                  className="size-8 p-0 flex items-center justify-center rounded border-2 border-foreground bg-surface hover:bg-surface-elevated transition-colors"
                  onClick={() => setActiveSheet(null)}
                  aria-label="Close"
                >
                  <X className="size-4 text-foreground" strokeWidth={2} />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-3">{rightPanel}</div>
            </div>
          </>
        )}
      </div>

      {/* Desktop Grid Layout (visible >= 1024px) */}
      <div className="hidden lg:grid min-h-0 max-w-full flex-1 gap-2 p-2 sm:gap-3 sm:p-3 lg:grid-cols-[240px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)_220px] lg:overflow-hidden">
        <section
          aria-labelledby="media-heading"
          className="card overflow-hidden rounded-lg border-2 border-foreground bg-surface shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
        >
          {leftPanel}
        </section>

        <section
          id="editor-preview"
          aria-labelledby="preview-heading"
          className="card overflow-hidden rounded-lg border-2 border-foreground bg-surface shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
        >
          {centerPanel}
        </section>

        <section
          aria-label="Editor settings"
          className="card overflow-hidden rounded-lg border-2 border-foreground bg-surface shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff]"
        >
          {rightPanel}
        </section>

        <section
          id="editor-timeline"
          aria-labelledby="timeline-heading"
          className="card overflow-hidden rounded-lg border-2 border-foreground bg-surface shadow-[2px_2px_0px_#000] dark:shadow-[2px_2px_0px_#fff] lg:col-span-3"
        >
          {bottomPanel}
        </section>
      </div>
    </div>
  );
}

// ── Top toolbar ───────────────────────────────────────────────────────────────

interface TopToolbarProps {
  title: string;
  onTitleChange: (t: string) => void;
  onTitleCommit: (t: string) => void;
  saveStatus: "saving" | "saved" | "error";
  onExport: () => void;
  onAiEdit: () => void;
  commandBar: EditorLayoutProps["commandBar"];
}

function TopToolbar({
  title,
  onTitleChange,
  onTitleCommit,
  saveStatus,
  onExport,
  onAiEdit,
  commandBar,
}: TopToolbarProps) {
  return (
    <div className="bg-surface border-b-4 border-foreground sticky top-0 z-40 flex flex-wrap items-center gap-2 px-3 py-2 min-h-[60px] max-w-full overflow-x-hidden">
      <Link
        href="/dashboard"
        className="flex items-center gap-1.5 pr-2.5 bg-surface text-foreground border-2 border-foreground rounded px-3 py-1 font-bold text-sm hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all duration-[100ms]"
        title="Back to dashboard"
        aria-label="Back to dashboard"
      >
        <ChevronLeft className="size-4 text-foreground" strokeWidth={2} />
        <Image
          src="/icon.svg"
          alt=""
          width={22}
          height={22}
          className="hidden size-5 sm:block"
        />
        <span className="hidden text-xs font-black uppercase tracking-[0.18em] sm:inline">
          Motionly
        </span>
      </Link>

      <div className="hidden lg:block h-6 w-0.5 bg-foreground mx-1" />

      <input
        aria-label="Project title"
        value={title}
        onChange={(e) => onTitleChange(e.target.value)}
        onBlur={(e) => onTitleCommit(e.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
        className="min-h-[36px] min-w-0 truncate bg-surface border-2 border-foreground rounded px-3 py-1 text-sm font-bold text-foreground placeholder:text-muted-foreground sm:max-w-[280px] lg:max-w-[260px] focus:outline-none focus:shadow-[2px_2px_0px_#000] dark:focus:shadow-[2px_2px_0px_#fff] transition-all"
        placeholder="Untitled project"
      />

      <div className="hidden sm:block">
        <SaveBadge status={saveStatus} />
      </div>

      <div className="hidden flex-1 lg:block" />

      <div className="hidden items-center gap-1 rounded border-2 border-foreground bg-muted px-1 py-1 lg:flex">
        <IconCommandButton
          label="Undo"
          onClick={commandBar.onUndo}
          disabled={!commandBar.canUndo}
          icon={Undo2}
        />
        <IconCommandButton
          label="Redo"
          onClick={commandBar.onRedo}
          disabled={!commandBar.canRedo}
          icon={Redo2}
        />
        <div className="mx-1 h-6 w-0.5 bg-foreground/50" />
        <IconCommandButton
          label="Retry"
          onClick={commandBar.onRetry}
          disabled={!commandBar.hasRetry}
          icon={RotateCcw}
        />
        <IconCommandButton
          label="Cancel"
          onClick={commandBar.onCancel}
          disabled={!commandBar.hasCancel}
          icon={Square}
        />
        <span className="ml-1 inline-flex min-h-8 items-center gap-1.5 rounded bg-surface px-2 text-[11px] font-black uppercase tracking-wide text-muted-foreground">
          <Activity className="size-3.5" strokeWidth={2} />
          {commandBar.jobLabel}
        </span>
      </div>

      <div className="flex items-center justify-end gap-2">
        <ThemeMenu
          items={[
            { href: "/dashboard", label: "Dashboard" },
            { label: "Open AI Edit", onClick: onAiEdit },
            { label: "Open Export", onClick: onExport },
          ]}
          className="lg:hidden"
        />

        <button
          onClick={onAiEdit}
          aria-label="Open AI Edit settings"
          className="hidden lg:inline-flex items-center gap-1.5 h-9 px-3 rounded border-2 border-foreground bg-accent text-accent-foreground text-sm font-bold hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none transition-all focus:outline-none"
        >
          <Sparkles className="size-4 text-foreground" strokeWidth={2} />
          <span>AI Edit</span>
        </button>

        <button
          onClick={onExport}
          aria-label="Open export settings"
          className="hidden lg:inline-flex h-9 px-4 bg-primary text-primary-foreground border-2 border-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[2px_2px_0px_#000] dark:hover:shadow-[2px_2px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none text-sm font-bold transition-all focus:outline-none"
        >
          <Download className="size-4 text-primary-foreground" strokeWidth={2} />
          <span>Export</span>
        </button>
      </div>

      <div className="col-span-3 sm:hidden">
        <SaveBadge status={saveStatus} />
      </div>
    </div>
  );
}

function CreatorProgressRail({
  flow,
  onExport,
  onAiEdit,
}: {
  flow: EditorLayoutProps["creatorFlow"];
  onExport: () => void;
  onAiEdit: () => void;
}) {
  const activeTask =
    flow.tasks.find((task) => task.key === flow.activeTaskKey) ??
    flow.tasks[0];
  return (
    <div className="border-b-2 border-foreground bg-muted px-3 py-2">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
        <div className="min-w-0 lg:w-64">
          <p className="truncate text-xs font-black uppercase tracking-[0.18em] text-muted-foreground">
            Creator flow
          </p>
          <div
            className="mt-1 h-2 overflow-hidden rounded-full border-2 border-foreground bg-surface"
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
        <div className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto">
          {flow.tasks.map((task) => (
            <button
              key={task.key}
              type="button"
              onClick={() => flow.onTaskSelect?.(task)}
              className={cn(
                "flex min-h-11 min-w-[7.25rem] shrink-0 items-center gap-2 rounded border-2 border-foreground px-2 text-left text-xs font-black transition",
                task.key === activeTask?.key
                  ? "bg-primary text-primary-foreground shadow-[2px_2px_0px_#000]"
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
            className="inline-flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded border-2 border-foreground bg-accent px-3 text-xs font-black text-accent-foreground lg:flex-none"
          >
            <Sparkles className="size-4" strokeWidth={2} />
            Draft
          </button>
          <button
            type="button"
            onClick={onExport}
            className="inline-flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded border-2 border-foreground bg-primary px-3 text-xs font-black text-primary-foreground lg:flex-none"
          >
            <Download className="size-4" strokeWidth={2} />
            Export
          </button>
        </div>
      </div>
    </div>
  );
}

function CurrentTaskCard({
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
      className="block w-full p-3 text-left"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-muted-foreground">
            Current task
          </p>
          <h2 className="mt-1 text-base font-black tracking-tight">
            {task.label}
          </h2>
          <p className="mt-1 text-xs font-semibold text-muted-foreground">
            {task.description}
          </p>
        </div>
        <TaskStatusIcon status={task.status} />
      </div>
      {progress !== null ? (
        <div className="mt-3 h-2 overflow-hidden rounded-full border-2 border-foreground bg-muted">
          <div className="h-full bg-primary" style={{ width: `${progress}%` }} />
        </div>
      ) : null}
      <div className="mt-3 flex items-center justify-between gap-3 text-[11px] font-black uppercase tracking-wide">
        <span className="truncate text-muted-foreground">
          {task.detail ?? task.status}
        </span>
        {task.actionLabel ? (
          <span className="shrink-0 rounded border-2 border-foreground bg-primary px-2 py-1 text-primary-foreground">
            {task.actionLabel}
          </span>
        ) : null}
      </div>
    </button>
  );
}

function TaskStatusIcon({ status }: { status: CreatorTaskStatus }) {
  if (status === "done")
    return <CheckCircle2 className="size-4 shrink-0 text-success" strokeWidth={2} />;
  if (status === "active")
    return <Clock3 className="size-4 shrink-0 text-primary" strokeWidth={2} />;
  if (status === "blocked")
    return <RotateCcw className="size-4 shrink-0 text-danger" strokeWidth={2} />;
  return <Circle className="size-4 shrink-0 text-muted-foreground" strokeWidth={2} />;
}

function IconCommandButton({
  label,
  icon: Icon,
  onClick,
  disabled,
}: {
  label: string;
  icon: React.ElementType;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex size-8 items-center justify-center rounded border-2 border-foreground bg-surface text-foreground transition hover:bg-surface-elevated disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Icon className="size-4" strokeWidth={2} />
    </button>
  );
}

// ── Save badge ────────────────────────────────────────────────────────────────

function SaveBadge({ status }: { status: "saving" | "saved" | "error" }) {
  return (
    <div
      className="flex items-center gap-1.5 text-xs font-mono font-bold rounded border-2 border-foreground bg-surface px-2.5 py-1 text-foreground shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff]"
      aria-live="polite"
      aria-atomic="true"
    >
      {status === "saving" ? (
        <>
          <div className="size-2.5 rounded-full bg-primary animate-ping" />
          <span>Saving…</span>
        </>
      ) : status === "error" ? (
        <>
          <div className="size-2.5 rounded-full bg-danger border border-foreground" />
          <span className="text-danger">Error</span>
        </>
      ) : (
        <>
          <div className="size-2 rounded-full bg-emerald-400" />
          <span>Saved</span>
        </>
      )}
    </div>
  );
}
