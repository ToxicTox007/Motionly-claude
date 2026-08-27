import type React from "react";

export type EditorMode = "creator" | "professional";

export type CreatorTaskStatus =
  | "done"
  | "active"
  | "waiting"
  | "blocked"
  | "skipped";

export type CreatorTask = {
  key: string;
  label: string;
  shortLabel: string;
  description: string;
  status: CreatorTaskStatus;
  progress?: number | null;
  detail?: string | null;
  actionLabel?: string | null;
};

export type CreatorFlow = {
  tasks: CreatorTask[];
  activeTaskKey: string;
  progressPct: number;
  workflowStatusLabel?: string | null;
  workflowStatusDetail?: string | null;
  onTaskSelect?: (task: CreatorTask) => void;
};

export type EditorCommandBar = {
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onRetry: () => void;
  onCancel: () => void;
  hasRetry: boolean;
  hasCancel: boolean;
  isPending: boolean;
  error?: string | null;
  jobLabel: string;
};

export type EditorPanels = {
  leftPanel: React.ReactNode;
  centerPanel: React.ReactNode;
  rightPanel: React.ReactNode;
  bottomPanel: React.ReactNode;
};
