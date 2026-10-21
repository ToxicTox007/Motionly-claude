"use server";

import { revalidatePath } from "next/cache";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

async function adminPost(path: string, body?: unknown) {
  const context = await getAdminApiContext();
  if (!context) throw new Error("Admin access is required.");
  await fetch(`${env.apiBaseUrl}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      ...context.headers,
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
      ...(process.env.ADMIN_API_TOKEN
        ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
        : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  revalidatePath("/admin/ops");
}

export async function retryQueueJob(formData: FormData) {
  const jobId = String(formData.get("jobId") ?? "");
  if (jobId)
    await adminPost(`/admin/queues/jobs/${encodeURIComponent(jobId)}/retry`);
}

export async function cancelQueueJob(formData: FormData) {
  const jobId = String(formData.get("jobId") ?? "");
  if (jobId)
    await adminPost(`/admin/queues/jobs/${encodeURIComponent(jobId)}/cancel`);
}

export async function retryRenderJob(formData: FormData) {
  const jobId = String(formData.get("jobId") ?? "");
  if (jobId)
    await adminPost(`/admin/renders/${encodeURIComponent(jobId)}/retry`);
}

export async function cancelRenderJob(formData: FormData) {
  const jobId = String(formData.get("jobId") ?? "");
  if (jobId)
    await adminPost(`/admin/renders/${encodeURIComponent(jobId)}/cancel`);
}

export async function deadLetterStuckJobs(formData: FormData) {
  // Give up on jobs stuck longer than the chosen timeout by moving them to the
  // dead-letter state (POST /admin/queues/stuck/dead-letter).
  const raw = Number(formData.get("timeoutSeconds"));
  const timeoutSeconds =
    Number.isFinite(raw) && raw >= 1 && raw <= 86_400 ? Math.floor(raw) : 1_800;
  await adminPost("/admin/queues/stuck/dead-letter", {
    timeout_seconds: timeoutSeconds,
  });
}
