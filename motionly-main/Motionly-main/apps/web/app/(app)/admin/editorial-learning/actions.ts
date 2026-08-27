"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getAdminApiContext } from "@/lib/api-auth";
import { env } from "@/lib/env";

function operationUrl(projectId: string, result: string) {
  const [key, value] = result.split("=", 2);
  return `/admin/editorial-learning?projectId=${encodeURIComponent(projectId)}&${key}=${value ?? "1"}`;
}

async function adminEditorialPost(path: string, body: Record<string, unknown>) {
  const context = await getAdminApiContext();
  if (!context) throw new Error("Admin access is required.");
  const response = await fetch(`${env.apiBaseUrl}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      ...context.headers,
      ...(process.env.ADMIN_API_TOKEN
        ? { "x-motionly-admin-token": process.env.ADMIN_API_TOKEN }
        : {}),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let code = "editorial_operation_failed";
    try {
      const payload = await response.json();
      if (typeof payload?.code === "string") code = payload.code;
    } catch {
      // Keep the browser redirect free of upstream response content.
    }
    throw new Error(code);
  }
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
  try {
    await adminEditorialPost(
      `/admin/editorial-learning/datasets/${encodeURIComponent(datasetId)}/promote?project_id=${encodeURIComponent(projectId)}`,
      { expected_revision: expectedRevision },
    );
  } catch (error) {
    const code = error instanceof Error ? error.message : "editorial_operation_failed";
    redirect(operationUrl(projectId, `error=${encodeURIComponent(code)}`));
  }
  revalidatePath("/admin/editorial-learning");
  redirect(operationUrl(projectId, "promoted"));
}

export async function rollbackEditorialDeployment(formData: FormData) {
  const projectId = String(formData.get("projectId") ?? "");
  const reason = String(formData.get("reason") ?? "admin_rollback").trim() || "admin_rollback";
  if (!projectId || formData.get("confirmation") !== "ROLLBACK") {
    redirect(operationUrl(projectId, "error=rollback_confirmation_required"));
  }
  try {
    await adminEditorialPost(
      `/admin/editorial-learning/deployments/${encodeURIComponent(projectId)}/rollback`,
      { reason: reason.slice(0, 160) },
    );
  } catch (error) {
    const code = error instanceof Error ? error.message : "editorial_operation_failed";
    redirect(operationUrl(projectId, `error=${encodeURIComponent(code)}`));
  }
  revalidatePath("/admin/editorial-learning");
  redirect(operationUrl(projectId, "rolled_back"));
}
