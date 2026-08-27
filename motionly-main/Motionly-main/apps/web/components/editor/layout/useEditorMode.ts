import { useCallback, useEffect, useSyncExternalStore } from "react";
import type { EditorMode } from "./types";

export const EDITOR_MODE_STORAGE_KEY = "motionly:editor-mode";
export const EDITOR_MODE_SHORTCUT = "Alt+Shift+M";
const EDITOR_MODE_CHANGE_EVENT = "motionly:editor-mode-change";
const DEFAULT_EDITOR_MODE: EditorMode = "creator";
let inMemoryEditorMode: EditorMode = DEFAULT_EDITOR_MODE;

function isEditorMode(value: string | null): value is EditorMode {
  return value === "creator" || value === "professional";
}

function readEditorMode(): EditorMode {
  try {
    const storedMode = window.localStorage.getItem(EDITOR_MODE_STORAGE_KEY);
    if (isEditorMode(storedMode)) inMemoryEditorMode = storedMode;
  } catch {
    // Storage can be disabled by browser privacy/security settings.
  }
  return inMemoryEditorMode;
}

function subscribeToEditorMode(onStoreChange: () => void) {
  window.addEventListener("storage", onStoreChange);
  window.addEventListener(EDITOR_MODE_CHANGE_EVENT, onStoreChange);
  return () => {
    window.removeEventListener("storage", onStoreChange);
    window.removeEventListener(EDITOR_MODE_CHANGE_EVENT, onStoreChange);
  };
}

export function useEditorMode() {
  const mode = useSyncExternalStore(
    subscribeToEditorMode,
    readEditorMode,
    () => DEFAULT_EDITOR_MODE,
  );
  const setMode = useCallback((nextMode: EditorMode) => {
    inMemoryEditorMode = nextMode;
    try {
      window.localStorage.setItem(EDITOR_MODE_STORAGE_KEY, nextMode);
    } catch {
      // The in-memory preference still keeps mode switching functional.
    }
    window.dispatchEvent(new Event(EDITOR_MODE_CHANGE_EVENT));
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!event.altKey || !event.shiftKey || event.key.toLowerCase() !== "m") {
        return;
      }
      event.preventDefault();
      setMode(mode === "creator" ? "professional" : "creator");
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mode, setMode]);

  return { mode, setMode };
}
