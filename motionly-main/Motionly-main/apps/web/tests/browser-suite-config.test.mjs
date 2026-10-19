// The browser suite's shape: five setups against the CPU stage through the
// HTTPS edge, with quality gates every test passes automatically.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { devices } from "@playwright/test";

process.env.MOTIONLY_CPU_STAGE_STATE ??= "/dev/null";
const { default: config } = await import("../playwright.browser.config.mjs");
const pkg = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);
const fixtures = readFileSync(
  new URL("./browser/support/fixtures.mjs", import.meta.url),
  "utf8",
);

test("the suite runs in Chromium, Firefox, WebKit, a Pixel 7 and an iPhone 14", () => {
  const expected = {
    chromium: "Desktop Chrome",
    firefox: "Desktop Firefox",
    webkit: "Desktop Safari",
    "pixel-7": "Pixel 7",
    "iphone-14": "iPhone 14",
  };
  assert.deepEqual(
    config.projects.map((project) => project.name),
    Object.keys(expected),
  );
  for (const project of config.projects) {
    const device = devices[expected[project.name]];
    assert.equal(
      project.use.defaultBrowserType,
      device.defaultBrowserType,
      project.name,
    );
    assert.deepEqual(project.use.viewport, device.viewport, project.name);
    assert.equal(project.use.isMobile, device.isMobile, project.name);
  }
});

test("the suite targets the CPU stage through the HTTPS edge and never retries", () => {
  assert.equal(
    config.use.baseURL,
    process.env.MOTIONLY_BROWSER_ORIGIN || "https://127.0.0.1:18443",
  );
  assert.match(config.use.baseURL, /^https:\/\//);
  assert.equal(config.retries, 0);
  assert.equal(config.testDir, "./tests/browser");
  assert.ok(config.reporter.some(([name]) => name === "junit"));
});

test("accessibility checks use an exactly pinned axe", () => {
  assert.match(pkg.devDependencies["@axe-core/playwright"], /^\d+\.\d+\.\d+$/);
});

test("every test passes the quality gates without opting in", () => {
  assert.match(fixtures, /gates: \[\s*async/);
  assert.match(fixtures, /\{ auto: true \}/);
  for (const gate of [
    '"console"',
    '"pageerror"',
    "response.status() >= 500",
    '"requestfailed"',
    "AxeBuilder",
  ]) {
    assert.ok(fixtures.includes(gate), gate);
  }
});

test("every test attaches the navigation timings of each full page load", () => {
  assert.match(fixtures, /page\.on\("load"/);
  assert.match(fixtures, /getEntriesByType\("navigation"\)/);
  assert.match(fixtures, /attach\("navigation-timings"/);
  for (const field of ["ttfb_ms", "dom_content_loaded_ms", "load_ms"]) {
    assert.ok(fixtures.includes(field), field);
  }
});
