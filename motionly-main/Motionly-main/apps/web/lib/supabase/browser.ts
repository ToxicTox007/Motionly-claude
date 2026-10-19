"use client";

import { createBrowserClient } from "@supabase/ssr";
import { env } from "@/lib/env";
import { authCookieOptions } from "@/lib/runtime-config.mjs";
import { browserAuthLock } from "@/lib/supabase/browser-lock.mjs";

export function createClient() {
  return createBrowserClient(env.supabaseUrl, env.supabaseAnonKey, {
    cookieOptions: authCookieOptions(window.location.origin),
    auth: { lock: browserAuthLock },
  });
}
