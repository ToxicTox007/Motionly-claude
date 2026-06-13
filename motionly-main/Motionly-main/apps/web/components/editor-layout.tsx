"use client";

import React from "react";
import Image from "next/image";
import Link from "next/link";
import {
  Sparkles,
  Download,
  Save,
  ChevronLeft,
} from "lucide-react";
import { ThemeMenu } from "@/components/theme-menu";

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
    leftPanel:   React.ReactNode;
    centerPanel: React.ReactNode;
    rightPanel:  React.ReactNode;
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
  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden bg-background lg:h-screen lg:overflow-hidden">
      <a href="#editor-preview" className="skip-link">Skip to preview</a>
      <a href="#editor-timeline" className="skip-link">Skip to timeline</a>
      <a href="#editor-export" className="skip-link">Skip to export settings</a>
      <TopToolbar
        title={title}
        onTitleChange={onTitleChange}
        onTitleCommit={onTitleCommit}
        saveStatus={saveStatus}
        onExport={onExport}
        onAiEdit={onAiEdit}
      />

      <div className="grid min-h-0 max-w-full flex-1 gap-2 overflow-x-hidden overflow-y-auto p-2 sm:gap-3 sm:p-3 lg:grid-cols-[220px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)_210px] lg:overflow-hidden">
        <section aria-labelledby="media-heading" className="pixel-panel order-2 min-h-[220px] min-w-0 max-w-full overflow-hidden lg:order-1 lg:min-h-0">
          {leftPanel}
        </section>

        <section id="editor-preview" aria-labelledby="preview-heading" className="pixel-panel-strong sticky top-[4.25rem] z-30 order-1 h-[min(48dvh,460px)] min-h-[340px] min-w-0 max-w-full overflow-hidden sm:min-h-[620px] lg:static lg:order-2 lg:h-auto lg:min-h-0">
          {centerPanel}
        </section>

        <section aria-label="Editor settings" className="pixel-panel order-3 min-h-[360px] min-w-0 max-w-full overflow-hidden lg:order-3 lg:min-h-0">
          {rightPanel}
        </section>

        <section id="editor-timeline" aria-labelledby="timeline-heading" className="pixel-panel order-4 min-h-[260px] min-w-0 max-w-full overflow-hidden lg:col-span-3 lg:min-h-0">
          {bottomPanel}
        </section>
      </div>
    </div>
  );
}

// ── Top toolbar ───────────────────────────────────────────────────────────────

interface TopToolbarProps {
  title:         string;
  onTitleChange: (t: string) => void;
  onTitleCommit: (t: string) => void;
  saveStatus:    "saving" | "saved" | "error";
  onExport:      () => void;
  onAiEdit:      () => void;
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
    <div className="glass-surface pixel-grid-fine sticky top-0 z-40 grid max-w-full shrink-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 overflow-x-hidden border-b border-border px-2 py-2 sm:px-3 lg:static lg:flex lg:min-h-12 lg:flex-nowrap">
      <Link
        href="/dashboard"
        className="pixel-button flex min-h-11 min-w-11 items-center justify-center gap-1 bg-surface px-2 py-1 text-foreground transition hover:bg-primary/10 lg:min-w-0"
        title="Back to dashboard"
        aria-label="Back to dashboard"
      >
        <ChevronLeft className="size-4" />
        <Image src="/icon.svg" alt="" width={22} height={22} className="hidden size-5 sm:block" />
        <span className="hidden text-sm font-bold uppercase tracking-[0.18em] sm:inline">
          Motionly
        </span>
      </Link>

      <div className="hidden mx-1 h-4 w-px bg-border lg:block" />

      <input
        aria-label="Project title"
        value={title}
        onChange={(e) => onTitleChange(e.target.value)}
        onBlur={(e) => onTitleCommit(e.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
        className="pixel-input min-h-11 min-w-0 truncate bg-transparent px-3 text-base font-semibold text-foreground placeholder:text-muted-foreground sm:max-w-[280px] lg:max-w-[260px]"
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
          className="hidden min-h-11 min-w-11 items-center justify-center gap-1.5 bg-primary/10 px-2.5 py-1 text-sm font-bold text-primary transition hover:bg-primary/20 lg:inline-flex"
        >
          <Sparkles className="size-4" />
          <span>AI Edit</span>
        </button>

        <button
          onClick={onExport}
          aria-label="Open export settings"
          className="pixel-button hidden min-h-11 min-w-11 items-center justify-center gap-1.5 bg-primary px-3 py-1.5 text-sm font-bold text-primary-foreground transition hover:brightness-105 lg:inline-flex"
        >
          <Download className="size-4" />
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
    <div className="flex items-center gap-1 text-xs text-muted-foreground" aria-live="polite" aria-atomic="true">
      {status === "saving" ? (
        <>
          <Save className="size-2.5 animate-pulse" />
          <span>Saving…</span>
        </>
      ) : status === "error" ? (
        <>
          <Save className="size-2.5 text-destructive" />
          <span className="text-destructive">Error</span>
        </>
      ) : (
        <>
          <Save className="size-2.5 text-teal-400" />
          <span>Saved</span>
        </>
      )}
    </div>
  );
}
