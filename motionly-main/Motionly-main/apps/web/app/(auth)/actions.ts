"use server";

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { env } from "@/lib/env";
import { publicUrl } from "@/lib/public-url";
import { safeNextPath } from "@/lib/safe-redirect.mjs";

function authErrorReason(error: {
  code?: string;
  status?: number;
  message: string;
}) {
  const message = error.message.toLowerCase();
  if (error.code === "over_email_send_rate_limit" || error.status === 429) {
    return "rate_limited";
  }
  if (message.includes("email not confirmed")) {
    return "email_not_confirmed";
  }
  return "invalid_credentials";
}

// Confirmation emails link back to the public origin, never to a value taken
// from the request (an attacker-controlled Origin header would otherwise
// choose where the emailed link points).
function getAuthCallbackUrl() {
  return publicUrl("/auth/callback").toString();
}

export async function login(formData: FormData) {
  if (env.skipAuthMiddleware) {
    redirect(safeNextPath(formData.get("next"), env.appUrl));
  }

  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");
  const nextPath = safeNextPath(formData.get("next"), env.appUrl);

  let redirectTo: string | null = null;
  try {
    const supabase = await createClient();
    const { error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    if (error) {
      console.error("motionly_login_failed", {
        code: error.code,
        status: error.status,
        message: error.message,
      });
      redirectTo = `/login?error=${authErrorReason(error)}`;
    } else {
      redirectTo = nextPath;
    }
  } catch (err: any) {
    console.error("motionly_login_system_error", err);
    redirectTo = "/login?error=system_error";
  }

  if (redirectTo) {
    redirect(redirectTo);
  }
}

export async function signup(formData: FormData) {
  if (env.skipAuthMiddleware) {
    redirect("/dashboard");
  }

  const fullName = String(formData.get("name") ?? "");
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

  let redirectTo: string | null = null;
  try {
    const supabase = await createClient();
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: {
          full_name: fullName,
        },
        emailRedirectTo: getAuthCallbackUrl(),
      },
    });

    if (error) {
      console.error("motionly_signup_failed", {
        code: error.code,
        status: error.status,
        message: error.message,
      });
      redirectTo = `/signup?error=${authErrorReason(error)}`;
    } else {
      redirectTo = data.session ? "/dashboard" : "/login?notice=check_email";
    }
  } catch (err: any) {
    console.error("motionly_signup_system_error", err);
    redirectTo = "/signup?error=system_error";
  }

  if (redirectTo) {
    redirect(redirectTo);
  }
}

export async function logout() {
  if (env.skipAuthMiddleware) {
    redirect("/login");
  }

  let redirectTo: string | null = null;
  try {
    const supabase = await createClient();
    await supabase.auth.signOut();
    redirectTo = "/login";
  } catch (err: any) {
    console.error("motionly_logout_system_error", err);
    redirectTo = "/login";
  }

  if (redirectTo) {
    redirect(redirectTo);
  }
}
