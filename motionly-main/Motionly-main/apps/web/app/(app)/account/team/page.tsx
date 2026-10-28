import { AppShell } from "@/components/app-shell";
import { Alert } from "@/components/brutal-ui";
import { getBillingStatus } from "@/lib/billing";
import { requireWorkspaceApiContext } from "@/lib/require-context";
import { serverApiFetch } from "@/lib/server-api";
import { canManageWorkspace } from "@/lib/workspace-roles.mjs";
import { seatUsage } from "@/lib/team.mjs";
import { createClient } from "@/lib/supabase/server";
import { env } from "@/lib/env";
import { TeamClient } from "./TeamClient";
import type { Invitation } from "./actions";

export type TeamMember = {
  user_id: string;
  role: "owner" | "admin" | "member";
  name: string;
  email: string | null;
  isSelf: boolean;
};

// The member list comes straight from workspace_members (Task 20 deliberately
// ships no list-members endpoint — the same membership rows the app shell and
// dashboard already read are the source of truth). Invitations come from the
// API, which only owners/admins may list.
async function loadMembers(workspaceId: string, selfId: string | null) {
  if (env.skipAuthMiddleware) return [] as TeamMember[];
  const supabase = await createClient();
  const { data: rows } = await supabase
    .from("workspace_members")
    .select("user_id, role")
    .eq("workspace_id", workspaceId)
    .eq("status", "active");
  const memberRows = rows ?? [];
  // workspace_members.user_id and profiles.id both reference auth.users(id) but
  // there is no direct foreign key between the two tables, so PostgREST cannot
  // embed profiles on workspace_members. Resolve the display fields with a
  // second lookup keyed by user id (the same direct profiles read used by the
  // app shell and account page).
  const userIds = memberRows.map((row) => String(row.user_id));
  const profilesById = new Map<
    string,
    { full_name: string | null; email: string | null }
  >();
  if (userIds.length > 0) {
    const { data: profileRows } = await supabase
      .from("profiles")
      .select("id, full_name, email")
      .in("id", userIds);
    for (const profile of profileRows ?? []) {
      profilesById.set(String(profile.id), {
        full_name: profile.full_name ?? null,
        email: profile.email ?? null,
      });
    }
  }
  const order = { owner: 0, admin: 1, member: 2 } as const;
  return memberRows
    .map((row) => {
      const profile = profilesById.get(String(row.user_id));
      const role = (row.role ?? "member") as TeamMember["role"];
      return {
        user_id: String(row.user_id),
        role: role in order ? role : "member",
        name: profile?.full_name || profile?.email || "Teammate",
        email: profile?.email ?? null,
        isSelf: String(row.user_id) === selfId,
      } satisfies TeamMember;
    })
    .sort((a, b) => order[a.role] - order[b.role]);
}

async function loadInvitations(
  headers: Record<string, string>,
): Promise<Invitation[]> {
  const result = await serverApiFetch<{ invitations?: Invitation[] }>(
    "/team/invitations",
    { headers },
  );
  return result.ok && Array.isArray(result.data?.invitations)
    ? result.data.invitations
    : [];
}

export default async function TeamPage() {
  const context = await requireWorkspaceApiContext("/account/team");
  const role = context.role;
  const canManage = canManageWorkspace(role);

  // Members, invitations and the plan are independent: load them together.
  const [members, invitations, status] = await Promise.all([
    loadMembers(context.workspaceId, context.userId),
    canManage ? loadInvitations(context.headers) : Promise.resolve([]),
    getBillingStatus(),
  ]);
  // Unknown plan: the seat limit is unknown too (never a fabricated Free one).
  const seatLimit = status ? status.entitlements.seats : null;
  const pending = invitations.filter(
    (invite) => !invite.revoked_at && !invite.accepted_at,
  ).length;
  const usage =
    seatLimit === null
      ? null
      : seatUsage({
          activeMembers: members.length,
          pendingInvites: pending,
          limit: seatLimit,
        });

  return (
    <AppShell title="Team">
      <div className="space-y-5">
        {!canManage ? (
          <Alert tone="info" title="Team members">
            You can see who is in this workspace. Only the owner or an admin can
            invite teammates or change roles.
          </Alert>
        ) : null}
        <TeamClient
          canManage={canManage}
          viewerRole={role}
          members={members}
          invitations={invitations}
          seatUsed={usage?.used ?? members.length + pending}
          seatLimit={usage?.limit ?? null}
        />
      </div>
    </AppShell>
  );
}
