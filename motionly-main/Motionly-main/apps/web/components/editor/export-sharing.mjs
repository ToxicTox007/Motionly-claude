/**
 * Shared, framework-free logic for export share links and the public watch
 * page. Kept in a .mjs module so it can be unit-tested with node:test without a
 * DOM, and imported by both the editor Exports panel and the watch page.
 */

/** Allowed share-link lifetimes, mirroring the server's ShareLinkExpiry enum. */
export const SHARE_LINK_EXPIRY_OPTIONS = [
  { value: 1, label: "1 day" },
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
];

/**
 * Build the absolute watch URL for a share link. The secret token lives in the
 * URL *fragment* (#t=...) so it is never sent to the server during navigation
 * and never lands in request logs, referrers, or access logs.
 *
 * @param {string} origin - e.g. "https://app.motionly.com"
 * @param {string} token - the one-time secret returned at creation
 */
export function buildWatchUrl(origin, token) {
  const base = String(origin || "").replace(/\/+$/, "");
  return `${base}/watch#t=${encodeURIComponent(token)}`;
}

/**
 * Read the token from a watch-page URL fragment. Accepts "#t=..." or a bare
 * fragment and tolerates extra fragment params. Returns null when absent.
 *
 * @param {string} hash - window.location.hash
 */
export function readTokenFromHash(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  if (!raw) return null;
  const params = new URLSearchParams(raw);
  const token = params.get("t");
  if (token && token.trim()) return token.trim();
  // Also accept a bare fragment that is just the token.
  if (!raw.includes("=") && raw.trim()) return raw.trim();
  return null;
}

/**
 * Decide how to share a link. Phones expose navigator.share (the native share
 * sheet); everything else copies the link. Returns "native" or "copy".
 *
 * @param {object} nav - a navigator-like object (or undefined)
 */
export function shareStrategy(nav) {
  if (nav && typeof nav.share === "function") return "native";
  return "copy";
}

/** True once a share link can no longer be resolved by a viewer. */
export function isShareLinkInactive(link, now = new Date()) {
  if (!link) return true;
  if (link.revoked_at) return true;
  const expires = Date.parse(link.expires_at);
  if (Number.isFinite(expires) && expires <= now.getTime()) return true;
  return false;
}

/** Human-readable state for a share link row. */
export function shareLinkStatusLabel(link, now = new Date()) {
  if (!link) return "Unknown";
  if (link.revoked_at) return "Revoked";
  if (isShareLinkInactive(link, now)) return "Expired";
  return link.allow_download ? "Active · downloads on" : "Active · watch only";
}
