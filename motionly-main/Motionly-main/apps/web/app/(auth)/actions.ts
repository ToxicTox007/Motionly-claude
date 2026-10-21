"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { env } from "@/lib/env";
import { safeNextPath } from "@/lib/safe-redirect.mjs";
import { signupErrorReason } from "@/lib/signup.mjs";
import { passwordProblem } from "@/lib/password-policy.mjs";
import { resetRequestErrorReason } from "@/lib/password-reset.mjs";
import { viewerAddressHeaders } from "@/lib/viewer-address.mjs";

function authErrorReason(error: { code?: string; status?: number }) {
  if (error.code === "over_request_rate_limit" || error.status === 429) {
    return "rate_limited";
  }
  return "invalid_credentials";
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

// Auth's own sign-up is disabled (GOTRUE_DISABLE_SIGNUP). The API creates
// the account, already confirmed, through Auth's admin API; nothing emails a
// link. The new user is then signed in with the password they chose.
export async function signup(formData: FormData) {
  // A safe post-signup destination (e.g. back to /invite). Defaults to the
  // dashboard; anything that could read as another site is rejected.
  const nextPath = safeNextPath(formData.get("next"), env.appUrl);
  if (env.skipAuthMiddleware) {
    redirect(nextPath);
  }

  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

  let redirectTo: string | null = null;
  try {
    const response = await fetch(`${env.apiBaseUrl}/auth/signup`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        // The API rate-limits sign-ups per viewer, not per web server.
        ...viewerAddressHeaders(await headers()),
      },
      body: JSON.stringify({
        full_name: String(formData.get("name") ?? ""),
        email,
        password,
      }),
      cache: "no-store",
    });
    if (response.status !== 201) {
      const payload = (await response.json().catch(() => null)) as {
        error?: { code?: string };
      } | null;
      const reason = signupErrorReason(response.status, payload?.error?.code);
      console.warn("motionly_signup_refused", {
        status: response.status,
        code: payload?.error?.code,
      });
      redirectTo = `/signup?error=${reason}`;
    } else {
      const created = (await response.json()) as { email: string };
      const supabase = await createClient();
      const { error } = await supabase.auth.signInWithPassword({
        email: created.email,
        password,
      });
      if (error) {
        // The account exists; only the automatic sign-in failed. Keep the
        // destination so the user lands where the invite intended after login.
        console.error("motionly_signup_sign_in_failed", {
          code: error.code,
          status: error.status,
        });
        redirectTo = `/login?notice=account_created&next=${encodeURIComponent(nextPath)}`;
      } else {
        redirectTo = nextPath;
      }
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

// A forgotten-password request. The API records it (recording unknown emails
// too, so there is no enumeration) and always answers 202; the page shows the
// same confirmation whatever the email was. An admin issues a recovery link
// from the Users page — nothing is emailed.
export async function requestPasswordReset(formData: FormData) {
  const email = String(formData.get("email") ?? "");

  let redirectTo = "/forgot-password?sent=1";
  if (!env.skipAuthMiddleware) {
    try {
      const response = await fetch(
        `${env.apiBaseUrl}/auth/password-reset-requests`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            // The API rate-limits per viewer, not per web server.
            ...viewerAddressHeaders(await headers()),
          },
          body: JSON.stringify({ email }),
          cache: "no-store",
        },
      );
      if (response.status !== 202) {
        const reason = resetRequestErrorReason(response.status);
        console.warn("motionly_password_reset_request_refused", {
          status: response.status,
        });
        redirectTo = `/forgot-password?error=${reason}`;
      }
    } catch (err: any) {
      console.error("motionly_password_reset_request_error", err);
      redirectTo = "/forgot-password?error=system_error";
    }
  }
  redirect(redirectTo);
}

// Set a new password from a recovery session, then end every OTHER session so
// a stolen-but-reset password cannot be reused elsewhere. The policy mirrors
// sign-up (lib/password-policy.mjs, a mirror of the Python source).
export async function resetPassword(formData: FormData) {
  if (env.skipAuthMiddleware) {
    redirect("/dashboard");
  }

  const password = String(formData.get("password") ?? "");
  const problem = passwordProblem(password);
  if (problem) {
    redirect("/reset-password?error=password_weak");
  }

  let redirectTo: string | null = null;
  try {
    const supabase = await createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      redirectTo = "/reset-password?error=no_session";
    } else {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) {
        console.error("motionly_reset_password_failed", {
          code: error.code,
          status: error.status,
        });
        redirectTo =
          error.code === "same_password"
            ? "/reset-password?error=same_password"
            : error.status === 422
              ? "/reset-password?error=password_weak"
              : "/reset-password?error=system_error";
      } else {
        // Invalidate every other session (the recovery one stays signed in).
        await supabase.auth.signOut({ scope: "others" });
        redirectTo = "/dashboard?notice=password_updated";
      }
    }
  } catch (err: any) {
    console.error("motionly_reset_password_system_error", err);
    redirectTo = "/reset-password?error=system_error";
  }

  if (redirectTo) {
    redirect(redirectTo);
  }
}
