import assert from "node:assert/strict";
import test from "node:test";

import { createRebuildCoalescer } from "../lib/coalesce-rebuild.mjs";

// A tiny deterministic fake-timer harness so we don't depend on real time.
function fakeClock() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  return {
    setTimeout(fn, delay) {
      const id = nextId++;
      timers.set(id, { fn, at: now + delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    advance(ms) {
      now += ms;
      // Fire anything due, in insertion order.
      for (const [id, timer] of [...timers.entries()]) {
        if (timer.at <= now) {
          timers.delete(id);
          timer.fn();
        }
      }
    },
  };
}

test("bursts of rebuild requests collapse into a single run", async () => {
  const clock = fakeClock();
  let runs = 0;
  const coalesce = createRebuildCoalescer(
    async () => {
      runs += 1;
    },
    {
      delayMs: 200,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    },
  );

  // A burst of five requests within the debounce window.
  coalesce();
  coalesce();
  coalesce();
  coalesce();
  coalesce();
  assert.equal(runs, 0, "should not run until the window elapses");

  clock.advance(199);
  assert.equal(runs, 0, "still within the window");

  clock.advance(1);
  // Allow the queued microtask (the async run) to settle.
  await Promise.resolve();
  assert.equal(runs, 1, "five requests collapse into exactly one run");
});

test("a new burst after the window runs again", async () => {
  const clock = fakeClock();
  let runs = 0;
  const coalesce = createRebuildCoalescer(
    async () => {
      runs += 1;
    },
    {
      delayMs: 100,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    },
  );

  coalesce();
  clock.advance(100);
  await Promise.resolve();
  assert.equal(runs, 1);

  coalesce();
  coalesce();
  clock.advance(100);
  await Promise.resolve();
  assert.equal(runs, 2, "the second burst triggers a second run");
});

test("flush runs a pending rebuild immediately", async () => {
  const clock = fakeClock();
  let runs = 0;
  const coalesce = createRebuildCoalescer(
    async () => {
      runs += 1;
    },
    {
      delayMs: 500,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    },
  );

  coalesce();
  assert.equal(runs, 0);
  await coalesce.flush();
  assert.equal(runs, 1, "flush forces the pending rebuild");

  // The scheduled timer must not fire a second time.
  clock.advance(500);
  await Promise.resolve();
  assert.equal(runs, 1, "no duplicate run after flush");
});

test("cancel drops a pending rebuild", async () => {
  const clock = fakeClock();
  let runs = 0;
  const coalesce = createRebuildCoalescer(
    async () => {
      runs += 1;
    },
    {
      delayMs: 300,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    },
  );

  coalesce();
  coalesce.cancel();
  clock.advance(300);
  await Promise.resolve();
  assert.equal(runs, 0, "cancelled rebuild never runs");
});
