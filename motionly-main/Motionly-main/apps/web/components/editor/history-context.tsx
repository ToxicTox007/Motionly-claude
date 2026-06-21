"use client";

import React, { createContext, useCallback, useContext, useState } from "react";

export type HistoryEntry = {
  id: string;
  label: string;
  undo: () => Promise<string> | string;
  redo: () => Promise<string> | string;
};

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

export function EditorHistoryProvider({ children }: { children: React.ReactNode }) {
  const [undoStack, setUndoStack] = useState<HistoryEntry[]>([]);
  const [redoStack, setRedoStack] = useState<HistoryEntry[]>([]);
  const [currentLabel, setCurrentLabel] = useState<string | null>(null);

  const pushHistory = useCallback((entry: HistoryEntry) => {
    setUndoStack((current) => [...current.slice(-(HISTORY_LIMIT - 1)), entry]);
    setRedoStack([]);
  }, []);

  const runEntry = useCallback(async (direction: "undo" | "redo", entry: HistoryEntry) => {
    setCurrentLabel(`${entry.label}: ${direction === "undo" ? "undoing" : "redoing"}…`);
    try {
      const label = await (direction === "undo" ? entry.undo() : entry.redo());
      setCurrentLabel(label);
    } catch (error) {
      setCurrentLabel((error as Error).message);
      throw error;
    }
  }, []);

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

export function useEditorHistory() {
  const context = useContext(EditorHistoryContext);
  if (!context) {
    throw new Error("useEditorHistory must be used within an EditorHistoryProvider");
  }
  return context;
}
