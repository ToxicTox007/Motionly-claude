// Pure, framework-free logic for the ⌘K command palette. Kept here (an .mjs
// with a sibling .d.mts) so `node --test` can import it without React. The
// client component in components/command-palette.tsx composes these.

/** Fixed display order for palette groups. Groups outside this list, or empty
 * ones, are omitted by groupCommands. */
export const PALETTE_GROUP_ORDER = [
  "Navigate",
  "Create",
  "Editor",
  "Account",
  "Help",
];

/**
 * True only for the palette's open shortcut: the "k" key with exactly one of
 * Meta or Ctrl held, no Alt, no Shift, and not mid-IME-composition. This lets
 * Cmd/Ctrl+K open the palette while plain "k" stays free for the editor
 * timeline's pause shortcut.
 */
export function isPaletteShortcut(event) {
  if (!event) return false;
  if (event.isComposing) return false;
  const key = event.key;
  if (key !== "k" && key !== "K") return false;
  if (event.altKey || event.shiftKey) return false;
  const meta = Boolean(event.metaKey);
  const ctrl = Boolean(event.ctrlKey);
  // Exactly one of Meta/Ctrl.
  return meta !== ctrl;
}

/**
 * Flatten a Map<source, commands[]> into a single de-duplicated, stable list.
 * Later entries with the same id replace earlier ones (latest registration
 * wins) while keeping first-seen position so the list does not jump around.
 * Map iteration order is insertion order, so "latest source wins".
 */
export function mergeCommands(sources) {
  /** @type {Map<string, any>} */
  const byId = new Map();
  const order = [];
  if (sources) {
    for (const commands of sources.values()) {
      if (!commands) continue;
      for (const command of commands) {
        if (!command || typeof command.id !== "string") continue;
        if (!byId.has(command.id)) order.push(command.id);
        byId.set(command.id, command);
      }
    }
  }
  return order.map((id) => byId.get(id));
}

/**
 * Group a flat command list into [group, commands] pairs in PALETTE_GROUP_ORDER,
 * omitting any group that ends up empty. Within a group, original order is kept.
 */
export function groupCommands(commands) {
  /** @type {Map<string, any[]>} */
  const buckets = new Map();
  for (const command of commands ?? []) {
    if (!command) continue;
    const group = command.group;
    if (!PALETTE_GROUP_ORDER.includes(group)) continue;
    const bucket = buckets.get(group);
    if (bucket) bucket.push(command);
    else buckets.set(group, [command]);
  }
  const result = [];
  for (const group of PALETTE_GROUP_ORDER) {
    const bucket = buckets.get(group);
    if (bucket && bucket.length > 0) result.push([group, bucket]);
  }
  return result;
}

/**
 * A stable string that changes only when the meaningful shape of the command
 * list changes (ids, labels, groups, hrefs, actions). Used by the client hook
 * to decide whether to re-register, so passing a fresh array literal with the
 * same contents on every render does not cause an update loop.
 */
export function commandKey(commands) {
  if (!commands || commands.length === 0) return "[]";
  return JSON.stringify(
    commands.map((command) => [
      command?.id ?? "",
      command?.label ?? "",
      command?.group ?? "",
      command?.href ?? "",
      command?.action ?? "",
      command?.shortcut ?? "",
    ]),
  );
}
