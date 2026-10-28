import { memo } from "react";
import type React from "react";
import Image from "next/image";
import Link from "next/link";
import {
  Activity,
  Check,
  ChevronLeft,
  Download,
  Keyboard,
  Loader2,
  Redo2,
  RotateCcw,
  Sparkles,
  Square,
  TriangleAlert,
  Undo2,
} from "lucide-react";
import { MobileNav } from "@/components/mobile-nav";
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

/** Memoized: the editor passes stable props, so unrelated editor updates
 * (clip selection, panel data) do not re-render the toolbar. */
export const EditorToolbar = memo(function EditorToolbar({
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
    <header className="sticky top-0 z-40 m-2 flex min-h-[64px] max-w-full flex-wrap items-center gap-2 rounded-neu-xl bg-surface px-3 py-2 shadow-neu-raised sm:mx-3">
      <Link
        href="/dashboard"
        className="neu-pressable flex min-h-10 items-center gap-1.5 rounded-neu bg-surface px-3 py-1 text-sm font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised"
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
        className="min-h-10 min-w-0 flex-1 truncate rounded-neu border border-control bg-surface px-3 py-1 text-sm font-semibold text-foreground transition-shadow duration-200 placeholder:text-muted-foreground focus:shadow-neu-inset-sm focus:outline-none focus:ring-2 focus:ring-focus sm:max-w-[280px] lg:max-w-[260px] focus:ring-offset-2 focus:ring-offset-background"
        placeholder="Untitled project"
      />

      <div className="hidden sm:block">
        <SaveBadge status={saveStatus} />
      </div>

      <div
        className="order-last flex min-h-11 w-full items-center gap-1 rounded-full bg-surface p-1 shadow-neu-inset-sm sm:order-none sm:w-auto"
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
        <div className="hidden items-center gap-1 rounded-full bg-surface p-1 shadow-neu-inset-sm lg:flex">
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
        className="flex min-h-10 items-center gap-1 rounded-full bg-surface p-1 shadow-neu-inset-sm"
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
            "ml-1 hidden min-h-8 max-w-48 items-center gap-1.5 truncate rounded-full bg-surface px-2.5 text-xs font-semibold shadow-neu-raised-sm sm:inline-flex",
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
        <MobileNav
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
          className="hidden min-h-10 items-center gap-1.5 rounded-neu bg-accent px-3 text-sm font-semibold text-accent-foreground shadow-neu-raised transition-all duration-200 hover:brightness-105 hover:shadow-neu-raised-lg focus-visible:outline-none active:shadow-neu-pressed lg:inline-flex focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <Sparkles className="size-4" strokeWidth={1.75} />
          AI Edit
        </button>
        <button
          type="button"
          onClick={onExport}
          aria-label="Open export settings"
          className="hidden min-h-10 items-center gap-1.5 rounded-neu bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 focus-visible:outline-none active:shadow-neu-pressed lg:inline-flex focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <Download className="size-4" strokeWidth={1.75} />
          Export
        </button>
      </div>

      <div className="sm:hidden">
        <SaveBadge status={saveStatus} />
      </div>
    </header>
  );
});

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
        "min-h-9 flex-1 rounded-full px-4 text-sm font-semibold transition-shadow duration-200 sm:flex-none",
        selected
          ? "bg-primary text-primary-foreground shadow-neu-glow"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function ShortcutGuide() {
  return (
    <details className="group relative hidden lg:block">
      <summary className="neu-pressable flex min-h-10 cursor-pointer list-none items-center gap-1.5 rounded-neu bg-surface px-3 text-sm font-semibold text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised [&::-webkit-details-marker]:hidden">
        <Keyboard className="size-4" strokeWidth={1.75} />
        Shortcuts
      </summary>
      <div className="absolute right-0 top-[calc(100%+0.5rem)] z-50 w-72 rounded-neu-lg bg-surface p-4 shadow-neu-raised-lg">
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
    <kbd className="whitespace-nowrap rounded-neu-sm bg-muted px-1.5 py-0.5 font-mono text-xs font-semibold text-foreground shadow-neu-inset-sm">
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
      className="neu-pressable inline-flex size-8 items-center justify-center rounded-neu bg-surface text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-neu-raised-sm"
    >
      <Icon className="size-4" strokeWidth={1.75} />
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
  const Icon =
    status === "saving" ? Loader2 : status === "error" ? TriangleAlert : Check;
  return (
    <div
      className="flex min-h-9 items-center gap-1.5 rounded-full bg-surface px-3 text-xs font-semibold text-foreground shadow-neu-raised-sm"
      aria-live="polite"
      aria-atomic="true"
    >
      <Icon
        aria-hidden="true"
        strokeWidth={2}
        className={cn(
          "size-3.5 shrink-0",
          status === "saving" && "animate-spin text-primary",
          status === "error" && "text-danger",
          status === "saved" && "text-success",
        )}
      />
      <span className={status === "error" ? "text-danger" : undefined}>
        {label}
      </span>
    </div>
  );
}
