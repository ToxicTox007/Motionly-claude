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

  const supabase = await createClient();

  const { error } = await supabase.auth.signInWithPassword({ email, password });

  if (error) {
    console.error("motionly_login_failed", {
      code: error.code,
      status: error.status,
      message: error.message,
    });
    redirect(`/login?error=${authErrorReason(error)}`);
  }

  redirect(nextPath);
}

export async function signup(formData: FormData) {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    redirect("/dashboard");
  }

  const fullName = String(formData.get("name") ?? "");
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

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
    redirect(`/signup?error=${authErrorReason(error)}`);
  }

  redirect(data.session ? "/dashboard" : "/login?notice=check_email");
}

export async function loginWithGoogle() {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    redirect("/dashboard");
  }

  if (!env.googleOAuthEnabled) {
    redirect("/login?error=google_not_configured");
  }

  const supabase = await createClient();
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: {
      redirectTo: await getAuthCallbackUrl(),
    },
  });

  if (error || !data.url) {
    redirect("/login?error=oauth_failed");
  }

  redirect(data.url);
}

export async function logout() {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    redirect("/login");
  }

  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}
