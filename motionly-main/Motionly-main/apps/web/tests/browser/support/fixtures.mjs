// Browser-suite fixtures (Task 6). Every test runs against the CPU stage
// through the CloudFront simulator, as a viewer would, and must pass the
// quality gates: no console errors, no uncaught page errors, no 5xx
// responses, no failed requests, and no serious or critical WCAG 2.1 A/AA
// accessibility violations on any page it visits.
import AxeBuilder from "@axe-core/playwright";
import { expect, test as base } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";

import {
  AXE_TAGS,
  blockingAxeViolations,
  blockingConsoleMessage,
  cancelledByNavigation,
  cancelledFetchNoise,
  describeConsoleArgs,
  blockingRequestFailure,
  formatProblems,
} from "./gates.mjs";
import { stackCredentials } from "./stack.mjs";

export { expect };

export const test = base.extend({
  // Service-role access to the stack, for test data only (never the browser).
  stack: [
    async ({ playwright }, use, workerInfo) => {
      const { serviceRoleKey } = stackCredentials();
      const admin = await playwright.request.newContext({
        baseURL: workerInfo.project.use.baseURL,
        ignoreHTTPSErrors: true,
        extraHTTPHeaders: {
          apikey: serviceRoleKey,
          authorization: `Bearer ${serviceRoleKey}`,
        },
      });
      await use({ admin });
      await admin.dispose();
    },
    { scope: "worker" },
  ],

  // A confirmed account that exists only for this test.
  user: async ({ stack }, use, testInfo) => {
    const email = `browser-${testInfo.workerIndex}-${randomBytes(6).toString("hex")}@motionly.test`;
    const password = `Browser-${randomBytes(12).toString("hex")}-Aa1!`;
    const response = await stack.admin.post("/supabase/auth/v1/admin/users", {
      data: { email, password, email_confirm: true },
    });
    expect(response.status(), await response.text()).toBe(200);
    const created = await response.json();
    await use({ id: created.id, email, password });
    // Deleting the account also deletes its workspaces and projects.
    const deleted = await stack.admin.delete(
      `/supabase/auth/v1/admin/users/${created.id}`,
    );
    expect(deleted.status(), await deleted.text()).toBe(200);
  },

  gates: [
    async ({ context }, use, testInfo) => {
      const problems = {
        console: [],
        pageErrors: [],
        serverErrors: [],
        failedRequests: [],
        accessibility: [],
      };
      const allowedConsole = [];
      const navigationTimings = [];
      const pendingConsole = [];
      // Requests the browser cancelled itself, and errors to check against them.
      const abortedRequests = [];
      const candidateNoise = [];
      const unfinished = new Map();
      const navigationTimes = [];
      const scanned = new Set();

      const watch = (page) => {
        // Hard and client-side (history) navigations of the page.
        page.on("framenavigated", (frame) => {
          if (frame === page.mainFrame()) navigationTimes.push(Date.now());
        });
        page.on("console", (message) => {
          const entry = { type: message.type(), text: message.text() };
          if (blockingConsoleMessage(entry, allowedConsole)) {
            const location = message.location();
            const at = Date.now();
            // Described asynchronously; collected before the test reports.
            pendingConsole.push(
              describeConsoleArgs(message.args())
                .catch(() => "")
                .then((described) => {
                  const text = described || entry.text;
                  candidateNoise.push({
                    kind: "console",
                    text,
                    at,
                    line: `${text} (${location.url || page.url()})`,
                  });
                }),
            );
          }
        });
        page.on("pageerror", (error) =>
          candidateNoise.push({
            kind: "pageErrors",
            text: String(error.message ?? error),
            at: Date.now(),
            line: String(error.stack ?? error),
          }),
        );
        // Timings of every full page load: the D2 browser baselines.
        page.on("load", async () => {
          try {
            const timing = await page.evaluate(async () => {
              // loadEventEnd is set once the load handlers have run.
              await new Promise((resolve) => setTimeout(resolve, 0));
              const nav = performance.getEntriesByType("navigation")[0];
              if (!nav) return null;
              return {
                path: location.pathname,
                ttfb_ms: Math.round(nav.responseStart),
                dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd),
                load_ms: Math.round(nav.loadEventEnd),
              };
            });
            if (timing) navigationTimings.push(timing);
          } catch {
            // The page navigated or closed before the timing could be read.
          }
        });
      };
      context.pages().forEach(watch);
      context.on("page", watch);
      context.on("response", (response) => {
        if (response.status() >= 500) {
          problems.serverErrors.push(
            `${response.status()} ${response.request().method()} ${response.url()}`,
          );
        }
      });
      context.on("request", (request) => {
        let mainFrameNavigation = false;
        try {
          mainFrameNavigation =
            request.isNavigationRequest() &&
            request.frame() === request.frame().page().mainFrame();
        } catch {
          // Service-worker requests have no frame.
        }
        if (!mainFrameNavigation)
          unfinished.set(request, { url: request.url(), at: Date.now() });
      });
      context.on("requestfinished", (request) => unfinished.delete(request));
      context.on("requestfailed", (request) => {
        unfinished.delete(request);
        const failure = request.failure()?.errorText;
        if (!blockingRequestFailure({ url: request.url(), failure })) {
          abortedRequests.push({ url: request.url(), at: Date.now() });
        } else {
          problems.failedRequests.push(
            `${failure} ${request.method()} ${request.url()}`,
          );
        }
      });

      const checkAccessibility = async (page) => {
        await page.waitForLoadState("load");
        const results = await new AxeBuilder({ page })
          .withTags(AXE_TAGS)
          .analyze();
        scanned.add(page.url());
        const path = new URL(page.url()).pathname;
        for (const issue of blockingAxeViolations(results.violations)) {
          problems.accessibility.push(`${issue} on ${path}`);
        }
        for (const violation of results.violations) {
          if (!["serious", "critical"].includes(violation.impact)) continue;
          for (const node of violation.nodes) {
            details.push({
              path,
              rule: violation.id,
              impact: violation.impact,
              target: node.target.join(" "),
              html: node.html.slice(0, 300),
              message: [...node.any, ...node.all, ...node.none]
                .map((check) => check.message)
                .join(" | "),
            });
          }
        }
      };
      const details = [];

      await use({
        checkAccessibility,
        /** Console errors a test expects, e.g. the browser logging an intended 404. */
        allowConsoleError: (pattern) => allowedConsole.push(pattern),
      });

      // Every test also checks the page it ends on.
      for (const page of context.pages()) {
        if (
          !page.isClosed() &&
          page.url().startsWith("https://") &&
          !scanned.has(page.url())
        ) {
          await checkAccessibility(page);
        }
      }
      await testInfo.attach("navigation-timings", {
        body: JSON.stringify(navigationTimings),
        contentType: "application/json",
      });
      await Promise.all(pendingConsole);
      abortedRequests.push(
        ...cancelledByNavigation([...unfinished.values()], navigationTimes),
      );
      // Errors are app failures unless they are how the browser reported a
      // fetch it cancelled itself (gates.mjs cancelledFetchNoise).
      for (const candidate of candidateNoise) {
        if (
          !cancelledFetchNoise(
            candidate.text,
            candidate.at,
            abortedRequests,
            navigationTimes,
          )
        ) {
          problems[candidate.kind].push(candidate.line);
        }
      }
      const report = formatProblems(problems);
      if (report) {
        writeFileSync(
          testInfo.outputPath("quality-gates.json"),
          JSON.stringify(
            {
              navigationTimes,
              candidateNoise: candidateNoise.map(({ kind, text, at }) => ({
                kind,
                text: text.slice(0, 120),
                at,
              })),
              project: testInfo.project.name,
              test: testInfo.title,
              problems,
              accessibility: details,
            },
            null,
            2,
          ),
        );
        await testInfo.attach("quality-gates", {
          body: report,
          contentType: "text/plain",
        });
      }
      expect(report, "quality gates").toBe("");
    },
    { auto: true },
  ],
});

