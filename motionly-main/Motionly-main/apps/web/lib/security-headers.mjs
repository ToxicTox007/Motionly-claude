// Per-request security headers. The Content-Security-Policy carries a fresh
// nonce for every response, so proxy.ts sets it (next.config.ts sets only the
// static headers). Next.js reads the nonce from the request's CSP header and
// applies it to its own scripts; the root layout applies it to ours.

/** 128 random bits, base64-encoded. */
export function createNonce() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

function websocketOrigin(origin) {
  return origin.replace(/^http/, "ws");
}

/**
 * Everything the browser calls lives on the one public origin ('self'):
 * /api, /supabase, and Supabase Realtime over a same-origin WebSocket.
 */
export function buildContentSecurityPolicy({
  nonce,
  publicOrigin,
  development,
}) {
  const scriptSources = [
    "'self'",
    `'nonce-${nonce}'`,
    "'strict-dynamic'",
    // React Refresh evaluates code in development only.
    ...(development ? ["'unsafe-eval'"] : []),
  ];
  return [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "frame-src 'none'",
    "form-action 'self'",
    `script-src ${scriptSources.join(" ")}`,
    // React style attributes need 'unsafe-inline' for styles (not scripts).
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data: https:",
    "media-src 'self' blob: data: https:",
    "font-src 'self' data:",
    "worker-src 'self' blob:",
    ["connect-src", "'self'", websocketOrigin(publicOrigin)].join(" "),
    "manifest-src 'self'",
    // Upgrading on a plain-HTTP stack (CPU stage, local) would break every load.
    ...(publicOrigin.startsWith("https:") ? ["upgrade-insecure-requests"] : []),
  ].join("; ");
}
