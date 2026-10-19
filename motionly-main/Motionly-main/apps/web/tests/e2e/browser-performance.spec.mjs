import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

const runBrowserPerf = process.env.RUN_BROWSER_PERF === "1";
const enforce = process.env.PERF_ENFORCE === "1";
const allowRedirects = process.env.ALLOW_BROWSER_PERF_REDIRECTS === "1";
const projectId =
  process.env.PERF_PROJECT_ID || "00000000-0000-4000-8000-000000000001";
const outputPath =
  process.env.BROWSER_PERF_OUTPUT || "test-results/browser-performance.json";

const surfaces = [
  { name: "landing", path: "/" },
  { name: "login", path: "/login" },
  { name: "dashboard", path: "/dashboard" },
  { name: "projects", path: "/projects" },
  { name: "project_editor", path: `/projects/${projectId}` },
  { name: "billing", path: "/billing" },
  { name: "account", path: "/account" },
  { name: "admin_ops", path: "/admin/ops" },
];

const thresholds = {
  domContentLoadedMs: Number(process.env.BROWSER_PERF_DCL_MS || 2500),
  loadMs: Number(process.env.BROWSER_PERF_LOAD_MS || 5000),
  firstContentfulPaintMs: Number(process.env.BROWSER_PERF_FCP_MS || 2500),
};

test.skip(
  !runBrowserPerf,
  "Set RUN_BROWSER_PERF=1 to run browser performance checks.",
);

test.describe("browser performance surfaces", () => {
  const samples = [];

  test.afterAll(async () => {
    if (samples.length === 0) return;
    await mkdir(outputPath.split("/").slice(0, -1).join("/") || ".", {
      recursive: true,
    });
    await writeFile(
      outputPath,
      `${JSON.stringify(
        {
          schema_version: 1,
          mode: enforce ? "enforce" : "observe",
          revision: process.env.DEPLOYMENT_REVISION || "unversioned",
          environment: process.env.APP_ENV || "local",
          browser: process.env.BROWSER || "chromium",
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
    test(`${surface.name} stays within navigation thresholds`, async ({
      page,
    }) => {
      await page.goto(surface.path, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("load");

      if (!allowRedirects) {
        expect(new URL(page.url()).pathname).toBe(surface.path);
      }

      const metrics = await page.evaluate(() => {
        const nav = performance.getEntriesByType("navigation")[0];
        const paint = performance.getEntriesByType("paint");
        const fcp = paint.find(
          (entry) => entry.name === "first-contentful-paint",
        );
        return {
          domContentLoadedMs: Math.round(nav.domContentLoadedEventEnd),
          loadMs: Math.round(nav.loadEventEnd),
          firstContentfulPaintMs: fcp ? Math.round(fcp.startTime) : null,
          transferSizeBytes: Number(nav.transferSize || 0),
          encodedBodySizeBytes: Number(nav.encodedBodySize || 0),
        };
      });

      samples.push({ surface: surface.name, path: surface.path, ...metrics });

      expect(metrics.domContentLoadedMs).toBeLessThanOrEqual(
        thresholds.domContentLoadedMs,
      );
      expect(metrics.loadMs).toBeLessThanOrEqual(thresholds.loadMs);
      if (metrics.firstContentfulPaintMs !== null) {
        expect(metrics.firstContentfulPaintMs).toBeLessThanOrEqual(
          thresholds.firstContentfulPaintMs,
        );
      }
    });
  }
});
