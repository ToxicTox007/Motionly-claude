// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { apiBaseUrl: "http://api.internal" } }));
vi.mock("@/lib/api-auth", () => ({
  getWorkspaceApiContext: vi.fn(async () => ({ headers: { a: "b" } })),
}));

const { getBillingStatus, getPaymentHistory } = await import("@/lib/billing");

function stub(body: string, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(body, { status })),
  );
}

const status = {
  subscription: { plan: "pro", status: "active" },
  current_plan: { key: "pro", name: "Pro" },
  plans: [],
  usage: { renders_used: 1 },
  entitlements: { seats: 1 },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getBillingStatus", () => {
  it("returns the API's status", async () => {
    stub(JSON.stringify(status));
    expect(await getBillingStatus()).toEqual(status);
  });

  it("returns null (unknown) instead of a fabricated Free plan on failure", async () => {
    stub("{}", 500);
    expect(await getBillingStatus()).toBeNull();
    stub("<html>", 200);
    expect(await getBillingStatus()).toBeNull();
    stub(JSON.stringify({ current_plan: { key: "pro" } }));
    expect(await getBillingStatus()).toBeNull();
  });
});

describe("getPaymentHistory", () => {
  it("returns receipts, an empty list, or null when unknown", async () => {
    stub(JSON.stringify({ receipts: [{ id: "r1" }] }));
    expect(await getPaymentHistory()).toEqual([{ id: "r1" }]);
    stub(JSON.stringify({ receipts: [] }));
    expect(await getPaymentHistory()).toEqual([]);
    stub("{}", 503);
    expect(await getPaymentHistory()).toBeNull();
  });
});
