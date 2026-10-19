// Unit tests for the browser-suite quality gates (tests/browser/support/gates.mjs).
import assert from "node:assert/strict";
import test from "node:test";

import {
  AXE_TAGS,
  blockingAxeViolations,
  blockingConsoleMessage,
  blockingRequestFailure,
  formatProblems,
} from "./browser/support/gates.mjs";

test("axe checks WCAG 2.1 A and AA and blocks only serious and critical issues", () => {
  assert.deepEqual(AXE_TAGS, ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]);
  const violations = [
    {
      id: "color-contrast",
      impact: "serious",
      help: "Contrast",
      nodes: [{ target: ["#a"] }],
    },
    {
      id: "image-alt",
      impact: "critical",
      help: "Alt text",
      nodes: [{ target: ["img"] }, { target: ["img.b"] }],
    },
    {
      id: "region",
      impact: "moderate",
      help: "Landmarks",
      nodes: [{ target: ["div"] }],
    },
    { id: "minor", impact: "minor", help: "Minor", nodes: [{ target: ["p"] }] },
    {
      id: "unknown-impact",
      impact: null,
      help: "Unknown",
      nodes: [{ target: ["x"] }],
    },
  ];

  assert.deepEqual(blockingAxeViolations(violations), [
    "color-contrast (serious): Contrast [#a]",
    "image-alt (critical): Alt text [img, img.b]",
  ]);
});

test("console errors block; other console levels and allowed messages do not", () => {
  assert.equal(
    blockingConsoleMessage({
      type: "error",
      text: "TypeError: x is undefined",
    }),
    true,
  );
  assert.equal(
    blockingConsoleMessage({ type: "warning", text: "deprecated" }),
    false,
  );
  assert.equal(blockingConsoleMessage({ type: "log", text: "hello" }), false);
  assert.equal(
    blockingConsoleMessage(
      {
        type: "error",
        text: "Failed to load resource: the server responded with a status of 404 (Not Found)",
      },
      [/status of 404/],
    ),
    false,
  );
});

test("failed requests block unless the browser aborted them during navigation", () => {
  assert.equal(
    blockingRequestFailure({
      url: "https://o/api/x",
      failure: "net::ERR_CONNECTION_RESET",
    }),
    true,
  );
  assert.equal(
    blockingRequestFailure({ url: "https://o/x", failure: "net::ERR_ABORTED" }),
    false,
  );
  assert.equal(
    blockingRequestFailure({
      url: "https://o/x",
      failure: "NS_BINDING_ABORTED",
    }),
    false,
  );
  assert.equal(
    blockingRequestFailure({ url: "https://o/x", failure: "cancelled" }),
    false,
  );
  assert.equal(
    blockingRequestFailure({
      url: "https://o/x",
      failure: "Load request cancelled",
    }),
    false,
  );
});

test("problems are reported together, one per line, with their kind", () => {
  assert.equal(formatProblems({}), "");
  assert.equal(
    formatProblems({
      console: ["TypeError: boom"],
      pageErrors: ["ReferenceError: y"],
      serverErrors: ["502 GET https://o/api/x"],
      failedRequests: [],
      accessibility: ["image-alt (critical): Alt text [img]"],
    }),
    [
      "console error: TypeError: boom",
      "page error: ReferenceError: y",
      "server error: 502 GET https://o/api/x",
      "accessibility: image-alt (critical): Alt text [img]",
    ].join("\n"),
  );
});

test("console arguments are described in full, including objects and errors", async () => {
  const { describeConsoleArgs } = await import("./browser/support/gates.mjs");
  const handle = (value) => ({ jsonValue: async () => value });
  const failing = {
    jsonValue: async () => {
      throw new Error("not serializable");
    },
    toString: () => "JSHandle@error",
  };

  assert.equal(
    await describeConsoleArgs([
      handle("Editor API request failed"),
      handle({ status: 502, path: "/x" }),
    ]),
    'Editor API request failed {"status":502,"path":"/x"}',
  );
  assert.equal(await describeConsoleArgs([failing]), "JSHandle@error");
  assert.equal(await describeConsoleArgs([]), "");
});

