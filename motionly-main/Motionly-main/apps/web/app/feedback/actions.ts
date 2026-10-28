"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { serverApiFetch } from "@/lib/server-api";
import { viewerAddressHeaders } from "@/lib/viewer-address.mjs";

// Public (signed-out) feedback form. Inputs are bounded here before anything
// reaches the API, and the viewer's address is forwarded so the API's
// per-viewer rate limit applies to the viewer, not to the web server.

// Mirrors apps/api/app/schemas/feedback.py.
const CATEGORIES = new Set([
  "general",
  "bug",
  "render_quality",
  "billing",
  "feature_request",
]);
const FEEDBACK_MIN_LENGTH = 10;
const FEEDBACK_MAX_LENGTH = 5000;
const PAGE_URL_MAX_LENGTH = 2000;

/** A same-origin path the feedback is about, or null. */
function safePagePath(raw: string): string | null {
  if (!raw.startsWith("/") || raw.startsWith("//") || /[\\\s]/.test(raw)) {
    return null;
  }
  return raw.slice(0, PAGE_URL_MAX_LENGTH);
}

export async function submitFeedback(formData: FormData) {
  const rawCategory = String(formData.get("category") ?? "general");
  const category = CATEGORIES.has(rawCategory) ? rawCategory : "general";
  const message = String(formData.get("message") ?? "").trim();
  const pageUrl = safePagePath(String(formData.get("pageUrl") ?? "").trim());

  if (message.length < FEEDBACK_MIN_LENGTH) {
    redirect("/feedback?error=message_required");
  }
  if (message.length > FEEDBACK_MAX_LENGTH) {
    redirect("/feedback?error=message_too_long");
  }

  const result = await serverApiFetch("/feedback", {
    method: "POST",
    headers: viewerAddressHeaders(await headers()),
    body: { category, message, page_url: pageUrl },
  });
  if (!result.ok) redirect("/feedback?error=feedback_failed");
  redirect("/feedback?sent=1");
}
