"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getAdminApiContext } from "@/lib/api-auth";
import { serverApiFetch } from "@/lib/server-api";

// Admin queue/render controls. Each reports its real outcome back to the page
// (?done= on success, ?error= on failure) instead of discarding the API's
// answer and always looking successful.

type Operation = {
  path: string;
  body?: unknown;
  done: string;
  failed: string;
};

async function runAdminOperation({
  path,
  body,
  done,
  failed,
}: Operation): Promise<never> {
  const context = await getAdminApiContext();
  if (!context) redirect("/dashboard?error=admin_access_denied");
  const result = await serverApiFetch(path, {
    method: "POST",
    headers: {
      ...context.headers,
      ...(process.env.ADMIN_API_TOKEN
        ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
        : {}),
    },
    body,
  });
  revalidatePath("/admin/ops");
  redirect(
    result.ok ? `/admin/ops?done=${done}` : `/admin/ops?error=${failed}`,
  );
}

function jobIdFrom(formData: FormData): string {
  const jobId = String(formData.get("jobId") ?? "").trim();
  if (!jobId) redirect("/admin/ops?error=job_required");
  return jobId;
}

export async function retryQueueJob(formData: FormData): Promise<void> {
  const jobId = jobIdFrom(formData);
  await runAdminOperation({
    path: `/admin/queues/jobs/${encodeURIComponent(jobId)}/retry`,
    done: "retried",
    failed: "retry_failed",
  });
}

export async function cancelQueueJob(formData: FormData): Promise<void> {
  const jobId = jobIdFrom(formData);
  await runAdminOperation({
    path: `/admin/queues/jobs/${encodeURIComponent(jobId)}/cancel`,
    done: "cancelled",
    failed: "cancel_failed",
  });
}

export async function retryRenderJob(formData: FormData): Promise<void> {
  const jobId = jobIdFrom(formData);
  await runAdminOperation({
    path: `/admin/renders/${encodeURIComponent(jobId)}/retry`,
    done: "retried",
    failed: "retry_failed",
  });
}

export async function cancelRenderJob(formData: FormData): Promise<void> {
  const jobId = jobIdFrom(formData);
  await runAdminOperation({
    path: `/admin/renders/${encodeURIComponent(jobId)}/cancel`,
    done: "cancelled",
    failed: "cancel_failed",
  });
}

export async function deadLetterStuckJobs(formData: FormData): Promise<void> {
  // Give up on jobs stuck longer than the chosen timeout by moving them to the
  // dead-letter state (POST /admin/queues/stuck/dead-letter).
  const raw = Number(formData.get("timeoutSeconds"));
  const timeoutSeconds =
    Number.isFinite(raw) && raw >= 1 && raw <= 86_400 ? Math.floor(raw) : 1_800;
  await runAdminOperation({
    path: "/admin/queues/stuck/dead-letter",
    body: { timeout_seconds: timeoutSeconds },
    done: "dead_lettered",
    failed: "dead_letter_failed",
  });
}
