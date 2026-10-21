import { useMemo } from "react";
import {
  useRegisterCommands,
  type PaletteCommand,
} from "@/components/command-palette";
import {
  buildEditorCommands,
  type EditorCommandDescriptor,
  type EditorJumpTarget,
  type EditorMode,
} from "@/lib/editor-commands.mjs";

interface UseEditorCommandsOptions {
  mode: EditorMode;
  setMode: (mode: EditorMode) => void;
  onExport: () => void;
  onAiEdit: () => void;
}

/** Move keyboard focus and scroll to one of the editor's landmark sections. */
function jumpToSection(target: EditorJumpTarget) {
  if (typeof document === "undefined") return;
  const element = document.getElementById(target);
  if (!element) return;
  element.focus({ preventScroll: false });
}

/**
 * Registers the editor's command-palette entries. Descriptors come from the
 * framework-free `buildEditorCommands`; this hook maps each `kind` to a `run`
 * callback and forwards the result to the shared command registry under the
 * "editor" source.
 */
export function useEditorCommands({
  mode,
  setMode,
  onExport,
  onAiEdit,
}: UseEditorCommandsOptions): void {
  const commands = useMemo<PaletteCommand[]>(() => {
    const descriptors = buildEditorCommands({ mode });
    return descriptors.map((descriptor: EditorCommandDescriptor) => {
      const { kind, target, ...rest } = descriptor;
      let run: () => void;
      switch (kind) {
        case "export":
          run = onExport;
          break;
        case "aiEdit":
          run = onAiEdit;
          break;
        case "toggleMode":
          run = () => setMode(mode === "creator" ? "professional" : "creator");
          break;
        case "jump":
        default:
          run = () => {
            if (target) jumpToSection(target);
          };
          break;
      }
      return { ...rest, run };
    });
  }, [mode, setMode, onExport, onAiEdit]);

  useRegisterCommands("editor", commands);
}