test("errors a browser reports for a fetch it cancelled itself are not app failures", async () => {
  const { cancelledFetchNoise } = await import("./browser/support/gates.mjs");
  const aborted = [{ url: "https://o/?_rsc=abc", at: 1_000 }];

  // WebKit names the cancelled URL.
  assert.equal(
    cancelledFetchNoise(
      "Fetch API cannot load https://o/?_rsc=abc due to access control checks.\n at C",
      1_100,
      aborted,
    ),
    true,
  );
  assert.equal(
    cancelledFetchNoise(
      "Fetch API cannot load https://o/other due to access control checks.",
      1_100,
      aborted,
    ),
    false,
  );
  // Firefox does not; it must follow a cancelled request closely.
  assert.equal(
    cancelledFetchNoise("TypeError: Error in input stream", 1_500, aborted),
    true,
  );
  assert.equal(
    cancelledFetchNoise("TypeError: Error in input stream", 9_000, aborted),
    false,
  );
  assert.equal(
    cancelledFetchNoise("TypeError: Error in input stream", 1_500, []),
    false,
  );
  // Anything else is never noise.
  assert.equal(
    cancelledFetchNoise("TypeError: x is undefined", 1_100, aborted),
    false,
  );
});

test("requests a later page navigation left unfinished count as cancelled by the browser", async () => {
  const { cancelledByNavigation } = await import("./browser/support/gates.mjs");
  const unfinished = [
    { url: "https://o/?_rsc=a", at: 1_000 },
    { url: "https://o/late", at: 5_000 },
  ];

  assert.deepEqual(cancelledByNavigation(unfinished, [2_000]), [
    { url: "https://o/?_rsc=a", at: 1_000 },
  ]);
  assert.deepEqual(cancelledByNavigation(unfinished, []), []);
});

test("WebKit's error for a Next.js prefetch counts as cancelled only next to a navigation", async () => {
  const { cancelledFetchNoise } = await import("./browser/support/gates.mjs");
  const rsc =
    "Fetch API cannot load https://o/signup?_rsc=x1 due to access control checks.";

  assert.equal(cancelledFetchNoise(rsc, 1_000, [], [1_500]), true);
  assert.equal(cancelledFetchNoise(rsc, 1_000, [], [9_000]), false);
  assert.equal(cancelledFetchNoise(rsc, 1_000, [], []), false);
  // Not a prefetch: needs a matching aborted request.
  assert.equal(
    cancelledFetchNoise(
      "Fetch API cannot load https://o/api/x due to access control checks.",
      1_000,
      [],
      [1_500],
    ),
    false,
  );
});

test("WebKit's cancelled-prefetch message is recognised however its URL is printed", async () => {
  const { cancelledFetchNoise } = await import("./browser/support/gates.mjs");
  for (const message of [
    "/127.0.0.1:18443/?_rsc=x1 due to access control checks.",
    "TypeError: Fetch API cannot load https://o/?_rsc=x1 due to access control checks.",
  ]) {
    assert.equal(
      cancelledFetchNoise(message, 1_000, [], [1_300]),
      true,
      message,
    );
    assert.equal(
      cancelledFetchNoise(message, 1_000, [], [8_000]),
      false,
      message,
    );
  }
});

test("generic network-failure messages count as cancelled only right next to a navigation", async () => {
  const { cancelledFetchNoise } = await import("./browser/support/gates.mjs");
  for (const message of [
    "TypeError: Load failed",
    "TypeError: Failed to fetch",
    "TypeError: NetworkError when attempting to fetch resource.",
    "TypeError: Error in input stream",
    // Firefox: the page navigated before the logged object could be read.
    "JSHandle@object",
  ]) {
    assert.equal(
      cancelledFetchNoise(message, 1_000, [], [1_400]),
      true,
      message,
    );
    assert.equal(
      cancelledFetchNoise(message, 1_000, [], [6_000]),
      false,
      message,
    );
    assert.equal(cancelledFetchNoise(message, 1_000, [], []), false, message);
  }
  assert.equal(
    cancelledFetchNoise("TypeError: x is not a function", 1_000, [], [1_100]),
    false,
  );
});