/**
 * Follows a navigation link; narrow screens reach it through the menu.
 *
 * The menu is retried: a tap before the page hydrates does nothing, and the
 * app shell is rendered separately by each loading screen and page, so an
 * open menu can be re-rendered closed when a streamed page replaces its
 * loading screen (finding for Task 31: one shared shell layout).
 */
export async function navigate(page, name) {
  const link = page.getByRole("link", { name, exact: true });
  // Read from the DOM: on narrow screens the sidebar link is hidden by CSS and
  // the menu's copy exists only while the menu is open.
  const href = await page.evaluate((label) => {
    const match = [...document.querySelectorAll("a[href]")].find(
      (anchor) => anchor.textContent.trim() === label,
    );
    return match?.getAttribute("href") ?? null;
  }, name);
  if (!href)
    throw new Error(`no link named ${JSON.stringify(name)} on ${page.url()}`);
  const arrived = (url) => url.pathname === href;
  const menu = page.getByRole("dialog", { name: "Navigation menu" });
  // A click can be lost while a streamed page replaces its loading screen;
  // retry until the browser is on the link's page.
  await expect(async () => {
    if (arrived(new URL(page.url()))) return;
    const visible = link.filter({ visible: true });
    if ((await visible.count()) > 0 && !(await menu.isVisible())) {
      await visible.first().click({ timeout: 5_000 });
    } else {
      if (!(await menu.isVisible())) {
        await page
          .getByRole("button", { name: "Menu", exact: true })
          .click({ timeout: 5_000 });
      }
      await menu
        .getByRole("link", { name, exact: true })
        .click({ timeout: 5_000 });
    }
    await page.waitForURL(arrived, { timeout: 5_000 });
  }).toPass({ timeout: 30_000 });
}

/** Signs in through the real login form, as a viewer does. */
export async function signIn(page, user, next = "/dashboard") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password").fill(user.password);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.waitForURL((url) => url.pathname === next);
}

/** The access token's claims, read from the Supabase session cookie. */
export async function accessTokenClaims(context) {
  const chunks = (await context.cookies())
    .filter((cookie) => /^sb-motionly-auth-token(\.\d+)?$/.test(cookie.name))
    .sort((a, b) => a.name.localeCompare(b.name, "en", { numeric: true }))
    .map((cookie) => cookie.value)
    .join("");
  const json = chunks.startsWith("base64-")
    ? Buffer.from(chunks.slice("base64-".length), "base64url").toString("utf8")
    : decodeURIComponent(chunks);
  const payload = JSON.parse(json).access_token.split(".")[1];
  return JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
}
