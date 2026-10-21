export type PaletteGroup =
  | "Navigate"
  | "Create"
  | "Editor"
  | "Account"
  | "Help";

export type PaletteCommand = {
  id: string;
  label: string;
  group: PaletteGroup;
  href?: string;
  action?: "logout";
  run?: () => void;
  keywords?: string[];
  icon?: string;
  shortcut?: string;
};

export const PALETTE_GROUP_ORDER: PaletteGroup[];

export function isPaletteShortcut(event: {
  key?: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  isComposing?: boolean;
}): boolean;

export function mergeCommands(
  sources: Map<string, PaletteCommand[]>,
): PaletteCommand[];

export function groupCommands(
  commands: PaletteCommand[],
): Array<[PaletteGroup, PaletteCommand[]]>;

export function commandKey(commands: PaletteCommand[]): string;
