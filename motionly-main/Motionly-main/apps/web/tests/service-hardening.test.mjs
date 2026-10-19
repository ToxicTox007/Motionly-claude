import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const config = readFileSync(resolve(here, "../next.config.ts"), "utf8");
const csp = readFileSync(resolve(here, "../lib/security-headers.mjs"), "utf8");

test("Next applies practical browser security headers to every route", () => {
  assert.match(config, /source: "\/:path\*"/);
  for (const header of [
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Permissions-Policy",
  ]) {
    assert.ok(config.includes(header), `missing ${header}`);
  }
  // The CSP carries a per-request nonce, so proxy.ts sets it on each response.
  assert.match(csp, /frame-ancestors 'none'/);
  assert.match(csp, /object-src 'none'/);
});

test("CSP keeps same-origin API, Supabase, media, and websocket connectivity", () => {
  assert.match(csp, /"connect-src"/);
  assert.match(csp, /websocketOrigin\(publicOrigin\)/);
  assert.match(csp, /"media-src 'self' blob: data: https:"/);
});

test("CSP does not allow the retired third-party error-monitoring services", () => {
  for (const source of [config, csp]) {
    assert.doesNotMatch(source, /sentry\.io/);
    assert.doesNotMatch(source, /glitchtip\.com/);
  }
});

test("sign-in offers only email and password, and errors are not reported to a third party", () => {
  const read = (path) => readFileSync(resolve(here, "..", path), "utf8");
  for (const path of [
    "app/(auth)/login/page.tsx",
    "app/(auth)/signup/page.tsx",
    "app/(auth)/actions.ts",
    "lib/env.ts",
  ]) {
    assert.doesNotMatch(
      read(path),
      /signInWithOAuth|Continue with Google|GOOGLE_OAUTH/,
      path,
    );
  }
  assert.doesNotMatch(
    read("app/global-error.tsx"),
    /captureException|has been reported/,
  );
});
