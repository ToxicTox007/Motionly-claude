// Helpers for the public share pages (/review/[shareId], /watch). Their access
// token travels only in the URL fragment, which browsers never send to a
// server. The pages read it once, keep it for this tab (so a reload works),
// and remove it from the address bar and history.

export const NETWORK_ERROR_MESSAGE =
  "We couldn't reach Motionly. Check your connection and try again.";

/** An error whose message is written for the viewer and safe to show. */
export class PublicLinkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PublicLinkError";
  }
}

/** The viewer-facing message for a failure: our own copy, a connection
 * problem, or the fallback; never a raw "Failed to fetch" or parser error. */
export function publicErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof PublicLinkError) return error.message;
  // fetch() rejects with a TypeError when the network request itself fails.
  if (error instanceof TypeError) return NETWORK_ERROR_MESSAGE;
  return fallback;
}

export type FragmentToken =
  | { status: "ok"; token: string }
  | { status: "missing" }
  | { status: "invalid" };

/** Reads a bare token fragment (`#<token>`), percent-decoded. A malformed
 * escape is reported as invalid instead of throwing. */
export function readFragmentToken(hash: string): FragmentToken {
  const raw = hash.replace(/^#/, "");
  if (!raw) return { status: "missing" };
  try {
    const token = decodeURIComponent(raw).trim();
    return token ? { status: "ok", token } : { status: "missing" };
  } catch {
    return { status: "invalid" };
  }
}

/** Drops the fragment from the address bar and the current history entry. */
export function clearUrlFragment() {
  if (typeof window === "undefined" || !window.location.hash) return;
  const { pathname, search } = window.location;
  window.history.replaceState(window.history.state, "", pathname + search);
}

/** Session-only memory of a share token, so reloading the tab still works
 * after the fragment is removed. Sign-out clears motionly-prefixed keys. */
export function rememberShareToken(key: string, token: string) {
  try {
    window.sessionStorage.setItem(key, token);
  } catch {
    // Storage disabled: the page still works until it is reloaded.
  }
}

export function recallShareToken(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
