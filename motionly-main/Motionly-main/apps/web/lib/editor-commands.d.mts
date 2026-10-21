import type { PixelIconName } from "@/components/pixel-icons";

export type EditorMode = "creator" | "professional";

/** Scroll/focus targets that mirror the editor skip-link ids. */
export type EditorJumpTarget =
  | "editor-preview"
  | "editor-timeline"
  | "editor-export";

/** Discriminator the React hook maps to a concrete `run` callback. */
export type EditorCommandKind = "export" | "aiEdit" | "toggleMode" | "jump";

export type EditorCommandDescriptor = {
  id: string;
  label: string;
  group: "Editor";
  kind: EditorCommandKind;
  keywords?: string[];
  shortcut?: string;
  icon?: PixelIconName;
  /** Present only when `kind` is "jump". */
  target?: EditorJumpTarget;
};

export const EDITOR_COMMAND_GROUP: "Editor";
export const EDITOR_MODE_COMMAND_SHORTCUT: string;

export function buildEditorCommands(options: {
  mode: EditorMode;
}): EditorCommandDescriptor[];
