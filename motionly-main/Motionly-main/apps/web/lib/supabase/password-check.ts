import { createClient } from "@supabase/supabase-js";
import { env } from "@/lib/env";

export type PasswordCheck = "ok" | "wrong" | "rate_limited" | "error";

/**
 * Verifies the signed-in viewer's current password before it is changed, so
 * a hijacked session alone cannot take over the account. Auth's emailed
 * re-authentication nonce is not available (secure_password_change is off and
 * the product sends no email), so this signs in once with a throwaway client
 * that keeps no session: nothing is written to cookies or storage, and the
 * temporary session is revoked straight away.
 */
export async function verifyCurrentPassword(
  email: string,
  password: string,
  viewerHeaders: Record<string, string>,
): Promise<PasswordCheck> {
  if (typeof window !== "undefined") {
    throw new Error("verifyCurrentPassword is server-only.");
  }
  const client = createClient(env.supabaseUrl, env.supabaseAnonKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
    // Auth rate-limits per viewer, not per web server.
    global: { headers: viewerHeaders },
  });
  try {
    const { data, error } = await client.auth.signInWithPassword({
      email,
      password,
    });
    if (error) {
      if (error.status === 429 || error.code === "over_request_rate_limit") {
        return "rate_limited";
      }
      if (error.code === "invalid_credentials" || error.status === 400) {
        return "wrong";
      }
      return "error";
    }
    if (data.session) {
      // Best effort: the caller also ends every other session on success.
      await client.auth.signOut({ scope: "local" }).catch(() => undefined);
    }
    return "ok";
  } catch {
    return "error";
  }
}
