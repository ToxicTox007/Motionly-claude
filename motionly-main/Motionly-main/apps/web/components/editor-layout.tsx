"use client";

import React from "react";
import Link from "next/link";
import {
  Scissors,
  MousePointer2,
  Type,
  Sparkles,
  Download,
  Undo2,
  Redo2,
  Save,
  ChevronLeft,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ── Public interface ──────────────────────────────────────────────────────────

interface EditorLayoutProps {
  /** Editable project title (passed from EditorClient) */
  title: string;
  /** Callback when user edits the project title */
  onTitleChange: (title: string) => void;
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
  saveStatus = "saved",
  onExport,
  onAiEdit,
  children: { leftPanel, centerPanel, rightPanel, bottomPanel },
}: EditorLayoutProps) {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      {/* ── Top toolbar ─────────────────────────────────────────────────── */}
      <TopToolbar
        title={title}
        onTitleChange={onTitleChange}
        saveStatus={saveStatus}
        onExport={onExport}
        onAiEdit={onAiEdit}
      />

      {/* ── Three-column body ────────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Left media panel */}
        <div className="w-52 shrink-0 overflow-hidden">
          {leftPanel}
        </div>

        {/* Center preview */}
        <div className="min-w-0 flex-1 overflow-hidden border-x border-border">
          {centerPanel}
        </div>

        {/* Right inspector */}
        <div className="w-80 shrink-0 overflow-hidden">
          {rightPanel}
        </div>
      </div>

      {/* ── Bottom timeline ──────────────────────────────────────────────── */}
      <div className="h-[184px] shrink-0 overflow-hidden">
        {bottomPanel}
      </div>
    </div>
  );
}

// ── Top toolbar ───────────────────────────────────────────────────────────────

interface TopToolbarProps {
  title:         string;
  onTitleChange: (t: string) => void;
  saveStatus:    "saving" | "saved" | "error";
  onExport:      () => void;
  onAiEdit:      () => void;
}

function TopToolbar({
  title,
  onTitleChange,
  saveStatus,
  onExport,
  onAiEdit,
}: TopToolbarProps) {
  return (
    <div className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-[hsl(var(--surface))] px-3">
      {/* Back + logo */}
      <Link
        href="/dashboard"
        className="flex items-center gap-1 text-muted-foreground transition hover:text-foreground"
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
        className="min-w-0 max-w-[180px] truncate bg-transparent text-sm font-semibold text-foreground outline-none ring-0 placeholder:text-muted-foreground focus:underline"
        placeholder="Untitled project"
      />

      {/* Save status */}
      <SaveBadge status={saveStatus} />

      <div className="flex-1" />

      {/* Undo / Redo */}
      <div className="flex items-center gap-0.5">
        <ToolbarButton title="Undo (⌘Z)">
          <Undo2 className="size-3.5" />
        </ToolbarButton>
        <ToolbarButton title="Redo (⌘⇧Z)">
          <Redo2 className="size-3.5" />
        </ToolbarButton>
      </div>

      <div className="mx-1 h-4 w-px bg-border" />

      {/* Edit tools */}
      <div className="flex items-center gap-0.5">
        <ToolbarButton title="Select tool (V)">
          <MousePointer2 className="size-3.5" />
        </ToolbarButton>
        <ToolbarButton title="Split tool (C)">
          <Scissors className="size-3.5" />
        </ToolbarButton>
        <ToolbarButton title="Text tool (T)">
          <Type className="size-3.5" />
        </ToolbarButton>
      </div>

      <div className="mx-1 h-4 w-px bg-border" />

      {/* AI edit shortcut */}
      <button
        onClick={onAiEdit}
        className="flex items-center gap-1.5 rounded-md bg-primary/10 px-2.5 py-1 text-[11px] font-semibold text-primary transition hover:bg-primary/20"
      >
        <Sparkles className="size-3" />
        AI Edit
      </button>

      {/* Export */}
      <button
        onClick={onExport}
        className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-[11px] font-bold text-primary-foreground shadow transition hover:opacity-90 active:scale-95"
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

// ── Toolbar button ────────────────────────────────────────────────────────────

function ToolbarButton({
  title,
  children,
  active,
}: {
  title:    string;
  children: React.ReactNode;
  active?:  boolean;
}) {
  return (
    <button
      title={title}
      className={cn(
        "flex size-7 items-center justify-center rounded text-muted-foreground transition",
        "hover:bg-muted hover:text-foreground",
        active && "bg-muted text-foreground",
      )}
    >
      {children}
    </button>
  );
}
