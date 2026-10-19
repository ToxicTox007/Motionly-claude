import type React from "react";
import Image from "next/image";
import Link from "next/link";
import {
  Activity,
  ChevronLeft,
  Download,
  Keyboard,
  Redo2,
  RotateCcw,
  Sparkles,
  Square,
  Undo2,
} from "lucide-react";
import { ThemeMenu } from "@/components/theme-menu";
import { cn } from "@/lib/utils";
import type { EditorCommandBar, EditorMode } from "./types";
import { EDITOR_MODE_SHORTCUT } from "./useEditorMode";

interface EditorToolbarProps {
  title: string;
  onTitleChange: (title: string) => void;
  onTitleCommit: (title: string) => void;
  saveStatus: "saving" | "saved" | "error";
  onExport: () => void;
  onAiEdit: () => void;
  mode: EditorMode;
  onModeChange: (mode: EditorMode) => void;
  commandBar: EditorCommandBar;
}

export function EditorToolbar({
  title,
  onTitleChange,
  onTitleCommit,
  saveStatus,
  onExport,
  onAiEdit,
  mode,
  onModeChange,
  commandBar,
}: EditorToolbarProps) {
  return (
    <header className="sticky top-0 z-40 flex min-h-[64px] max-w-full flex-wrap items-center gap-2 border-b-2 border-foreground bg-surface px-3 py-2">
      <Link
        href="/dashboard"
        className="flex min-h-10 items-center gap-1.5 rounded border-2 border-foreground bg-surface px-3 py-1 text-sm font-semibold transition-all duration-100 hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal-sm"
        title="Back to dashboard"
        aria-label="Back to dashboard"
      >
        <ChevronLeft className="size-4" strokeWidth={2} />
        <Image
          src="/icon.svg"
          alt=""
          width={22}
          height={22}
          className="hidden size-5 sm:block"
        />
        <span className="hidden sm:inline">Motionly</span>
      </Link>

      <input
        aria-label="Project title"
        value={title}
        onChange={(event) => onTitleChange(event.target.value)}
        onBlur={(event) => onTitleCommit(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
        className="min-h-10 min-w-0 flex-1 truncate rounded border-2 border-foreground bg-surface px-3 py-1 text-sm font-semibold text-foreground transition-all placeholder:text-muted-foreground focus:outline-none focus:shadow-brutal-sm sm:max-w-[280px] lg:max-w-[260px]"
        placeholder="Untitled project"
      />

      <div className="hidden sm:block">
        <SaveBadge status={saveStatus} />
      </div>

      <div
        className="order-last flex min-h-11 w-full items-center rounded border-2 border-foreground bg-muted p-1 sm:order-none sm:w-auto"
        role="group"
        aria-label="Editor mode"
        data-editor-mode-toggle
      >
        <ModeButton
          selected={mode === "creator"}
          onClick={() => onModeChange("creator")}
        >
          Creator
        </ModeButton>
        <ModeButton
          selected={mode === "professional"}
          onClick={() => onModeChange("professional")}
        >
          Professional
        </ModeButton>
      </div>

      <div className="hidden flex-1 xl:block" />

      {mode === "professional" ? (
        <div className="hidden items-center gap-1 rounded border-2 border-foreground bg-muted p-1 lg:flex">
          <IconCommandButton
            label="Undo"
            shortcut="Ctrl/⌘ Z"
            onClick={commandBar.onUndo}
            disabled={!commandBar.canUndo}
            icon={Undo2}
          />
          <IconCommandButton
            label="Redo"
            shortcut="Shift Ctrl/⌘ Z"
            onClick={commandBar.onRedo}
            disabled={!commandBar.canRedo}
            icon={Redo2}
          />
        </div>
      ) : null}

      <div
        className="flex min-h-10 items-center gap-1 rounded border-2 border-foreground bg-muted p-1"
        aria-busy={commandBar.isPending}
      >
        <IconCommandButton
          label="Retry workflow"
          onClick={commandBar.onRetry}
          disabled={!commandBar.hasRetry || commandBar.isPending}
          icon={RotateCcw}
        />
        <IconCommandButton
          label="Cancel workflow"
          onClick={commandBar.onCancel}
          disabled={!commandBar.hasCancel || commandBar.isPending}
          icon={Square}
        />
        <span
          className={cn(
            "ml-1 hidden min-h-8 max-w-48 items-center gap-1.5 truncate rounded bg-surface px-2 text-xs font-semibold sm:inline-flex",
            commandBar.error ? "text-danger" : "text-muted-foreground",
          )}
          role="status"
          aria-live="polite"
          title={commandBar.error ?? commandBar.jobLabel}
          data-testid="workflow-command-status"
        >
          <Activity className="size-3.5 shrink-0" strokeWidth={2} />
          {commandBar.error ?? commandBar.jobLabel}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <ShortcutGuide />
        <ThemeMenu
          items={[
            { href: "/dashboard", label: "Dashboard" },
            { label: "Open AI Edit", onClick: onAiEdit },
            { label: "Open Export", onClick: onExport },
          ]}
          className="lg:hidden"
        />
        <button
          type="button"
          onClick={onAiEdit}
          aria-label="Open AI Edit settings"
          className="hidden min-h-10 items-center gap-1.5 rounded border-2 border-foreground bg-accent px-3 text-sm font-semibold text-accent-foreground transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal-sm focus:outline-none lg:inline-flex"
        >
          <Sparkles className="size-4" strokeWidth={2} />
          AI Edit
        </button>
        <button
          type="button"
          onClick={onExport}
          aria-label="Open export settings"
          className="hidden min-h-10 items-center gap-1.5 border-2 border-foreground bg-primary px-4 text-sm font-semibold text-primary-foreground transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal-sm focus:outline-none lg:inline-flex"
        >
          <Download className="size-4" strokeWidth={2} />
          Export
        </button>
      </div>

      <div className="sm:hidden">
        <SaveBadge status={saveStatus} />
      </div>
    </header>
  );
}

function ModeButton({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "min-h-9 flex-1 rounded px-3 text-sm font-semibold transition-colors sm:flex-none",
        selected
          ? "bg-primary text-primary-foreground shadow-brutal-sm"
          : "text-muted-foreground hover:bg-surface hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function ShortcutGuide() {
  return (
    <details className="group relative hidden lg:block">
      <summary className="flex min-h-10 cursor-pointer list-none items-center gap-1.5 rounded border-2 border-foreground bg-surface px-3 text-sm font-semibold hover:bg-surface-elevated [&::-webkit-details-marker]:hidden">
        <Keyboard className="size-4" strokeWidth={2} />
        Shortcuts
      </summary>
      <div className="absolute right-0 top-[calc(100%+0.5rem)] z-50 w-72 rounded border-2 border-foreground bg-surface p-3 shadow-brutal">
        <p className="mb-2 text-sm font-semibold">Precision controls</p>
        <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-sm">
          <dt>Play backward / pause / forward</dt>
          <dd>
            <ShortcutKeys keys="J K L" />
          </dd>
          <dt>Trim selected clip to playhead</dt>
          <dd>
            <ShortcutKeys keys="[ ]" />
          </dd>
          <dt>Undo / redo</dt>
          <dd>
            <ShortcutKeys keys="Ctrl/⌘ Z" />
          </dd>
          <dt>Switch editor mode</dt>
          <dd>
            <ShortcutKeys keys={EDITOR_MODE_SHORTCUT} />
          </dd>
          <dt>Move focused timeline clip</dt>
          <dd>
            <ShortcutKeys keys="← →" />
          </dd>
        </dl>
      </div>
    </details>
  );
}

function ShortcutKeys({ keys }: { keys: string }) {
  return (
    <kbd className="whitespace-nowrap rounded border border-foreground bg-muted px-1.5 py-0.5 font-mono text-xs font-semibold">
      {keys}
    </kbd>
  );
}

function IconCommandButton({
  label,
  shortcut,
  icon: Icon,
  onClick,
  disabled,
}: {
  label: string;
  shortcut?: string;
  icon: React.ElementType;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      title={shortcut ? `${label} (${shortcut})` : label}
      aria-label={shortcut ? `${label}, ${shortcut}` : label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex size-8 items-center justify-center rounded border-2 border-foreground bg-surface text-foreground transition hover:bg-surface-elevated disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Icon className="size-4" strokeWidth={2} />
    </button>
  );
}

function SaveBadge({ status }: { status: "saving" | "saved" | "error" }) {
  const label =
    status === "saving"
      ? "Saving…"
      : status === "error"
        ? "Save failed"
        : "Saved";
  return (
    <div
      className="flex min-h-9 items-center gap-1.5 rounded border-2 border-foreground bg-surface px-2.5 text-xs font-semibold text-foreground shadow-brutal-sm"
      aria-live="polite"
      aria-atomic="true"
    >
      <span
        className={cn(
          "size-2 rounded-full border border-foreground",
          status === "saving" && "animate-pulse bg-primary",
          status === "error" && "bg-danger",
          status === "saved" && "bg-success",
        )}
      />
      <span className={status === "error" ? "text-danger" : undefined}>
        {label}
      </span>
    </div>
  );
}
