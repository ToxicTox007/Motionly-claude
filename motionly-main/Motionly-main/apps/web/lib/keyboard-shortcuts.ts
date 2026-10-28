import { hasOpenFocusTrap } from "@/lib/useFocusTrap";

// Shared guard for window-level keyboard shortcuts (editor timeline J/K/L,
// undo/redo, the command palette), so none of them fires while someone types,
// composes text with an IME, uses a browser chord, or works inside a dialog.

/** Input types that take text; checkboxes, radios and buttons do not. */
const NON_TEXT_INPUT_TYPES = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

function asElement(target: EventTarget | null): Element | null {
  if (!target || typeof Element === "undefined") return null;
  return target instanceof Element ? target : null;
}

function inContentEditable(element: Element) {
  if (element instanceof HTMLElement && element.isContentEditable) return true;
  // jsdom and some embedded editors do not report isContentEditable, so also
  // honour the attribute itself; contenteditable="false" opts back out.
  const host = element.closest("[contenteditable]");
  return host !== null && host.getAttribute("contenteditable") !== "false";
}

/** True for textareas and rich-text regions, where Ctrl/Cmd chords such as
 * Ctrl+K (insert link) already mean something. */
export function isRichTextTarget(target: EventTarget | null) {
  const element = asElement(target);
  if (!element) return false;
  return element.tagName === "TEXTAREA" || inContentEditable(element);
}

/** True when the key press is text entry: a text input, textarea, select or
 * contenteditable region (including anything nested inside one). */
export function isEditableTarget(target: EventTarget | null) {
  const element = asElement(target);
  if (!element) return false;
  if (element.tagName === "SELECT" || isRichTextTarget(element)) return true;
  if (element.tagName === "INPUT") {
    const type = (element.getAttribute("type") ?? "text").toLowerCase();
    return !NON_TEXT_INPUT_TYPES.has(type);
  }
  return false;
}

function hasCommandModifier(event: KeyboardEvent) {
  // AltGr reports as Ctrl+Alt on Windows; it produces characters such as "["
  // on many layouts, so it is not a chord.
  if (event.getModifierState?.("AltGraph")) return false;
  return event.ctrlKey || event.metaKey || event.altKey;
}

export type ShortcutGuardOptions = {
  /**
   * Single-key shortcuts (J, K, L, [, ]) set this so Ctrl/Cmd/Alt chords, such
   * as Ctrl+L for the address bar, reach the browser untouched. Shift is not a
   * modifier here: it is part of typing the character.
   */
  requireNoModifiers?: boolean;
  /**
   * "all" (default) skips every editable target. "rich-text" skips only
   * textareas and contenteditable regions, so a chord like Ctrl+K still works
   * from a single-line input such as a search box.
   */
  editable?: "all" | "rich-text";
  /** Keep the shortcut live while a dialog or sheet is open. */
  allowInDialog?: boolean;
};

/** Whether a window-level shortcut handler should leave this event alone. */
export function shouldIgnoreShortcut(
  event: KeyboardEvent,
  options: ShortcutGuardOptions = {},
) {
  if (event.defaultPrevented || event.isComposing) return true;
  if (options.requireNoModifiers && hasCommandModifier(event)) return true;

  const target = event.target;
  const editable =
    options.editable === "rich-text"
      ? isRichTextTarget(target)
      : isEditableTarget(target);
  if (editable) return true;

  if (!options.allowInDialog) {
    if (hasOpenFocusTrap()) return true;
    if (asElement(target)?.closest('[role="dialog"], [role="alertdialog"]')) {
      return true;
    }
  }
  return false;
}
