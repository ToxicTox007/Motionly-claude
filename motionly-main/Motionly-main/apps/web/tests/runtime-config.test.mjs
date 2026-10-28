// Task 5: the web image is configured when it starts, not when it is built,
// and the browser reaches everything through one public origin:
//   <origin>/          Next.js
//   <origin>/api/      FastAPI
//   <origin>/supabase/ Supabase (Auth, REST, Storage) behind Kong
import assert from "node:assert/strict";
import test from "node:test";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webDir, "..", "..");
const read = (path) => readFileSync(resolve(webDir, path), "utf8");
const runtimeConfig = () => import("../lib/runtime-config.mjs");
const securityHeaders = () => import("../lib/security-headers.mjs");

const PRODUCTION_ENV = Object.freeze({
  APP_ENV: "production",
  PUBLIC_ORIGIN: "https://d111abcdef8.cloudfront.net",
  API_INTERNAL_URL: "http://api:8000",
  SUPABASE_URL: "http://supabase-gateway:8000",
  SUPABASE_ANON_KEY: "anon-key-value",
  BILLING_MODE: "disabled",
});

function directives(policy) {
  return Object.fromEntries(
    policy
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const [name, ...values] = part.split(/\s+/);
        return [name, values];
      }),
  );
}

function sourceFiles(directory) {
  const files = [];
  for (const entry of readdirSync(directory)) {
    if ([".next", "node_modules", "test-results", ".turbo"].includes(entry)) {
      continue;
    }
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) files.push(...sourceFiles(path));
    else if (/\.(ts|tsx|mts|mjs|js)$/.test(entry)) files.push(path);
  }
  return files;
}

const appSourceFiles = () =>
  ["app", "components", "hooks", "lib"]
    .flatMap((directory) => sourceFiles(resolve(webDir, directory)))
    .concat(
      ["proxy.ts", "next.config.ts"].map((file) => resolve(webDir, file)),
    );

test("the server reads its configuration from runtime variables", async () => {
  const { parseServerConfig } = await runtimeConfig();

  assert.deepEqual(parseServerConfig(PRODUCTION_ENV), {
    deployment: "production",
    publicOrigin: "https://d111abcdef8.cloudfront.net",
    apiInternalUrl: "http://api:8000",
    supabaseUrl: "http://supabase-gateway:8000",
    supabaseAnonKey: "anon-key-value",
    billingEnabled: false,
    billingMode: "disabled",
    skipAuthMiddleware: false,
    billingCheckoutOrigins: [],
  });
  // Build-time NEXT_PUBLIC_* variables are not configuration any more.
  assert.throws(
    () =>
      parseServerConfig({
        NODE_ENV: "production",
        NEXT_PUBLIC_APP_URL: "https://d111abcdef8.cloudfront.net",
        NEXT_PUBLIC_API_BASE_URL: "https://api.example.com",
        NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon-key-value",
      }),
    /PUBLIC_ORIGIN is required/,
  );
});

test("each missing runtime variable is named", async () => {
  const { parseServerConfig } = await runtimeConfig();
  for (const name of [
    "PUBLIC_ORIGIN",
    "API_INTERNAL_URL",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
  ]) {
    const environment = { ...PRODUCTION_ENV, [name]: undefined };
    assert.throws(
      () => parseServerConfig(environment),
      new RegExp(`${name} is required`),
      name,
    );
    // Compose passes unset variables as empty strings.
    assert.throws(
      () => parseServerConfig({ ...PRODUCTION_ENV, [name]: "" }),
      new RegExp(`${name} is required`),
      name,
    );
  }
});

