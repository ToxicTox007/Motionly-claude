import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export async function getWorkspaceApiContext() {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    const mockUserId = "00000000-0000-4000-8000-000000000001";
    const mockWorkspaceId = "00000000-0000-4000-8000-000000000002";
    return {
      userId: mockUserId,
      workspaceId: mockWorkspaceId,
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

  const { data: membership } = await supabase
    .from("workspace_members")
    .select("workspace_id")
    .eq("user_id", user.id)
    .eq("status", "active")
    .limit(1)
    .maybeSingle();

  if (!membership?.workspace_id) {
    redirect("/dashboard?error=workspace_missing");
  }

  return {
    userId: user.id,
    workspaceId: membership.workspace_id as string,
    headers: {
      authorization: `Bearer ${session.access_token}`,
      "content-type": "application/json",
      "x-motionly-workspace-id": membership.workspace_id as string,
    },
  };
}
