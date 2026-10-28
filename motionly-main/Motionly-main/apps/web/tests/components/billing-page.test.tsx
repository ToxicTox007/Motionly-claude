import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import type { BillingStatusSummary } from "@motionly/shared";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigationMock, resetNextMocks } from "../setup/next-mocks";

vi.mock("next/navigation", () => navigationMock);
vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/app/(app)/billing/actions", () => ({
  startCheckout: vi.fn(),
  cancelSubscription: vi.fn(),
  retryRenewal: vi.fn(),
}));
const envState = {
  billingEnabled: true,
  billingMode: "demo" as "demo" | "disabled",
};
vi.mock("@/lib/env", () => ({ env: envState }));
vi.mock("@/lib/api-auth", () => ({
  getWorkspaceApiContext: vi.fn(async () => ({ role: "owner" })),
  canManageBilling: (role: string) => role === "owner",
}));
const billing = {
  status: null as BillingStatusSummary | null,
  history: [] as unknown[] | null,
};
vi.mock("@/lib/billing", () => ({
  getBillingStatus: vi.fn(async () => billing.status),
  getPaymentHistory: vi.fn(async () => billing.history),
}));

const { default: BillingPage } = await import("@/app/(app)/billing/page");

const entitlements = {
  monthly_renders: 30,
  watermark: false,
  max_resolution: "1080p",
  broll: true,
  priority_queue: false,
  premium_effects: false,
  seats: 1,
  brand_kits: false,
  bulk_uploads: false,
  api_access: false,
};

function plan(key: string, name: string, price: number) {
  return {
    key,
    name,
    price_paise: price,
    currency: "INR",
    interval: "month",
    entitlements,
    features: [`${name} feature`],
  };
}

const proStatus = {
  subscription: {
    workspace_id: "w1",
    plan: "pro",
    status: "active",
    cancel_at_period_end: false,
    payment_issue: false,
    updated_at: "2026-10-01T00:00:00Z",
  },
  current_plan: plan("pro", "Pro", 99900),
  plans: [
    plan("free", "Free", 0),
    plan("pro", "Pro", 99900),
    plan("agency", "Agency", 299900),
  ],
  usage: {
    workspace_id: "w1",
    period_start: "2026-10-01T00:00:00Z",
    period_end: "2026-11-01T00:00:00Z",
    renders_used: 4,
    renders_limit: 100,
    remaining_renders: 96,
    percent_used: 4,
  },
  can_render: true,
  entitlements,
} as unknown as BillingStatusSummary;

async function renderPage(params: Record<string, string> = {}) {
  render(await BillingPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  resetNextMocks();
  billing.status = null;
  billing.history = [];
  envState.billingEnabled = true;
  envState.billingMode = "demo";
});

describe("billing page", () => {
  it("never shows a fabricated plan or plan actions when status is unknown", async () => {
    await renderPage();
    expect(
      screen.getByText("Billing status is unavailable"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /upgrade/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /cancel at period end/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Free workspace")).not.toBeInTheDocument();
    expect(screen.queryByText("₹0")).not.toBeInTheDocument();
  });

  it("shows the real plan and upgrades for a known status", async () => {
    billing.status = proStatus;
    await renderPage();
    expect(screen.getAllByText("Pro").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: /upgrade/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /cancel at period end/i }),
    ).toBeInTheDocument();
  });

  it("labels demo pricing only in demo mode", async () => {
    billing.status = proStatus;
    envState.billingMode = "disabled";
    await renderPage();
    expect(screen.queryByText("Demo")).not.toBeInTheDocument();
    expect(screen.queryByText(/no money is charged/)).not.toBeInTheDocument();
  });

  it("maps notices and never echoes a crafted error", async () => {
    billing.status = proStatus;
    await renderPage({
      notice: "cancel_scheduled",
      error: "Your_card_was_stolen_call_us",
    });
    expect(screen.getByText(/subscription is cancelled/i)).toBeInTheDocument();
    expect(screen.queryByText(/stolen/i)).not.toBeInTheDocument();
    expect(screen.getByText(/could not be completed/i)).toBeInTheDocument();
  });

  it("says when payment history could not be loaded", async () => {
    billing.status = proStatus;
    billing.history = null;
    await renderPage();
    expect(
      screen.getByText(/payment history could not be loaded/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no payments yet/i)).not.toBeInTheDocument();
  });
});
