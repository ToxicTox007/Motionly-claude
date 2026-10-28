"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getAdminApiContext } from "@/lib/api-auth";
import { validateRefundRupees } from "@/lib/payment-history.mjs";
import { env } from "@/lib/env";
import { failureCode, serverApiFetch } from "@/lib/server-api";

const BASE = "/admin/users";

function adminHeaders(contextHeaders: Record<string, string>) {
  return {
    ...contextHeaders,
    ...(process.env.ADMIN_API_TOKEN
      ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
      : {}),
  };
}

async function requireContext() {
  const context = await getAdminApiContext();
  // A lost admin session lands on an explained page, not the error screen.
  if (!context) redirect("/dashboard?error=admin_access_denied");
  return context;
}

function usersUrl(params: Record<string, string>) {
  const query = new URLSearchParams(params).toString();
  return `/admin/users${query ? `?${query}` : ""}`;
}

export type ResetLinkState =
  | { status: "idle" }
  | { status: "ok"; link: string }
  | { status: "error"; error: string };

// Create a one-time recovery link (a live account-recovery credential, valid
// for an hour). It is returned to the calling component as action state and
// held only in that admin's browser tab: it never goes into a URL (browser
// history, access logs, Referer), and it is never persisted or logged here.
// Any earlier link for the same user is invalidated by GoTrue. The page is not
// revalidated here, so a resolved request's row does not unmount and drop the
// link before the admin copies it; the panel refreshes the list when closed.
export async function createResetLink(
  _previous: ResetLinkState,
  formData: FormData,
): Promise<ResetLinkState> {
  const email = String(formData.get("email") ?? "").trim();
  const requestId = String(formData.get("requestId") ?? "").trim();
  if (!email) return { status: "error", error: "email_required" };

  const context = await getAdminApiContext();
  if (!context) return { status: "error", error: "admin_access_denied" };

  const result = await serverApiFetch<{ link?: unknown }>(
    `${BASE}/reset-links`,
    {
      method: "POST",
      headers: adminHeaders(context.headers),
      body: { email, ...(requestId ? { request_id: requestId } : {}) },
    },
  );
  if (!result.ok) {
    return {
      status: "error",
      error: failureCode(result, "reset_link_failed"),
    };
  }
  const link = result.data?.link;
  return typeof link === "string" && link
    ? { status: "ok", link }
    : { status: "error", error: "reset_link_failed" };
}

export async function dismissResetRequest(formData: FormData) {
  const requestId = String(formData.get("requestId") ?? "").trim();
  if (!requestId) redirect(usersUrl({ error: "request_required" }));

  const context = await requireContext();
  const result = await serverApiFetch(`${BASE}/reset-requests/dismiss`, {
    method: "POST",
    headers: adminHeaders(context.headers),
    body: { request_id: requestId },
  });
  revalidatePath("/admin/users");
  redirect(
    usersUrl(result.ok ? { done: "dismissed" } : { error: "dismiss_failed" }),
  );
}

export async function setUserBan(formData: FormData) {
  const userId = String(formData.get("userId") ?? "").trim();
  const email = String(formData.get("email") ?? "").trim();
  const banned = String(formData.get("banned") ?? "") === "true";
  if (!userId) redirect(usersUrl({ error: "user_required" }));

  const context = await requireContext();
  const result = await serverApiFetch(`${BASE}/${banned ? "ban" : "unban"}`, {
    method: "POST",
    headers: adminHeaders(context.headers),
    body: { user_id: userId },
  });
  revalidatePath("/admin/users");
  redirect(
    usersUrl({
      q: email,
      ...(result.ok
        ? { done: banned ? "banned" : "unbanned" }
        : { error: banned ? "ban_failed" : "unban_failed" }),
    }),
  );
}

export type RefundResult = { ok: true } | { ok: false; error: string };

// Issue a refund for a workspace (admin surface, Task 23). The amount is
// entered in whole rupees and validated here before it is sent; an empty
// amount refunds what is still refundable on the latest payment (the API
// default). The API is platform-admin gated (require_admin), caps the amount at
// the captured payment, refunds through the gateway, and audit-logs the refund
// (billing.refund). Mirrors the pure
// validateRefundRupees helper so the client dialog and this action agree.
export async function issueRefund(
  workspaceId: string,
  amountRupees: string,
  reason: string,
): Promise<RefundResult> {
  const trimmedWorkspace = String(workspaceId ?? "").trim();
  if (!trimmedWorkspace) {
    return { ok: false, error: "refund_workspace_required" };
  }

  const amount = validateRefundRupees(amountRupees);
  if (!amount.ok) {
    return { ok: false, error: amount.error };
  }

  const context = await getAdminApiContext();
  if (!context) {
    return { ok: false, error: "admin_access_denied" };
  }

  const response = await fetch(`${env.apiBaseUrl}/billing/refunds`, {
    method: "POST",
    cache: "no-store",
    headers: {
      ...adminHeaders(context.headers),
      "content-type": "application/json",
    },
    body: JSON.stringify({
      workspace_id: trimmedWorkspace,
      ...(amount.amountPaise != null
        ? { amount_paise: amount.amountPaise }
        : {}),
      ...(reason?.trim() ? { reason: reason.trim() } : {}),
    }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string };
    } | null;
    return { ok: false, error: body?.error?.code ?? "refund_failed" };
  }
  revalidatePath("/admin/users");
  return { ok: true };
}
