"use client";

import React from "react";
import Link from "next/link";
import {
  Sparkles,
  Download,
  Save,
  ChevronLeft,
} from "lucide-react";

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
      <TopToolbar
        title={title}
        onTitleChange={onTitleChange}
        onTitleCommit={onTitleCommit}
        saveStatus={saveStatus}
        onExport={onExport}
        onAiEdit={onAiEdit}
      />

      <div className="grid min-h-0 flex-1 gap-3 overflow-y-auto p-3 lg:grid-cols-[220px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)_190px] lg:overflow-hidden">
        <div className="pixel-panel order-2 min-h-[260px] overflow-hidden lg:order-1 lg:min-h-0">
          {leftPanel}
        </div>

        <div className="pixel-panel-strong order-1 min-h-[460px] min-w-0 overflow-hidden lg:order-2 lg:min-h-0">
          {centerPanel}
        </div>

        <div className="pixel-panel order-3 min-h-[420px] overflow-hidden lg:order-3 lg:min-h-0">
          {rightPanel}
        </div>

        <div className="pixel-panel order-4 min-h-[190px] overflow-hidden lg:col-span-3 lg:min-h-0">
          {bottomPanel}
        </div>
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
    <div className="pixel-grid-fine flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b border-border bg-surface/95 px-3 py-2 backdrop-blur lg:flex-nowrap">
      {/* Back + logo */}
      <Link
        href="/dashboard"
        className="flex items-center gap-1 border border-transparent px-2 py-1 text-muted-foreground transition hover:border-primary/30 hover:bg-muted hover:text-foreground"
        title="Back to dashboard"
      >
        <ChevronLeft className="size-4" />
        <span className="text-[10px] font-bold uppercase tracking-widest">
          Motionly
        </span>
      </Link>

      <div className="mx-1 h-4 w-px bg-border" />

      {/* Editable project title */}
      <input
        value={title}
        onChange={(e) => onTitleChange(e.target.value)}
        onBlur={(e) => onTitleCommit(e.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
        className="pixel-input h-8 min-w-0 max-w-[220px] truncate bg-transparent px-2 text-sm font-semibold text-foreground placeholder:text-muted-foreground"
        placeholder="Untitled project"
      />

      {/* Save status */}
      <SaveBadge status={saveStatus} />

      <div className="flex-1" />

      {/* AI edit shortcut */}
      <button
        onClick={onAiEdit}
        className="pixel-button flex items-center gap-1.5 bg-primary/10 px-2.5 py-1 text-[11px] font-bold text-primary transition hover:bg-primary/20"
      >
        <Sparkles className="size-3" />
        AI Edit
      </button>

      {/* Export */}
      <button
        onClick={onExport}
        className="pixel-button flex items-center gap-1.5 bg-primary px-3 py-1.5 text-[11px] font-bold text-primary-foreground transition hover:brightness-105"
      >
        <Download className="size-3" />
        Export
      </button>
    </div>
  );
}

// ── Save badge ────────────────────────────────────────────────────────────────

function SaveBadge({ status }: { status: "saving" | "saved" | "error" }) {
  return (
    <div className="flex items-center gap-1 text-[9px] text-muted-foreground/60">
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
