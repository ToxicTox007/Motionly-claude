// Browser suite (Task 6): the CPU stage through the CloudFront simulator, in
// five setups. Start the stack first:
//   python scripts/verify/cpu_stage_stack.py up
//   node node_modules/@playwright/test/cli.js test --config playwright.browser.config.mjs
// The edge certificate comes from the stack's per-run test CA, which browsers
// do not know, so certificate errors are ignored here; the stack checker
// verifies the chain against that CA.
import { defineConfig, devices } from "@playwright/test";

const results = "../../test-results/browser";
const chromiumArgs = { launchOptions: { args: ["--no-sandbox"] } };

export default defineConfig({
  testDir: "./tests/browser",
  testMatch: "**/*.spec.mjs",
  outputDir: `${results}/artifacts`,
  globalSetup: "./tests/browser/support/global-setup.mjs",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: Number(process.env.MOTIONLY_BROWSER_WORKERS || 2),
  reporter: [
    ["list"],
    ["junit", { outputFile: `${results}/junit.xml` }],
    ["json", { outputFile: `${results}/results.json` }],
  ],
  use: {
    baseURL: process.env.MOTIONLY_BROWSER_ORIGIN || "https://127.0.0.1:18443",
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], ...chromiumArgs },
    },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
    { name: "pixel-7", use: { ...devices["Pixel 7"], ...chromiumArgs } },
    { name: "iphone-14", use: { ...devices["iPhone 14"] } },
  ],
});
