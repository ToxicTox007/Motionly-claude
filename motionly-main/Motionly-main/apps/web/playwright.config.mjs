// Browser performance harness (tests/e2e). It measures a production build
// (`next build && next start`), never `next dev`, behind a local stand-in for
// the edge (tests/e2e/support/edge-proxy.mjs) that serves /api and /supabase
// on the one public origin, as deployed.
import { defineConfig, devices } from "@playwright/test";

const supabaseUrl = process.env.SUPABASE_URL || "http://127.0.0.1:54321";
const supabaseAnonKey = process.env.SUPABASE_ANON_KEY || "";
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
const jwtSecret = process.env.SUPABASE_JWT_SECRET || "";
const publicOrigin = "http://127.0.0.1:3000";
const apiOrigin = "http://127.0.0.1:8000";
const webOrigin = "http://127.0.0.1:3001";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: publicOrigin,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          args: ["--no-sandbox", "--disable-setuid-sandbox"],
        },
      },
    },
  ],
  webServer: [
    {
      command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: "../api",
      url: `${apiOrigin}/health/live`,
      reuseExistingServer: true,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: "test",
        PROCESS_ROLE: "api",
        // One public origin: the browser reaches the API and Supabase through
        // the edge proxy, which routes /api and /supabase.
        API_PUBLIC_BASE_URL: `${publicOrigin}/api`,
        API_CORS_ORIGINS: publicOrigin,
        SUPABASE_URL: supabaseUrl,
        SUPABASE_PUBLIC_URL: `${publicOrigin}/supabase`,
        SUPABASE_ANON_KEY: supabaseAnonKey,
        SUPABASE_SERVICE_ROLE_KEY: serviceRoleKey,
        SUPABASE_JWT_SECRET: jwtSecret,
        WORKER_API_TOKEN: "e2e-worker-token",
        ALLOW_FIXTURE_PROVIDERS: "true",
        VISUAL_PROVIDER: "fixture",
        MEDIA_WORK_DIR: "/tmp/motionly-e2e-media",
        FORCE_LOCAL_MEDIA_STORAGE: "true",
      },
    },
    {
      // A production build: dev-server timings say nothing about what
      // viewers get.
      command:
        "pnpm exec next build && pnpm exec next start --hostname 127.0.0.1 --port 3001",
      url: `${webOrigin}/login`,
      reuseExistingServer: true,
      timeout: 600_000,
      env: {
        ...process.env,
        APP_ENV: "test",
        PUBLIC_ORIGIN: publicOrigin,
        API_INTERNAL_URL: apiOrigin,
        SUPABASE_URL: supabaseUrl,
        SUPABASE_ANON_KEY: supabaseAnonKey,
      },
    },
    {
      command: "node tests/e2e/support/edge-proxy.mjs",
      url: `${publicOrigin}/login`,
      reuseExistingServer: true,
      timeout: 60_000,
      env: {
        ...process.env,
        EDGE_PORT: "3000",
        EDGE_WEB_ORIGIN: webOrigin,
        EDGE_API_ORIGIN: apiOrigin,
        EDGE_SUPABASE_ORIGIN: supabaseUrl,
      },
    },
  ],
});
