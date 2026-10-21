import { cookies } from "next/headers";
import { redirect } from "next/navigation";
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

export async function getWorkspaceApiContext(requestedWorkspaceId?: string) {
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
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!user || !session?.access_token) {
    return null;
  }

  const { data: memberships } = await supabase
    .from("workspace_members")
    .select("workspace_id, role")
    .eq("user_id", user.id)
    .eq("status", "active");

  if (!memberships?.length) {
    redirect("/dashboard?error=workspace_missing");
  }

  const cookieStore = await cookies();
  const selectedWorkspaceId =
    requestedWorkspaceId?.trim() ||
    cookieStore.get(WORKSPACE_COOKIE_NAME)?.value?.trim() ||
    (memberships.length === 1 ? String(memberships[0].workspace_id) : "");
  const membership = memberships.find(
    (item) => String(item.workspace_id) === selectedWorkspaceId,
  );

  if (!membership?.workspace_id) {
    redirect("/dashboard?error=workspace_selection_required");
  }

  const workspaceId = String(membership.workspace_id);
  const role: WorkspaceRole = normalizeWorkspaceRole(
    (membership as { role?: unknown }).role,
  );
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

export async function getAdminApiContext() {
  const context = await getWorkspaceApiContext();
  if (!context) return null;
  const allowed = isAdminIdentityAllowed(context, {
    adminUserIds: process.env.ADMIN_USER_IDS,
    adminWorkspaceIds: process.env.ADMIN_WORKSPACE_IDS,
  });
  return allowed ? context : null;
}
