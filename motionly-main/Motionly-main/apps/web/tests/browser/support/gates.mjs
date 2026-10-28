// Quality gates every browser test must pass (Task 6). Pure rules, unit
// tested in tests/browser-gates.test.mjs; fixtures.mjs applies them to pages.

/**
 * WCAG 2.0, 2.1 and 2.2 at levels A and AA (axe rule tags). axe-core 4.13
 * tags no rule `wcag22a` yet; it is listed so a future rule is not missed.
 */
export const AXE_TAGS = [
  "wcag2a",
  "wcag2aa",
  "wcag21a",
  "wcag21aa",
  "wcag22a",
  "wcag22aa",
];
const BLOCKING_IMPACTS = new Set(["serious", "critical"]);
const ADVISORY_IMPACTS = new Set(["moderate"]);

// Requests a browser cancels itself when a page navigates away. Every engine
// names this differently; none of them is a failure of the application.
const ABORTED_REQUEST =
  /^(net::ERR_ABORTED|NS_BINDING_ABORTED|cancelled|Load request cancelled)$/i;

export function blockingAxeViolations(violations) {
  return violations
    .filter((violation) => BLOCKING_IMPACTS.has(violation.impact))
    .map(
      (violation) =>
        `${violation.id} (${violation.impact}): ${violation.help} [${violation.nodes
          .map((node) => node.target.join(" "))
          .join(", ")}]`,
    );
}

/**
 * Moderate issues: reported in the run's artifacts so they can be fixed, but
 * they do not fail a test.
 */
export function advisoryAxeViolations(violations, path) {
  return violations
    .filter((violation) => ADVISORY_IMPACTS.has(violation.impact))
    .map((violation) => ({
      path,
      rule: violation.id,
      impact: violation.impact,
      help: violation.help,
      targets: violation.nodes.map((node) => node.target.join(" ")),
    }));
}

export function blockingConsoleMessage({ type, text }, allowed = []) {
  if (type !== "error") return false;
  return !allowed.some((pattern) => pattern.test(text));
}

export function blockingRequestFailure({ failure }) {
  return !ABORTED_REQUEST.test(String(failure ?? "").trim());
}

const LABELS = {
  console: "console error",
  pageErrors: "page error",
  serverErrors: "server error",
  failedRequests: "failed request",
  accessibility: "accessibility",
};

export function formatProblems(problems) {
  return Object.entries(LABELS)
    .flatMap(([key, label]) =>
      (problems[key] ?? []).map((item) => `${label}: ${item}`),
    )
    .join("\n");
}

/**
 * Describes console.* arguments in full. Playwright's message.text() prints
 * an object argument as "JSHandle@object", which says nothing; errors are
 * described by their stack, other values as JSON.
 */
export async function describeConsoleArgs(args) {
  const parts = [];
  for (const arg of args) {
    try {
      if (typeof arg.evaluate === "function") {
        parts.push(
          await arg.evaluate((value) => {
            if (value instanceof Error)
              return value.stack || `${value.name}: ${value.message}`;
            if (typeof value === "string") return value;
            try {
              return JSON.stringify(value);
            } catch {
              return String(value);
            }
          }),
        );
        continue;
      }
      const value = await arg.jsonValue();
      parts.push(typeof value === "string" ? value : JSON.stringify(value));
    } catch {
      parts.push(String(arg));
    }
  }
  return parts.join(" ");
}

const WEBKIT_CANCELLED_FETCH = /(\S+) due to access control checks\./;
// How engines describe a fetch that never completed (WebKit "Load failed",
// Chromium "Failed to fetch", Firefox "NetworkError ..." and "Error in input
// stream"), and Firefox's placeholder for an object it could not read because
// the page had already navigated.
const NETWORK_FAILURE =
  /^(TypeError: (Load failed|Failed to fetch|NetworkError when attempting to fetch resource\.|Error in input stream)|JSHandle@object)(?=$|\s|:)/;
const CANCELLED_WINDOW_MS = 2_000;

/**
 * True when an error is how a browser reports a fetch it cancelled itself
 * (the page navigated away while Next.js was prefetching or streaming).
 * WebKit names the URL, which must be a request the browser aborted, or a
 * Next.js RSC prefetch (`_rsc=`) within 2 s of a page navigation. Firefox
 * names nothing, and other engines log a cut-off fetch as a generic network
 * failure; those must come within 2 s of an aborted request or a navigation.
 */
export function cancelledFetchNoise(
  message,
  at,
  aborted,
  navigationTimes = [],
) {
  const text = String(message);
  const webkit = WEBKIT_CANCELLED_FETCH.exec(text);
  if (webkit) {
    if (aborted.some((request) => request.url === webkit[1])) return true;
    // A Next.js RSC prefetch cut off by a navigation; WebKit does not always
    // report the cancelled request itself.
    const prefetch = /[?&]_rsc=/.test(webkit[1]);
    return (
      prefetch &&
      navigationTimes.some((time) => Math.abs(time - at) <= CANCELLED_WINDOW_MS)
    );
  }
  if (NETWORK_FAILURE.test(text)) {
    const near = (time) => Math.abs(time - at) <= CANCELLED_WINDOW_MS;
    return (
      aborted.some((request) => near(request.at)) || navigationTimes.some(near)
    );
  }
  return false;
}

/**
 * Requests that never finished and were followed by a main-frame navigation:
 * the navigation cancelled them. WebKit reports these without a
 * requestfailed event.
 */
export function cancelledByNavigation(unfinished, navigationTimes) {
  return unfinished.filter((request) =>
    navigationTimes.some((time) => time >= request.at),
  );
}
