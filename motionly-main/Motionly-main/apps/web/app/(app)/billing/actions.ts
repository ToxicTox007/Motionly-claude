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

  redirect(body.checkout_url);
}

export async function openCustomerPortal() {
  const context = await getWorkspaceApiContext();

  if (!context) {
    redirect(`/login?next=${encodeURIComponent("/billing")}`);
  }

  const response = await fetch(`${env.apiBaseUrl}/billing/customer-portal`, {
    method: "POST",
    headers: context.headers,
    cache: "no-store",
  });
  const body = await response.json();
  if (!response.ok || !body.portal_url) {
    redirect(
      `/billing?error=${encodeURIComponent(body?.error?.code ?? "portal_failed")}`,
    );
  }

  redirect(body.portal_url);
}
