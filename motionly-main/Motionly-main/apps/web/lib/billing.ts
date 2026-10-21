import type { BillingStatusSummary } from "@motionly/shared";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

// Offline degradation only. Real plan prices (INR paise) come from the API,
// which reads apps/api/app/config/plans.json — the single source of truth. This
// fallback deliberately carries price_paise: 0 so prices are never duplicated
// here; the UI shows the live catalogue whenever the API is reachable.
const unavailableWorkspaceId = "00000000-0000-0000-0000-000000000000";

export const fallbackBillingStatus: BillingStatusSummary = {
  subscription: {
    workspace_id: unavailableWorkspaceId,
    plan: "free",
    status: "none",
    cancel_at_period_end: false,
    payment_issue: false,
    updated_at: new Date(0).toISOString(),
  },
  current_plan: {
    key: "free",
    name: "Free",
    price_paise: 0,
    currency: "INR",
    interval: "month",
    entitlements: {
      monthly_renders: 3,
      watermark: true,
      max_resolution: "720p",
      broll: false,
      priority_queue: false,
      premium_effects: false,
      seats: 1,
      brand_kits: false,
      bulk_uploads: false,
      api_access: false,
    },
    features: [
      "3 renders/month",
      "Watermarked exports",
      "720p exports",
      "No B-roll",
    ],
  },
  plans: [
    {
      key: "free",
      name: "Free",
      price_paise: 0,
      currency: "INR",
      interval: "month",
      entitlements: {
        monthly_renders: 3,
        watermark: true,
        max_resolution: "720p",
        broll: false,
        priority_queue: false,
        premium_effects: false,
        seats: 1,
        brand_kits: false,
        bulk_uploads: false,
        api_access: false,
      },
      features: [
        "3 renders/month",
        "Watermarked exports",
        "720p exports",
        "No B-roll",
      ],
    },
    {
      key: "creator",
      name: "Creator",
      price_paise: 0,
      currency: "INR",
      interval: "month",
      entitlements: {
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
      },
      features: ["30 renders/month", "No watermark", "1080p exports", "B-roll"],
    },
    {
      key: "pro",
      name: "Pro",
      price_paise: 0,
      currency: "INR",
      interval: "month",
      entitlements: {
        monthly_renders: 100,
        watermark: false,
        max_resolution: "1080p",
        broll: true,
        priority_queue: true,
        premium_effects: true,
        seats: 1,
        brand_kits: false,
        bulk_uploads: false,
        api_access: false,
      },
      features: [
        "100 renders/month",
        "Priority queue",
        "Premium effects",
        "B-roll",
      ],
    },
    {
      key: "agency",
      name: "Agency",
      price_paise: 0,
      currency: "INR",
      interval: "month",
      entitlements: {
        monthly_renders: 250,
        watermark: false,
        max_resolution: "4k",
        broll: true,
        priority_queue: true,
        premium_effects: true,
        seats: 5,
        brand_kits: true,
        bulk_uploads: true,
        api_access: true,
      },
      features: [
        "5 seats",
        "4K exports",
        "Brand kits",
        "Bulk uploads",
        "API access",
      ],
    },
  ],
  usage: {
    workspace_id: unavailableWorkspaceId,
    period_start: new Date(0).toISOString(),
    period_end: new Date(0).toISOString(),
    renders_used: 0,
    renders_limit: 3,
    remaining_renders: 3,
    percent_used: 0,
  },
  can_render: true,
  entitlements: {
    monthly_renders: 3,
    watermark: true,
    max_resolution: "720p",
    broll: false,
    priority_queue: false,
    premium_effects: false,
    seats: 1,
    brand_kits: false,
    bulk_uploads: false,
    api_access: false,
  },
};

export async function getBillingStatus(): Promise<BillingStatusSummary> {
  const context = await getWorkspaceApiContext();
  if (!context) {
    return fallbackBillingStatus;
  }

  try {
    const response = await fetch(`${env.apiBaseUrl}/billing/status`, {
      headers: context.headers,
      cache: "no-store",
    });
    if (!response.ok) {
      return fallbackBillingStatus;
    }
    return (await response.json()) as BillingStatusSummary;
  } catch {
    return fallbackBillingStatus;
  }
}

// A single in-app receipt as the billing history table and receipt view render
// it. Mirrors the API's PaymentReceipt (apps/api/app/schemas/billing.py); the
// amount is in INR paise.
export type PaymentReceiptSummary = {
  id: string;
  workspace_id: string;
  kind: string; // "charge" | "refund"
  plan?: string | null;
  amount_paise: number;
  currency?: string;
  gateway_event_id?: string | null;
  gateway_order_id?: string | null;
  reason?: string | null;
  created_at: string;
  demo_notice?: string;
};

// Payment history for the acting workspace, read from the API's receipts store.
// Returns an empty list on any failure so the billing page still renders (the
// owner just sees "no payments yet"); the history surface is otherwise
// owner-gated by the page.
export async function getPaymentHistory(): Promise<PaymentReceiptSummary[]> {
  const context = await getWorkspaceApiContext();
  if (!context) {
    return [];
  }

  try {
    const response = await fetch(`${env.apiBaseUrl}/billing/history`, {
      headers: context.headers,
      cache: "no-store",
    });
    if (!response.ok) {
      return [];
    }
    const body = (await response.json()) as {
      receipts?: PaymentReceiptSummary[];
    };
    return body.receipts ?? [];
  } catch {
    return [];
  }
}
