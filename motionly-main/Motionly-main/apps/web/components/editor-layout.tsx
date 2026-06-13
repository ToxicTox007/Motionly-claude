"use client";

import React from "react";
import Link from "next/link";
import {
  Sparkles,
  Download,
  Save,
  ChevronLeft,
  Moon,
  Sun,
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

      <div className="grid min-h-0 flex-1 gap-2 overflow-y-auto p-2 sm:gap-3 sm:p-3 lg:grid-cols-[220px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)_210px] lg:overflow-hidden">
        <section aria-labelledby="media-heading" className="pixel-panel order-2 min-h-[220px] overflow-hidden lg:order-1 lg:min-h-0">
          {leftPanel}
        </section>

        <section id="editor-preview" aria-labelledby="preview-heading" className="pixel-panel-strong order-1 min-h-[560px] min-w-0 overflow-hidden sm:min-h-[620px] lg:order-2 lg:min-h-0">
          {centerPanel}
        </section>

        <section aria-label="Editor settings" className="pixel-panel order-3 min-h-[360px] overflow-hidden lg:order-3 lg:min-h-0">
          {rightPanel}
        </section>

        <section id="editor-timeline" aria-labelledby="timeline-heading" className="pixel-panel order-4 min-h-[260px] overflow-hidden lg:col-span-3 lg:min-h-0">
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
  const [theme, setTheme] = React.useState<"light" | "dark">("light");

  React.useEffect(() => {
    const stored = window.localStorage.getItem("motionly-theme");
    const nextTheme =
      stored === "dark" || stored === "light"
        ? stored
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
  }, []);

  function toggleTheme() {
    const nextTheme = theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("motionly-theme", nextTheme);
  }

  return (
    <div className="pixel-grid-fine sticky top-0 z-40 grid shrink-0 grid-cols-[auto_1fr_auto] items-center gap-2 border-b-2 border-border bg-surface px-2 py-2 shadow-[0_4px_0_hsl(var(--pixel-shadow)/0.18)] sm:px-3 lg:static lg:flex lg:min-h-12 lg:flex-nowrap">
      {/* Back + logo */}
      <Link
        href="/dashboard"
        className="pixel-button flex min-h-11 min-w-11 items-center justify-center gap-1 bg-muted px-2 py-1 text-foreground transition hover:bg-primary/10 lg:min-w-0"
        title="Back to dashboard"
        aria-label="Back to dashboard"
      >
        <ChevronLeft className="size-4" />
        <span className="hidden font-['Pixelify_Sans'] text-sm font-bold uppercase sm:inline">
          Motionly
        </span>
      </Link>

      <div className="hidden mx-1 h-4 w-px bg-border lg:block" />

      {/* Editable project title */}
      <input
        aria-label="Project title"
        value={title}
        onChange={(e) => onTitleChange(e.target.value)}
        onBlur={(e) => onTitleCommit(e.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
        className="pixel-input min-h-11 min-w-0 truncate bg-transparent px-2 font-['Pixelify_Sans'] text-base font-semibold text-foreground placeholder:text-muted-foreground sm:max-w-[280px] lg:max-w-[220px]"
        placeholder="Untitled project"
      />

      {/* Save status */}
      <div className="hidden sm:block">
        <SaveBadge status={saveStatus} />
      </div>

      <div className="hidden flex-1 lg:block" />

      <div className="flex items-center justify-end gap-2">
        <button
          onClick={toggleTheme}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          className="pixel-button flex min-h-11 min-w-11 items-center justify-center bg-muted text-foreground transition hover:bg-primary/10"
        >
          {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>

        {/* AI edit shortcut */}
        <button
          onClick={onAiEdit}
          aria-label="Open AI Edit settings"
          className="pixel-button flex min-h-11 min-w-11 items-center justify-center gap-1.5 bg-primary/10 px-2.5 py-1 font-['Pixelify_Sans'] text-sm font-bold text-primary transition hover:bg-primary/20 sm:min-w-0"
        >
          <Sparkles className="size-4" />
          <span className="hidden sm:inline">AI</span>
        </button>

        {/* Export */}
        <button
          onClick={onExport}
          aria-label="Open export settings"
          className="pixel-button flex min-h-11 min-w-11 items-center justify-center gap-1.5 bg-primary px-3 py-1.5 font-['Pixelify_Sans'] text-sm font-bold text-primary-foreground transition hover:brightness-105 sm:min-w-0"
        >
          <Download className="size-4" />
          <span className="hidden sm:inline">Export</span>
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
