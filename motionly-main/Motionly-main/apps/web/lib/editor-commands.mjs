/**
 * Framework-free descriptors for the editor's command-palette entries.
 *
 * This module is intentionally DOM- and React-free so it can be unit-tested
 * with node:test. It returns serializable descriptors describing WHAT each
 * command is (its id, label, group, keywords, shortcut and a `kind`
 * discriminator). The React hook `useEditorCommands` maps each `kind` to a
 * concrete `run` callback and registers the result with the command palette.
 *
 * The scroll-target command kinds mirror the editor skip links
 * (#editor-preview, #editor-timeline, #editor-export).
 */

/** @typedef {import("./editor-commands.d.mts").EditorMode} EditorMode */
/** @typedef {import("./editor-commands.d.mts").EditorCommandDescriptor} EditorCommandDescriptor */

export const EDITOR_COMMAND_GROUP = "Editor";
export const EDITOR_MODE_COMMAND_SHORTCUT = "Alt+Shift+M";

/**
 * Build the ordered list of editor command descriptors for the given mode.
 *
 * @param {{ mode: EditorMode }} options
 * @returns {EditorCommandDescriptor[]}
 */
export function buildEditorCommands({ mode }) {
  const switchesToProfessional = mode === "creator";
  return [
    {
      id: "editor.export",
      label: "Export video",
      group: EDITOR_COMMAND_GROUP,
      kind: "export",
      keywords: ["render", "download", "publish", "mp4"],
      icon: "export",
    },
    {
      id: "editor.ai-edit",
      label: "Open AI edit",
      group: EDITOR_COMMAND_GROUP,
      kind: "aiEdit",
      keywords: ["ai", "assist", "auto", "draft", "generate"],
      icon: "ai-cut",
    },
    {
      id: "editor.toggle-mode",
      label: switchesToProfessional
        ? "Switch to Professional mode"
        : "Switch to Creator mode",
      group: EDITOR_COMMAND_GROUP,
      kind: "toggleMode",
      keywords: ["mode", "workspace", "creator", "professional", "switch"],
      shortcut: EDITOR_MODE_COMMAND_SHORTCUT,
      icon: "settings",
    },
    {
      id: "editor.jump-preview",
      label: "Jump to preview",
      group: EDITOR_COMMAND_GROUP,
      kind: "jump",
      target: "editor-preview",
      keywords: ["preview", "canvas", "player", "video"],
      icon: "play",
    },
    {
      id: "editor.jump-timeline",
      label: "Jump to timeline",
      group: EDITOR_COMMAND_GROUP,
      kind: "jump",
      target: "editor-timeline",
      keywords: ["timeline", "clips", "tracks"],
      icon: "project",
    },
    {
      id: "editor.jump-export",
      label: "Jump to export settings",
      group: EDITOR_COMMAND_GROUP,
      kind: "jump",
      target: "editor-export",
      keywords: ["export", "settings", "output", "format"],
      icon: "export",
    },
  ];
}
