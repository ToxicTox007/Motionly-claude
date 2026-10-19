import { createServerClient, type SetAllCookies } from "@supabase/ssr";
import { cookies, headers } from "next/headers";
import { env } from "@/lib/env";
import { authCookieOptions } from "@/lib/runtime-config.mjs";
import { writeSessionCookies } from "@/lib/supabase/session-cookies.mjs";
import { viewerAddressHeaders } from "@/lib/viewer-address.mjs";

export async function createClient() {
  const cookieStore = await cookies();
  const requestHeaders = await headers();

  return createServerClient(env.supabaseUrl, env.supabaseAnonKey, {
    // The browser client reaches Supabase at a different URL; a fixed cookie
    // name lets both read the same session.
    cookieOptions: authCookieOptions(env.appUrl),
    // Auth rate-limits per viewer, not per web server.
    global: { headers: viewerAddressHeaders(requestHeaders) },
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet: Parameters<SetAllCookies>[0]) {
        writeSessionCookies(cookieStore, cookiesToSet);
      },
    },
  });
}
