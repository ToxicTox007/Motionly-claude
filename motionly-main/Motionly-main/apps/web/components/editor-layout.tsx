import type React from "react";
import { CreatorProgressRail } from "./editor/layout/CreatorProgressRail";
import { EditorToolbar } from "./editor/layout/EditorToolbar";
import { EditorWorkspaces } from "./editor/layout/EditorWorkspaces";
import type {
  CreatorFlow,
  EditorCommandBar,
  EditorPanels,
} from "./editor/layout/types";
import { useEditorMode } from "./editor/layout/useEditorMode";

export type { CreatorTask } from "./editor/layout/types";

interface EditorLayoutProps {
  title: string;
  onTitleChange: (title: string) => void;
  onTitleCommit: (title: string) => void;
  saveStatus?: "saving" | "saved" | "error";
  onExport: () => void;
  onAiEdit: () => void;
  creatorFlow: CreatorFlow;
  commandBar: EditorCommandBar;
  children: EditorPanels;
}

export function EditorLayout({
  title,
  onTitleChange,
  onTitleCommit,
  saveStatus = "saved",
  onExport,
  onAiEdit,
  creatorFlow,
  commandBar,
  children,
}: EditorLayoutProps) {
  const { mode, setMode } = useEditorMode();

  return (
    <div
      className="editor-shell flex min-h-[100dvh] flex-col overflow-x-hidden bg-background lg:h-screen lg:overflow-hidden"
      data-editor-mode={mode}
    >
      <a href="#editor-preview" className="skip-link">
        Skip to preview
      </a>
      <a href="#editor-timeline" className="skip-link">
        Skip to timeline
      </a>
      <a href="#editor-export" className="skip-link">
        Skip to export settings
      </a>

      <EditorToolbar
        title={title}
        onTitleChange={onTitleChange}
        onTitleCommit={onTitleCommit}
        saveStatus={saveStatus}
        onExport={onExport}
        onAiEdit={onAiEdit}
        mode={mode}
        onModeChange={setMode}
        commandBar={commandBar}
      />

      {mode === "creator" ? (
        <CreatorProgressRail
          flow={creatorFlow}
          onExport={onExport}
          onAiEdit={onAiEdit}
        />
      ) : null}

      <EditorWorkspaces
        mode={mode}
        creatorFlow={creatorFlow}
        panels={children}
      />

      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {mode === "creator"
          ? "Creator mode active. Guided tools and the current task are shown."
          : "Professional mode active. Element palette, inspector, and full timeline are shown."}
      </p>
    </div>
  );
}