test("boolean flags accept only the literals true and false", async () => {
  const { parseServerConfig } = await runtimeConfig();
  for (const [value, expected] of [
    ["true", true],
    ["false", false],
    [undefined, false],
    ["", false],
  ]) {
    assert.equal(
      parseServerConfig({ ...PRODUCTION_ENV, BILLING_ENABLED: value })
        .billingEnabled,
      expected,
      String(value),
    );
  }
  // z.coerce.boolean() used to turn "false" into true.
  for (const value of ["1", "0", "yes", "no", "TRUE", "False", "on", " true"]) {
    assert.throws(
      () => parseServerConfig({ ...PRODUCTION_ENV, BILLING_ENABLED: value }),
      /BILLING_ENABLED must be "true" or "false"/,
      value,
    );
    assert.throws(
      () =>
        parseServerConfig({
          ...PRODUCTION_ENV,
          APP_ENV: "development",
          SKIP_AUTH_MIDDLEWARE: value,
        }),
      /SKIP_AUTH_MIDDLEWARE must be "true" or "false"/,
      value,
    );
  }
});

test("production refuses the auth bypass and non-HTTPS, local, or path-bearing origins", async () => {
  const { parseServerConfig } = await runtimeConfig();

  assert.throws(
    () =>
      parseServerConfig({ ...PRODUCTION_ENV, SKIP_AUTH_MIDDLEWARE: "true" }),
    /SKIP_AUTH_MIDDLEWARE is only allowed when APP_ENV is development or local/,
  );
  for (const origin of [
    "http://d111abcdef8.cloudfront.net",
    "https://localhost:3000",
    "https://127.0.0.1",
    "https://app.build.motionly.invalid",
  ]) {
    assert.throws(
      () => parseServerConfig({ ...PRODUCTION_ENV, PUBLIC_ORIGIN: origin }),
      /PUBLIC_ORIGIN must be the deployed HTTPS origin/,
      origin,
    );
  }
  for (const origin of [
    "https://d111abcdef8.cloudfront.net/app",
    "https://d111abcdef8.cloudfront.net/?x=1",
    "not a url",
  ]) {
    assert.throws(
      () => parseServerConfig({ ...PRODUCTION_ENV, PUBLIC_ORIGIN: origin }),
      /PUBLIC_ORIGIN must be an origin/,
      origin,
    );
  }
  for (const key of ["replace-with-supabase-anon-key", "build-placeholder"]) {
    assert.throws(
      () => parseServerConfig({ ...PRODUCTION_ENV, SUPABASE_ANON_KEY: key }),
      /SUPABASE_ANON_KEY must be the deployment's anon key/,
    );
  }
  assert.throws(
    () => parseServerConfig({ ...PRODUCTION_ENV, APP_ENV: "staging" }),
    /APP_ENV must be one of/,
  );
});

test("the web runtime config states the billing mode explicitly (B17)", async () => {
  const { parseServerConfig } = await runtimeConfig();

  // Production must state BILLING_MODE; there is no default.
  const { BILLING_MODE: _omit, ...withoutMode } = PRODUCTION_ENV;
  assert.throws(
    () => parseServerConfig(withoutMode),
    /BILLING_MODE is required in production/,
  );

  // Only the two supported values are accepted (no stripe/live mode).
  for (const mode of ["stripe", "live", "enabled", "yes"]) {
    assert.throws(
      () => parseServerConfig({ ...PRODUCTION_ENV, BILLING_MODE: mode }),
      /BILLING_MODE must be "disabled" or "demo"/,
      mode,
    );
  }

  assert.equal(
    parseServerConfig({ ...PRODUCTION_ENV, BILLING_MODE: "disabled" })
      .billingMode,
    "disabled",
  );
  assert.equal(
    parseServerConfig({
      ...PRODUCTION_ENV,
      BILLING_MODE: "demo",
      BILLING_ENABLED: "true",
    }).billingMode,
    "demo",
  );

  // Non-production derives the mode from BILLING_ENABLED when it is unset.
  assert.equal(
    parseServerConfig({
      ...PRODUCTION_ENV,
      APP_ENV: "cpu-stage",
      BILLING_MODE: undefined,
    }).billingMode,
    "disabled",
  );
});

