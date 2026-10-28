"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";

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
  /** Plan the entry was recorded against; stamped by the provider. */
  planId?: string | null;
};

type PersistedHistoryEntry = Pick<
  HistoryEntry,
  "id" | "label" | "undoAction" | "redoAction" | "planId"
>;

interface EditorHistoryContextValue {
  pushHistory: (entry: HistoryEntry) => void;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  canUndo: boolean;
  canRedo: boolean;
  /** An undo or redo is running; further presses are ignored until it ends. */
  isPending: boolean;
  currentLabel: string | null;
}

const EditorHistoryContext = createContext<EditorHistoryContextValue | null>(
  null,
);

const HISTORY_LIMIT = 50;
const HISTORY_PERSIST_DEBOUNCE_MS = 250;

type HistorySnapshot = {
  undo: HistoryEntry[];
  redo: HistoryEntry[];
  pending: boolean;
  currentLabel: string | null;
};

// What the server renders and what hydration starts from. Stacks are read from
// localStorage only on the client, so the first client render matches the
// server HTML, then the restored history appears.
const SERVER_SNAPSHOT: HistorySnapshot = {
  undo: [],
  redo: [],
  pending: false,
  currentLabel: null,
};
const getServerSnapshot = () => SERVER_SNAPSHOT;

/**
 * The stacks live outside React state so undo/redo read the true top entry
 * and the in-flight flag synchronously: a second Ctrl+Z pressed before the
 * first request returns sees `pending` and does nothing, instead of replaying
 * the same entry from a stale render closure.
 */
function createHistoryStore(storageKey: string, planId: string | null) {
  let snapshot: HistorySnapshot | null = null;
  const listeners = new Set<() => void>();

  function getSnapshot(): HistorySnapshot {
    if (snapshot === null) {
      snapshot = {
        undo: readStack(storageKey, "undo", planId),
        redo: readStack(storageKey, "redo", planId),
        pending: false,
        currentLabel: null,
      };
    }
    return snapshot;
  }

  return {
    getSnapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    update(change: Partial<HistorySnapshot>) {
      snapshot = { ...getSnapshot(), ...change };
      for (const listener of listeners) listener();
    },
  };
}

function pushLimited(stack: HistoryEntry[], entry: HistoryEntry) {
  return [...stack.slice(-(HISTORY_LIMIT - 1)), entry];
}

function withoutEntry(stack: HistoryEntry[], entry: HistoryEntry) {
  return stack[stack.length - 1] === entry
    ? stack.slice(0, -1)
    : stack.filter((item) => item !== entry);
}

/**
 * Durable undo/redo for one editor session. `storageKey` scopes the persisted
 * stacks (callers include workspace, project, user and plan), and `planId`
 * drops any restored entry recorded against a different plan, so a history
 * saved for one plan is never replayed onto another.
 */
export function EditorHistoryProvider({
  children,
  storageKey,
  planId = null,
  executeAction,
}: {
  children: React.ReactNode;
  storageKey: string;
  planId?: string | null;
  executeAction: (action: DurableHistoryAction) => Promise<string>;
}) {
  const store = useMemo(
    () => createHistoryStore(storageKey, planId),
    [storageKey, planId],
  );
  const snapshot = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    getServerSnapshot,
  );
  const hydrated = snapshot !== SERVER_SNAPSHOT;
  const { undo: undoStack, redo: redoStack, pending, currentLabel } = snapshot;

  const pushHistory = useCallback(
    (entry: HistoryEntry) => {
      if (!entry.undoAction || !entry.redoAction) {
        throw new Error(
          "Editor history entries must include durable undo and redo actions.",
        );
      }
      store.update({
        undo: pushLimited(store.getSnapshot().undo, { ...entry, planId }),
        redo: [],
      });
    },
    [planId, store],
  );

  useDebouncedHistoryPersistence(
    storageKey,
    "undo",
    hydrated ? undoStack : null,
  );
  useDebouncedHistoryPersistence(
    storageKey,
    "redo",
    hydrated ? redoStack : null,
  );

  const runEntry = useCallback(
    async (direction: "undo" | "redo") => {
      const current = store.getSnapshot();
      if (current.pending) return;
      const source = direction === "undo" ? current.undo : current.redo;
      const entry = source[source.length - 1];
      if (!entry) return;

      store.update({
        pending: true,
        currentLabel: `${entry.label}: ${direction === "undo" ? "undoing" : "redoing"}…`,
      });
      try {
        const callback = direction === "undo" ? entry.undo : entry.redo;
        const action =
          direction === "undo" ? entry.undoAction : entry.redoAction;
        const label = callback
          ? await callback()
          : action
            ? await executeAction(action)
            : entry.label;
        const latest = store.getSnapshot();
        store.update(
          direction === "undo"
            ? {
                undo: withoutEntry(latest.undo, entry),
                redo: pushLimited(latest.redo, entry),
                pending: false,
                currentLabel: label,
              }
            : {
                redo: withoutEntry(latest.redo, entry),
                undo: pushLimited(latest.undo, entry),
                pending: false,
                currentLabel: label,
              },
        );
      } catch (error) {
        store.update({
          pending: false,
          currentLabel: (error as Error).message,
        });
        throw error;
      }
    },
    [executeAction, store],
  );

  const undo = useCallback(() => runEntry("undo"), [runEntry]);
  const redo = useCallback(() => runEntry("redo"), [runEntry]);

  const value = useMemo<EditorHistoryContextValue>(
    () => ({
      pushHistory,
      undo,
      redo,
      canUndo: undoStack.length > 0,
      canRedo: redoStack.length > 0,
      isPending: pending,
      currentLabel,
    }),
    [currentLabel, pending, pushHistory, redo, redoStack, undo, undoStack],
  );

  return (
    <EditorHistoryContext.Provider value={value}>
      {children}
    </EditorHistoryContext.Provider>
  );
}

function useDebouncedHistoryPersistence(
  storageKey: string,
  direction: "undo" | "redo",
  entries: HistoryEntry[] | null,
) {
  useEffect(() => {
    // Nothing to save until the stored history has been read on the client.
    if (entries === null) return;
    const timer = window.setTimeout(() => {
      persistStack(storageKey, direction, entries);
    }, HISTORY_PERSIST_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [direction, entries, storageKey]);
}

function readStack(
  storageKey: string,
  direction: "undo" | "redo",
  planId: string | null,
): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const value = window.localStorage.getItem(`${storageKey}:${direction}`);
    const entries = value ? (JSON.parse(value) as PersistedHistoryEntry[]) : [];
    if (!Array.isArray(entries)) return [];
    return entries
      .filter(
        (entry) =>
          entry?.undoAction &&
          entry.redoAction &&
          (entry.planId ?? null) === planId,
      )
      .slice(-HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function persistStack(
  storageKey: string,
  direction: "undo" | "redo",
  entries: HistoryEntry[],
) {
  try {
    const serializable: PersistedHistoryEntry[] = entries.map(
      ({ id, label, undoAction, redoAction, planId }) => ({
        id,
        label,
        undoAction,
        redoAction,
        planId: planId ?? null,
      }),
    );
    window.localStorage.setItem(
      `${storageKey}:${direction}`,
      JSON.stringify(serializable),
    );
  } catch {
    // History persistence is best-effort when storage is disabled or full.
  }
}

export function useEditorHistory() {
  const context = useContext(EditorHistoryContext);
  if (!context) {
    throw new Error(
      "useEditorHistory must be used within an EditorHistoryProvider",
    );
  }
  return context;
}
