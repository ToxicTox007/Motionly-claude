// Browser suite (Task 6): the CPU stage through the CloudFront simulator, in
// five setups. Start the stack first:
//   python scripts/verify/cpu_stage_stack.py up
//   node node_modules/@playwright/test/cli.js test --config playwright.browser.config.mjs
// The edge certificate comes from the stack's per-run test CA, which browsers
// do not know, so certificate errors are ignored here; the stack checker
// verifies the chain against that CA.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const results = "../../test-results/browser";
// Node-side clients in the specs (supabase-js Realtime sockets, API calls)
// reach the edge too, so the test runner and its workers trust the stack's
// per-run edge CA the way the stack checker and k6 do. Workers inherit this
// environment from the runner, which reads NODE_EXTRA_CA_CERTS at startup.
const stackStatePath = new URL(
  "../../test-results/cpu-stage/state.json",
  import.meta.url,
);
if (!process.env.NODE_EXTRA_CA_CERTS && existsSync(stackStatePath)) {
  const { secrets_root: secretsRoot } = JSON.parse(
    readFileSync(stackStatePath, "utf8"),
  );
  const edgeCa = secretsRoot && join(secretsRoot, "secrets", "edge_ca_cert");
  if (edgeCa && existsSync(edgeCa)) process.env.NODE_EXTRA_CA_CERTS = edgeCa;
}
const chromiumArgs = { launchOptions: { args: ["--no-sandbox"] } };
// Specs that only make sense in some setups are not run elsewhere (rather
// than skipped: the skip audit allows no undeclared skips). Tab order needs a
// desktop keyboard; the responsive spec sets every width itself, so the
// desktop engines cover phone widths and the phones keep their own.
const KEYBOARD_ONLY = ["**/focus-visible.spec.mjs"];
const EXPLICIT_WIDTHS = ["**/responsive.spec.mjs"];

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
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
      // WebKit's Tab skips links unless the viewer opts in.
      testIgnore: KEYBOARD_ONLY,
    },
    {
      name: "pixel-7",
      use: { ...devices["Pixel 7"], ...chromiumArgs },
      testIgnore: [...KEYBOARD_ONLY, ...EXPLICIT_WIDTHS],
    },
    {
      name: "iphone-14",
      use: { ...devices["iPhone 14"] },
      testIgnore: [...KEYBOARD_ONLY, ...EXPLICIT_WIDTHS],
    },
  ],
});
