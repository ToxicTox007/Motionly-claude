"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { getWorkspaceApiContext } from "@/lib/api-auth";
import { failureCode, serverApiFetch } from "@/lib/server-api";

// Every API outcome becomes an allowlisted ?error= code (lib/error-messages.ts)
// rather than a crash: a 502 HTML page or an empty body never reaches
// response.json() unchecked.

const STATUSES = new Set(["draft", "ready", "failed"]);
const MAX_TITLE_LENGTH = 120;

function projectPath(projectId: string) {
  return `/projects/${encodeURIComponent(projectId)}`;
}

export async function createProject(formData: FormData) {
  const title =
    String(formData.get("title") ?? "Untitled reel")
      .trim()
      .slice(0, MAX_TITLE_LENGTH) || "Untitled reel";

  const context = await getWorkspaceApiContext();
  if (!context) {
    redirect(`/login?next=${encodeURIComponent("/projects/new")}`);
  }

  const result = await serverApiFetch<{ id?: unknown }>("/projects", {
    method: "POST",
    headers: context.headers,
    body: { title },
  });
  const projectId =
    result.ok && typeof result.data?.id === "string" ? result.data.id : null;
  if (!projectId) {
    redirect(
      `/projects/new?error=${result.ok ? "create_failed" : failureCode(result, "create_failed")}`,
    );
  }

  redirect(projectPath(projectId));
}

export async function updateProject(formData: FormData) {
  const projectId = String(formData.get("projectId") ?? "").trim();
  const titleValue = formData.get("title");
  const statusValue = formData.get("status");

  if (!projectId) redirect("/projects?error=missing_project");

  const payload: { title?: string; status?: string } = {};
  if (typeof titleValue === "string") {
    const title = titleValue.trim();
    // Clearing the title used to "succeed" while silently keeping the old one.
    if (!title) redirect("/projects?error=title_required");
    payload.title = title.slice(0, MAX_TITLE_LENGTH);
  }
  if (typeof statusValue === "string" && statusValue !== "") {
    if (!STATUSES.has(statusValue)) redirect("/projects?error=status_invalid");
    payload.status = statusValue;
  }

  const context = await getWorkspaceApiContext();
  if (!context) redirect(`/login?next=${encodeURIComponent("/projects")}`);

  const result = await serverApiFetch<{ id?: unknown }>(
    projectPath(projectId),
    { method: "PATCH", headers: context.headers, body: payload },
  );
  if (!result.ok || typeof result.data?.id !== "string") {
    redirect(
      `/projects?error=${result.ok ? "update_failed" : failureCode(result, "update_failed")}`,
    );
  }

  revalidatePath("/projects");
  revalidatePath("/dashboard");
  revalidatePath(projectPath(projectId));
  redirect("/projects?updated=1");
}

export async function deleteProject(formData: FormData) {
  const projectId = String(formData.get("projectId") ?? "").trim();
  if (!projectId) redirect("/projects?error=missing_project");

  const result = await deleteProjectResult(projectId);
  if (!result.ok) redirect(`/projects?error=${result.error}`);
  redirect("/projects?deleted=1");
}

export type DeleteProjectResult = { ok: true } | { ok: false; error: string };

/**
 * Delete a project and report the real outcome instead of relying on a redirect
 * throwing NEXT_REDIRECT (B34). The dashboard modal calls this so a failed
 * delete surfaces an error toast and keeps the card on screen.
 */
export async function deleteProjectResult(
  projectId: string,
): Promise<DeleteProjectResult> {
  if (!projectId) {
    return { ok: false, error: "missing_project" };
  }

  const context = await getWorkspaceApiContext();
  if (!context) {
    return { ok: false, error: "not_authenticated" };
  }

  const result = await serverApiFetch<{ deleted?: unknown }>(
    projectPath(projectId),
    { method: "DELETE", headers: context.headers },
  );
  if (!result.ok) {
    return { ok: false, error: failureCode(result, "delete_failed") };
  }
  if (result.data?.deleted !== true) {
    return { ok: false, error: "delete_failed" };
  }

  revalidatePath("/projects");
  revalidatePath("/dashboard");
  return { ok: true };
}
