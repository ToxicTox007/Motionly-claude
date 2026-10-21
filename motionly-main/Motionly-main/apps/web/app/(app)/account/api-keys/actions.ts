"use server";

import { revalidatePath } from "next/cache";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { canManageWorkspace } from "@/lib/workspace-roles.mjs";
import { env } from "@/lib/env";

export type ApiKeyView = {
  id: string;
  name: string;
  prefix: string;
  last4: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

export type CreateApiKeyResult =
  | { ok: true; secret: string; key: ApiKeyView }
  | { ok: false; error: string };

export type RevokeApiKeyResult = { ok: true } | { ok: false; error: string };

// Owners/admins mint a key. The raw secret is returned once, to be shown to the
// user with a copy button, and is never stored by the web app.
export async function createApiKey(name: string): Promise<CreateApiKeyResult> {
  const context = await getWorkspaceApiContext();
  if (!context || !canManageWorkspace(context.role)) {
    return { ok: false, error: "workspace_role_forbidden" };
  }
  const trimmed = name.trim();
  if (!trimmed) {
    return { ok: false, error: "name_required" };
  }
  const response = await fetch(`${env.apiBaseUrl}/api-keys`, {
    method: "POST",
    headers: context.headers,
    body: JSON.stringify({ name: trimmed }),
    cache: "no-store",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.secret) {
    return { ok: false, error: body?.error?.code ?? "api_key_create_failed" };
  }
  revalidatePath("/account/api-keys");
  return { ok: true, secret: body.secret, key: body.key };
}

export async function revokeApiKey(keyId: string): Promise<RevokeApiKeyResult> {
  const context = await getWorkspaceApiContext();
  if (!context || !canManageWorkspace(context.role)) {
    return { ok: false, error: "workspace_role_forbidden" };
  }
  const response = await fetch(
    `${env.apiBaseUrl}/api-keys/${encodeURIComponent(keyId)}/revoke`,
    {
      method: "POST",
      headers: context.headers,
      cache: "no-store",
    },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    return { ok: false, error: body?.error?.code ?? "api_key_revoke_failed" };
  }
  revalidatePath("/account/api-keys");
  return { ok: true };
}
