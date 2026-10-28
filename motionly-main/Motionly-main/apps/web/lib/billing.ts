import type { BillingStatusSummary } from "@motionly/shared";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { serverApiFetch } from "@/lib/server-api";

// Real plan prices (INR paise) and the workspace's subscription come from the
// API, which reads apps/api/app/config/plans.json — the single source of truth.
// When the API cannot answer, the status is *unknown*: callers show "billing
// status unavailable" and hide every plan action. There is deliberately no
// hand-written fallback plan list — a fabricated "Free" status would show a
// paying owner an Upgrade button for the plan they already pay for.

function isBillingStatus(value: unknown): value is BillingStatusSummary {
  if (!value || typeof value !== "object") return false;
  const status = value as Partial<BillingStatusSummary>;
  return Boolean(
    status.current_plan &&
    status.subscription &&
    status.entitlements &&
    status.usage &&
    Array.isArray(status.plans),
  );
}

/** The acting workspace's billing status, or null when it is unknown. */
export async function getBillingStatus(): Promise<BillingStatusSummary | null> {
  const context = await getWorkspaceApiContext();
  if (!context) return null;
  const result = await serverApiFetch<unknown>("/billing/status", {
    headers: context.headers,
  });
  return result.ok && isBillingStatus(result.data) ? result.data : null;
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

/**
 * Payment history for the acting workspace, read from the API's receipts
 * store, or null when it could not be loaded (the table then says so rather
 * than claiming "no payments yet"). The page only asks for it as the owner.
 */
export async function getPaymentHistory(): Promise<
  PaymentReceiptSummary[] | null
> {
  const context = await getWorkspaceApiContext();
  if (!context) return null;
  const result = await serverApiFetch<{ receipts?: PaymentReceiptSummary[] }>(
    "/billing/history",
    { headers: context.headers },
  );
  if (!result.ok) return null;
  const body = result.data ?? {};
  return Array.isArray(body.receipts) ? (body.receipts ?? []) : null;
}
