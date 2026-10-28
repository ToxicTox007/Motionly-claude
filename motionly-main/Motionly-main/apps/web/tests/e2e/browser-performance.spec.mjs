// Browser performance of the main surfaces (FE-31), against a production
// build (playwright.config.mjs). Records navigation timing and the Core Web
// Vitals (LCP, CLS, INP from PerformanceObserver) to browser-performance.json.
// Budgets fail the run only with PERF_ENFORCE=1; otherwise violations are
// recorded in the JSON and as test annotations.
//
// App surfaces are measured signed in, through the login form: as
// PERF_USER_EMAIL / PERF_USER_PASSWORD, or as a throwaway confirmed user
// created with SUPABASE_SERVICE_ROLE_KEY (and deleted afterwards).
import { expect, request, test } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import {
  WEB_VITALS_BUDGETS,
  budgetViolations,
  cumulativeLayoutShift,
  interactionToNextPaint,
  largestContentfulPaint,
  observeWebVitals,
} from "./support/web-vitals.mjs";

const runBrowserPerf = process.env.RUN_BROWSER_PERF === "1";
const enforce = process.env.PERF_ENFORCE === "1";
const allowRedirects = process.env.ALLOW_BROWSER_PERF_REDIRECTS === "1";
const outputPath =
  process.env.BROWSER_PERF_OUTPUT || "test-results/browser-performance.json";
const authStatePath = "test-results/browser-performance-auth.json";
// /admin/ops needs a platform admin (ADMIN_USER_IDS); only a provided user
// can be one.
const userIsAdmin = process.env.PERF_USER_IS_ADMIN === "1";

const surfaces = [
  { name: "landing", path: "/", signedIn: false },
  { name: "login", path: "/login", signedIn: false },
  { name: "dashboard", path: "/dashboard", signedIn: true },
  { name: "projects", path: "/projects", signedIn: true },
  { name: "project_editor", path: "/projects/:projectId", signedIn: true },
  { name: "billing", path: "/billing", signedIn: true },
  { name: "account", path: "/account", signedIn: true },
  { name: "admin_ops", path: "/admin/ops", signedIn: true, admin: true },
];

const thresholds = {
  domContentLoadedMs: Number(process.env.BROWSER_PERF_DCL_MS || 2500),
  loadMs: Number(process.env.BROWSER_PERF_LOAD_MS || 5000),
  firstContentfulPaintMs: Number(process.env.BROWSER_PERF_FCP_MS || 2500),
  lcpMs: Number(process.env.BROWSER_PERF_LCP_MS || WEB_VITALS_BUDGETS.lcpMs),
  cls: Number(process.env.BROWSER_PERF_CLS || WEB_VITALS_BUDGETS.cls),
  inpMs: Number(process.env.BROWSER_PERF_INP_MS || WEB_VITALS_BUDGETS.inpMs),
};

test.skip(
  !runBrowserPerf,
  "Set RUN_BROWSER_PERF=1 to run browser performance checks.",
);

/** Signs in through the real login form, as a viewer does. */
async function signIn(page, user, next = "/dashboard") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password").fill(user.password);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.waitForURL((url) => url.pathname === next);
}

/** The viewer to measure as, and how to clean it up. */
async function perfUser() {
  if (process.env.PERF_USER_EMAIL && process.env.PERF_USER_PASSWORD) {
    return {
      user: {
        email: process.env.PERF_USER_EMAIL,
        password: process.env.PERF_USER_PASSWORD,
      },
      cleanup: async () => {},
    };
  }
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!serviceRoleKey) {
    throw new Error(
      "Signed-in surfaces need PERF_USER_EMAIL and PERF_USER_PASSWORD, or SUPABASE_SERVICE_ROLE_KEY to create a throwaway user.",
    );
  }
  const admin = await request.newContext({
    baseURL: process.env.SUPABASE_URL || "http://127.0.0.1:54321",
    extraHTTPHeaders: {
      apikey: serviceRoleKey,
      authorization: `Bearer ${serviceRoleKey}`,
    },
  });
  const email = `perf-${randomBytes(6).toString("hex")}@motionly.test`;
  const password = `Perf-${randomBytes(12).toString("hex")}-Aa1!`;
  const created = await admin.post("/auth/v1/admin/users", {
    data: { email, password, email_confirm: true },
  });
  expect(created.status(), await created.text()).toBe(200);
  const { id } = await created.json();
  return {
    user: { email, password },
    cleanup: async () => {
      await admin.delete(`/auth/v1/admin/users/${id}`);
      await admin.dispose();
    },
  };
}

