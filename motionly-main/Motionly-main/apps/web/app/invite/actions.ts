"use server";

import { cookies } from "next/headers";
import { getWorkspaceApiContext, WORKSPACE_COOKIE_NAME } from "@/lib/api-auth";
import { createClient } from "@/lib/supabase/server";
import { env } from "@/lib/env";

export type AcceptInviteResult =
  | { ok: true; workspaceId: string; workspaceName: string }
  | { ok: false; error: string; signedIn: boolean };

// Accept an invite token. The caller must be signed in (the /invite page sends
// a signed-out visitor through login/signup first, keeping the token in
// sessionStorage). On success the new workspace is pinned as the active one and
// its name is resolved so the page can greet the user before sending them to
// its dashboard.
export async function acceptInvite(token: string): Promise<AcceptInviteResult> {
  const trimmed = token.trim();
  if (!trimmed) {
    return { ok: false, error: "invite_token_missing", signedIn: true };
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!user || !session?.access_token) {
    return { ok: false, error: "not_signed_in", signedIn: false };
  }

  // Accept runs with the user's own bearer; any workspace header is ignored by
  // the endpoint, which resolves the target workspace from the token itself.
  const response = await fetch(`${env.apiBaseUrl}/team/invitations/accept`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${session.access_token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ token: trimmed }),
    cache: "no-store",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body?.workspace_id) {
    const code =
      (body as { error?: { code?: string } } | null)?.error?.code ??
      "invite_accept_failed";
    return { ok: false, error: code, signedIn: true };
  }

  const workspaceId = String(body.workspace_id);

  // Pin the joined workspace as the active one so the dashboard loads it.
  const cookieStore = await cookies();
  cookieStore.set({
    name: WORKSPACE_COOKIE_NAME,
    value: workspaceId,
    httpOnly: true,
    sameSite: "lax",
    secure: env.appUrl.startsWith("https:"),
    path: "/",
    maxAge: 60 * 60 * 24 * 365,
  });

  // The membership context is now resolvable; read the workspace name to greet
  // the user. A failure here still counts as a successful join.
  let workspaceName = "your new workspace";
  const context = await getWorkspaceApiContext(workspaceId);
  if (context) {
    const { data: workspace } = await supabase
      .from("workspaces")
      .select("name")
      .eq("id", workspaceId)
      .maybeSingle();
    if (workspace?.name) workspaceName = workspace.name;
  }

  return { ok: true, workspaceId, workspaceName };
}
