"use server";

import { redirect } from "next/navigation";
import { env } from "@/lib/env";

export async function submitFeedback(formData: FormData) {
  const category = String(formData.get("category") ?? "general");
  const message = String(formData.get("message") ?? "").trim();
  const pageUrl = String(formData.get("pageUrl") ?? "").trim();
  const response = await fetch(`${env.apiBaseUrl}/feedback`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ category, message, page_url: pageUrl || null }),
    cache: "no-store",
  });
  if (!response.ok) redirect("/feedback?error=save_failed");
  redirect("/feedback?sent=1");
}
