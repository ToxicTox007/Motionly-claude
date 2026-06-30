"use client";

import React, { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { Sparkles, Download, ChevronLeft, Eye, LayoutGrid, SlidersHorizontal, X } from "lucide-react";
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
  /** The four main content areas, passed as an object for clarity */
  children: {
    leftPanel: React.ReactNode;
    centerPanel: React.ReactNode;
    rightPanel: React.ReactNode;
    bottomPanel: React.ReactNode;
  };
}

// ── Layout ────────────────────────────────────────────────────────────────────

export function EditorLayout({
  title,
  onTitleChange,
  onTitleCommit,
  saveStatus = "saved",
  onExport,
  onAiEdit,
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
      />

      {/* Mobile/Tablet View (< 1024px) */}
      <div className="lg:hidden flex flex-col flex-1 min-h-0 relative">
        {/* Compact Mobile Toolbar */}
        <div className="bg-surface border-b-2 border-foreground flex items-center justify-around p-1 shrink-0 z-30 font-bold gap-1">
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
        <div className="flex-1 flex flex-col min-h-0 overflow-y-auto p-2 gap-2">
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
              className="fixed inset-0 top-[8rem] z-40 bg-background/60 backdrop-blur-sm w-full h-full text-left"
              onClick={() => setActiveSheet(null)}
            />
            <div
              ref={leftTrapRef}
              role="dialog"
              aria-modal="true"
              aria-label="Element Palette"
              className="fixed bottom-0 left-0 right-0 top-[8.25rem] z-50 flex flex-col bg-surface border-t-2 border-x-2 border-foreground rounded-t-xl overflow-hidden shadow-xl"
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
              className="fixed inset-0 top-[8rem] z-40 bg-background/60 backdrop-blur-sm w-full h-full text-left"
              onClick={() => setActiveSheet(null)}
            />
            <div
              ref={rightTrapRef}
              role="dialog"
              aria-modal="true"
              aria-label="Editor Settings"
              className="fixed bottom-0 left-0 right-0 top-[8.25rem] z-50 flex flex-col bg-surface border-t-2 border-x-2 border-foreground rounded-t-xl overflow-hidden shadow-xl"
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
}

function TopToolbar({
  title,
  onTitleChange,
  onTitleCommit,
  saveStatus,
  onExport,
  onAiEdit,
}: TopToolbarProps) {
  return (
    <div className="bg-surface border-b-4 border-foreground sticky top-0 z-40 flex items-center gap-2 px-3 py-2 min-h-[60px] max-w-full overflow-x-hidden">
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
          className="hidden size-5 sm:block invert dark:invert-0"
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
