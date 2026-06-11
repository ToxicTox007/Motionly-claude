"use client";

import { createClient } from "@/lib/supabase/browser";

type BuildApiHeadersOptions = {
  workspaceId: string;
  userId?: string;
  accessToken?: string;
  json?: boolean;
};

export async function buildApiHeaders({
  workspaceId,
  userId,
  accessToken,
  json = true,
}: BuildApiHeadersOptions) {
  let token = accessToken;
  let sessionUserId = userId;
  if (!token) {
    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    token = session?.access_token;
    sessionUserId = session?.user.id ?? userId;
  }

  if (!token) {
    throw new Error("Your session expired. Sign in again before continuing.");
  }

  return {
    ...(json ? { "content-type": "application/json" } : {}),
    authorization: `Bearer ${token}`,
    "x-motionly-workspace-id": workspaceId,
    "x-motionly-user-id": sessionUserId ?? "",
  };
}
