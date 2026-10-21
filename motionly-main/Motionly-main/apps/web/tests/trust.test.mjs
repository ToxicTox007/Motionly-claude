import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("runtime config refuses placeholder keys and the auth bypass in production", async () => {
  const { parseServerConfig } = await import("../lib/runtime-config.mjs");
  const production = {
    APP_ENV: "production",
    PUBLIC_ORIGIN: "https://d111abcdef8.cloudfront.net",
    API_INTERNAL_URL: "http://api:8000",
    SUPABASE_URL: "http://supabase-gateway:8000",
    // Production must state its billing mode (Task 18, B17).
    BILLING_MODE: "disabled",
  };

  for (const key of ["build-placeholder", "replace-with-supabase-anon-key"]) {
    assert.throws(
      () => parseServerConfig({ ...production, SUPABASE_ANON_KEY: key }),
      /SUPABASE_ANON_KEY must be the deployment's anon key/,
    );
  }
  assert.throws(
    () =>
      parseServerConfig({
        ...production,
        SUPABASE_ANON_KEY: "anon-key-value",
        SKIP_AUTH_MIDDLEWARE: "true",
      }),
    /SKIP_AUTH_MIDDLEWARE is only allowed/,
  );
});

test("client-api.ts catches and friendly-maps AbortError and network failures", async () => {
  const source = await readFile(new URL("lib/client-api.ts", root), "utf8");

  // Verify try-catch fetch wrappers
  assert.match(source, /err\.name === "AbortError"/);
  assert.match(source, /code: "request_aborted"/);
  assert.match(source, /code: "network_failure"/);
  assert.match(source, /Network connection lost/);
});

test("billing page labels demo payments and the disabled state honestly", async () => {
  const source = await readFile(
    new URL("app/(app)/billing/page.tsx", root),
    "utf8",
  );

  // Task 23: payments run through the mock gateway; every payment surface says
  // so, and disabled billing says it is disabled.
  assert.match(
    source,
    /const DEMO_NOTICE = "Demo payment — no money is charged";/,
  );
  assert.match(source, /Billing Integration Offline/);
  assert.match(source, /Billing is currently disabled\./);
  assert.match(source, /Only the owner can manage billing/);
  // The Stripe-era sandbox copy is gone.
  assert.doesNotMatch(source, /Sandbox|Mock Plan|Stripe/i);
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
