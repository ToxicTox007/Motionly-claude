// Pure active-link resolver for the app shell navigation. No React, no DOM.
//
// activeHref(pathname, hrefs) returns the single href that should be marked
// active for the current pathname, or null. An href is a candidate when it
// equals the pathname exactly, or is a path-segment prefix of it (so "/projects"
// matches "/projects/abc" but not "/projectsx"). The longest matching href wins,
// so "/projects/new" beats "/projects" on the "/projects/new" route.

/**
 * @param {string} pathname current URL pathname
 * @param {string[]} hrefs candidate nav hrefs
 * @returns {string | null} the active href, or null if none match
 */
export function activeHref(pathname, hrefs) {
  if (typeof pathname !== "string" || !Array.isArray(hrefs)) return null;
  const path = normalize(pathname);
  let best = null;
  for (const raw of hrefs) {
    if (typeof raw !== "string") continue;
    const href = normalize(raw);
    if (!isMatch(path, href)) continue;
    if (best === null || href.length > best.length) best = href;
  }
  // Return the original (non-normalized) href the caller passed, so callers can
  // compare identity. Find it back by its normalized form.
  if (best === null) return null;
  return (
    hrefs.find((raw) => typeof raw === "string" && normalize(raw) === best) ??
    null
  );
}

// Strip a trailing slash (except for the root "/") so "/projects/" and
// "/projects" compare equal.
function normalize(value) {
  if (value.length > 1 && value.endsWith("/")) return value.slice(0, -1);
  return value;
}

// href matches path when identical, or when path continues href at a segment
// boundary ("/projects" vs "/projects/abc").
function isMatch(path, href) {
  if (path === href) return true;
  if (href === "/") return false;
  return path.startsWith(href + "/");
}
