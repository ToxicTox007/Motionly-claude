"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";

export type DurableHistoryAction = {
  path: string;
  body: Record<string, unknown>;
  resultKind: "plan" | "caption" | "broll" | "music";
  successLabel: string;
};

export type HistoryEntry = {
  id: string;
  label: string;
  undo?: () => Promise<string> | string;
  redo?: () => Promise<string> | string;
  undoAction?: DurableHistoryAction;
  redoAction?: DurableHistoryAction;
};

type PersistedHistoryEntry = Pick<HistoryEntry, "id" | "label" | "undoAction" | "redoAction">;

interface EditorHistoryContextValue {
  pushHistory: (entry: HistoryEntry) => void;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  canUndo: boolean;
  canRedo: boolean;
  currentLabel: string | null;
}

const EditorHistoryContext = createContext<EditorHistoryContextValue | null>(null);

const HISTORY_LIMIT = 50;

export function EditorHistoryProvider({
  children,
  storageKey,
  executeAction,
}: {
  children: React.ReactNode;
  storageKey: string;
  executeAction: (action: DurableHistoryAction) => Promise<string>;
}) {
  const [undoStack, setUndoStack] = useState<HistoryEntry[]>(() => readStack(storageKey, "undo"));
  const [redoStack, setRedoStack] = useState<HistoryEntry[]>(() => readStack(storageKey, "redo"));
  const [currentLabel, setCurrentLabel] = useState<string | null>(null);

  const pushHistory = useCallback((entry: HistoryEntry) => {
    if (!entry.undoAction || !entry.redoAction) {
      throw new Error("Editor history entries must include durable undo and redo actions.");
    }
    setUndoStack((current) => [...current.slice(-(HISTORY_LIMIT - 1)), entry]);
    setRedoStack([]);
  }, []);

  useEffect(() => persistStack(storageKey, "undo", undoStack), [storageKey, undoStack]);
  useEffect(() => persistStack(storageKey, "redo", redoStack), [redoStack, storageKey]);

  const runEntry = useCallback(async (direction: "undo" | "redo", entry: HistoryEntry) => {
    setCurrentLabel(`${entry.label}: ${direction === "undo" ? "undoing" : "redoing"}…`);
    try {
      const callback = direction === "undo" ? entry.undo : entry.redo;
      const action = direction === "undo" ? entry.undoAction : entry.redoAction;
      const label = callback ? await callback() : action ? await executeAction(action) : entry.label;
      setCurrentLabel(label);
    } catch (error) {
      setCurrentLabel((error as Error).message);
      throw error;
    }
  }, [executeAction]);

  const undo = useCallback(async () => {
    const entry = undoStack[undoStack.length - 1];
    if (!entry) return;
    await runEntry("undo", entry);
    setUndoStack((current) => current.slice(0, -1));
    setRedoStack((current) => [...current.slice(-(HISTORY_LIMIT - 1)), entry]);
  }, [runEntry, undoStack]);

  const redo = useCallback(async () => {
    const entry = redoStack[redoStack.length - 1];
    if (!entry) return;
    await runEntry("redo", entry);
    setRedoStack((current) => current.slice(0, -1));
    setUndoStack((current) => [...current.slice(-(HISTORY_LIMIT - 1)), entry]);
  }, [redoStack, runEntry]);

  const value: EditorHistoryContextValue = {
    pushHistory,
    undo,
    redo,
    canUndo: undoStack.length > 0,
    canRedo: redoStack.length > 0,
    currentLabel,
  };

  return (
    <EditorHistoryContext.Provider value={value}>
      {children}
    </EditorHistoryContext.Provider>
  );
}

function readStack(storageKey: string, direction: "undo" | "redo"): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const value = window.localStorage.getItem(`${storageKey}:${direction}`);
    const entries = value ? JSON.parse(value) as PersistedHistoryEntry[] : [];
    return entries.filter((entry) => entry.undoAction && entry.redoAction).slice(-HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function persistStack(storageKey: string, direction: "undo" | "redo", entries: HistoryEntry[]) {
  try {
    const serializable: PersistedHistoryEntry[] = entries.map(({ id, label, undoAction, redoAction }) => ({
      id, label, undoAction, redoAction,
    }));
    window.localStorage.setItem(`${storageKey}:${direction}`, JSON.stringify(serializable));
  } catch {
    // History persistence is best-effort when storage is disabled or full.
  }
}

export function useEditorHistory() {
  const context = useContext(EditorHistoryContext);
  if (!context) {
    throw new Error("useEditorHistory must be used within an EditorHistoryProvider");
  }
  return context;
}
