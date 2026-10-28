// Unit tests for the Core Web Vitals maths of the browser performance harness
// (tests/e2e/support/web-vitals.mjs).
import assert from "node:assert/strict";
import test from "node:test";

import {
  WEB_VITALS_BUDGETS,
  budgetViolations,
  cumulativeLayoutShift,
  interactionToNextPaint,
  largestContentfulPaint,
  observeWebVitals,
} from "./e2e/support/web-vitals.mjs";

test("the default budgets are the Core Web Vitals 'good' thresholds", () => {
  assert.deepEqual(WEB_VITALS_BUDGETS, { lcpMs: 2500, cls: 0.1, inpMs: 200 });
});

test("LCP is the last largest-contentful-paint candidate", () => {
  assert.equal(largestContentfulPaint([]), null);
  assert.equal(
    largestContentfulPaint([{ startTime: 400.2 }, { startTime: 1210.7 }]),
    1211,
  );
});

test("CLS is the worst session window, ignoring shifts caused by input", () => {
  assert.equal(cumulativeLayoutShift([]), 0);
  // One window: shifts less than 1 s apart add up.
  assert.equal(
    cumulativeLayoutShift([
      { value: 0.05, startTime: 0, hadRecentInput: false },
      { value: 0.04, startTime: 600, hadRecentInput: false },
    ]),
    0.09,
  );
  // A gap of 1 s or more starts a new window; the worst window counts.
  assert.equal(
    cumulativeLayoutShift([
      { value: 0.05, startTime: 0, hadRecentInput: false },
      { value: 0.04, startTime: 600, hadRecentInput: false },
      { value: 0.12, startTime: 3000, hadRecentInput: false },
    ]),
    0.12,
  );
  // A window is at most 5 s long.
  const steady = Array.from({ length: 12 }, (_, index) => ({
    value: 0.01,
    startTime: index * 900,
    hadRecentInput: false,
  }));
  assert.equal(cumulativeLayoutShift(steady), 0.06);
  // Shifts right after an interaction are expected and do not count.
  assert.equal(
    cumulativeLayoutShift([
      { value: 0.3, startTime: 100, hadRecentInput: true },
      { value: 0.02, startTime: 200, hadRecentInput: false },
    ]),
    0.02,
  );
});

test("INP is the slowest interaction, with one outlier ignored per 50", () => {
  assert.equal(interactionToNextPaint([]), null);
  // Entries without an interaction id (hover, scroll) are not interactions.
  assert.equal(
    interactionToNextPaint([{ interactionId: 0, duration: 400 }]),
    null,
  );
  // An interaction's duration is its slowest event (pointerdown/up, click).
  assert.equal(
    interactionToNextPaint([
      { interactionId: 7, duration: 40 },
      { interactionId: 7, duration: 96 },
      { interactionId: 9, duration: 64 },
    ]),
    96,
  );
  const many = Array.from({ length: 60 }, (_, index) => ({
    interactionId: index + 1,
    duration: index === 0 ? 900 : 50 + index,
  }));
  // 60 interactions: the single worst (900) is treated as an outlier.
  assert.equal(interactionToNextPaint(many), 109);
});

test("budget violations name each metric over budget and skip unmeasured ones", () => {
  const budgets = { lcpMs: 2500, cls: 0.1, inpMs: 200, loadMs: 5000 };
  assert.deepEqual(
    budgetViolations(
      { lcpMs: 3100, cls: 0.02, inpMs: null, loadMs: 5000 },
      budgets,
    ),
    ["lcpMs 3100 > 2500"],
  );
  assert.deepEqual(
    budgetViolations({ lcpMs: 900, cls: 0.25, inpMs: 240 }, budgets),
    ["cls 0.25 > 0.1", "inpMs 240 > 200"],
  );
});

test("the in-page observer records LCP, layout shifts and interactions", () => {
  const observers = [];
  const previous = {
    window: globalThis.window,
    PerformanceObserver: globalThis.PerformanceObserver,
  };
  globalThis.window = {};
  globalThis.PerformanceObserver = class {
    constructor(callback) {
      this.callback = callback;
      observers.push(this);
    }
    observe(options) {
      if (options.type === "unsupported") throw new Error("not supported");
      this.options = options;
    }
  };
  try {
    observeWebVitals();
    const byType = Object.fromEntries(
      observers.map((observer) => [observer.options.type, observer]),
    );
    for (const observer of observers) {
      assert.equal(observer.options.buffered, true);
    }
    assert.equal(byType.event.options.durationThreshold, 16);
    const deliver = (type, entries) =>
      byType[type].callback({ getEntries: () => entries });
    deliver("largest-contentful-paint", [{ startTime: 812 }]);
    deliver("layout-shift", [
      { value: 0.01, startTime: 50, hadRecentInput: false },
    ]);
    deliver("event", [{ interactionId: 3, duration: 48, name: "click" }]);
    deliver("first-input", [
      { interactionId: 3, duration: 52, name: "pointerdown" },
    ]);

    assert.deepEqual(window.__motionlyVitals, {
      lcp: [{ startTime: 812 }],
      shifts: [{ value: 0.01, startTime: 50, hadRecentInput: false }],
      events: [
        { interactionId: 3, duration: 48, name: "click" },
        { interactionId: 3, duration: 52, name: "pointerdown" },
      ],
    });
  } finally {
    globalThis.window = previous.window;
    globalThis.PerformanceObserver = previous.PerformanceObserver;
  }
});
