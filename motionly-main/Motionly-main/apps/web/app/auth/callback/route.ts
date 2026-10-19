import { NextResponse, type NextRequest } from "next/server";
import { publicUrl } from "@/lib/public-url";
import { createClient } from "@/lib/supabase/server";

function loginRedirect(error: "callback_missing" | "callback_failed") {
  const loginUrl = publicUrl("/login");
  loginUrl.searchParams.set("error", error);
  return NextResponse.redirect(loginUrl, 303);
}

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  if (!code) return loginRedirect("callback_missing");

  try {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return loginRedirect("callback_failed");
  } catch {
    return loginRedirect("callback_failed");
  }

  return NextResponse.redirect(publicUrl("/dashboard"), 303);
}
