// Where a billing action may send the viewer for checkout. The API returns the
// gateway's checkout URL; following any URL it hands back would turn a
// compromised or misconfigured API into an open redirect. Accepted:
//   - the app's own public origin, under /pay/checkout/ (the demo gateway is
//     routed there by the edge), or
//   - an origin listed in BILLING_CHECKOUT_ORIGINS (an external gateway).

export const CHECKOUT_PATH_PREFIX = "/pay/checkout/";

export function isAllowedCheckoutUrl(
  candidate: unknown,
  publicOrigin: string,
  extraOrigins: readonly string[] = [],
): boolean {
  if (typeof candidate !== "string" || candidate.trim() !== candidate) {
    return false;
  }
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return false;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return false;
  if (url.username || url.password) return false;
  let ownOrigin: string;
  try {
    ownOrigin = new URL(publicOrigin).origin;
  } catch {
    return false;
  }
  if (url.origin === ownOrigin) {
    return url.pathname.startsWith(CHECKOUT_PATH_PREFIX);
  }
  return extraOrigins.includes(url.origin);
}
