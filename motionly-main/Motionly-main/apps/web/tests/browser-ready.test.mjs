// The browser suite waits for a freshly started CPU stage to become ready.
import assert from "node:assert/strict";
import test from "node:test";

import { waitForStack } from "./browser/support/global-setup.mjs";

function fakeContext(answers) {
  const calls = [];
  return {
    calls,
    async get(path) {
      calls.push(path);
      const next = answers.shift();
      if (next instanceof Error) throw next;
      return { status: () => next.status, json: async () => next.body };
    },
  };
}

const READY = {
  status: 200,
  body: { status: "ready", environment: "cpu-stage" },
};

test("waits through 502s and refused connections until the stack is ready", async () => {
  const context = fakeContext([
    new Error("ECONNREFUSED"),
    { status: 502, body: {} },
    READY,
  ]);

  const body = await waitForStack(context, { timeoutMs: 1_000, intervalMs: 1 });

  assert.equal(body.environment, "cpu-stage");
  assert.deepEqual(context.calls, [
    "/api/health/ready",
    "/api/health/ready",
    "/api/health/ready",
  ]);
});

test("gives up after the timeout with the last answer", async () => {
  const context = fakeContext(
    Array.from({ length: 50 }, () => ({ status: 502, body: {} })),
  );

  await assert.rejects(
    waitForStack(context, { timeoutMs: 20, intervalMs: 5 }),
    /did not report a ready cpu-stage stack \(last answer 502: \{\}\)/,
  );
});

test("a ready stack that is not the CPU stage fails at once", async () => {
  const context = fakeContext([
    { status: 200, body: { environment: "production" } },
    READY,
  ]);

  await assert.rejects(
    waitForStack(context, { timeoutMs: 1_000, intervalMs: 1 }),
    /last answer 200: \{"environment":"production"\}/,
  );
  assert.equal(context.calls.length, 1);
});
