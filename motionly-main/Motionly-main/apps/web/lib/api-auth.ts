import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";
import { isAdminIdentityAllowed } from "@/lib/admin-auth.mjs";
import {
  canManageBilling,
  normalizeWorkspaceRole,
} from "@/lib/workspace-roles.mjs";
import { createClient } from "@/lib/supabase/server";
import { env } from "@/lib/env";

export const WORKSPACE_COOKIE_NAME = "motionly_workspace_id";

// The three workspace_role values, mirrored from the API (services/auth.py).
export type WorkspaceRole = "owner" | "admin" | "member";

export { canManageBilling };

// Memoized per request (React cache): a page, its AppShell and the helpers it
// calls share one Supabase getUser/getSession/membership lookup instead of
// repeating them.
export const getWorkspaceApiContext = cache((requestedWorkspaceId?: string) =>
  resolveWorkspaceApiContext(requestedWorkspaceId),
);

export type ActiveMembership = {
  workspaceId: string;
  role: WorkspaceRole;
  name: string;
};

/** The signed-in viewer's Supabase user and session, once per request. */
export const getSessionSnapshot = cache(async () => {
  const supabase = await createClient();
  const [
    {
      data: { user },
    },
    {
      data: { session },
    },
  ] = await Promise.all([supabase.auth.getUser(), supabase.auth.getSession()]);
  return { supabase, user, session };
});

/** The viewer's active workspace memberships, once per request. */
export const getActiveMemberships = cache(
  async (userId: string): Promise<ActiveMembership[]> => {
    const { supabase } = await getSessionSnapshot();
    const { data } = await supabase
      .from("workspace_members")
      .select("workspace_id, role, workspaces(name)")
      .eq("user_id", userId)
      .eq("status", "active");
    return (data ?? []).map((item) => {
      const workspace = Array.isArray(item.workspaces)
        ? item.workspaces[0]
        : item.workspaces;
      return {
        workspaceId: String(item.workspace_id),
        role: normalizeWorkspaceRole((item as { role?: unknown }).role),
        name:
          (workspace as { name?: string } | null | undefined)?.name ??
          "Workspace",
      };
    });
  },
);

async function resolveWorkspaceApiContext(requestedWorkspaceId?: string) {
  if (env.skipAuthMiddleware) {
    const mockUserId = "00000000-0000-4000-8000-000000000001";
    const mockWorkspaceId = "00000000-0000-4000-8000-000000000002";
    return {
      userId: mockUserId,
      workspaceId: mockWorkspaceId,
      role: "owner" as WorkspaceRole,
      headers: {
        authorization: `Bearer mock-local-bearer`,
        "content-type": "application/json",
        "x-motionly-workspace-id": mockWorkspaceId,
      },
    };
  }
  const { user, session } = await getSessionSnapshot();

  if (!user || !session?.access_token) {
    return null;
  }

  const memberships = await getActiveMemberships(user.id);

  if (!memberships.length) {
    redirect("/dashboard?error=workspace_missing");
  }

  const cookieStore = await cookies();
  const selectedWorkspaceId =
    requestedWorkspaceId?.trim() ||
    cookieStore.get(WORKSPACE_COOKIE_NAME)?.value?.trim() ||
    (memberships.length === 1 ? memberships[0].workspaceId : "");
  const membership = memberships.find(
    (item) => item.workspaceId === selectedWorkspaceId,
  );

  if (!membership) {
    redirect("/dashboard?error=workspace_selection_required");
  }

  const workspaceId = membership.workspaceId;
  const role: WorkspaceRole = membership.role;
  return {
    userId: user.id,
    workspaceId,
    role,
    headers: {
      authorization: `Bearer ${session.access_token}`,
      "content-type": "application/json",
      "x-motionly-workspace-id": workspaceId,
    },
  };
}

export const getAdminApiContext = cache(async () => {
  const context = await getWorkspaceApiContext();
  if (!context) return null;
  const allowed = isAdminIdentityAllowed(context, {
    adminUserIds: process.env.ADMIN_USER_IDS,
    adminWorkspaceIds: process.env.ADMIN_WORKSPACE_IDS,
  });
  return allowed ? context : null;
});