test("the CPU stage and local development may use plain-HTTP loopback origins", async () => {
  const { parseServerConfig } = await runtimeConfig();

  const cpuStage = parseServerConfig({
    ...PRODUCTION_ENV,
    APP_ENV: "cpu-stage",
    PUBLIC_ORIGIN: "http://127.0.0.1:18080",
  });
  assert.equal(cpuStage.deployment, "cpu-stage");
  assert.equal(cpuStage.publicOrigin, "http://127.0.0.1:18080");
  // The CPU stage is a production-like stack: no auth bypass there either.
  assert.throws(
    () =>
      parseServerConfig({
        ...PRODUCTION_ENV,
        APP_ENV: "cpu-stage",
        PUBLIC_ORIGIN: "http://127.0.0.1:18080",
        SKIP_AUTH_MIDDLEWARE: "true",
      }),
    /SKIP_AUTH_MIDDLEWARE is only allowed/,
  );

  const development = parseServerConfig({
    NODE_ENV: "development",
    PUBLIC_ORIGIN: "http://127.0.0.1:3000",
    API_INTERNAL_URL: "http://127.0.0.1:8000",
    SUPABASE_URL: "http://127.0.0.1:54321",
    SUPABASE_ANON_KEY: "local-anon-key",
    SKIP_AUTH_MIDDLEWARE: "true",
  });
  assert.equal(development.deployment, "development");
  assert.equal(development.skipAuthMiddleware, true);

  // Without APP_ENV, a production Node runtime is treated as production.
  assert.equal(
    parseServerConfig({
      ...PRODUCTION_ENV,
      APP_ENV: undefined,
      NODE_ENV: "production",
    }).deployment,
    "production",
  );
});

test("the page carries only public values, serialized safely for HTML", async () => {
  const {
    parseServerConfig,
    publicConfig,
    serializePublicConfig,
    parsePublicConfig,
  } = await runtimeConfig();

  const config = parseServerConfig({
    ...PRODUCTION_ENV,
    BILLING_ENABLED: "true",
  });
  assert.deepEqual(publicConfig(config), {
    publicOrigin: "https://d111abcdef8.cloudfront.net",
    supabaseAnonKey: "anon-key-value",
    billingEnabled: true,
  });

  const hostile = {
    publicOrigin: "https://d111abcdef8.cloudfront.net",
    supabaseAnonKey: "</script><script>alert(1)</script>\u2028&",
    billingEnabled: false,
  };
  const serialized = serializePublicConfig(hostile);
  assert.doesNotMatch(serialized, /[<>&\u2028\u2029]/);
  assert.deepEqual(parsePublicConfig(serialized), hostile);

  assert.throws(
    () =>
      parsePublicConfig(JSON.stringify({ ...hostile, billingEnabled: "yes" })),
    /public configuration is invalid/,
  );
  assert.throws(() => parsePublicConfig(""), /public configuration is missing/);
});

test("the browser reaches Supabase at an absolute same-origin URL (N3)", async () => {
  const { browserSupabaseUrl } = await runtimeConfig();
  const { createClient } = await import("@supabase/supabase-js");

  const url = browserSupabaseUrl("http://127.0.0.1:3000");
  assert.equal(url, "http://127.0.0.1:3000/supabase");
  const client = createClient(url, "anon-key-value");
  assert.equal(client.storage.url, "http://127.0.0.1:3000/supabase/storage/v1");
  // The old relative value is rejected by supabase-js.
  assert.throws(
    () => createClient("/supabase", "anon-key-value"),
    /Invalid supabaseUrl/,
  );
});

