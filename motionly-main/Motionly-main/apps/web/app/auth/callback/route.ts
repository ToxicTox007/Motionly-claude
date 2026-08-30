import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

function loginRedirect(request: NextRequest, error: "callback_missing" | "callback_failed") {
  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("error", error);
  return NextResponse.redirect(loginUrl, 303);
}

export async function GET(request: NextRequest) {
  const code = new URL(request.url).searchParams.get("code");
  if (!code) return loginRedirect(request, "callback_missing");

  try {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return loginRedirect(request, "callback_failed");
  } catch {
    return loginRedirect(request, "callback_failed");
  }

  return NextResponse.redirect(new URL("/dashboard", request.url), 303);
}