async function measure(page, path) {
  await page.addInitScript(observeWebVitals);
  await page.goto(path, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("load");

  // One harmless interaction (a click on the page heading), so INP has
  // something to measure; LCP candidates stop at the first input.
  const heading = page.locator("h1").first();
  if (await heading.isVisible().catch(() => false)) await heading.click();
  // Event timing entries are reported after the next paint.
  await page.waitForTimeout(500);

  const navigation = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const fcp = performance
      .getEntriesByType("paint")
      .find((entry) => entry.name === "first-contentful-paint");
    return {
      domContentLoadedMs: Math.round(nav.domContentLoadedEventEnd),
      loadMs: Math.round(nav.loadEventEnd),
      firstContentfulPaintMs: fcp ? Math.round(fcp.startTime) : null,
      transferSizeBytes: Number(nav.transferSize || 0),
      encodedBodySizeBytes: Number(nav.encodedBodySize || 0),
    };
  });
  const vitals = await page.evaluate(() => window.__motionlyVitals);
  return {
    ...navigation,
    lcpMs: largestContentfulPaint(vitals.lcp),
    cls: cumulativeLayoutShift(vitals.shifts),
    inpMs: interactionToNextPaint(vitals.events),
  };
}

test.describe("browser performance surfaces", () => {
  const samples = [];
  let projectId = process.env.PERF_PROJECT_ID || null;
  let cleanup = async () => {};

  test.beforeAll(async ({ browser }) => {
    const session = await perfUser();
    cleanup = session.cleanup;
    const context = await browser.newContext();
    const page = await context.newPage();
    await signIn(page, session.user);
    // Without a given project, measure the editor on a new, empty one.
    if (!projectId) {
      await page.goto("/projects/new");
      await page.getByLabel("Project Name").fill("Performance check");
      await page.getByRole("button", { name: /Continue to Upload/i }).click();
      await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
      projectId = new URL(page.url()).pathname.split("/").at(-1);
    }
    await mkdir(dirname(authStatePath), { recursive: true });
    await context.storageState({ path: authStatePath });
    await context.close();
  });

  test.afterAll(async () => {
    await cleanup();
    if (samples.length === 0) return;
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(
      outputPath,
      `${JSON.stringify(
        {
          schema_version: 1,
          mode: enforce ? "enforce" : "observe",
          revision: process.env.DEPLOYMENT_REVISION || "unversioned",
          environment: process.env.APP_ENV || "local",
          browser: process.env.BROWSER || "chromium",
          server: "next start",
          thresholds,
          sample_count: samples.length,
          samples,
        },
        null,
        2,
      )}\n`,
      "utf8",
    );
  });

  for (const surface of surfaces) {
    test(`${surface.name} stays within its budgets`, async ({ browser }) => {
      test.skip(
        Boolean(surface.admin) && !userIsAdmin,
        "Set PERF_USER_IS_ADMIN=1 with a platform-admin PERF_USER_EMAIL to measure /admin/ops.",
      );
      const context = await browser.newContext(
        surface.signedIn ? { storageState: authStatePath } : {},
      );
      const page = await context.newPage();
      const path = surface.path.replace(":projectId", projectId ?? "");
      try {
        const metrics = await measure(page, path);
        // A redirect would measure the wrong page; that is never a budget
        // question.
        if (!allowRedirects) {
          expect(new URL(page.url()).pathname).toBe(path);
        }

        const violations = budgetViolations(metrics, thresholds);
        samples.push({
          surface: surface.name,
          path: surface.path,
          ...metrics,
          violations,
        });
        if (enforce) {
          expect(violations, `${surface.name} budgets`).toEqual([]);
        } else if (violations.length > 0) {
          test.info().annotations.push({
            type: "over budget (observe mode)",
            description: violations.join("; "),
          });
        }
      } finally {
        await context.close();
      }
    });
  }
});