test("server and browser Supabase clients share one auth cookie", async () => {
  const { AUTH_COOKIE_NAME, authCookieOptions } = await runtimeConfig();
  const { createServerClient } = await import("@supabase/ssr");
  const cookies = { getAll: () => [], setAll: () => {} };

  // Without a fixed name, @supabase/ssr names the cookie after the Supabase
  // host, so the server (internal URL) and the browser (public URL) would
  // each look for a different cookie.
  const keys = [
    "http://supabase-gateway:8000",
    "https://d111abcdef8.cloudfront.net/supabase",
  ].map(
    (url) =>
      createServerClient(url, "anon-key-value", {
        cookies,
        cookieOptions: authCookieOptions("https://d111abcdef8.cloudfront.net"),
      }).auth.storageKey,
  );
  assert.deepEqual(keys, [AUTH_COOKIE_NAME, AUTH_COOKIE_NAME]);

  assert.equal(
    authCookieOptions("https://d111abcdef8.cloudfront.net").secure,
    true,
  );
  assert.equal(authCookieOptions("http://127.0.0.1:18080").secure, false);
  for (const path of [
    "lib/supabase/browser.ts",
    "lib/supabase/server.ts",
    "proxy.ts",
  ]) {
    assert.match(read(path), /cookieOptions:\s*authCookieOptions\(/, path);
  }
});

test("the CSP uses a per-request nonce instead of unsafe-inline scripts", async () => {
  const { buildContentSecurityPolicy, createNonce } = await securityHeaders();

  const first = createNonce();
  const second = createNonce();
  assert.notEqual(first, second);
  assert.match(first, /^[A-Za-z0-9+/]{22,}={0,2}$/);

  const production = directives(
    buildContentSecurityPolicy({
      nonce: "abc123",
      publicOrigin: "https://d111abcdef8.cloudfront.net",
      development: false,
    }),
  );
  assert.deepEqual(production["script-src"], [
    "'self'",
    "'nonce-abc123'",
    "'strict-dynamic'",
  ]);
  assert.deepEqual(production["connect-src"], [
    "'self'",
    "wss://d111abcdef8.cloudfront.net",
  ]);
  assert.ok("upgrade-insecure-requests" in production);
  assert.deepEqual(production["frame-ancestors"], ["'none'"]);
  assert.deepEqual(production["object-src"], ["'none'"]);
  assert.deepEqual(production["base-uri"], ["'self'"]);
  assert.deepEqual(production["form-action"], ["'self'"]);

  // Plain-HTTP stacks (CPU stage, local) must not upgrade requests to HTTPS.
  const plainHttp = directives(
    buildContentSecurityPolicy({
      nonce: "abc123",
      publicOrigin: "http://127.0.0.1:18080",
      development: false,
    }),
  );
  assert.equal("upgrade-insecure-requests" in plainHttp, false);
  assert.deepEqual(plainHttp["connect-src"], [
    "'self'",
    "ws://127.0.0.1:18080",
  ]);

  const development = directives(
    buildContentSecurityPolicy({
      nonce: "abc123",
      publicOrigin: "http://127.0.0.1:3000",
      development: true,
    }),
  );
  assert.ok(development["script-src"].includes("'unsafe-eval'"));
  for (const policy of [production, plainHttp, development]) {
    assert.equal(policy["script-src"].includes("'unsafe-inline'"), false);
  }
});

test("the proxy sets the CSP on every page response and protects app routes", () => {
  const proxy = read("proxy.ts");

  assert.match(proxy, /createNonce\(\)/);
  assert.match(proxy, /buildContentSecurityPolicy\(/);
  assert.match(proxy, /"x-nonce"/);
  assert.match(proxy, /response\.headers\.set\(\s*"content-security-policy"/);

  const matcher = proxy.match(/source:\s*"([^"]+)"/);
  assert.ok(matcher, "proxy matcher source not found");
  const pattern = new RegExp(`^${matcher[1].replaceAll("\\\\", "\\")}$`);
  for (const path of [
    "/",
    "/login",
    "/pricing",
    "/dashboard",
    "/projects/1",
    "/review/abc",
  ]) {
    assert.match(path, pattern, path);
  }
  for (const path of [
    "/_next/static/chunks/app.js",
    "/_next/image",
    "/icon.svg",
  ]) {
    assert.doesNotMatch(path, pattern, path);
  }
  // A single CSP source: next.config.ts no longer sets one.
  assert.doesNotMatch(
    read("next.config.ts"),
    /key:\s*"Content-Security-Policy"/i,
  );
});

test("the root layout injects the public configuration and nonces its inline script", () => {
  const layout = read("app/layout.tsx");

  assert.match(layout, /await headers\(\)/);
  assert.match(layout, /\.get\("x-nonce"\)/);
  assert.match(layout, /nonce=\{nonce\}/);
  assert.match(layout, /type="application\/json"/);
  assert.match(layout, /serializePublicConfig\(/);
});

test("no build-time NEXT_PUBLIC_ configuration remains", () => {
  const files = [
    ...appSourceFiles(),
    resolve(webDir, "Dockerfile"),
    resolve(webDir, "playwright.config.mjs"),
    resolve(webDir, ".env.example"),
    resolve(repoRoot, ".env.example"),
    ...readdirSync(resolve(repoRoot, "infra"))
      .filter((name) => /^docker-compose.*\.yml$|\.env\.example$/.test(name))
      .map((name) => resolve(repoRoot, "infra", name)),
  ];
  // A variable name, not the generic "NEXT_PUBLIC_*" in an explanation.
  const offenders = files.filter((file) =>
    /NEXT_PUBLIC_[A-Z]/.test(readFileSync(file, "utf8")),
  );
  assert.deepEqual(
    offenders.map((file) => relative(repoRoot, file)),
    [],
  );
});

test("flags are read through the runtime config, and resolveProxiedUrl is gone", () => {
  for (const file of appSourceFiles()) {
    const source = readFileSync(file, "utf8");
    const name = relative(webDir, file);
    assert.doesNotMatch(source, /resolveProxiedUrl/, name);
    if (name !== join("lib", "runtime-config.mjs")) {
      assert.doesNotMatch(source, /process\.env\.SKIP_AUTH_MIDDLEWARE/, name);
    }
  }
});

test("redirects use the configured public origin", () => {
  // Behind the edge, Next.js builds request.url from its own listen address
  // (http://localhost:3000), and the Origin/X-Forwarded-* headers come from the
  // viewer. Absolute URLs must come from PUBLIC_ORIGIN instead.
  const files = [
    "proxy.ts",
    "app/app-api/workspaces/select/route.ts",
    "app/(auth)/actions.ts",
  ];
  for (const file of files) {
    const source = read(file);
    assert.doesNotMatch(source, /new URL\([^)]*request\.url\)/, file);
    assert.doesNotMatch(
      source,
      /get\("(origin|x-forwarded-host|x-forwarded-proto)"\)/,
      file,
    );
  }
  assert.match(read("proxy.ts"), /new URL\("\/login", config\.publicOrigin\)/);
  assert.match(read("lib/public-url.ts"), /new URL\(path, env\.appUrl\)/);
  assert.match(
    read("app/app-api/workspaces/select/route.ts"),
    /publicUrl\("\/dashboard"\)/,
  );
});

test("Next route handlers live under /app-api because /api belongs to FastAPI", () => {
  assert.equal(existsSync(resolve(webDir, "app/api")), false);
  for (const route of [
    "app/app-api/health/route.ts",
    "app/app-api/workspaces/select/route.ts",
  ]) {
    assert.ok(existsSync(resolve(webDir, route)), route);
  }
  assert.match(
    read("components/workspace-picker.tsx"),
    /action="\/app-api\/workspaces\/select"/,
  );
  // The health endpoint is public; it must not reveal private-network URLs.
  assert.doesNotMatch(read("app/app-api/health/route.ts"), /url: apiUrl/);
});

const CONFIG_VARIABLES = [
  "APP_ENV",
  "PUBLIC_ORIGIN",
  "API_INTERNAL_URL",
  "SUPABASE_URL",
  "SUPABASE_ANON_KEY",
  "BILLING_ENABLED",
  "BILLING_MODE",
  "SKIP_AUTH_MIDDLEWARE",
];

async function withEnvironment(values, run) {
  const saved = Object.fromEntries(
    CONFIG_VARIABLES.map((name) => [name, process.env[name]]),
  );
  const apply = (entries) => {
    for (const name of CONFIG_VARIABLES) {
      if (entries[name] === undefined) delete process.env[name];
      else process.env[name] = entries[name];
    }
  };
  apply(values);
  try {
    return await run();
  } finally {
    apply(saved);
  }
}

const nextPhases = () => import("next/constants.js");
const loadNextConfig = async () => (await import("../next.config.ts")).default;

test("the build is a standalone server traced from the monorepo root", async () => {
  const { PHASE_PRODUCTION_BUILD } = await nextPhases();
  const nextConfig = await loadNextConfig();
  const config = nextConfig(PHASE_PRODUCTION_BUILD);

  // The image ships .next/standalone (server.js plus only the traced
  // node_modules); tracing from the monorepo root includes packages/shared.
  assert.equal(config.output, "standalone");
  assert.equal(config.outputFileTracingRoot, repoRoot);
});

test("Next proxies /api and /supabase only in development; the edge does it when deployed", async () => {
  const {
    PHASE_DEVELOPMENT_SERVER,
    PHASE_PRODUCTION_BUILD,
    PHASE_PRODUCTION_SERVER,
  } = await nextPhases();
  const nextConfig = await loadNextConfig();
  assert.equal(typeof nextConfig, "function");

  await withEnvironment(PRODUCTION_ENV, async () => {
    for (const phase of [PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER]) {
      const config = nextConfig(phase);
      assert.deepEqual(await config.rewrites(), [], phase);
      assert.equal(config.allowedDevOrigins, undefined, phase);
      assert.equal(
        config.experimental?.serverActions?.allowedOrigins,
        undefined,
        phase,
      );
    }
  });

  await withEnvironment(
    {
      PUBLIC_ORIGIN: "http://127.0.0.1:3000",
      API_INTERNAL_URL: "http://127.0.0.1:8000",
      SUPABASE_URL: "http://127.0.0.1:54321",
      SUPABASE_ANON_KEY: "local-anon-key",
    },
    async () => {
      assert.deepEqual(await nextConfig(PHASE_DEVELOPMENT_SERVER).rewrites(), [
        { source: "/api/:path*", destination: "http://127.0.0.1:8000/:path*" },
        {
          source: "/supabase/:path*",
          destination: "http://127.0.0.1:54321/:path*",
        },
      ]);
    },
  );
});

test("a server refuses to start with invalid runtime configuration; a build needs none", async () => {
  const { PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER } =
    await nextPhases();
  const nextConfig = await loadNextConfig();

  await withEnvironment({}, async () => {
    assert.doesNotThrow(() => nextConfig(PHASE_PRODUCTION_BUILD));
    assert.throws(
      () => nextConfig(PHASE_PRODUCTION_SERVER),
      /PUBLIC_ORIGIN is required/,
    );
  });
  await withEnvironment(
    { ...PRODUCTION_ENV, SKIP_AUTH_MIDDLEWARE: "true" },
    async () => {
      assert.throws(
        () => nextConfig(PHASE_PRODUCTION_SERVER),
        /SKIP_AUTH_MIDDLEWARE is only allowed/,
      );
    },
  );
  await withEnvironment(PRODUCTION_ENV, async () => {
    assert.doesNotThrow(() => nextConfig(PHASE_PRODUCTION_SERVER));
  });
});

test("extra checkout origins are validated, de-duplicated and never public", async () => {
  const { parseServerConfig, publicConfig } = await runtimeConfig();
  const config = parseServerConfig({
    ...PRODUCTION_ENV,
    BILLING_CHECKOUT_ORIGINS:
      " https://pay.example.com, https://pay.example.com ,https://pay2.example.com/",
  });
  assert.deepEqual(config.billingCheckoutOrigins, [
    "https://pay.example.com",
    "https://pay2.example.com",
  ]);
  assert.equal("billingCheckoutOrigins" in publicConfig(config), false);
  assert.throws(
    () =>
      parseServerConfig({
        ...PRODUCTION_ENV,
        BILLING_CHECKOUT_ORIGINS: "https://pay.example.com/checkout",
      }),
    /BILLING_CHECKOUT_ORIGINS must be an origin/,
  );
  assert.throws(
    () =>
      parseServerConfig({
        ...PRODUCTION_ENV,
        BILLING_CHECKOUT_ORIGINS: "javascript:alert(1)",
      }),
    /BILLING_CHECKOUT_ORIGINS must be an origin/,
  );
});
