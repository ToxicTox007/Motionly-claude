import { defineConfig, devices } from "@playwright/test";

const supabaseUrl = process.env.SUPABASE_URL || "http://127.0.0.1:54321";
const supabaseAnonKey = process.env.SUPABASE_ANON_KEY || "";
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
const jwtSecret = process.env.SUPABASE_JWT_SECRET || "";
const publicOrigin = "http://127.0.0.1:3000";

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
      url: "http://127.0.0.1:8000/health/live",
      reuseExistingServer: true,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: "test",
        PROCESS_ROLE: "api",
        // One public origin: the browser reaches the API and Supabase through
        // the web server, which proxies /api and /supabase in development.
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
      command: "pnpm dev",
      url: "http://127.0.0.1:3000/login",
      reuseExistingServer: true,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: "test",
        PUBLIC_ORIGIN: publicOrigin,
        API_INTERNAL_URL: "http://127.0.0.1:8000",
        SUPABASE_URL: supabaseUrl,
        SUPABASE_ANON_KEY: supabaseAnonKey,
      },
    },
  ],
});
