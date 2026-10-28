// Same-origin check for state-changing route handlers (server actions get
// Next's own check; route handlers do not). A cross-site page can make the
// browser POST a form here, and SameSite=Lax cookies do not stop a same-site
// subdomain, so the browser-set Origin must name this site.

/**
 * True when the request comes from this site: its Origin's host is the public
 * origin's host or the host the request was addressed to (Host /
 * X-Forwarded-Host, which a cross-site page cannot set). Without an Origin,
 * Sec-Fetch-Site decides; a request with neither (not a browser form post) is
 * allowed, as the auth cookie is what authorizes it.
 */
export function isSameSiteRequest(
  headers: Headers,
  publicOrigin: string,
): boolean {
  const origin = headers.get("origin");
  if (origin) {
    if (origin === "null") return false;
    let host: string;
    try {
      host = new URL(origin).host;
    } catch {
      return false;
    }
    const allowed = new Set<string>();
    try {
      allowed.add(new URL(publicOrigin).host);
    } catch {
      // A bad public origin allows nothing extra.
    }
    for (const name of ["x-forwarded-host", "host"]) {
      const value = headers.get(name)?.split(",")[0]?.trim();
      if (value) allowed.add(value);
    }
    return allowed.has(host);
  }
  const site = headers.get("sec-fetch-site");
  if (site) return site === "same-origin" || site === "none";
  return true;
}
