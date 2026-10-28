// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  navigationMock,
  RedirectError,
  resetNextMocks,
} from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/lib/env", () => ({
  env: {
    apiBaseUrl: "http://api.internal",
    appUrl: "https://app.example.com",
    billingCheckoutOrigins: [],
  },
}));

const context = {
  userId: "u1",
  workspaceId: "w1",
  role: "owner" as "owner" | "admin" | "member",
  headers: { authorization: "Bearer t", "x-motionly-workspace-id": "w1" },
};
vi.mock("@/lib/api-auth", async () => {
  const roles = await import("@/lib/workspace-roles.mjs");
  return {
    getWorkspaceApiContext: vi.fn(async () => context),
    canManageBilling: roles.canManageBilling,
  };
});

const { startCheckout, cancelSubscription, retryRenewal } =
  await import("@/app/(app)/billing/actions");

function form(values: Record<string, string>) {
  const data = new FormData();
  for (const [key, value] of Object.entries(values)) data.set(key, value);
  return data;
}

function stubFetch(body: string | null, status = 200) {
  const fetchMock = vi.fn(async () => new Response(body, { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function redirectOf(run: () => Promise<unknown>): Promise<string> {
  try {
    await run();
  } catch (error) {
    if (error instanceof RedirectError) return error.location;
    throw error;
  }
  throw new Error("expected a redirect");
}

beforeEach(() => {
  resetNextMocks();
  context.role = "owner";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("startCheckout", () => {
  it("follows the gateway checkout URL on the app's own origin", async () => {
    const fetchMock = stubFetch(
      JSON.stringify({
        checkout_url: "https://app.example.com/pay/checkout/order_1",
      }),
    );
    expect(await redirectOf(() => startCheckout(form({ plan: "pro" })))).toBe(
      "https://app.example.com/pay/checkout/order_1",
    );
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("http://api.internal/billing/checkout");
    expect(init.body).toBe('{"plan":"pro"}');
  });

  it("refuses a checkout URL that points anywhere else", async () => {
    stubFetch(JSON.stringify({ checkout_url: "https://evil.example/steal" }));
    expect(await redirectOf(() => startCheckout(form({ plan: "pro" })))).toBe(
      "/billing?error=checkout_unavailable",
    );
  });

  it("maps an HTML 502 to an error instead of crashing", async () => {
    stubFetch("<html>Bad gateway</html>", 502);
    expect(await redirectOf(() => startCheckout(form({ plan: "pro" })))).toBe(
      "/billing?error=checkout_failed",
    );
  });

  it("keeps the API's error code when it is a safe code", async () => {
    stubFetch(JSON.stringify({ error: { code: "billing_disabled" } }), 409);
    expect(await redirectOf(() => startCheckout(form({ plan: "pro" })))).toBe(
      "/billing?error=billing_disabled",
    );
  });

  it("refuses members on the server, not just in the UI", async () => {
    context.role = "member";
    const fetchMock = stubFetch("{}");
    expect(await redirectOf(() => startCheckout(form({ plan: "pro" })))).toBe(
      "/billing?error=billing_owner_only",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a malformed plan before calling the API", async () => {
    const fetchMock = stubFetch("{}");
    expect(
      await redirectOf(() => startCheckout(form({ plan: "../admin" }))),
    ).toBe("/billing?error=plan_invalid");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("cancelSubscription", () => {
  it("reports the scheduled cancellation", async () => {
    stubFetch(JSON.stringify({ ok: true }));
    expect(await redirectOf(() => cancelSubscription())).toBe(
      "/billing?notice=cancel_scheduled",
    );
  });

  it("maps a network failure to an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    expect(await redirectOf(() => cancelSubscription())).toBe(
      "/billing?error=network_error",
    );
  });
});

describe("retryRenewal", () => {
  it("refuses an unsafe retry URL", async () => {
    stubFetch(JSON.stringify({ checkout_url: "javascript:alert(1)" }));
    expect(await redirectOf(() => retryRenewal())).toBe(
      "/billing?error=retry_failed",
    );
  });
});
