// Copying is best effort: the async Clipboard API is missing in insecure
// contexts and some browsers, and a write can be refused. Callers get an
// honest result instead of assuming success.

export type CopyResult = { ok: boolean };

export async function copyText(text: string): Promise<CopyResult> {
  try {
    const clipboard =
      typeof navigator === "undefined" ? undefined : navigator.clipboard;
    if (!clipboard?.writeText) return { ok: false };
    await clipboard.writeText(text);
    return { ok: true };
  } catch {
    return { ok: false };
  }
}

/** Selects an element's text, so a failed copy can fall back to Ctrl+C / ⌘C. */
export function selectElementText(element: HTMLElement | null): boolean {
  if (!element || typeof window === "undefined") return false;
  const selection = window.getSelection();
  if (!selection) return false;
  const range = document.createRange();
  range.selectNodeContents(element);
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}
