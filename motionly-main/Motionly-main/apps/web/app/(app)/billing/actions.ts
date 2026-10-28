"use server";

import { redirect } from "next/navigation";
import { canManageBilling, getWorkspaceApiContext } from "@/lib/api-auth";
import { isAllowedCheckoutUrl } from "@/lib/checkout-url";
import { env } from "@/lib/env";
import { failureCode, serverApiFetch } from "@/lib/server-api";

// Billing actions re-check the role on the server: hiding the buttons from
// members is not authorization (the API enforces it too). Every API outcome is
// mapped to an allowlisted ?error= code; a checkout URL is followed only when
// it points at the app's own gateway path or a configured gateway origin.

const PLAN_KEY = /^[a-z][a-z0-9_-]{0,31}$/;

async function ownerContext(returnTo: string) {
  const context = await getWorkspaceApiContext();
  if (!context) redirect(`/login?next=${encodeURIComponent(returnTo)}`);
  if (!canManageBilling(context.role)) {
    redirect("/billing?error=billing_owner_only");
  }
  return context;
}

function checkoutRedirect(url: unknown, fallbackError: string): never {
  if (
    typeof url === "string" &&
    isAllowedCheckoutUrl(url, env.appUrl, env.billingCheckoutOrigins)
  ) {
    redirect(url);
  }
  redirect(`/billing?error=${fallbackError}`);
}

export async function startCheckout(formData: FormData) {
  const plan = String(formData.get("plan") ?? "");
  if (!PLAN_KEY.test(plan)) redirect("/billing?error=plan_invalid");
  const context = await ownerContext("/pricing");

  const result = await serverApiFetch<{ checkout_url?: unknown }>(
    "/billing/checkout",
    { method: "POST", headers: context.headers, body: { plan } },
  );
  if (!result.ok) {
    redirect(`/billing?error=${failureCode(result, "checkout_failed")}`);
  }
  // Off to the gateway's checkout page. The gateway does not return the viewer
  // to a known URL; the billing page offers "Refresh status" for after paying.
  checkoutRedirect(result.data?.checkout_url, "checkout_unavailable");
}

export async function cancelSubscription() {
  const context = await ownerContext("/billing");

  const result = await serverApiFetch("/billing/cancel", {
    method: "POST",
    headers: context.headers,
  });
  if (!result.ok) {
    redirect(`/billing?error=${failureCode(result, "cancel_failed")}`);
  }
  redirect("/billing?notice=cancel_scheduled");
}

export async function retryRenewal() {
  const context = await ownerContext("/billing");

  const result = await serverApiFetch<{ checkout_url?: unknown }>(
    "/billing/retry",
    { method: "POST", headers: context.headers },
  );
  if (!result.ok) {
    redirect(`/billing?error=${failureCode(result, "retry_failed")}`);
  }
  checkoutRedirect(result.data?.checkout_url, "retry_failed");
}
