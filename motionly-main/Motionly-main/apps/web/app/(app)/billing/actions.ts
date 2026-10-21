"use server";

import { redirect } from "next/navigation";
import type { BillingPlanKey } from "@motionly/shared";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

export async function startCheckout(formData: FormData) {
  const plan = String(formData.get("plan") ?? "") as BillingPlanKey;
  const context = await getWorkspaceApiContext();

  if (!context) {
    redirect(`/login?next=${encodeURIComponent("/pricing")}`);
  }

  const response = await fetch(`${env.apiBaseUrl}/billing/checkout`, {
    method: "POST",
    headers: context.headers,
    body: JSON.stringify({ plan }),
    cache: "no-store",
  });
  const body = await response.json();
  if (!response.ok || !body.checkout_url) {
    redirect(
      `/billing?error=${encodeURIComponent(body?.error?.code ?? "checkout_failed")}`,
    );
  }

  // Off to the gateway's checkout page; on return the billing page polls
  // /billing/status until the signed webhook lands.
  redirect(body.checkout_url);
}

export async function cancelSubscription() {
  const context = await getWorkspaceApiContext();

  if (!context) {
    redirect(`/login?next=${encodeURIComponent("/billing")}`);
  }

  const response = await fetch(`${env.apiBaseUrl}/billing/cancel`, {
    method: "POST",
    headers: context.headers,
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    redirect(
      `/billing?error=${encodeURIComponent(body?.error?.code ?? "cancel_failed")}`,
    );
  }
  redirect("/billing?notice=cancel_scheduled");
}

export async function retryRenewal() {
  const context = await getWorkspaceApiContext();

  if (!context) {
    redirect(`/login?next=${encodeURIComponent("/billing")}`);
  }

  const response = await fetch(`${env.apiBaseUrl}/billing/retry`, {
    method: "POST",
    headers: context.headers,
    cache: "no-store",
  });
  const body = await response.json();
  if (!response.ok || !body.checkout_url) {
    redirect(
      `/billing?error=${encodeURIComponent(body?.error?.code ?? "retry_failed")}`,
    );
  }
  redirect(body.checkout_url);
}
