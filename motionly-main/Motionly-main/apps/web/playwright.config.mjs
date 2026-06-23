import { defineConfig, devices } from "@playwright/test";

const supabaseUrl = process.env.SUPABASE_URL || "http://127.0.0.1:54321";
const supabaseAnonKey = process.env.SUPABASE_ANON_KEY || "";
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
const jwtSecret = process.env.SUPABASE_JWT_SECRET || "";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: "../api",
      url: "http://127.0.0.1:8000/health/live",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ...process.env,
        APP_ENV: "test",
        PROCESS_ROLE: "api",
        API_PUBLIC_BASE_URL: "http://127.0.0.1:8000",
        API_CORS_ORIGINS: "http://127.0.0.1:3000",
        SUPABASE_URL: supabaseUrl,
        SUPABASE_ANON_KEY: supabaseAnonKey,
        SUPABASE_SERVICE_ROLE_KEY: serviceRoleKey,
        SUPABASE_JWT_SECRET: jwtSecret,
        ADMIN_API_TOKEN: "e2e-worker-token",
        PUBLISH_TOKEN_ENCRYPTION_KEY: "e2e-publish-token-key-at-least-32-characters",
        ALLOW_FIXTURE_PROVIDERS: "true",
        VISUAL_PROVIDER: "fixture",
        RENDER_EXECUTE_FFMPEG: "false",
        MEDIA_WORK_DIR: "/tmp/motionly-e2e-media",
      },
    },
    {
      command: "pnpm dev",
      url: "http://127.0.0.1:3000/login",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        ...process.env,
        NEXT_PUBLIC_APP_URL: "http://127.0.0.1:3000",
        NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:8000",
        NEXT_PUBLIC_SUPABASE_URL: supabaseUrl,
        NEXT_PUBLIC_SUPABASE_ANON_KEY: supabaseAnonKey,
        SUPABASE_URL: supabaseUrl,
        SUPABASE_ANON_KEY: supabaseAnonKey,
      },
    },
  ],
});
