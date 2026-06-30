"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export async function updateProfile(formData: FormData) {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    redirect("/account?updated=1");
  }

  const fullName = String(formData.get("fullName") ?? "").trim();
  const company = String(formData.get("company") ?? "").trim();
  const role = String(formData.get("role") ?? "").trim();

  const supabase = await createClient();
  const { error } = await supabase.rpc("update_own_profile", {
    profile_full_name: fullName,
    profile_company: company,
    profile_role: role,
  });

  if (error) {
    redirect(`/account?error=${encodeURIComponent(error.code ?? "profile_update_failed")}`);
  }

  revalidatePath("/account");
  redirect("/account?updated=1");
}

export async function updatePassword(formData: FormData) {
  if (process.env.SKIP_AUTH_MIDDLEWARE === "true") {
    redirect("/account?password=1");
  }

  const password = String(formData.get("password") ?? "");
  const confirmPassword = String(formData.get("confirmPassword") ?? "");

  if (password.length < 8 || password !== confirmPassword) {
    redirect("/account?error=password_mismatch");
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.updateUser({ password });

  if (error) {
    redirect(`/account?error=${encodeURIComponent(error.code ?? "password_update_failed")}`);
  }

  redirect("/account?password=1");
}
