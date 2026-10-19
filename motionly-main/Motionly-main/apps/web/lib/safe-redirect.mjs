// Where the login page may send a viewer afterwards (B14). Only paths on this
// site are accepted; anything a browser could read as another site falls back.
// Browsers treat "\" like "/" and ignore tabs and newlines in URLs, so
// "/\evil.example" and "/\t/evil.example" mean "//evil.example".
const UNSAFE_CHARACTERS = /[\\\u0000-\u001f\u007f]/;

export function safeNextPath(value, origin, fallback = "/dashboard") {
  const candidate = typeof value === "string" ? value : "";
  if (!candidate || UNSAFE_CHARACTERS.test(candidate)) return fallback;
  if (!candidate.startsWith("/") && !candidate.startsWith(`${origin}/`)) {
    return fallback;
  }
  if (candidate.startsWith("//")) return fallback;
  let url;
  try {
    url = new URL(candidate, origin);
  } catch {
    return fallback;
  }
  const path = `${url.pathname}${url.search}${url.hash}`;
  // Dot segments can still normalize to "//host".
  if (url.origin !== new URL(origin).origin || path.startsWith("//")) {
    return fallback;
  }
  return path;
}
