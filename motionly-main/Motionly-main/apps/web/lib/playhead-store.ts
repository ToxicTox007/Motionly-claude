import { createContext, useContext, useSyncExternalStore } from "react";

// The editor playhead changes several times a second during playback. Kept in
// React state at the top of the editor, every tick re-rendered the whole
// editor (toolbar, inspector, tabs). It lives in this small store instead:
// only components that call usePlayheadMs() (the timeline and the preview)
// re-render as it moves; everything else reads it on demand with get().

export type PlayheadStore = {
  get: () => number;
  set: (next: number | ((previous: number) => number)) => void;
  subscribe: (listener: () => void) => () => void;
};

export function createPlayheadStore(initialMs = 0): PlayheadStore {
  let current = initialMs;
  const listeners = new Set<() => void>();
  return {
    get: () => current,
    set(next) {
      const value = typeof next === "function" ? next(current) : next;
      if (Object.is(value, current)) return;
      current = value;
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export const PlayheadStoreContext = createContext<PlayheadStore | null>(null);

export function usePlayheadStore(): PlayheadStore {
  const store = useContext(PlayheadStoreContext);
  if (!store) {
    throw new Error("usePlayheadStore must be used inside the editor.");
  }
  return store;
}

/** The current playhead; the calling component re-renders as it moves. */
export function usePlayheadMs(): number {
  const store = usePlayheadStore();
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}
