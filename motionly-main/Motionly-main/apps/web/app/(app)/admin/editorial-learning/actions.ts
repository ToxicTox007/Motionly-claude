"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getAdminApiContext } from "@/lib/api-auth";
import { failureCode, serverApiFetch } from "@/lib/server-api";

function operationUrl(projectId: string, result: string) {
  const [key, value] = result.split("=", 2);
  return `/admin/editorial-learning?projectId=${encodeURIComponent(projectId)}&${key}=${value ?? "1"}`;
}

/** Posts an editorial operation; returns an allowlist-safe error code or null. */
async function adminEditorialPost(
  path: string,
  body: Record<string, unknown>,
): Promise<string | null> {
  const context = await getAdminApiContext();
  if (!context) redirect("/dashboard?error=admin_required");
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
  // Only a code ever reaches the redirect, never upstream response content.
  if (!result.ok) return failureCode(result, "editorial_operation_failed");
  return null;
}

export async function promoteEditorialDataset(formData: FormData) {
  const projectId = String(formData.get("projectId") ?? "");
  const datasetId = String(formData.get("datasetId") ?? "");
  const expectedRevision = Number(formData.get("expectedRevision"));
  if (!projectId || !datasetId || formData.get("confirmation") !== "PROMOTE") {
    redirect(operationUrl(projectId, "error=promotion_confirmation_required"));
  }
  if (!Number.isInteger(expectedRevision) || expectedRevision < 0) {
    redirect(operationUrl(projectId, "error=deployment_revision_required"));
  }
  const code = await adminEditorialPost(
    `/admin/editorial-learning/datasets/${encodeURIComponent(datasetId)}/promote?project_id=${encodeURIComponent(projectId)}`,
    { expected_revision: expectedRevision },
  );
  if (code) redirect(operationUrl(projectId, `error=${code}`));
  revalidatePath("/admin/editorial-learning");
  redirect(operationUrl(projectId, "promoted"));
}

export async function rollbackEditorialDeployment(formData: FormData) {
  const projectId = String(formData.get("projectId") ?? "");
  const reason =
    String(formData.get("reason") ?? "admin_rollback").trim() ||
    "admin_rollback";
  if (!projectId || formData.get("confirmation") !== "ROLLBACK") {
    redirect(operationUrl(projectId, "error=rollback_confirmation_required"));
  }
  const code = await adminEditorialPost(
    `/admin/editorial-learning/deployments/${encodeURIComponent(projectId)}/rollback`,
    { reason: reason.slice(0, 160) },
  );
  if (code) redirect(operationUrl(projectId, `error=${code}`));
  revalidatePath("/admin/editorial-learning");
  redirect(operationUrl(projectId, "rolled_back"));
}
