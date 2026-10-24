"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getAdminApiContext } from "@/lib/api-auth";
import { validateRefundRupees } from "@/lib/payment-history.mjs";
import { env } from "@/lib/env";

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
  if (!context) throw new Error("Admin access is required.");
  return context;
}

function usersUrl(params: Record<string, string>) {
  const query = new URLSearchParams(params).toString();
  return `/admin/users${query ? `?${query}` : ""}`;
}

// Create a one-time recovery link. The link is returned to this action and
// shown once to the admin via the page's ?link= param; it is never persisted
// or logged. Any earlier link for the same user is invalidated by GoTrue.
export async function createResetLink(formData: FormData) {
  const email = String(formData.get("email") ?? "").trim();
  const requestId = String(formData.get("requestId") ?? "").trim();
  if (!email) redirect(usersUrl({ error: "email_required" }));

  const context = await requireContext();
  let link: string | null = null;
  let errorCode = "reset_link_failed";
  try {
    const response = await fetch(`${env.apiBaseUrl}${BASE}/reset-links`, {
      method: "POST",
      cache: "no-store",
      headers: {
        ...adminHeaders(context.headers),
        "content-type": "application/json",
      },
      body: JSON.stringify({
        email,
        ...(requestId ? { request_id: requestId } : {}),
      }),
    });
    if (response.ok) {
      const payload = (await response.json()) as { link: string };
      link = payload.link;
    } else {
      const payload = (await response.json().catch(() => null)) as {
        error?: { code?: string };
      } | null;
      errorCode = payload?.error?.code ?? errorCode;
    }
  } catch {
    errorCode = "reset_link_failed";
  }

  revalidatePath("/admin/users");
  if (link) {
    redirect(usersUrl({ q: email, link }));
  }
  redirect(usersUrl({ q: email, error: errorCode }));
}

export async function dismissResetRequest(formData: FormData) {
  const requestId = String(formData.get("requestId") ?? "").trim();
  if (!requestId) redirect(usersUrl({ error: "request_required" }));

  const context = await requireContext();
  await fetch(`${env.apiBaseUrl}${BASE}/reset-requests/dismiss`, {
    method: "POST",
    cache: "no-store",
    headers: {
      ...adminHeaders(context.headers),
      "content-type": "application/json",
    },
    body: JSON.stringify({ request_id: requestId }),
  });
  revalidatePath("/admin/users");
  redirect(usersUrl({ dismissed: "1" }));
}

export async function setUserBan(formData: FormData) {
  const userId = String(formData.get("userId") ?? "").trim();
  const email = String(formData.get("email") ?? "").trim();
  const banned = String(formData.get("banned") ?? "") === "true";
  if (!userId) redirect(usersUrl({ error: "user_required" }));

  const context = await requireContext();
  await fetch(`${env.apiBaseUrl}${BASE}/${banned ? "ban" : "unban"}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      ...adminHeaders(context.headers),
      "content-type": "application/json",
    },
    body: JSON.stringify({ user_id: userId }),
  });
  revalidatePath("/admin/users");
  redirect(usersUrl({ q: email, [banned ? "banned" : "unbanned"]: "1" }));
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
