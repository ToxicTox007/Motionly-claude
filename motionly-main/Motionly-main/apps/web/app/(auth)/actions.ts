"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { env } from "@/lib/env";

function safeRedirectPath(value: FormDataEntryValue | null) {
  const path = String(value ?? "/dashboard");
  if (!path.startsWith("/") || path.startsWith("//")) {
    return "/dashboard";
  }

  return path;
}

function authErrorReason(error: { code?: string; status?: number; message: string }) {
  const message = error.message.toLowerCase();
  if (error.code === "over_email_send_rate_limit" || error.status === 429) {
    return "rate_limited";
  }
  if (message.includes("email not confirmed")) {
    return "email_not_confirmed";
  }
  return "invalid_credentials";
}

async function getAuthCallbackUrl() {
  const headerStore = await headers();
  const origin = headerStore.get("origin");
  const forwardedHost = headerStore.get("x-forwarded-host");
  const forwardedProto = headerStore.get("x-forwarded-proto") ?? "https";
  const fallbackOrigin = forwardedHost ? `${forwardedProto}://${forwardedHost}` : env.appUrl;

  return `${origin ?? fallbackOrigin}/auth/callback`;
}

export async function login(formData: FormData) {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    redirect(safeRedirectPath(formData.get("next")));
  }

  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");
  const nextPath = safeRedirectPath(formData.get("next"));

  let redirectTo: string | null = null;
  try {
    const supabase = await createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });

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
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
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
        emailRedirectTo: await getAuthCallbackUrl(),
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

export async function loginWithGoogle() {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    redirect("/dashboard");
  }

  if (!env.googleOAuthEnabled) {
    redirect("/login?error=google_not_configured");
  }

  let redirectTo: string | null = null;
  try {
    const supabase = await createClient();
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: await getAuthCallbackUrl(),
      },
    });

    if (error || !data.url) {
      console.error("motionly_oauth_failed", error);
      redirectTo = "/login?error=oauth_failed";
    } else {
      redirectTo = data.url;
    }
  } catch (err: any) {
    console.error("motionly_oauth_system_error", err);
    redirectTo = "/login?error=system_error";
  }

  if (redirectTo) {
    redirect(redirectTo);
  }
}

export async function logout() {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
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
