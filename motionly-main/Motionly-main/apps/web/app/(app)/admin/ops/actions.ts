"use server";

import { revalidatePath } from "next/cache";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

async function adminPost(path: string) {
  const context = await getAdminApiContext();
  if (!context) throw new Error("Admin access is required.");
  await fetch(`${env.apiBaseUrl}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      ...context.headers,
      ...(process.env.ADMIN_API_TOKEN
        ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
        : {}),
    },
  });
  revalidatePath("/admin/ops");
}

export async function retryQueueJob(formData: FormData) {
  const jobId = String(formData.get("jobId") ?? "");
  if (jobId) await adminPost(`/admin/queues/jobs/${encodeURIComponent(jobId)}/retry`);
}

export async function cancelQueueJob(formData: FormData) {
  const jobId = String(formData.get("jobId") ?? "");
  if (jobId) await adminPost(`/admin/queues/jobs/${encodeURIComponent(jobId)}/cancel`);
}

export async function retryRenderJob(formData: FormData) {
  const jobId = String(formData.get("jobId") ?? "");
  if (jobId) await adminPost(`/admin/renders/${encodeURIComponent(jobId)}/retry`);
}

export async function cancelRenderJob(formData: FormData) {
  const jobId = String(formData.get("jobId") ?? "");
  if (jobId) await adminPost(`/admin/renders/${encodeURIComponent(jobId)}/cancel`);
}
