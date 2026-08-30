import { NextRequest, NextResponse } from "next/server";
import { WORKSPACE_COOKIE_NAME } from "@/lib/api-auth";
import { createClient } from "@/lib/supabase/server";

export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const workspaceId = String(formData.get("workspace_id") ?? "").trim();
  const redirectUrl = new URL("/dashboard", request.url);

  if (!workspaceId) {
    redirectUrl.searchParams.set("error", "workspace_missing");
    return NextResponse.redirect(redirectUrl, 303);
  }

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    redirectUrl.pathname = "/login";
    redirectUrl.searchParams.set("next", "/dashboard");
    return NextResponse.redirect(redirectUrl, 303);
  }

  const { data: membership, error: membershipError } = await supabase
    .from("workspace_members")
    .select("workspace_id")
    .eq("user_id", user.id)
    .eq("workspace_id", workspaceId)
    .eq("status", "active")
    .maybeSingle();

  if (membershipError) {
    redirectUrl.searchParams.set("error", "workspace_unavailable");
    return NextResponse.redirect(redirectUrl, 303);
  }
  if (!membership) {
    redirectUrl.searchParams.set("error", "workspace_invalid");
    return NextResponse.redirect(redirectUrl, 303);
  }

  const response = NextResponse.redirect(redirectUrl, 303);
  response.cookies.set({
    name: WORKSPACE_COOKIE_NAME,
    value: workspaceId,
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 365,
  });
  return response;
}
