// Waits until the CPU stage answers through the CloudFront simulator, then
// fails fast with a clear message if it never does. A freshly started stack
// can need a little longer than `compose up --wait`: the API has no container
// health check yet (Task 9), so the edge answers 502 until it listens.
import { request } from "@playwright/test";

import { stackState } from "./stack.mjs";

const READY_TIMEOUT_MS = Number(
  process.env.MOTIONLY_BROWSER_READY_TIMEOUT_MS || 180_000,
);
const POLL_INTERVAL_MS = 3_000;

export async function waitForStack(
  context,
  { timeoutMs = READY_TIMEOUT_MS, intervalMs = POLL_INTERVAL_MS } = {},
) {
  const deadline = Date.now() + timeoutMs;
  let last = "no answer";
  for (;;) {
    try {
      const response = await context.get("/api/health/ready", {
        timeout: 10_000,
      });
      const body = await response.json().catch(() => ({}));
      if (response.status() === 200 && body.environment === "cpu-stage")
        return body;
      last = `${response.status()}: ${JSON.stringify(body)}`;
      // A ready stack that is not the CPU stage will not become one.
      if (response.status() === 200) break;
    } catch (error) {
      last = error.message;
    }
    if (Date.now() + intervalMs > deadline) break;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(
    `/api/health/ready did not report a ready cpu-stage stack (last answer ${last})`,
  );
}

export default async function globalSetup(config) {
  stackState();
  const { baseURL } = config.projects[0].use;
  const context = await request.newContext({
    baseURL,
    ignoreHTTPSErrors: true,
  });
  try {
    await waitForStack(context);
  } catch (error) {
    throw new Error(
      `The browser suite needs the CPU stage behind the edge at ${baseURL}: ${error.message}`,
    );
  } finally {
    await context.dispose();
  }
}
