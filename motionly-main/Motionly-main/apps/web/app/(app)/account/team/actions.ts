"use server";

import { revalidatePath } from "next/cache";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { canManageWorkspace } from "@/lib/workspace-roles.mjs";
import { env } from "@/lib/env";

export type InvitableRole = "admin" | "member";

export type Invitation = {
  id: string;
  role: InvitableRole;
  created_by: string;
  expires_at: string;
  revoked_at?: string | null;
  accepted_by?: string | null;
  accepted_at?: string | null;
  created_at: string;
};

export type CreateInviteResult =
  | { ok: true; invitation: Invitation; url: string; token: string }
  | { ok: false; error: string };

export type MutationResult = { ok: true } | { ok: false; error: string };

function errorCode(body: unknown, fallback: string) {
  const code = (body as { error?: { code?: string } } | null)?.error?.code;
  return code ?? fallback;
}

// Owners/admins mint an invite link. The raw token + URL come back from the API
// exactly once; the web app shows them with a copy button and never stores them.
export async function createInvitation(
  role: InvitableRole,
  expiresInDays: number,
): Promise<CreateInviteResult> {
  const context = await getWorkspaceApiContext();
  if (!context || !canManageWorkspace(context.role)) {
    return { ok: false, error: "workspace_role_forbidden" };
  }
  const response = await fetch(`${env.apiBaseUrl}/team/invitations`, {
    method: "POST",
    headers: context.headers,
    body: JSON.stringify({ role, expires_in_days: expiresInDays }),
    cache: "no-store",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body?.url || !body?.token) {
    return { ok: false, error: errorCode(body, "invite_create_failed") };
  }
  revalidatePath("/account/team");
  return {
    ok: true,
    invitation: body.invitation,
    url: body.url,
    token: body.token,
  };
}

export async function revokeInvitation(
  invitationId: string,
): Promise<MutationResult> {
  const context = await getWorkspaceApiContext();
  if (!context || !canManageWorkspace(context.role)) {
    return { ok: false, error: "workspace_role_forbidden" };
  }
  const response = await fetch(
    `${env.apiBaseUrl}/team/invitations/${encodeURIComponent(invitationId)}/revoke`,
    { method: "POST", headers: context.headers, cache: "no-store" },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    return { ok: false, error: errorCode(body, "invite_revoke_failed") };
  }
  revalidatePath("/account/team");
  return { ok: true };
}

export async function changeMemberRole(
  userId: string,
  role: InvitableRole,
): Promise<MutationResult> {
  const context = await getWorkspaceApiContext();
  if (!context || !canManageWorkspace(context.role)) {
    return { ok: false, error: "workspace_role_forbidden" };
  }
  const response = await fetch(
    `${env.apiBaseUrl}/team/members/${encodeURIComponent(userId)}/role`,
    {
      method: "PATCH",
      headers: context.headers,
      body: JSON.stringify({ role }),
      cache: "no-store",
    },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    return { ok: false, error: errorCode(body, "role_change_failed") };
  }
  revalidatePath("/account/team");
  return { ok: true };
}

export async function removeMember(userId: string): Promise<MutationResult> {
  const context = await getWorkspaceApiContext();
  if (!context || !canManageWorkspace(context.role)) {
    return { ok: false, error: "workspace_role_forbidden" };
  }
  const response = await fetch(
    `${env.apiBaseUrl}/team/members/${encodeURIComponent(userId)}`,
    { method: "DELETE", headers: context.headers, cache: "no-store" },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    return { ok: false, error: errorCode(body, "member_remove_failed") };
  }
  revalidatePath("/account/team");
  return { ok: true };
}

// Any active member can leave the workspace (self-service). The API refuses the
// last owner; we surface the API's own error message so the user learns they
// must transfer ownership first. `message` carries that human text when present.
export type LeaveResult =
  | { ok: true }
  | { ok: false; error: string; message?: string };

export async function leaveWorkspace(): Promise<LeaveResult> {
  const context = await getWorkspaceApiContext();
  if (!context) {
    return { ok: false, error: "not_signed_in" };
  }
  const response = await fetch(`${env.apiBaseUrl}/team/leave`, {
    method: "POST",
    headers: context.headers,
    cache: "no-store",
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      error?: { code?: string; message?: string };
    };
    return {
      ok: false,
      error: body?.error?.code ?? "leave_failed",
      message: body?.error?.message,
    };
  }
  revalidatePath("/account/team");
  return { ok: true };
}

// Only the owner may transfer ownership (owner-only). The new owner is named in
// the confirmation dialog before this runs. The API makes the target the owner
// and demotes the current owner to admin, invalidating the role cache.
export async function transferOwnership(
  userId: string,
): Promise<MutationResult> {
  const context = await getWorkspaceApiContext();
  if (!context || context.role !== "owner") {
    return { ok: false, error: "workspace_role_forbidden" };
  }
  const response = await fetch(`${env.apiBaseUrl}/team/transfer-ownership`, {
    method: "POST",
    headers: context.headers,
    body: JSON.stringify({ user_id: userId }),
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    return { ok: false, error: errorCode(body, "transfer_failed") };
  }
  revalidatePath("/account/team");
  return { ok: true };
}
