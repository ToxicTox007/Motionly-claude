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
  const leftTrapRef = useFocusTrap(activeSheet === "left", () =>
    setActiveSheet(null),
  );
  const rightTrapRef = useFocusTrap(activeSheet === "right", () =>
    setActiveSheet(null),
  );

  const activeTask =
    creatorFlow.tasks.find((task) => task.key === creatorFlow.activeTaskKey) ??
    creatorFlow.tasks[0];

  return (
    <div className="relative flex min-h-0 flex-1 flex-col lg:hidden">
      <div className="fixed inset-x-0 bottom-0 z-40 mx-auto mb-2 flex w-[calc(100%-1rem)] max-w-md items-center justify-around gap-1 rounded-full bg-surface p-1.5 shadow-neu-raised-lg">
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

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-3 pb-24">
        {mode === "creator" ? (
          <section
            aria-label="Current creator task"
            className="shrink-0 overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised"
          >
            <CurrentTaskCard
              task={activeTask}
              onSelect={creatorFlow.onTaskSelect}
            />
          </section>
        ) : null}
        <section
          id="editor-preview"
          tabIndex={-1}
          aria-labelledby="preview-heading"
          className="h-[min(42dvh,380px)] min-h-[280px] shrink-0 overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          {panels.centerPanel}
        </section>
        <section
          id="editor-timeline"
          tabIndex={-1}
          aria-labelledby="timeline-heading"
          className="min-h-[220px] flex-1 overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
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
      className="hidden min-h-0 max-w-full flex-1 gap-4 overflow-hidden p-3 lg:grid lg:grid-cols-[minmax(0,1fr)_380px] lg:grid-rows-[minmax(0,1fr)_210px]"
      aria-label="Creator editor workspace"
      data-editor-workspace="creator"
    >
      <section
        id="editor-preview"
        tabIndex={-1}
        aria-labelledby="preview-heading"
        className="overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        {panels.centerPanel}
      </section>
      <aside
        className="flex min-h-0 flex-col gap-4"
        aria-label="Guided editor settings"
      >
        <section className="shrink-0 overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised">
          <CurrentTaskCard
            task={activeTask}
            onSelect={creatorFlow.onTaskSelect}
          />
        </section>
        <section className="min-h-0 flex-1 overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised">
          {panels.rightPanel}
        </section>
      </aside>
      <section
        id="editor-timeline"
        tabIndex={-1}
        aria-labelledby="timeline-heading"
        className="col-span-2 overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        {panels.bottomPanel}
      </section>
    </main>
  );
}

function ProfessionalWorkspace({ panels }: { panels: EditorPanels }) {
  return (
    <main
      className="hidden min-h-0 max-w-full flex-1 gap-4 overflow-hidden p-3 lg:grid lg:grid-cols-[240px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)_220px]"
      aria-label="Professional editor workspace"
      data-editor-workspace="professional"
    >
      <section
        aria-labelledby="media-heading"
        className="overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised"
      >
        {panels.leftPanel}
      </section>
      <section
        id="editor-preview"
        tabIndex={-1}
        aria-labelledby="preview-heading"
        className="overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        {panels.centerPanel}
      </section>
      <section
        aria-label="Editor settings"
        className="overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised"
      >
        {panels.rightPanel}
      </section>
      <section
        id="editor-timeline"
        tabIndex={-1}
        aria-labelledby="timeline-heading"
        className="col-span-3 overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
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
        "flex min-h-12 flex-1 flex-col items-center justify-center gap-1 rounded-full py-1 text-xs font-semibold transition-shadow duration-200",
        active
          ? "bg-primary text-primary-foreground shadow-neu-glow"
          : "text-muted-foreground hover:text-foreground",
      )}
      onClick={onClick}
    >
      <Icon className="size-4" strokeWidth={1.75} />
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
        className="fixed inset-0 bottom-16 z-40 w-full bg-foreground/20 text-left backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="fixed inset-x-2 bottom-20 top-28 z-50 flex flex-col overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised-lg"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-hairline bg-surface-elevated p-3">
          <h2 className="text-base font-semibold">{label}</h2>
          <button
            type="button"
            className="neu-pressable flex size-10 items-center justify-center rounded-neu bg-surface text-foreground shadow-neu-raised-sm transition-shadow duration-200 hover:shadow-neu-raised"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="size-4" strokeWidth={1.75} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">{children}</div>
      </div>
    </>
  );
}
