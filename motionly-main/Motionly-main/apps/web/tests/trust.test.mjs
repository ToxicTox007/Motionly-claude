import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("env.ts implements placeholder protection and production restrictions", async () => {
  const source = await readFile(new URL("lib/env.ts", root), "utf8");

  // Verify production check includes SUPABASE_ANON_KEY security violations
  assert.match(source, /const finalSupabaseAnonKey =/);
  assert.match(source, /finalSupabaseAnonKey === "build-placeholder"/);
  assert.match(source, /throw new Error\("Security violation/);
});

test("client-api.ts catches and friendly-maps AbortError and network failures", async () => {
  const source = await readFile(new URL("lib/client-api.ts", root), "utf8");

  // Verify try-catch fetch wrappers
  assert.match(source, /err\.name === "AbortError"/);
  assert.match(source, /code: "request_aborted"/);
  assert.match(source, /code: "network_failure"/);
  assert.match(source, /Network connection lost/);
});

test("billing page contains sandbox warnings and Mock Plan badges", async () => {
  const source = await readFile(
    new URL("app/(app)/billing/page.tsx", root),
    "utf8",
  );

  // Verify banner titles and Mock Plan badge text
  assert.match(source, /Billing Sandbox Preview/);
  assert.match(source, /Billing Integration Offline/);
  assert.match(source, /Manage sandbox billing/);
  assert.match(source, /Preview checkout/);
  assert.match(source, /Mock Plan/);
});

test("admin ops page sanitizes provider costs and integrates AdminJobsList", async () => {
  const source = await readFile(
    new URL("app/(app)/admin/ops/page.tsx", root),
    "utf8",
  );

  // Verify placeholder filtering and fallback messages
  assert.match(source, /No cost data yet/);
  assert.match(source, /!keyLower\.includes\("placeholder"\)/);
  assert.match(source, /!valString\.includes\("placeholder"\)/);

  // Verify AdminJobsList integration
  assert.match(source, /import \{ AdminJobsList \} from/);
  assert.match(source, /<AdminJobsList/);
});
