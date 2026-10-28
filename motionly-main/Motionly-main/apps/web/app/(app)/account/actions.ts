"use server";

import { revalidatePath } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getSessionSnapshot } from "@/lib/api-auth";
import { env } from "@/lib/env";
import { passwordProblem } from "@/lib/password-policy.mjs";
import { verifyCurrentPassword } from "@/lib/supabase/password-check";
import { viewerAddressHeaders } from "@/lib/viewer-address.mjs";

export async function updateProfile(formData: FormData) {
  if (env.skipAuthMiddleware) {
    redirect("/account?updated=1");
  }

  const fullName = String(formData.get("fullName") ?? "").trim();
  const company = String(formData.get("company") ?? "").trim();
  const role = String(formData.get("role") ?? "").trim();

  const { supabase } = await getSessionSnapshot();
  const { error } = await supabase.rpc("update_own_profile", {
    profile_full_name: fullName,
    profile_company: company,
    profile_role: role,
  });

  if (error) {
    // The page maps codes to copy; a database code is never shown raw.
    redirect("/account?error=profile_update_failed");
  }

  revalidatePath("/account");
  redirect("/account?updated=1");
}

/**
 * Change the password of the signed-in account. It requires the current
 * password (a hijacked session alone must not be enough), applies the same
 * policy as sign-up and reset (lib/password-policy.mjs), and on success ends
 * every other session, as a reset does.
 */
export async function updatePassword(formData: FormData) {
  if (env.skipAuthMiddleware) {
    redirect("/account?password=1");
  }

  const currentPassword = String(formData.get("currentPassword") ?? "");
  const password = String(formData.get("password") ?? "");
  const confirmPassword = String(formData.get("confirmPassword") ?? "");

  if (!currentPassword) redirect("/account?error=current_password_required");
  if (password !== confirmPassword)
    redirect("/account?error=password_mismatch");

  const { supabase, user } = await getSessionSnapshot();
  if (!user?.email) redirect("/login?next=%2Faccount");
  if (passwordProblem(password, user.email)) {
    redirect("/account?error=password_weak");
  }

  const check = await verifyCurrentPassword(
    user.email,
    currentPassword,
    viewerAddressHeaders(await headers()),
  );
  if (check === "wrong") redirect("/account?error=current_password_wrong");
  if (check === "rate_limited") redirect("/account?error=rate_limited");
  if (check !== "ok") redirect("/account?error=system_error");

  const { error } = await supabase.auth.updateUser({ password });
  if (error) {
    redirect(
      error.code === "same_password"
        ? "/account?error=same_password"
        : error.status === 422
          ? "/account?error=password_weak"
          : error.status === 429
            ? "/account?error=rate_limited"
            : "/account?error=system_error",
    );
  }

  // A changed password must not leave a stolen session elsewhere alive.
  await supabase.auth.signOut({ scope: "others" });
  redirect("/account?password=1");
}
