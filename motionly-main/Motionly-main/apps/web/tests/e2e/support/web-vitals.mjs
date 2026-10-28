// Core Web Vitals for the browser performance harness (FE-31). The browser
// records raw PerformanceObserver entries (observeWebVitals, installed before
// any page script runs); the metrics are computed here, the way the
// web-vitals library defines them, so the maths is unit tested
// (tests/web-vitals.test.mjs).

/** The Core Web Vitals "good" thresholds. */
export const WEB_VITALS_BUDGETS = { lcpMs: 2500, cls: 0.1, inpMs: 200 };

/** LCP: the start time of the last largest-contentful-paint candidate. */
export function largestContentfulPaint(entries) {
  const last = entries.at(-1);
  return last ? Math.round(last.startTime) : null;
}

/**
 * CLS: the worst session window of layout shifts. A window ends after a 1 s
 * gap or at 5 s; shifts right after user input are expected and excluded.
 */
export function cumulativeLayoutShift(shifts) {
  let worst = 0;
  let current = 0;
  let windowStart = null;
  let previous = null;
  const ordered = [...shifts].sort((a, b) => a.startTime - b.startTime);
  for (const shift of ordered) {
    if (shift.hadRecentInput) continue;
    const sameWindow =
      windowStart !== null &&
      shift.startTime - previous < 1000 &&
      shift.startTime - windowStart < 5000;
    if (sameWindow) {
      current += shift.value;
    } else {
      current = shift.value;
      windowStart = shift.startTime;
    }
    previous = shift.startTime;
    worst = Math.max(worst, current);
  }
  return Number(worst.toFixed(4));
}

/**
 * INP: the slowest interaction (an interaction lasts as long as its slowest
 * event), ignoring one outlier per 50 interactions (the 98th percentile).
 * Null when the page saw no interaction.
 */
export function interactionToNextPaint(events) {
  const durations = new Map();
  for (const event of events) {
    if (!event.interactionId) continue;
    durations.set(
      event.interactionId,
      Math.max(durations.get(event.interactionId) ?? 0, event.duration),
    );
  }
  if (durations.size === 0) return null;
  const slowestFirst = [...durations.values()].sort((a, b) => b - a);
  const index = Math.min(
    slowestFirst.length - 1,
    Math.floor(slowestFirst.length / 50),
  );
  return Math.round(slowestFirst[index]);
}

/** "metric value > budget" for each measured metric over its budget. */
export function budgetViolations(metrics, budgets) {
  return Object.entries(budgets)
    .filter(([name, budget]) => {
      const value = metrics[name];
      return typeof value === "number" && value > budget;
    })
    .map(([name, budget]) => `${name} ${metrics[name]} > ${budget}`);
}

/**
 * Runs in the page before its own scripts (page.addInitScript), so it must
 * not use anything from this module. Entry types a browser does not support
 * are skipped.
 */
export function observeWebVitals() {
  const store = { lcp: [], shifts: [], events: [] };
  window.__motionlyVitals = store;
  const observe = (type, record, options = {}) => {
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) record(entry);
      }).observe({ type, buffered: true, ...options });
    } catch {
      // Not supported by this browser.
    }
  };
  const interaction = (entry) =>
    store.events.push({
      interactionId: entry.interactionId,
      duration: entry.duration,
      name: entry.name,
    });
  observe("largest-contentful-paint", (entry) =>
    store.lcp.push({ startTime: entry.startTime }),
  );
  observe("layout-shift", (entry) =>
    store.shifts.push({
      value: entry.value,
      startTime: entry.startTime,
      hadRecentInput: entry.hadRecentInput,
    }),
  );
  observe("event", interaction, { durationThreshold: 16 });
  observe("first-input", interaction);
}
