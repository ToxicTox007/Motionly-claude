import {
  useState,
  useSyncExternalStore,
  type ElementType,
  type ReactNode,
  type RefObject,
} from "react";
import { Eye, LayoutGrid, SlidersHorizontal, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { CurrentTaskCard } from "./CreatorProgressRail";
import type { CreatorFlow, EditorMode, EditorPanels } from "./types";


const DESKTOP_WORKSPACE_QUERY = "(min-width: 1024px)";

function subscribeToDesktopWorkspace(onStoreChange: () => void) {
  const mediaQuery = window.matchMedia(DESKTOP_WORKSPACE_QUERY);
  mediaQuery.addEventListener("change", onStoreChange);
  return () => mediaQuery.removeEventListener("change", onStoreChange);
}

function isDesktopWorkspace() {
  return window.matchMedia(DESKTOP_WORKSPACE_QUERY).matches;
}

function useDesktopWorkspace() {
  return useSyncExternalStore(
    subscribeToDesktopWorkspace,
    isDesktopWorkspace,
    () => false,
  );
}
export function EditorWorkspaces({
  mode,
  creatorFlow,
  panels,
}: {
  mode: EditorMode;
  creatorFlow: CreatorFlow;
  panels: EditorPanels;
}) {
  const desktopWorkspace = useDesktopWorkspace();

  if (!desktopWorkspace) {
    return (
      <MobileWorkspace
        key={mode}
        mode={mode}
        creatorFlow={creatorFlow}
        panels={panels}
      />
    );
  }

  return mode === "creator" ? (
    <CreatorWorkspace creatorFlow={creatorFlow} panels={panels} />
  ) : (
    <ProfessionalWorkspace panels={panels} />
  );
}

function MobileWorkspace({
  mode,
  creatorFlow,
  panels,
}: {
  mode: EditorMode;
  creatorFlow: CreatorFlow;
  panels: EditorPanels;
}) {
  const [activeSheet, setActiveSheet] = useState<"left" | "right" | null>(null);
  const leftTrapRef = useFocusTrap(activeSheet === "left", () => setActiveSheet(null));
  const rightTrapRef = useFocusTrap(activeSheet === "right", () => setActiveSheet(null));

  const activeTask =
    creatorFlow.tasks.find((task) => task.key === creatorFlow.activeTaskKey) ??
    creatorFlow.tasks[0];

  return (
    <div className="relative flex min-h-0 flex-1 flex-col lg:hidden">
      <div className="fixed inset-x-0 bottom-0 z-40 flex items-center justify-around gap-1 border-t-2 border-foreground bg-surface p-1 shadow-brutal">
        <MobileTab
          label="Preview"
          icon={Eye}
          active={activeSheet === null}
          onClick={() => setActiveSheet(null)}
        />
        {mode === "professional" ? (
          <MobileTab
            label="Elements"
            icon={LayoutGrid}
            active={activeSheet === "left"}
            onClick={() => setActiveSheet("left")}
          />
        ) : null}
        <MobileTab
          label="Settings"
          icon={SlidersHorizontal}
          active={activeSheet === "right"}
          onClick={() => setActiveSheet("right")}
        />
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2 pb-20">
        {mode === "creator" ? (
          <section
            aria-label="Current creator task"
            className="shrink-0 overflow-hidden rounded border border-foreground bg-surface"
          >
            <CurrentTaskCard task={activeTask} onSelect={creatorFlow.onTaskSelect} />
          </section>
        ) : null}
        <section
          id="editor-preview"
          aria-labelledby="preview-heading"
          className="h-[min(42dvh,380px)] min-h-[280px] shrink-0 overflow-hidden rounded border border-foreground bg-surface"
        >
          {panels.centerPanel}
        </section>
        <section
          id="editor-timeline"
          aria-labelledby="timeline-heading"
          className="min-h-[220px] flex-1 overflow-hidden rounded border border-foreground bg-surface"
        >
          {panels.bottomPanel}
        </section>
      </div>

      {activeSheet === "left" ? (
        <EditorSheet
          panelRef={leftTrapRef}
          label="Element palette"
          onClose={() => setActiveSheet(null)}
        >
          {panels.leftPanel}
        </EditorSheet>
      ) : null}
      {activeSheet === "right" ? (
        <EditorSheet
          panelRef={rightTrapRef}
          label="Editor settings"
          onClose={() => setActiveSheet(null)}
        >
          {panels.rightPanel}
        </EditorSheet>
      ) : null}
    </div>
  );
}

function CreatorWorkspace({
  creatorFlow,
  panels,
}: {
  creatorFlow: CreatorFlow;
  panels: EditorPanels;
}) {
  const activeTask =
    creatorFlow.tasks.find((task) => task.key === creatorFlow.activeTaskKey) ??
    creatorFlow.tasks[0];

  return (
    <main
      className="hidden min-h-0 max-w-full flex-1 gap-3 overflow-hidden p-3 lg:grid lg:grid-cols-[minmax(0,1fr)_380px] lg:grid-rows-[minmax(0,1fr)_210px]"
      aria-label="Creator editor workspace"
      data-editor-workspace="creator"
    >
      <section
        id="editor-preview"
        aria-labelledby="preview-heading"
        className="overflow-hidden rounded border border-foreground bg-surface"
      >
        {panels.centerPanel}
      </section>
      <aside className="flex min-h-0 flex-col gap-3" aria-label="Guided editor settings">
        <section className="shrink-0 overflow-hidden rounded border border-foreground bg-surface">
          <CurrentTaskCard task={activeTask} onSelect={creatorFlow.onTaskSelect} />
        </section>
        <section className="min-h-0 flex-1 overflow-hidden rounded border border-foreground bg-surface">
          {panels.rightPanel}
        </section>
      </aside>
      <section
        id="editor-timeline"
        aria-labelledby="timeline-heading"
        className="col-span-2 overflow-hidden rounded border border-foreground bg-surface"
      >
        {panels.bottomPanel}
      </section>
    </main>
  );
}

function ProfessionalWorkspace({ panels }: { panels: EditorPanels }) {
  return (
    <main
      className="hidden min-h-0 max-w-full flex-1 gap-3 overflow-hidden p-3 lg:grid lg:grid-cols-[240px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)_220px]"
      aria-label="Professional editor workspace"
      data-editor-workspace="professional"
    >
      <section
        aria-labelledby="media-heading"
        className="overflow-hidden rounded border border-foreground bg-surface"
      >
        {panels.leftPanel}
      </section>
      <section
        id="editor-preview"
        aria-labelledby="preview-heading"
        className="overflow-hidden rounded border border-foreground bg-surface"
      >
        {panels.centerPanel}
      </section>
      <section
        aria-label="Editor settings"
        className="overflow-hidden rounded border border-foreground bg-surface"
      >
        {panels.rightPanel}
      </section>
      <section
        id="editor-timeline"
        aria-labelledby="timeline-heading"
        className="col-span-3 overflow-hidden rounded border border-foreground bg-surface"
      >
        {panels.bottomPanel}
      </section>
    </main>
  );
}

function MobileTab({
  label,
  icon: Icon,
  active,
  onClick,
}: {
  label: string;
  icon: ElementType;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "flex min-h-12 flex-1 flex-col items-center justify-center gap-1 rounded border border-transparent py-1 text-xs font-semibold transition-colors",
        active
          ? "border-foreground bg-primary text-primary-foreground"
          : "text-muted-foreground hover:bg-surface-elevated hover:text-foreground",
      )}
      onClick={onClick}
    >
      <Icon className="size-4" strokeWidth={2} />
      {label}
    </button>
  );
}

function EditorSheet({
  panelRef,
  label,
  onClose,
  children,
}: {
  panelRef: RefObject<HTMLDivElement | null>;
  label: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <>
      <button
        type="button"
        aria-label={`Close ${label}`}
        className="fixed inset-0 bottom-16 z-40 w-full bg-background/75 text-left"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="fixed inset-x-0 bottom-16 top-28 z-50 flex flex-col overflow-hidden border-x-2 border-t-2 border-foreground bg-surface shadow-brutal-lg"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-foreground bg-surface-elevated p-3">
          <h2 className="text-base font-semibold">{label}</h2>
          <button
            type="button"
            className="flex size-10 items-center justify-center rounded border border-foreground bg-surface hover:bg-muted"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="size-4" strokeWidth={2} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">{children}</div>
      </div>
    </>
  );
}
