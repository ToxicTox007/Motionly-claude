import { NextRequest, NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";
import { resetLinkErrorReason } from "@/lib/password-reset.mjs";

// The CHECK for an admin-issued recovery link runs HERE, on the server. The
// /auth/confirm page reads the token from the URL *fragment* (which never
// reaches server access logs), clears it from the address bar, then POSTs the
// token_hash to this handler. We verify it through the SERVER Supabase client
// (lib/supabase/server.ts): that both carries the viewer address for Auth's
// per-viewer rate limits and — crucially — sets the recovery session cookie on
// the response here, server-side, rather than in the browser.
//
// POST only: a token must never arrive as a GET query string, or it would land
// in server logs. Invalid/expired/reused/missing tokens map to the existing
// clear error reasons (lib/password-reset.mjs); they are never a 500 and the
// token itself is never logged.
export async function POST(request: NextRequest) {
  let tokenHash: unknown;
  try {
    const body = (await request.json()) as { token_hash?: unknown } | null;
    tokenHash = body?.token_hash;
  } catch {
    tokenHash = undefined;
  }

  if (typeof tokenHash !== "string" || tokenHash.length === 0) {
    return NextResponse.json({ reason: "missing" }, { status: 400 });
  }

  try {
    const supabase = await createClient();
    const { error } = await supabase.auth.verifyOtp({
      type: "recovery",
      token_hash: tokenHash,
    });
    if (error) {
      // Log the failure class only — never the token.
      console.warn("motionly_recovery_verify_failed", {
        code: error.code,
        status: error.status,
      });
      return NextResponse.json(
        { reason: resetLinkErrorReason(error.code) },
        { status: 400 },
      );
    }
    // verifyOtp set the recovery session cookies on this response via the
    // server client's setAll handler. The client now navigates to
    // /reset-password, where the guard finds that recovery session.
    return NextResponse.json({ ok: true });
  } catch (err) {
    console.error("motionly_recovery_verify_error", err);
    return NextResponse.json({ reason: "invalid" }, { status: 400 });
  }
}
