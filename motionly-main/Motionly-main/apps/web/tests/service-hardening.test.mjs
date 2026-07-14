import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const config = readFileSync(resolve(here, "../next.config.ts"), "utf8");

test("Next applies practical browser security headers to every route", () => {
  assert.match(config, /source: "\/:path\*"/);
  for (const header of [
    "Content-Security-Policy",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Permissions-Policy",
  ]) {
    assert.ok(config.includes(header), `missing ${header}`);
  }
  assert.match(config, /frame-ancestors 'none'/);
  assert.match(config, /object-src 'none'/);
});

test("CSP retains API, Supabase, media, websocket, and observability connectivity", () => {
  assert.match(config, /configuredApiOrigin = originOf\(apiUrl\)/);
  assert.match(config, /configuredSupabaseOrigin = originOf\(supabaseUrl\)/);
  assert.match(config, /configuredSupabaseWebsocketOrigin = websocketOrigin\(supabaseUrl\)/);
  assert.match(config, /`media-src \$\{\[/);
  assert.match(config, /sentry\.io/);
  assert.match(config, /glitchtip\.com/);
});
